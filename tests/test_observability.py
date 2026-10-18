import json
import logging
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from app.execution.runner import approve_plan, execute_plan, reconcile_execution
from app.main import create_app
from app.observability import emit, reset_metrics, snapshot
from app.planning.fixtures import SAFE_OFFBOARD, UNSAFE_OFFBOARD
from app.plans.service import create_and_preflight, create_plan

_ROOT = Path(__file__).resolve().parents[1]


def _approved(db):
    plan_id = create_and_preflight(SAFE_OFFBOARD, db)["plan_id"]
    approve_plan(plan_id, "id-engineer-9", "test approval", db)
    return plan_id


def test_blocked_preflight_emits_events_and_latency(fresh_graph):
    reset_metrics()
    create_and_preflight(UNSAFE_OFFBOARD, fresh_graph)
    metrics = snapshot()

    for event in ("plan.validated", "plan.created", "plan.preflighted", "invariant.blocked", "repair.suggested"):
        assert metrics["counters"][event] == 1
    assert metrics["block_reasons"] == {"CRITICAL_SERVICE_WOULD_BE_ORPHANED": 1}
    assert metrics["latency_ms"]["preflight_ms"]["count"] == 1
    assert metrics["latency_ms"]["preflight_ms"]["p50"] > 0
    blocked = [event for event in metrics["recent_events"] if event["event"] == "invariant.blocked"][0]
    assert blocked["rule_id"] == "INV-CRITICAL-SERVICE-OWNER"
    assert blocked["plan_id"]


def test_verified_run_emits_every_step_event(fresh_graph):
    plan_id = _approved(fresh_graph)
    reset_metrics()
    execute_plan(plan_id, "exec-obs-1", fresh_graph)
    metrics = snapshot()

    assert metrics["counters"]["execution.started"] == 1
    assert metrics["counters"]["action.started"] == 2
    assert metrics["counters"]["action.response_received"] == 2
    assert metrics["counters"]["action.readback_completed"] == 2
    assert metrics["verified"] == 1
    assert metrics["verification_rate"] == 1.0
    events = [event["event"] for event in metrics["recent_events"]]
    assert events.index("action.readback_completed") < events.index("plan.verified")


def test_retry_duplicate_and_unknown_are_counted(fresh_graph):
    reset_metrics()
    retried = _approved(fresh_graph)
    execute_plan(retried, "exec-obs-retry", fresh_graph, injection={"action_type": "disable_identity", "mode": "timeout_unchanged"})
    execute_plan(retried, "exec-obs-retry", fresh_graph)

    from app.graph.seed import reset_scenario

    reset_scenario(fresh_graph)
    unknown_plan = _approved(fresh_graph)
    unknown = execute_plan(unknown_plan, "exec-obs-unknown", fresh_graph, injection={"action_type": "disable_identity", "mode": "timeout_unknown"})
    reconcile_execution(unknown["execution_id"], fresh_graph)
    metrics = snapshot()

    assert metrics["retries"] == 1
    assert metrics["duplicates_prevented"] == 1
    assert metrics["unknown_outcomes"] == 1
    assert metrics["counters"]["execution.reconciled"] == 1
    assert metrics["counters"]["execution.partial_failure"] == 1
    assert metrics["needs_review"] >= 1


def test_graph_unavailable_is_counted(monkeypatch):
    reset_metrics()
    monkeypatch.setenv("FALKORDB_PORT", "6391")
    try:
        create_plan(UNSAFE_OFFBOARD)
    except Exception:
        pass
    assert snapshot()["graph_unavailable"] == 1


def test_events_are_single_json_lines():
    captured = []

    class Capture(logging.Handler):
        def emit(self, record):
            captured.append(record.getMessage())

    logger = logging.getLogger("actiongraph.events")
    handler = Capture()
    level = logger.level
    logger.setLevel(logging.INFO)
    logger.addHandler(handler)
    try:
        emit("plan.created", plan_id="plan-x", graph_version="seed-v1", ignored=None)
    finally:
        logger.removeHandler(handler)
        logger.setLevel(level)

    record = json.loads(captured[0])
    assert record["event"] == "plan.created"
    assert record["plan_id"] == "plan-x"
    assert "ignored" not in record
    assert "\n" not in captured[0]


def test_metrics_route_returns_the_snapshot(fresh_graph):
    reset_metrics()
    client = TestClient(create_app())
    plan_id = client.post("/v1/plans", json=UNSAFE_OFFBOARD).json()["plan_id"]
    client.post("/v1/plans/%s/preflight" % plan_id)
    body = client.get("/v1/metrics").json()

    assert body["preflights"] == 1
    assert body["blocked"] == 1
    assert body["scope"] == "this process since it started"


def test_mcp_stdout_stays_protocol_only_when_events_are_logged(fresh_graph):
    call = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": "propose_plan", "arguments": {"actions": UNSAFE_OFFBOARD["actions"]}},
    }
    completed = subprocess.run(
        [sys.executable, "-m", "app.mcp_server"],
        input=json.dumps(call) + "\n",
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        timeout=20,
    )
    stdout_lines = completed.stdout.splitlines()
    stderr_events = [json.loads(line)["event"] for line in completed.stderr.splitlines() if line.startswith("{")]

    assert len(stdout_lines) == 1
    assert json.loads(stdout_lines[0])["result"]["structuredContent"]["verdict"] == "BLOCK"
    assert "invariant.blocked" in stderr_events
