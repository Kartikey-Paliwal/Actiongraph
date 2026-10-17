import json
import subprocess
import sys
from pathlib import Path

from app import cli
from app.graph.client import graph
from app.mcp_server import handle

_ROOT = Path(__file__).resolve().parents[1]

UNSAFE_ACTIONS = [
    {"type": "disable_identity", "target_id": "id-contractor-17"},
    {"type": "revoke_credential", "target_id": "credential-deploy-4"},
]

HANDOFF_ACTIONS = [
    {"type": "assign_service_owner", "target_id": "svc-billing", "params": {"new_owner_id": "id-engineer-9"}},
    {"type": "disable_identity", "target_id": "id-contractor-17"},
]


def _call(name, arguments=None, message_id=1):
    params = {"name": name}
    if arguments is not None:
        params["arguments"] = arguments
    return handle({"jsonrpc": "2.0", "id": message_id, "method": "tools/call", "params": params})


def _content(response):
    result = response["result"]
    assert json.loads(result["content"][0]["text"]) == result["structuredContent"]
    return result["structuredContent"], result["isError"]


def test_initialize_negotiates_version_and_advertises_tools():
    response = handle(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}}
    )
    result = response["result"]

    assert result["protocolVersion"] == "2025-03-26"
    assert result["capabilities"] == {"tools": {}}
    assert result["serverInfo"]["name"] == "actiongraph"
    assert "cannot approve" in result["instructions"]

    newer = handle({"jsonrpc": "2.0", "id": 2, "method": "initialize", "params": {"protocolVersion": "2099-01-01"}})
    assert newer["result"]["protocolVersion"] == "2025-06-18"


def test_tools_list_has_no_approve_tool():
    tools = handle({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})["result"]["tools"]
    names = sorted(tool["name"] for tool in tools)

    assert names == [
        "execute_plan",
        "get_plan_report",
        "impact",
        "list_workspace",
        "propose_plan",
        "reconcile_execution",
        "request_approval",
    ]
    assert not any(name.startswith("approve") for name in names)


def test_protocol_errors_and_notifications():
    assert handle({"jsonrpc": "2.0", "method": "notifications/initialized"}) is None
    assert handle({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})["error"]["code"] == -32601
    assert _call("approve_plan", {"plan_id": "plan-x"})["error"]["code"] == -32602
    extra = _call("propose_plan", {"actions": UNSAFE_ACTIONS, "approved": True})
    assert extra["error"]["code"] == -32602


def test_unsafe_proposal_blocks_with_graph_suggested_repair(fresh_graph):
    body, is_error = _content(_call("propose_plan", {"actions": UNSAFE_ACTIONS}))

    assert is_error is False
    assert body["verdict"] == "BLOCK"
    assert body["reason_code"] == "CRITICAL_SERVICE_WOULD_BE_ORPHANED"
    assert body["execution_started"] is False
    assert body["suggested_repair"]["candidates"][0]["identity_id"] == "id-engineer-9"


def test_agent_flow_needs_a_human_approval_before_execute(fresh_graph):
    proposed, _ = _content(_call("propose_plan", {"actions": HANDOFF_ACTIONS}))
    plan_id = proposed["plan_id"]
    assert proposed["verdict"] == "PASS"
    assert proposed["status"] == "AWAITING_APPROVAL"

    refused, is_error = _content(_call("execute_plan", {"plan_id": plan_id}))
    assert is_error is True
    assert "APPROVED" in refused["message"]

    request, is_error = _content(_call("request_approval", {"plan_id": plan_id, "summary": "Hand billing to engineer-9"}))
    assert is_error is False
    assert request["approval_recorded"] is False
    assert plan_id in request["human_command"]
    assert "id-contractor-17" not in request["eligible_approvers"]
    report, _ = _content(_call("get_plan_report", {"plan_id": plan_id}))
    assert report["status"] == "AWAITING_APPROVAL"
    assert report["approval"] is None

    assert cli.main(["approve", "--plan", plan_id, "--approver", "id-engineer-9", "--reason", "Engineer takes billing"]) == 0

    executed, is_error = _content(_call("execute_plan", {"plan_id": plan_id}))
    assert is_error is False
    assert executed["status"] == "VERIFIED"
    assert executed["verification"]["invariant_holds"] is True


def test_unsupported_action_is_an_error_and_stores_nothing(fresh_graph):
    before = graph(fresh_graph).query("MATCH (p:Plan) RETURN count(p)").result_set[0][0]
    response = _call("propose_plan", {"actions": [{"type": "run_shell", "target_id": "id-contractor-17"}]})

    assert response["error"]["code"] == -32602
    unknown_target, is_error = _content(
        _call("propose_plan", {"actions": [{"type": "disable_identity", "target_id": "id-missing-1"}]})
    )
    assert is_error is True
    assert "unknown target" in unknown_target["message"]
    assert graph(fresh_graph).query("MATCH (p:Plan) RETURN count(p)").result_set[0][0] == before


def test_graph_down_returns_an_error_not_a_verdict(monkeypatch):
    monkeypatch.setenv("FALKORDB_PORT", "6391")
    body, is_error = _content(_call("propose_plan", {"actions": UNSAFE_ACTIONS}))

    assert is_error is True
    assert body["error"] == "graph_unavailable"
    assert "verdict" not in body


def test_stdio_server_speaks_one_json_message_per_line():
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-06-18"}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
    ]
    stdin = "\n".join(json.dumps(item) for item in requests) + "\nnot json\n"
    completed = subprocess.run(
        [sys.executable, "-m", "app.mcp_server"],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(_ROOT),
        timeout=20,
    )
    lines = [json.loads(line) for line in completed.stdout.splitlines()]

    assert completed.returncode == 0
    assert [line.get("id") for line in lines] == [1, 2, None]
    assert lines[1]["result"]["tools"]
    assert lines[2]["error"]["code"] == -32700
