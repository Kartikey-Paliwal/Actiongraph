"""A planning agent for the demo page, backed by Groq's OpenAI-compatible API.

The model only proposes. It sees the same tools as the MCP server, minus
execution and approval: list_workspace, impact, propose_plan, get_plan_report.
Every verdict comes from the graph through propose_plan. A person approves and
executes on the page.

Configuration (environment):
    GROQ_API_KEY            required; without it the agent reports "not configured"
    GROQ_MODEL              default llama-3.3-70b-versatile
    AGENT_MAX_STEPS         tool-calling rounds per request, default 8
    AGENT_DAILY_LIMIT       requests per process per UTC day, default 300
    AGENT_PER_MINUTE        requests per client per minute, default 4
"""

import json
import os
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timezone

import httpx

from app.mcp_server import TOOLS, call_tool
from app.observability import emit

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
AGENT_TOOLS = ("list_workspace", "impact", "propose_plan", "get_plan_report")
MAX_PROMPT_CHARS = 500
MAX_TOOL_RESULT_CHARS = 6000

SYSTEM_PROMPT = """You are an operations agent for a synthetic company. You change
identities, service ownership, credentials and resources only by proposing plans
to ActionGraph, which checks every plan against a FalkorDB dependency graph.

Rules:
- The verdict comes from the graph. Never claim a plan is safe unless propose_plan returned PASS.
- Start by calling list_workspace or impact to understand who owns what.
- Call propose_plan with the plan the user asked for, exactly as asked, first.
- On BLOCK, read evidence and suggested_repair. If suggested_repair.actions exists, propose those actions next.
- On NEEDS_REVIEW, explain the risk in one sentence, then propose the plan again without the risky step,
  and say that step needs a separate human review.
- Stop as soon as a plan returns PASS. You cannot approve or execute anything:
  a person approves and executes on the page.
- Use only these action types: assign_service_owner (target_id = service id,
  params.new_owner_id = identity id), disable_identity, revoke_credential, delete_resource.
- Finish with a short summary in plain English (at most 5 sentences): what was blocked and why
  (name the graph path), what you changed, and which plan id is waiting for human approval."""


class AgentNotConfigured(Exception):
    pass


class AgentRateLimited(Exception):
    pass


class AgentError(Exception):
    pass


class _Limiter:
    def __init__(self):
        self.lock = threading.Lock()
        self.per_client = defaultdict(deque)
        self.day = None
        self.day_count = 0

    def check(self, client_id):
        per_minute = int(os.environ.get("AGENT_PER_MINUTE", "4"))
        daily = int(os.environ.get("AGENT_DAILY_LIMIT", "300"))
        now = time.time()
        today = datetime.now(timezone.utc).date()
        with self.lock:
            if self.day != today:
                self.day, self.day_count = today, 0
            if self.day_count >= daily:
                raise AgentRateLimited("the demo agent has reached today's limit; try the guided demo instead")
            recent = self.per_client[client_id]
            while recent and now - recent[0] > 60:
                recent.popleft()
            if len(recent) >= per_minute:
                raise AgentRateLimited("too many agent requests; wait a minute and try again")
            recent.append(now)
            self.day_count += 1

    def reset(self):
        with self.lock:
            self.per_client.clear()
            self.day, self.day_count = None, 0


LIMITER = _Limiter()


def configured():
    return bool(os.environ.get("GROQ_API_KEY"))


def _tool_specs():
    return [
        {
            "type": "function",
            "function": {
                "name": tool["name"],
                "description": tool["description"],
                "parameters": tool["inputSchema"],
            },
        }
        for tool in TOOLS
        if tool["name"] in AGENT_TOOLS
    ]


def _compact(name, payload):
    """Keep what the model needs; drop duplicated findings to save tokens."""
    if name == "propose_plan" and isinstance(payload, dict) and "verdict" in payload:
        keep = {key: payload.get(key) for key in ("plan_id", "status", "verdict", "reason_code", "invariant", "evidence")}
        repair = payload.get("suggested_repair")
        if repair:
            keep["suggested_repair"] = {
                "service_id": repair.get("service_id"),
                "candidates": [
                    {"identity_id": c["identity_id"], "evidence_path": c["evidence_path"]} for c in repair.get("candidates", [])
                ],
                "actions": repair.get("actions"),
                "recheck": (repair.get("preflight") or {}).get("verdict"),
                "recheck_reason": (repair.get("preflight") or {}).get("reason_code"),
                "note": repair.get("note"),
            }
        payload = keep
    text = json.dumps(payload, default=str)
    if len(text) > MAX_TOOL_RESULT_CHARS:
        text = text[:MAX_TOOL_RESULT_CHARS] + "...(truncated)"
    return text


