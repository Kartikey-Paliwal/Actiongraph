"""MCP server over stdio: the agent proposes, the graph decides, a human approves.

JSON-RPC 2.0, one message per line. Written without the MCP SDK because the SDK
needs Python 3.10 and this project runs on 3.9. Every tool wraps existing code;
no policy is decided here. There is no approve tool on purpose.

Run: python -m app.mcp_server
"""

import json
import sys
import traceback

from app.errors import ActionGraphError, GraphUnavailable, PlanNotFound, PlanValidationError
from app.execution.runner import build_report, execute_plan, reconcile_execution
from app.graph.queries import ownership_impact
from app.graph.state import load_observed_state
from app.plans.service import create_and_preflight
from app.plans.store import get_plan

SERVER_VERSION = "0.4.0"
SUPPORTED_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
DEFAULT_SCENARIO = "synthetic_identity_offboarding"
INJECTION_MODES = ["timeout_unchanged", "timeout_unknown", "success_without_write"]

INSTRUCTIONS = (
    "ActionGraph checks infrastructure changes against a FalkorDB dependency graph. "
    "Inspect with list_workspace and impact. Submit a plan with propose_plan. The verdict "
    "comes from the graph, not from you: do not argue with it. On BLOCK, read evidence and "
    "suggested_repair, then propose a revised plan. On NEEDS_REVIEW, explain why and split "
    "the risky step into a separate plan for human review. You cannot approve plans: call "
    "request_approval, show the human the command it returns, and wait until they say they "
    "approved. Then call execute_plan and report only what the read-back verified."
)


class InvalidParams(Exception):
    pass


_ACTION_ITEM = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["assign_service_owner", "disable_identity", "revoke_credential", "delete_resource"],
        },
        "target_id": {"type": "string"},
        "params": {
            "type": "object",
            "properties": {"new_owner_id": {"type": "string"}},
            "additionalProperties": False,
        },
    },
    "required": ["type", "target_id"],
    "additionalProperties": False,
}

TOOLS = [
    {
        "name": "list_workspace",
        "description": "List identities, services with their owners, resources, and credentials from the live graph. Read-only.",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "impact",
        "description": "Show which critical services an identity owns and the graph paths behind that, including co-owners that do not count. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"identity_id": {"type": "string"}},
            "required": ["identity_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "propose_plan",
        "description": (
            "Store a plan and preflight it against the live graph. Returns PASS, BLOCK, or NEEDS_REVIEW "
            "with the invariant and evidence path. On BLOCK, suggested_repair holds a graph-backed fix "
            "you can propose next. Nothing is executed."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "actions": {"type": "array", "items": _ACTION_ITEM, "minItems": 1},
                "scenario": {"type": "string"},
            },
            "required": ["actions"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
    {
        "name": "get_plan_report",
        "description": "Return a plan's status, latest verdict, approval, and execution steps. Read-only.",
        "inputSchema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string"}},
            "required": ["plan_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "request_approval",
        "description": (
            "Ask a human to approve a plan that is AWAITING_APPROVAL. You cannot approve it yourself. "
            "Returns the command the human runs. Writes nothing."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"plan_id": {"type": "string"}, "summary": {"type": "string"}},
            "required": ["plan_id", "summary"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "execute_plan",
        "description": (
            "Run a human-approved plan one step at a time, reading the graph back after each step. "
            "Fails if no human has approved it. inject is for the demo only: it simulates a failure "
            "on the disable_identity step."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "plan_id": {"type": "string"},
                "idempotency_key": {"type": "string"},
                "inject": {"type": "string", "enum": INJECTION_MODES},
            },
            "required": ["plan_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": True},
    },
    {
        "name": "reconcile_execution",
        "description": "Re-read the live graph for an UNKNOWN execution and report what actually happened.",
        "inputSchema": {
            "type": "object",
            "properties": {"execution_id": {"type": "string"}},
            "required": ["execution_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": False, "destructiveHint": False},
    },
]

_TOOLS_BY_NAME = {tool["name"]: tool for tool in TOOLS}


def _check(value, schema, path):
    kind = schema.get("type")
    if kind == "object":
        if not isinstance(value, dict):
            raise InvalidParams("%s must be an object" % path)
        properties = schema.get("properties", {})
        for key in schema.get("required", []):
            if key not in value:
                raise InvalidParams("%s.%s is required" % (path, key))
        for key, item in value.items():
            if key not in properties:
                raise InvalidParams("%s.%s is not allowed" % (path, key))
            _check(item, properties[key], "%s.%s" % (path, key))
    elif kind == "array":
        if not isinstance(value, list) or len(value) < schema.get("minItems", 0):
            raise InvalidParams("%s must be a list with at least %s item(s)" % (path, schema.get("minItems", 0)))
        for index, item in enumerate(value):
            _check(item, schema["items"], "%s[%s]" % (path, index))
    elif kind == "string":
        if not isinstance(value, str):
            raise InvalidParams("%s must be a string" % path)
    if "enum" in schema and value not in schema["enum"]:
        raise InvalidParams("%s must be one of %s" % (path, ", ".join(schema["enum"])))


def _list_workspace():
    state = load_observed_state()
    owners = {}
    for identity_id, service_id in state["owns"]:
        identity = state["identities"][identity_id]
        owners.setdefault(service_id, []).append(
            {
                "identity_id": identity_id,
                "counts_as_eligible_owner": identity["status"] == "active" and identity["eligible_owner"],
            }
        )
    return {
        "graph_version": state["graph_version"],
        "identities": [dict(record, id=key) for key, record in sorted(state["identities"].items())],
        "services": [
            dict(record, id=key, owners=owners.get(key, [])) for key, record in sorted(state["services"].items())
        ],
        "resources": [dict(record, id=key) for key, record in sorted(state["resources"].items())],
        "credentials": [dict(record, id=key) for key, record in sorted(state["credentials"].items())],
    }


def _propose_plan(args):
    return create_and_preflight({"scenario": args.get("scenario", DEFAULT_SCENARIO), "actions": args["actions"]})


def _request_approval(args):
    plan = get_plan(args["plan_id"])
    if plan is None:
        raise PlanNotFound(args["plan_id"])
    if plan["status"] != "AWAITING_APPROVAL":
        raise PlanValidationError("approval can only be requested when the plan is AWAITING_APPROVAL, it is %s" % plan["status"])
    state = load_observed_state()
    disabled = {action["target_id"] for action in plan["actions"] if action["type"] == "disable_identity"}
    approvers = sorted(
        identity_id
        for identity_id, record in state["identities"].items()
        if record["status"] == "active" and identity_id not in disabled
    )
    example = approvers[0] if approvers else "<active-identity-id>"
    return {
        "plan_id": plan["plan_id"],
        "status": plan["status"],
        "approval_recorded": False,
        "summary": args["summary"],
        "eligible_approvers": approvers,
        "human_command": 'python -m app.cli approve --plan %s --approver %s --reason "<why this is safe>"'
        % (plan["plan_id"], example),
        "note": "A human must run this command. Call execute_plan only after they confirm.",
    }


def _execute_plan(args):
    injection = None
    if "inject" in args:
        injection = {"action_type": "disable_identity", "mode": args["inject"]}
    return execute_plan(args["plan_id"], args.get("idempotency_key", "mcp-%s" % args["plan_id"]), injection=injection)


_HANDLERS = {
    "list_workspace": lambda args: _list_workspace(),
    "impact": lambda args: ownership_impact(args["identity_id"]),
    "propose_plan": _propose_plan,
    "get_plan_report": lambda args: build_report(args["plan_id"]),
    "request_approval": _request_approval,
    "execute_plan": _execute_plan,
    "reconcile_execution": lambda args: reconcile_execution(args["execution_id"]),
}


def _tool_result(payload, is_error=False):
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, default=str)}],
        "structuredContent": payload,
        "isError": is_error,
    }


