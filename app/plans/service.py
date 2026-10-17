"""Create a validated plan and preflight it against the live FalkorDB graph."""

import uuid

from app.errors import PlanExists, PlanNotFound, PlanValidationError
from app.graph.state import load_observed_state
from app.planning.validator import validate_plan_request
from app.plans.store import get_plan, plan_exists, save_decision, save_plan, set_status
from app.policy.evaluator import evaluate_plan

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
    save_plan(plan, state["graph_version"], db)
    return get_plan(plan["plan_id"], db)


def preflight_plan(plan_id, db=None):
    plan = get_plan(plan_id, db)
    if plan is None:
        raise PlanNotFound(plan_id)
    state = load_observed_state(db)
    set_status(plan_id, "VALIDATING", db)
    result = evaluate_plan(plan, state)
    save_decision(result, db)
    return result


def create_and_preflight(payload, db=None):
    created = create_plan(payload, db)
    return preflight_plan(created["plan_id"], db)
