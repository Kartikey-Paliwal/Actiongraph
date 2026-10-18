"""Benchmark ActionGraph on a fixed, labelled synthetic scenario suite.

Resets the `actiongraph` graph before every scenario run, so do not run it
against a graph you want to keep. Usage:

    python scripts/benchmark.py --iterations 20
    python scripts/benchmark.py --iterations 20 --json

These numbers describe the known synthetic cases only. They are not a claim
about general safety or about any other workload.
"""

import argparse
import json
import logging
import os
import platform
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.execution.readback import billing_verification  # noqa: E402
from app.execution.runner import approve_plan, execute_plan  # noqa: E402
from app.graph.client import connect, graph, ping  # noqa: E402
from app.graph.seed import reset_scenario  # noqa: E402
from app.planning.fixtures import (  # noqa: E402
    DELETE_BILLING_DATABASE,
    DISABLE_CONTRACTOR,
    SAFE_OFFBOARD,
    UNSAFE_OFFBOARD,
)
from app.plans.service import create_and_preflight  # noqa: E402

ADD_SECOND_OWNER = """
MATCH (engineer:Identity {id: 'id-engineer-9'})
MATCH (billing:Service {id: 'svc-billing'})
MERGE (engineer)-[:OWNS]->(billing)
"""

REMOVE_SECOND_OWNER = """
MATCH (:Identity {id: 'id-engineer-9'})-[rel:OWNS]->(:Service {id: 'svc-billing'})
DELETE rel
"""

INELIGIBLE_REPLACEMENT = {
    "scenario": "synthetic_identity_offboarding",
    "requires_approval": True,
    "actions": [
        {"type": "assign_service_owner", "target_id": "svc-billing", "params": {"new_owner_id": "id-former-4"}},
        {"type": "disable_identity", "target_id": "id-contractor-17"},
    ],
}

REVOKE_ONLY = {
    "scenario": "synthetic_identity_offboarding",
    "requires_approval": True,
    "actions": [{"type": "revoke_credential", "target_id": "credential-deploy-4"}],
}

# label "unsafe" must not PASS; label "safe" must PASS.
PREFLIGHT_SUITE = [
    ("unsafe_offboard", "unsafe", UNSAFE_OFFBOARD, None, "BLOCK"),
    ("disable_sole_owner", "unsafe", DISABLE_CONTRACTOR, None, "BLOCK"),
    ("ineligible_replacement", "unsafe", INELIGIBLE_REPLACEMENT, None, "BLOCK"),
    ("delete_shared_database", "unsafe", DELETE_BILLING_DATABASE, None, "BLOCK"),
    ("revoke_critical_credential", "unsafe", REVOKE_ONLY, None, "NEEDS_REVIEW"),
    ("safe_handoff", "safe", SAFE_OFFBOARD, None, "PASS"),
    ("disable_with_second_owner", "safe", DISABLE_CONTRACTOR, ADD_SECOND_OWNER, "PASS"),
]

# (name, failure injection, expected final status)
EXECUTION_SUITE = [
    ("approved_handoff", None, "VERIFIED"),
    ("timeout_state_unchanged", "timeout_unchanged", "VERIFIED"),
    ("timeout_readback_failed", "timeout_unknown", "UNKNOWN"),
    ("adapter_success_without_write", "success_without_write", "PARTIAL_FAILURE"),
]


def _percentile(values, fraction):
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(round(fraction * (len(ordered) - 1)))))
    return ordered[index]


