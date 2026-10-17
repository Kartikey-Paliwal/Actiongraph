"""Day 1 commands: health, reset, and ownership impact."""

import argparse
import json
import sys

from app.execution.runner import approve_plan, execute_plan
from app.graph.client import ping
from app.graph.queries import ownership_impact
from app.graph.seed import reset_scenario
from app.planning.fixtures import DELETE_BILLING_DATABASE, SAFE_OFFBOARD, UNSAFE_OFFBOARD
from app.plans.service import create_and_preflight

_FIXTURES = {
    "unsafe": UNSAFE_OFFBOARD,
    "safe": SAFE_OFFBOARD,
    "delete-resource": DELETE_BILLING_DATABASE,
}


def _print_impact(impact):
    print("graph: %s" % impact["graph"])
    print("graph_version: %s" % impact["graph_version"])
    print("identity: %s" % impact["identity_id"])
    print("owns_critical_service: %s" % str(impact["owns_critical_service"]).lower())
    limit = impact["traversal"]
    print(
        "traversal: %s, max_hops=%s, result_limit=%s, truncated=%s"
        % (limit["pattern"], limit["max_hops"], limit["result_limit"], str(limit["truncated"]).lower())
    )
    print("")
    print("critical services:")
    if not impact["critical_services"]:
        print("  (none)")
    for service in impact["critical_services"]:
        print(
            "  %s  criticality=%s  status=%s  counts_as_eligible_owner=%s"
            % (
                service["service_id"],
                service["criticality"],
                service["status"],
                str(service["counts_as_eligible_owner"]).lower(),
            )
        )
    print("")
    print("invariants:")
    if not impact["invariants"]:
        print("  (none)")
    for rule in impact["invariants"]:
        print("  %s protects %s" % (rule["id"], rule["service_id"]))
    print("")
    print("evidence paths:")
    if not impact["evidence_paths"]:
        print("  (none)")
    for path in impact["evidence_paths"]:
        hop_label = "hop" if path["hops"] == 1 else "hops"
        print("  [%s %s] %s" % (path["hops"], hop_label, path["reason"]))
        for step in path["steps"]:
            print("    %s" % step)
        if "counts_as_eligible_owner" in path:
            print("    counts_as_eligible_owner=%s" % str(path["counts_as_eligible_owner"]).lower())
    print("")
    print("ignored dependencies:")
    if not impact["ignored_dependencies"]:
        print("  (none)")
    for path in impact["ignored_dependencies"]:
        print("  %s" % path["reason"])
        for step in path["steps"]:
            print("    %s" % step)


def _run_fixture(name, inject):
    preflight = create_and_preflight(_FIXTURES[name])
    if preflight["verdict"] != "PASS":
        return preflight
    approve_plan(preflight["plan_id"], "id-engineer-9", "Replacement owner is active and eligible")
    injection = {"action_type": "disable_identity", "mode": inject} if inject else None
    return execute_plan(preflight["plan_id"], "exec-%s" % preflight["plan_id"], injection=injection)


def main(argv=None):
    parser = argparse.ArgumentParser(description="ActionGraph graph foundation commands")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("health", help="Check that FalkorDB answers a ping")
    sub.add_parser("reset", help="Delete the scenario graph and load synthetic seed data")

    preflight = sub.add_parser("preflight", help="Create a fixture plan and return the graph verdict")
    preflight.add_argument("--fixture", required=True, choices=sorted(_FIXTURES))

    run = sub.add_parser("run", help="Preflight a fixture, approve it when it passes, and execute it")
    run.add_argument("--fixture", required=True, choices=["safe", "unsafe"])
    run.add_argument("--inject", choices=["timeout_unchanged", "timeout_unknown", "success_without_write"])

    impact = sub.add_parser("impact", help="Show critical-service ownership evidence for an identity")
    impact.add_argument("--identity", default="id-contractor-17")
    impact.add_argument("--json", action="store_true", dest="as_json")

    args = parser.parse_args(argv)
    try:
        if args.command == "health":
            ping()
            print("falkordb: ok")
            return 0
        if args.command == "preflight":
            print(json.dumps(create_and_preflight(_FIXTURES[args.fixture]), indent=2))
            return 0
        if args.command == "run":
            print(json.dumps(_run_fixture(args.fixture, args.inject), indent=2))
            return 0
        if args.command == "reset":
            summary = reset_scenario()
            print("graph: %s" % summary["graph"])
            print("graph_version: %s" % summary["graph_version"])
            print("seeded:")
            for label, count in summary["counts"].items():
                print("  %s: %s" % (label, count))
            return 0
        impact_result = ownership_impact(args.identity)
        if args.as_json:
            print(json.dumps(impact_result, indent=2))
        else:
            _print_impact(impact_result)
        return 0
    except Exception as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
