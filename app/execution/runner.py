"""Run an approved plan one action at a time, then verify by reading the graph."""

from app.errors import ExecutionRejected, PlanNotFound, PlanValidationError, ReadbackError
from app.execution.adapter import apply_action
from app.execution.journal import (
    create_execution,
    find_execution,
    finish_execution,
    latest_approval,
    latest_execution,
    record_event,
    save_approval,
    verified_event_exists,
)
from app.execution.readback import billing_verification, observe_action, postcondition_holds
from app.graph.queries import validate_identity_id
from app.graph.state import load_observed_state
from app.observability import STATUS_EVENTS, emit
from app.plans.store import get_plan, save_decision
from app.policy.evaluator import evaluate_plan

_INJECTION_MODES = ("timeout_unchanged", "timeout_unknown", "success_without_write")


def _key(value, field_name):
    try:
        return validate_identity_id(value)
    except ValueError as exc:
        raise PlanValidationError("%s: %s" % (field_name, exc)) from exc


def approve_plan(plan_id, approver_id, reason, db=None):
    approver_id = _key(approver_id, "approver_id")
    if not isinstance(reason, str) or not reason.strip():
        raise PlanValidationError("approval reason is required")
    plan = get_plan(plan_id, db)
    if plan is None:
        raise PlanNotFound(plan_id)
    if plan["status"] != "AWAITING_APPROVAL":
        raise ExecutionRejected("approval requires AWAITING_APPROVAL, current status is %s" % plan["status"])
    state = load_observed_state(db)
    approver = state["identities"].get(approver_id)
    if approver is None or approver["status"] != "active":
        raise PlanValidationError("approver must be an active identity in the graph")
    disabled = [step["target_id"] for step in plan["actions"] if step["type"] == "disable_identity"]
    if approver_id in disabled:
        raise PlanValidationError("the identity being disabled cannot approve the plan")
    save_approval(plan_id, approver_id, reason.strip(), db)
    emit("approval.recorded", plan_id=plan_id, approver_id=approver_id)
    stored = get_plan(plan_id, db)
    stored["approval"] = latest_approval(plan_id, db)
    stored["execution_started"] = False
    return stored


def _injection_for(action, injection, used):
    if not injection or used["done"]:
        return None
    if injection.get("action_type") != action["type"]:
        return None
    mode = injection.get("mode")
    if mode not in _INJECTION_MODES:
        raise PlanValidationError("unsupported failure injection")
    used["done"] = True
    return mode


def _step(action, attempt, adapter_result, observation, verified, note):
    return {
        "action_id": action["action_id"],
        "action_type": action["type"],
        "target_id": action["target_id"],
        "attempt": attempt,
        "adapter_outcome": adapter_result["outcome"],
        "adapter_wrote": adapter_result["wrote"],
        "readback": observation,
        "postcondition_verified": verified,
        "note": note,
    }


def _body(plan_id, execution_id, status, steps, verification, review_required):
    return {
        "plan_id": plan_id,
        "execution_id": execution_id,
        "status": status,
        "execution_started": True,
        "duplicate": False,
        "review_required": review_required,
        "steps": steps,
        "verification": verification,
    }


def _stop(plan_id, execution_id, status, steps, db, review_required=False):
    try:
        verification = billing_verification(db)
    except ReadbackError:
        verification = None
        if status not in ("UNKNOWN",):
            status = "UNKNOWN"
            review_required = True
    body = _body(plan_id, execution_id, status, steps, verification, review_required)
    finish_execution(execution_id, plan_id, status, body, db)
    emit(STATUS_EVENTS.get(status, "execution.finished"), plan_id=plan_id, execution_id=execution_id, status=status)
    if review_required:
        emit("plan.needs_review", plan_id=plan_id, execution_id=execution_id, reason_code="EXECUTION_%s" % status)
    return body