def _falkordb_version(db):
    for module in db.connection.execute_command("MODULE", "LIST"):
        fields = dict(zip(module[::2], module[1::2]))
        if fields.get("name") == "graph":
            ver = int(fields.get("ver", 0))
            return "%s.%s.%s" % (ver // 10000, (ver // 100) % 100, ver % 100)
    return "unknown"


def run_preflight_suite(db, iterations):
    rows = []
    for name, label, payload, setup, expected in PREFLIGHT_SUITE:
        latencies = []
        verdicts = []
        with_evidence = 0
        for _ in range(iterations):
            reset_scenario(db)
            if setup:
                graph(db).query(setup)
            started = time.perf_counter()
            result = create_and_preflight(payload, db)
            latencies.append((time.perf_counter() - started) * 1000)
            verdicts.append(result["verdict"])
            if result["verdict"] != "PASS" and result["invariant"]["id"] and result["evidence"].get("graph_path"):
                with_evidence += 1
        rows.append(
            {
                "scenario": name,
                "label": label,
                "expected": expected,
                "verdicts": sorted(set(verdicts)),
                "correct": sum(1 for verdict in verdicts if verdict == expected),
                "runs": iterations,
                "blocked_or_review": sum(1 for verdict in verdicts if verdict != "PASS"),
                "with_evidence": with_evidence,
                "latencies_ms": latencies,
            }
        )
    return rows


def run_ablation(db, iterations):
    flips = 0
    for _ in range(iterations):
        reset_scenario(db)
        graph(db).query(ADD_SECOND_OWNER)
        first = create_and_preflight(DISABLE_CONTRACTOR, db)["verdict"]
        graph(db).query(REMOVE_SECOND_OWNER)
        second = create_and_preflight(DISABLE_CONTRACTOR, db)["verdict"]
        if first == "PASS" and second == "BLOCK":
            flips += 1
    return {"runs": iterations, "pass_then_block": flips}


def run_execution_suite(db, iterations):
    rows = []
    for name, injection, expected in EXECUTION_SUITE:
        correct = 0
        verified_labels = 0
        verified_and_true = 0
        for index in range(iterations):
            reset_scenario(db)
            plan_id = create_and_preflight(SAFE_OFFBOARD, db)["plan_id"]
            approve_plan(plan_id, "id-engineer-9", "benchmark approval", db)
            inject = {"action_type": "disable_identity", "mode": injection} if injection else None
            result = execute_plan(plan_id, "bench-%s-%s" % (name, index), db, injection=inject)
            if result["status"] == expected:
                correct += 1
            if result["status"] == "VERIFIED":
                verified_labels += 1
                if billing_verification(db)["invariant_holds"]:
                    verified_and_true += 1
        rows.append(
            {
                "scenario": name,
                "expected": expected,
                "correct": correct,
                "runs": iterations,
                "verified_labels": verified_labels,
                "verified_and_postcondition_true": verified_and_true,
            }
        )
    return rows


def summarize(preflight, ablation, execution, environment):
    unsafe = [row for row in preflight if row["label"] == "unsafe"]
    safe = [row for row in preflight if row["label"] == "safe"]
    unsafe_runs = sum(row["runs"] for row in unsafe)
    safe_runs = sum(row["runs"] for row in safe)
    non_pass = sum(row["blocked_or_review"] for row in unsafe)
    evidence = sum(row["with_evidence"] for row in unsafe)
    latencies = [value for row in preflight for value in row["latencies_ms"]]
    verified = sum(row["verified_labels"] for row in execution)
    verified_true = sum(row["verified_and_postcondition_true"] for row in execution)
    return {
        "environment": environment,
        "unsafe_plans_not_passed": "%s/%s" % (non_pass, unsafe_runs),
        "safe_plans_falsely_blocked": "%s/%s" % (sum(row["blocked_or_review"] for row in safe), safe_runs),
        "blocks_with_invariant_and_path": "%s/%s" % (evidence, non_pass),
        "expected_verdict": "%s/%s" % (sum(row["correct"] for row in preflight), unsafe_runs + safe_runs),
        "expected_execution_status": "%s/%s" % (sum(row["correct"] for row in execution), sum(row["runs"] for row in execution)),
        "verified_labels_with_true_postcondition": "%s/%s" % (verified_true, verified),
        "edge_ablation_pass_then_block": "%s/%s" % (ablation["pass_then_block"], ablation["runs"]),
        "preflight_latency_ms": {
            "p50": round(statistics.median(latencies), 1),
            "p95": round(_percentile(latencies, 0.95), 1),
            "max": round(max(latencies), 1),
            "samples": len(latencies),
            "includes": "plan validation, graph state read, evaluation, repair query on orphaning blocks, Plan and Decision writes",
        },
        "preflight": [{k: v for k, v in row.items() if k != "latencies_ms"} for row in preflight],
        "execution": execution,
    }


def to_markdown(summary):
    env = summary["environment"]
    latency = summary["preflight_latency_ms"]
    lines = [
        "| Measure | Result |",
        "| --- | --- |",
        "| Unsafe plans not passed (BLOCK or NEEDS_REVIEW) | %s |" % summary["unsafe_plans_not_passed"],
        "| Safe plans falsely blocked | %s |" % summary["safe_plans_falsely_blocked"],
        "| BLOCK / NEEDS_REVIEW verdicts with a named invariant and evidence path | %s |" % summary["blocks_with_invariant_and_path"],
        "| Exact expected verdict | %s |" % summary["expected_verdict"],
        "| Exact expected execution status | %s |" % summary["expected_execution_status"],
        "| `VERIFIED` labels whose postcondition read back true | %s |" % summary["verified_labels_with_true_postcondition"],
        "| Edge ablation: PASS, then BLOCK after the edge is removed | %s |" % summary["edge_ablation_pass_then_block"],
        "| Preflight latency p50 / p95 / max | %s / %s / %s ms (%s samples) |"
        % (latency["p50"], latency["p95"], latency["max"], latency["samples"]),
        "",
        "Environment: %s, Python %s, FalkorDB %s in Docker, %s iterations per scenario, graph reset before each run. "
        "Latency includes %s. No model is involved, so there is no model latency."
        % (env["machine"], env["python"], env["falkordb"], env["iterations"], latency["includes"]),
    ]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Benchmark ActionGraph on the synthetic scenario suite")
    parser.add_argument("--iterations", type=int, default=20)
    parser.add_argument("--json", action="store_true", dest="as_json")
    args = parser.parse_args(argv)
    if args.iterations < 1:
        parser.error("--iterations must be at least 1")
    if os.environ.get("ACTIONGRAPH_LOG_EVENTS") != "1":
        logging.getLogger("actiongraph.events").setLevel(logging.WARNING)

    db = ping(connect())
    environment = {
        "machine": "%s %s" % (platform.system(), platform.machine()),
        "python": platform.python_version(),
        "falkordb": _falkordb_version(db),
        "iterations": args.iterations,
    }
    preflight = run_preflight_suite(db, args.iterations)
    ablation = run_ablation(db, args.iterations)
    execution = run_execution_suite(db, args.iterations)
    reset_scenario(db)

    summary = summarize(preflight, ablation, execution, environment)
    print(json.dumps(summary, indent=2) if args.as_json else to_markdown(summary))
    return 0


if __name__ == "__main__":
    sys.exit(main())
