import json

import httpx
import pytest
from fastapi.testclient import TestClient

from app import agent
from app.main import create_app
from app.plans.store import get_plan

UNSAFE = [
    {"type": "disable_identity", "target_id": "id-contractor-17"},
    {"type": "revoke_credential", "target_id": "credential-deploy-4"},
]
HANDOFF = [
    {"type": "assign_service_owner", "target_id": "svc-billing", "params": {"new_owner_id": "id-engineer-9"}},
    {"type": "disable_identity", "target_id": "id-contractor-17"},
]


def _call(call_id, name, arguments):
    return {"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}


def _fake_groq(script, seen):
    """Answer each chat completion with the next scripted assistant message."""
    replies = iter(script)

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"message": next(replies)}]})

    return httpx.Client(transport=httpx.MockTransport(handler))


@pytest.fixture
def groq(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_PER_MINUTE", "100")
    monkeypatch.delenv("GROQ_MODEL", raising=False)
    agent.LIMITER.reset()
    yield
    agent.LIMITER.reset()


def test_agent_is_blocked_then_proposes_a_plan_that_passes(fresh_graph, groq):
    seen = []
    client = _fake_groq(
        [
            {"content": "", "tool_calls": [_call("c1", "propose_plan", {"actions": UNSAFE})]},
            {"content": "", "tool_calls": [_call("c2", "propose_plan", {"actions": HANDOFF})]},
            {"content": "Blocked first; engineer-9 takes billing; the plan waits for approval."},
        ],
        seen,
    )
    result = agent.run_agent("Offboard contractor 17", "tester", http_client=client)

    assert [step["verdict"] for step in result["steps"]] == ["BLOCK", "PASS"]
    assert result["steps"][0]["repair_candidate"] == "id-engineer-9"
    assert result["outcome"] == "plan_ready"
    assert result["ready_plan_id"] == result["steps"][1]["plan_id"]
    assert get_plan(result["ready_plan_id"], fresh_graph)["status"] == "AWAITING_APPROVAL"
    assert "engineer-9" in result["answer"]
    assert {tool["function"]["name"] for tool in seen[0]["tools"]} == {"list_workspace", "impact", "propose_plan", "get_plan_report"}
    blocked = json.loads([m for m in seen[1]["messages"] if m["role"] == "tool"][0]["content"])
    assert blocked["suggested_repair"]["candidates"][0]["identity_id"] == "id-engineer-9"
    assert seen[0]["model"] == "llama-3.3-70b-versatile"


def test_agent_cannot_execute_or_approve(fresh_graph, groq):
    client = _fake_groq(
        [
            {"content": "", "tool_calls": [_call("c1", "execute_plan", {"plan_id": "plan-x"}), _call("c2", "approve_plan", {"plan_id": "plan-x"})]},
            {"content": "I cannot execute."},
        ],
        [],
    )
    result = agent.run_agent("Just execute it", "tester", http_client=client)

    assert [step["error"] for step in result["steps"]] == ["unknown tool or invalid arguments"] * 2
    assert result["ready_plan_id"] is None


def test_bad_arguments_go_back_to_the_model(fresh_graph, groq):
    client = _fake_groq(
        [
            {"content": "", "tool_calls": [_call("c1", "propose_plan", {"actions": [{"type": "run_shell", "target_id": "x"}]})]},
            {"content": "That action is not allowed."},
        ],
        [],
    )
    result = agent.run_agent("Run a shell command", "tester", http_client=client)

    assert result["steps"][0]["error"]
    assert result["outcome"] == "answered"


def test_step_limit_stops_the_loop(fresh_graph, groq, monkeypatch):
    monkeypatch.setenv("AGENT_MAX_STEPS", "2")
    loop = {"content": "", "tool_calls": [_call("c", "list_workspace", {})]}
    result = agent.run_agent("loop", "tester", http_client=_fake_groq([loop, loop, loop], []))

    assert result["outcome"] == "step_limit"
    assert len(result["steps"]) == 2


def test_agent_needs_a_key_and_a_short_prompt(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(agent.AgentNotConfigured):
        agent.run_agent("hi")
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    agent.LIMITER.reset()
    with pytest.raises(ValueError):
        agent.run_agent("x" * 501)


def test_rate_limit_per_client(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_PER_MINUTE", "1")
    agent.LIMITER.reset()
    client = _fake_groq([{"content": "ok"}, {"content": "ok"}], [])
    agent.run_agent("first", "same-ip", http_client=client)
    with pytest.raises(agent.AgentRateLimited):
        agent.run_agent("second", "same-ip", http_client=client)
    agent.LIMITER.reset()


def test_api_routes_report_configuration(monkeypatch):
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    client = TestClient(create_app())

    assert client.get("/v1/agent/status").json()["configured"] is False
    assert client.post("/v1/agent", json={"prompt": "hi"}).status_code == 503


def test_provider_errors_become_clear_errors(monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_PER_MINUTE", "100")
    agent.LIMITER.reset()
    limited = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(429, json={})))
    with pytest.raises(agent.AgentRateLimited):
        agent.run_agent("hi", "tester", http_client=limited)
    broken = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(400, json={"error": {"message": "model not found"}})))
    with pytest.raises(agent.AgentError) as raised:
        agent.run_agent("hi", "tester", http_client=broken)
    assert "model not found" in str(raised.value)
    agent.LIMITER.reset()
