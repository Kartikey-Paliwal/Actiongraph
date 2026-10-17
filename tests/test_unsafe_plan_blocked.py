from app.planning.fixtures import UNSAFE_OFFBOARD
from app.plans.service import create_and_preflight


def test_unsafe_offboard_is_blocked_without_execution(fresh_graph):
    result = create_and_preflight(UNSAFE_OFFBOARD, fresh_graph)

    assert result["verdict"] == "BLOCK"
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "CRITICAL_SERVICE_WOULD_BE_ORPHANED"
    assert result["invariant"]["id"] == "INV-CRITICAL-SERVICE-OWNER"
    assert result["execution_started"] is False
    assert result["evidence"]["current_eligible_owners"] == ["id-contractor-17"]
    assert result["evidence"]["owners_removed_by_plan"] == ["id-contractor-17"]
    assert result["evidence"]["owners_added_by_plan"] == []
    assert result["evidence"]["eligible_after"] == []
    assert result["evidence"]["ineligible_owners"] == ["id-former-4"]
    assert "id-contractor-17 -[OWNS]-> svc-billing" in result["evidence"]["graph_path"]
    assert "svc-billing -[DEPENDS_ON]-> db-billing-primary" in result["evidence"]["graph_path"]