def _summary(name, args, payload, is_error):
    step = {"tool": name, "arguments": args, "error": None}
    if is_error:
        step["error"] = payload.get("message") if isinstance(payload, dict) else str(payload)
        return step
    if name == "propose_plan":
        step.update(
            {
                "plan_id": payload.get("plan_id"),
                "verdict": payload.get("verdict"),
                "status": payload.get("status"),
                "reason_code": payload.get("reason_code"),
                "graph_path": (payload.get("evidence") or {}).get("graph_path", []),
                "repair_candidate": ((payload.get("suggested_repair") or {}).get("candidates") or [{}])[0].get("identity_id"),
            }
        )
    elif name == "impact":
        step["critical_services"] = [s["service_id"] for s in payload.get("critical_services", [])]
    return step


def _complete(client, messages):
    headers = {"Authorization": "Bearer %s" % os.environ["GROQ_API_KEY"]}
    body = {
        "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "messages": messages,
        "tools": _tool_specs(),
        "tool_choice": "auto",
        "temperature": 0,
        "max_tokens": 1024,
    }
    try:
        response = client.post(GROQ_URL, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise AgentError("could not reach the model provider: %s" % exc.__class__.__name__) from exc
    if response.status_code == 429:
        raise AgentRateLimited("the model provider is rate limiting; try again shortly")
    if response.status_code >= 400:
        detail = ""
        try:
            detail = response.json().get("error", {}).get("message", "")
        except ValueError:
            pass
        raise AgentError("model provider returned %s %s" % (response.status_code, detail[:200]))
    return response.json()["choices"][0]["message"]


def run_agent(prompt, client_id="local", http_client=None):
    if not configured():
        raise AgentNotConfigured("the agent is not configured on this server (GROQ_API_KEY is not set)")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required")
    prompt = prompt.strip()
    if len(prompt) > MAX_PROMPT_CHARS:
        raise ValueError("prompt must be at most %s characters" % MAX_PROMPT_CHARS)
    LIMITER.check(client_id)

    max_steps = int(os.environ.get("AGENT_MAX_STEPS", "8"))
    messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}]
    steps = []
    ready_plan_id = None
    last_plan_id = None
    started = time.perf_counter()
    owns_client = http_client is None
    client = http_client or httpx.Client(timeout=45)
    emit("agent.started", client_id=client_id, prompt_chars=len(prompt))
    try:
        for _ in range(max_steps):
            message = _complete(client, messages)
            calls = message.get("tool_calls") or []
            assistant = {"role": "assistant", "content": message.get("content") or ""}
            if calls:
                assistant["tool_calls"] = calls
            messages.append(assistant)
            if not calls:
                return _finish(steps, message.get("content") or "", ready_plan_id, last_plan_id, started, "answered")
            for call in calls:
                name = call.get("function", {}).get("name")
                try:
                    args = json.loads(call.get("function", {}).get("arguments") or "{}")
                except ValueError:
                    args = None
                if name not in AGENT_TOOLS or not isinstance(args, dict):
                    payload, is_error = {"error": "rejected", "message": "unknown tool or invalid arguments"}, True
                else:
                    try:
                        result = call_tool(name, args)
                        payload, is_error = result["structuredContent"], result["isError"]
                    except Exception as exc:  # schema rejection from the MCP validator
                        payload, is_error = {"error": "invalid_arguments", "message": str(exc)}, True
                step = _summary(name, args, payload, is_error)
                steps.append(step)
                if step.get("plan_id"):
                    last_plan_id = step["plan_id"]
                    if step.get("verdict") == "PASS":
                        ready_plan_id = step["plan_id"]
                messages.append({"role": "tool", "tool_call_id": call.get("id"), "content": _compact(name, payload)})
            if ready_plan_id:
                message = _complete(client, messages + [{"role": "user", "content": "Summarize now, without calling tools."}])
                return _finish(steps, message.get("content") or "", ready_plan_id, last_plan_id, started, "plan_ready")
        return _finish(steps, "Stopped after %s steps without a passing plan." % max_steps, ready_plan_id, last_plan_id, started, "step_limit")
    finally:
        if owns_client:
            client.close()


def _finish(steps, answer, ready_plan_id, last_plan_id, started, outcome):
    elapsed = round((time.perf_counter() - started) * 1000)
    emit("agent.finished", outcome=outcome, steps=len(steps), plan_id=ready_plan_id or last_plan_id, latency_ms=elapsed)
    return {
        "outcome": outcome,
        "answer": answer.strip(),
        "steps": steps,
        "ready_plan_id": ready_plan_id,
        "last_plan_id": last_plan_id,
        "model": os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        "latency_ms": elapsed,
        "decided_by": "FalkorDB graph and the ActionGraph evaluator; the model only proposed plans",
    }
