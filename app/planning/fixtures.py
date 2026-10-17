"""Fixed plans for the synthetic offboarding demo. No model output is required."""

UNSAFE_OFFBOARD = {
    "scenario": "synthetic_identity_offboarding",
    "requires_approval": True,
    "actions": [
        {"type": "disable_identity", "target_id": "id-contractor-17"},
        {"type": "revoke_credential", "target_id": "credential-deploy-4"},
    ],
}

SAFE_OFFBOARD = {
    "scenario": "synthetic_identity_offboarding",
    "requires_approval": True,
    "actions": [
        {
            "type": "assign_service_owner",
            "target_id": "svc-billing",
            "params": {"new_owner_id": "id-engineer-9"},
        },
        {"type": "disable_identity", "target_id": "id-contractor-17"},
    ],
}

DISABLE_CONTRACTOR = {
    "scenario": "synthetic_identity_offboarding",
    "requires_approval": True,
    "actions": [
        {"type": "disable_identity", "target_id": "id-contractor-17"},
    ],
}

DELETE_BILLING_DATABASE = {
    "scenario": "synthetic_identity_offboarding",
    "requires_approval": True,
    "actions": [
        {"type": "delete_resource", "target_id": "db-billing-primary"},
    ],
}
