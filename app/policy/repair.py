"""Turn an orphaning BLOCK into a graph-backed repair suggestion.

Candidates come from a Cypher read (app.graph.queries.replacement_candidates).
The suggested plan is re-checked in memory with the same evaluator. Nothing is
stored as a plan, nothing is executed, and the observed graph is not changed.
"""

from app.policy.evaluator import evaluate_plan

ORPHANED = "CRITICAL_SERVICE_WOULD_BE_ORPHANED"

BASIS = "active eligible owner of a service that shares a critical dependency"


def needs_repair(result):
    return result["verdict"] == "BLOCK" and result["reason_code"] == ORPHANED


def disabled_identity_ids(plan):
    return sorted({action["target_id"] for action in plan["actions"] if action["type"] == "disable_identity"})


def orphaned_service_id(result):
    return result["evidence"]["affected_service_ids"][0]


def _blocking_action_id(result):
    for finding in result["findings"]:
        if finding["verdict"] == "BLOCK" and finding["reason_code"] == ORPHANED:
            return finding["action_id"]
    return None


def _payload_action(action):
    return {"type": action["type"], "target_id": action["target_id"], "params": dict(action["params"])}


def _suggested_actions(plan, blocking_action_id, service_id, owner_id):
    actions = []
    for action in plan["actions"]:
        if action["action_id"] == blocking_action_id:
            actions.append(
                {
                    "type": "assign_service_owner",
                    "target_id": service_id,
                    "params": {"new_owner_id": owner_id},
                }
            )
        actions.append(_payload_action(action))
    return actions


def suggest_repair(plan, result, state, candidates):
    service_id = orphaned_service_id(result)
    suggestion = {
        "service_id": service_id,
        "basis": BASIS,
        "candidates": candidates,
        "actions": None,
        "preflight": None,
        "is_projection": True,
        "applied": False,
    }
    if not candidates:
        suggestion["note"] = "no graph-backed replacement owner; a human must choose one"
        return suggestion

    owner_id = candidates[0]["identity_id"]
    actions = _suggested_actions(plan, _blocking_action_id(result), service_id, owner_id)
    projected = {
        "plan_id": "%s:suggested" % plan["plan_id"],
        "requires_approval": True,
        "actions": [
            dict(action, action_id="%s:suggested:a%s" % (plan["plan_id"], index))
            for index, action in enumerate(actions, start=1)
        ],
    }
    recheck = evaluate_plan(projected, state)
    suggestion["preflight"] = {
        "verdict": recheck["verdict"],
        "reason_code": recheck["reason_code"],
        "status": recheck["status"],
        "invariant": recheck["invariant"],
        "evidence": recheck["evidence"],
    }
    if recheck["verdict"] == "BLOCK":
        suggestion["note"] = "suggested repair is still blocked: %s" % recheck["reason_code"]
        return suggestion
    suggestion["actions"] = actions
    return suggestion