def call_tool(name, arguments):
    tool = _TOOLS_BY_NAME.get(name)
    if tool is None:
        raise InvalidParams("unknown tool: %s" % name)
    arguments = {} if arguments is None else arguments
    _check(arguments, tool["inputSchema"], "arguments")
    try:
        return _tool_result(_HANDLERS[name](arguments))
    except (ActionGraphError, ValueError) as exc:
        kind = "graph_unavailable" if isinstance(exc, GraphUnavailable) else type(exc).__name__
        return _tool_result({"error": kind, "message": str(exc), "execution_started": False}, is_error=True)
    except Exception as exc:  # never let an unexpected failure look like a verdict
        traceback.print_exc(file=sys.stderr)
        return _tool_result({"error": "internal_error", "message": str(exc), "execution_started": False}, is_error=True)


def _error(message_id, code, text):
    return {"jsonrpc": "2.0", "id": message_id, "error": {"code": code, "message": text}}


def handle(message):
    """Return the JSON-RPC response for one message, or None for a notification."""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or "method" not in message:
        return _error(message.get("id") if isinstance(message, dict) else None, -32600, "invalid request")
    method = message["method"]
    if "id" not in message:
        return None
    message_id = message["id"]
    params = message.get("params") or {}
    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_VERSIONS else SUPPORTED_VERSIONS[0]
        result = {
            "protocolVersion": version,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "actiongraph", "version": SERVER_VERSION},
            "instructions": INSTRUCTIONS,
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call":
        try:
            result = call_tool(params.get("name"), params.get("arguments"))
        except InvalidParams as exc:
            return _error(message_id, -32602, str(exc))
    else:
        return _error(message_id, -32601, "method not found: %s" % method)
    return {"jsonrpc": "2.0", "id": message_id, "result": result}


def serve(stdin=None, stdout=None):
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            response = _error(None, -32700, "parse error")
        else:
            response = handle(message)
        if response is not None:
            stdout.write(json.dumps(response, default=str) + "\n")
            stdout.flush()


if __name__ == "__main__":
    serve()