def _run_attempt(action, db, mode):
    ids = {"execution_id": action["_execution_id"], "action_id": action["action_id"], "action_type": action["type"]}
    emit("action.started", injected=mode, **ids)
    adapter_result = apply_action(action, db, mode=mode)
    emit("action.response_received", outcome=adapter_result["outcome"], wrote=adapter_result["wrote"], **ids)
    if mode == "timeout_unknown":
        try:
            observe_action(action, db, force_failure=True)
        except ReadbackError as exc:
            emit("action.readback_completed", readback="failed", verified=False, **ids)
            record_event(action.get("_execution_id"), action, "unknown", None, db)
            return adapter_result, None, False, str(exc)
    observation = observe_action(action, db, force_failure=False)
    verified = postcondition_holds(action, observation)
    emit("action.readback_completed", readback="ok", verified=verified, **ids)
    outcome = "verified" if verified else "postcondition_mismatch"
    if adapter_result["outcome"] == "timeout" and verified:
        outcome = "verified"
    elif adapter_result["outcome"] == "timeout" and not verified:
        outcome = "unchanged"
    record_event(action["_execution_id"], action, outcome, observation, db)
    return adapter_result, observation, verified, outcome


def execute_plan(plan_id, idempotency_key, db=None, injection=None):
    idempotency_key = _key(idempotency_key, "idempotency_key")
    if injection is not None and (
        not isinstance(injection, dict) or injection.get("mode") not in _INJECTION_MODES
    ):
        raise PlanValidationError("unsupported failure injection")
    existing = find_execution(plan_id, idempotency_key, db)
    if existing and existing["body"]:
        body = dict(existing["body"])
        body["duplicate"] = True
        emit("execution.duplicate_prevented", plan_id=plan_id, execution_id=existing["execution_id"])
        return body
    if existing and not existing["body"]:
        raise ExecutionRejected("execution %s is already in progress" % existing["execution_id"])

    plan = get_plan(plan_id, db)
    if plan is None:
        raise PlanNotFound(plan_id)
    if plan["status"] == "EXECUTING":
        raise ExecutionRejected("plan is already executing")
    if plan["status"] != "APPROVED":
        raise ExecutionRejected("execute requires APPROVED, current status is %s" % plan["status"])

    state = load_observed_state(db)
    recheck = evaluate_plan(plan, state)
    if recheck["verdict"] != "PASS":
        recheck["execution_started"] = False
        save_decision(recheck, db)
        emit("execution.blocked_before_write", plan_id=plan_id, verdict=recheck["verdict"], reason_code=recheck["reason_code"])
        return recheck

    execution_id = create_execution(plan_id, idempotency_key, db)
    emit("execution.started", plan_id=plan_id, execution_id=execution_id)
    steps = []
    used = {"done": False}
    actions = plan["actions"]
    for index, action in enumerate(actions):
        action = dict(action)
        action["_execution_id"] = execution_id
        if verified_event_exists(action["action_id"], db):
            steps.append(
                {
                    "action_id": action["action_id"],
                    "action_type": action["type"],
                    "target_id": action["target_id"],
                    "attempt": 0,
                    "adapter_outcome": "skipped",
                    "adapter_wrote": False,
                    "readback": None,
                    "postcondition_verified": True,
                    "note": "idempotent replay",
                }
            )
            continue
        mode = _injection_for(action, injection, used)
        adapter_result, observation, verified, note = _run_attempt(action, db, mode)
        steps.append(_step(action, 1, adapter_result, observation, verified, note))
        if mode == "timeout_unknown":
            return _stop(plan_id, execution_id, "UNKNOWN", steps, db, review_required=True)
        if adapter_result["outcome"] == "timeout" and not verified:
            emit("action.retried", execution_id=execution_id, action_id=action["action_id"], reason="timeout_with_unchanged_state")
            retry_result, retry_observation, retry_verified, retry_note = _run_attempt(action, db, None)
            steps.append(_step(action, 2, retry_result, retry_observation, retry_verified, "retry after timeout: %s" % retry_note))
            verified = retry_verified
        if not verified:
            prior = any(
                step["postcondition_verified"]
                for step in steps
                if step["action_id"] != action["action_id"]
            )
            status = "PARTIAL_FAILURE" if prior else "FAILED"
            return _stop(plan_id, execution_id, status, steps, db)
        remaining = actions[index + 1 :]
        if remaining:
            live = load_observed_state(db)
            nxt = evaluate_plan(
                {"plan_id": plan_id, "requires_approval": False, "actions": remaining},
                live,
            )
            if nxt["verdict"] != "PASS":
                steps.append(
                    {
                        "action_id": remaining[0]["action_id"],
                        "action_type": remaining[0]["type"],
                        "target_id": remaining[0]["target_id"],
                        "attempt": 0,
                        "adapter_outcome": "not_started",
                        "adapter_wrote": False,
                        "readback": None,
                        "postcondition_verified": False,
                        "note": nxt["reason_code"],
                    }
                )
                return _stop(plan_id, execution_id, "PARTIAL_FAILURE", steps, db)

    verification = billing_verification(db)
    status = "VERIFIED" if verification["invariant_holds"] else "FAILED"
    return _stop(plan_id, execution_id, status, steps, db)


