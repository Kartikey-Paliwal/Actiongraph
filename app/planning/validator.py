"""Allow-listed plan schema. Unsupported actions never reach the graph writer."""

from app.errors import PlanValidationError
from app.graph.queries import validate_identity_id

ALLOWED_ACTIONS = (
    "assign_service_owner",
    "disable_identity",
    "revoke_credential",
    "delete_resource",
)

HIGH_RISK_ACTIONS = (
    "disable_identity",
    "revoke_credential",
    "delete_resource",
)

ALLOWED_CONDITIONS = (
    "new_owner_is_active",
    "new_owner_is_eligible",
    "new_owner_owns_service",
    "critical_services_have_eligible_owner",
    "identity_status_is_disabled",
)

_TARGET_LABEL = {
    "assign_service_owner": "Service",
    "disable_identity": "Identity",
    "revoke_credential": "Credential",
    "delete_resource": "Resource",
}

_MAX_ACTIONS = 20


def _require_id(value, field_name):
    try:
        return validate_identity_id(value)
    except ValueError as exc:
        raise PlanValidationError("%s: %s" % (field_name, exc)) from exc


def _conditions(raw, field_name):
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise PlanValidationError("%s must be a list" % field_name)
    cleaned = []
    for item in raw:
        if item not in ALLOWED_CONDITIONS:
            raise PlanValidationError("unsupported condition: %s" % item)
        cleaned.append(item)
    return cleaned


def validate_plan_request(payload):
    if not isinstance(payload, dict):
        raise PlanValidationError("plan must be an object")
    scenario = payload.get("scenario")
    if not isinstance(scenario, str) or not scenario.strip() or len(scenario) > 80:
        raise PlanValidationError("scenario must be 1-80 characters")
    actions = payload.get("actions")
    if not isinstance(actions, list) or not actions or len(actions) > _MAX_ACTIONS:
        raise PlanValidationError("actions must contain 1 to %s allow-listed steps" % _MAX_ACTIONS)
    requires_approval = payload.get("requires_approval", True)
    if not isinstance(requires_approval, bool):
        raise PlanValidationError("requires_approval must be a boolean")

    plan_id = payload.get("plan_id")
    if plan_id is not None:
        plan_id = _require_id(plan_id, "plan_id")

    normalized = []
    for index, action in enumerate(actions, start=1):
        if not isinstance(action, dict):
            raise PlanValidationError("action %s must be an object" % index)
        action_type = action.get("type")
        if action_type not in ALLOWED_ACTIONS:
            raise PlanValidationError("unsupported action: %s" % action_type)
        target_id = _require_id(action.get("target_id"), "target_id")
        params = action.get("params") or {}
        if not isinstance(params, dict):
            raise PlanValidationError("action %s params must be an object" % index)
        cleaned_params = {}
        if action_type == "assign_service_owner":
            if set(params) != {"new_owner_id"}:
                raise PlanValidationError("assign_service_owner accepts only new_owner_id")
            cleaned_params["new_owner_id"] = _require_id(params.get("new_owner_id"), "new_owner_id")
        elif params:
            raise PlanValidationError("action %s does not accept params" % action_type)
        normalized.append(
            {
                "sequence": index,
                "type": action_type,
                "target_id": target_id,
                "target_label": _TARGET_LABEL[action_type],
                "params": cleaned_params,
                "preconditions": _conditions(action.get("preconditions"), "preconditions"),
                "postconditions": _conditions(action.get("postconditions"), "postconditions"),
            }
        )

    if requires_approval is False and any(step["type"] in HIGH_RISK_ACTIONS for step in normalized):
        requires_approval = True

    return {
        "plan_id": plan_id,
        "scenario": scenario.strip(),
        "requires_approval": requires_approval,
        "actions": normalized,
    }
