from app.planning.fixtures import SAFE_OFFBOARD
from app.plans.service import create_and_preflight


def test_safe_handoff_passes_and_waits_for_approval(fresh_graph):
    result = create_and_preflight(SAFE_OFFBOARD, fresh_graph)

    assert result["verdict"] == "PASS"
    assert result["status"] == "AWAITING_APPROVAL"
    assert result["reason_code"] == "OWNER_INVARIANT_HOLDS"
    assert result["execution_started"] is False
    assert result["evidence"]["owners_added_by_plan"] == ["id-engineer-9"]
    assert result["evidence"]["owners_removed_by_plan"] == ["id-contractor-17"]
    assert result["evidence"]["eligible_after"] == ["id-engineer-9"]


def test_ineligible_replacement_is_blocked(fresh_graph):
    from app.plans.service import create_and_preflight as preflight

    result = preflight(
        {
            "scenario": "synthetic_identity_offboarding",
            "requires_approval": True,
            "actions": [
                {
                    "type": "assign_service_owner",
                    "target_id": "svc-billing",
                    "params": {"new_owner_id": "id-former-4"},
                },
                {"type": "disable_identity", "target_id": "id-contractor-17"},
            ],
        },
        fresh_graph,
    )

    assert result["verdict"] == "BLOCK"
    assert result["reason_code"] == "REPLACEMENT_NOT_ELIGIBLE"
    assert result["execution_started"] is False


def test_deleting_shared_database_names_both_services(fresh_graph):
    from app.planning.fixtures import DELETE_BILLING_DATABASE

    result = create_and_preflight(DELETE_BILLING_DATABASE, fresh_graph)

    assert result["verdict"] == "BLOCK"
    assert result["reason_code"] == "CRITICAL_RESOURCE_HAS_ACTIVE_DEPENDENTS"
    assert result["evidence"]["affected_service_ids"] == ["svc-billing", "svc-payments"]
    assert result["invariant"]["id"] == "INV-CRITICAL-DEPENDENCY"
    assert result["invariant"]["description"]
    assert "svc-payments -[DEPENDS_ON]-> db-billing-primary" in result["evidence"]["graph_path"]
