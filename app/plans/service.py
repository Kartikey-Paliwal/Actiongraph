"""Create a validated plan and preflight it against the live FalkorDB graph."""

import time
import uuid

from app.config import settings
from app.errors import PlanExists, PlanNotFound, PlanValidationError
from app.graph.queries import replacement_candidates, target_impact
from app.graph.state import load_observed_state
from app.observability import emit, observe
from app.planning.validator import validate_plan_request
from app.plans.store import get_plan, plan_exists, save_decision, save_plan, set_status
from app.policy.evaluator import evaluate_plan
from app.policy.repair import disabled_identity_ids, needs_repair, orphaned_service_id, suggest_repair

_BUCKET = {
    "Identity": "identities",
    "Service": "services",
    "Credential": "credentials",
    "Resource": "resources",
}


def _new_plan_id():
    return "plan-%s" % uuid.uuid4().hex[:12]


def _assert_targets_exist(plan, state):
    for action in plan["actions"]:
        bucket = state[_BUCKET[action["target_label"]]]
        if action["target_id"] not in bucket:
            raise PlanValidationError("unknown target: %s" % action["target_id"])
        new_owner_id = action["params"].get("new_owner_id")
        if new_owner_id and new_owner_id not in state["identities"]:
            raise PlanValidationError("unknown target: %s" % new_owner_id)


def create_plan(payload, db=None):
    plan = validate_plan_request(payload)
    state = load_observed_state(db)
    _assert_targets_exist(plan, state)
    if not plan["plan_id"]:
        plan["plan_id"] = _new_plan_id()
    if plan_exists(plan["plan_id"], db):
        raise PlanExists(plan["plan_id"])
    for action in plan["actions"]:
        action["action_id"] = "%s:a%s" % (plan["plan_id"], action["sequence"])
    emit("plan.validated", plan_id=plan["plan_id"], action_count=len(plan["actions"]))
    save_plan(plan, state["graph_version"], db)
    emit("plan.created", plan_id=plan["plan_id"], graph_version=state["graph_version"])
    return get_plan(plan["plan_id"], db)


def preflight_plan(plan_id, db=None):
    started = time.perf_counter()
    plan = get_plan(plan_id, db)
    if plan is None:
        raise PlanNotFound(plan_id)
    state = load_observed_state(db)
    set_status(plan_id, "VALIDATING", db)
    result = evaluate_plan(plan, state)
    if needs_repair(result):
        candidates = replacement_candidates(orphaned_service_id(result), disabled_identity_ids(plan), db)
        result["suggested_repair"] = suggest_repair(plan, result, state, candidates)
    save_decision(result, db)
    latency_ms = (time.perf_counter() - started) * 1000
    observe("preflight_ms", latency_ms)
    fields = {
        "plan_id": plan_id,
        "verdict": result["verdict"],
        "reason_code": result["reason_code"],
        "rule_id": result["invariant"]["id"],
        "graph_version": result["graph_version"],
    }
    emit("plan.preflighted", latency_ms=round(latency_ms, 2), **fields)
    if result["verdict"] == "PASS":
        emit("invariant.passed", **fields)
    elif result["verdict"] == "BLOCK":
        emit("invariant.blocked", **fields)
        if "suggested_repair" in result:
            repair = result["suggested_repair"]
            emit(
                "repair.suggested",
                plan_id=plan_id,
                candidate_ids=[candidate["identity_id"] for candidate in repair["candidates"]],
                offered=repair["actions"] is not None,
            )
    else:
        emit("plan.needs_review", **fields)
    return result


def create_and_preflight(payload, db=None):
    created = create_plan(payload, db)
    return preflight_plan(created["plan_id"], db)


def plan_impact(plan_id, db=None):
    """Bounded graph paths behind every action in a stored plan. Read-only."""
    plan = get_plan(plan_id, db)
    if plan is None:
        raise PlanNotFound(plan_id)
    targets = [target_impact(action, db) for action in plan["actions"]]
    return {
        "plan_id": plan_id,
        "targets": targets,
        "traversal": {
            "patterns": "explicit OWNS, DEPENDS_ON, GRANTS_ACCESS_TO, ISSUED_TO; no variable-length paths",
            "result_limit": settings()["result_limit"],
            "truncated": any(target["truncated"] for target in targets),
        },
    }