def reconcile_execution(execution_id, db=None):
    from app.graph.client import graph
    from app.config import settings

    rows = graph(db).ro_query(
        """
        MATCH (plan:Plan)-[:HAS_EXECUTION]->(execution:Execution {id: $execution_id})
        RETURN plan.id, execution.status, execution.response_json
        LIMIT 1
        """,
        params={"execution_id": execution_id},
        timeout=settings()["query_timeout_ms"],
    ).result_set
    if not rows:
        raise PlanNotFound(execution_id)
    plan_id, status, raw = rows[0]
    if status != "UNKNOWN":
        body = json_load(raw)
        body["reconciled"] = False
        return body
    stored = json_load(raw)
    plan = get_plan(plan_id, db)
    refreshed = []
    established_failure = False
    all_verified = True
    for action in plan["actions"]:
        try:
            observation = observe_action(action, db)
        except ReadbackError:
            finish_execution(stored["execution_id"], plan_id, "UNKNOWN", stored, db)
            stored["reconciled"] = False
            stored["review_required"] = True
            return stored
        verified = postcondition_holds(action, observation)
        refreshed.append(
            {
                "action_id": action["action_id"],
                "action_type": action["type"],
                "target_id": action["target_id"],
                "attempt": 1,
                "adapter_outcome": "reconciled",
                "adapter_wrote": None,
                "readback": observation,
                "postcondition_verified": verified,
                "note": "reconciled from read-back",
            }
        )
        record_event(stored["execution_id"], action, "verified" if verified else "failed", observation, db)
        if not verified:
            established_failure = True
            all_verified = False
    if all_verified and billing_verification(db)["invariant_holds"]:
        status = "VERIFIED"
    elif established_failure and any(step["postcondition_verified"] for step in refreshed):
        status = "PARTIAL_FAILURE"
    elif established_failure:
        status = "FAILED"
    else:
        status = "UNKNOWN"
    emit("execution.reconciled", plan_id=plan_id, execution_id=stored["execution_id"], status=status)
    return _stop(plan_id, stored["execution_id"], status, refreshed, db, review_required=status == "UNKNOWN")


def json_load(raw):
    import json

    return json.loads(raw) if raw else {}


def build_report(plan_id, db=None):
    plan = get_plan(plan_id, db)
    if plan is None:
        raise PlanNotFound(plan_id)
    execution = latest_execution(plan_id, db)
    return {
        "plan_id": plan_id,
        "status": plan["status"],
        "execution_started": plan["execution_started"],
        "actions": plan["actions"],
        "latest_verdict": plan["latest_verdict"],
        "approval": latest_approval(plan_id, db),
        "execution": None if execution is None else execution["body"],
    }
