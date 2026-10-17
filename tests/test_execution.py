import pytest

from app.errors import ExecutionRejected
from app.execution.runner import approve_plan, execute_plan, reconcile_execution
from app.graph.client import graph
from app.planning.fixtures import SAFE_OFFBOARD, UNSAFE_OFFBOARD
from app.plans.service import create_and_preflight


def _owns_billing(db, identity_id):
    rows = graph(db).query(
        """
        MATCH (:Identity {id: $identity_id})-[rel:OWNS]->(:Service {id: 'svc-billing'})
        RETURN count(rel) AS hits
        """,
        params={"identity_id": identity_id},
    ).result_set
    return rows[0][0]


def _approved_safe_plan(db):
    preflight = create_and_preflight(SAFE_OFFBOARD, db)
    assert preflight["status"] == "AWAITING_APPROVAL"
    approve_plan(preflight["plan_id"], "id-engineer-9", "Replacement owner is active and eligible", db)
    return preflight["plan_id"]


def test_blocked_plan_never_executes(fresh_graph):
    preflight = create_and_preflight(UNSAFE_OFFBOARD, fresh_graph)
    with pytest.raises(ExecutionRejected):
        execute_plan(preflight["plan_id"], "exec-unsafe-1", fresh_graph)
    assert _owns_billing(fresh_graph, "id-engineer-9") == 0


def test_execute_before_approval_is_rejected(fresh_graph):
    preflight = create_and_preflight(SAFE_OFFBOARD, fresh_graph)
    with pytest.raises(ExecutionRejected):
        execute_plan(preflight["plan_id"], "exec-too-soon", fresh_graph)
    assert _owns_billing(fresh_graph, "id-engineer-9") == 0


def test_approved_safe_plan_is_verified_by_readback(fresh_graph):
    plan_id = _approved_safe_plan(fresh_graph)
    result = execute_plan(plan_id, "exec-safe-1", fresh_graph)

    assert result["status"] == "VERIFIED"
    assert result["execution_started"] is True
    assert result["verification"]["eligible_owners"] == ["id-engineer-9"]
    assert result["verification"]["contractor_status"] == "disabled"
    assert result["verification"]["invariant_holds"] is True
    assert [step["postcondition_verified"] for step in result["steps"]] == [True, True]
    assert _owns_billing(fresh_graph, "id-engineer-9") == 1


def test_duplicate_execute_does_not_apply_the_edge_twice(fresh_graph):
    plan_id = _approved_safe_plan(fresh_graph)
    first = execute_plan(plan_id, "exec-safe-once", fresh_graph)
    second = execute_plan(plan_id, "exec-safe-once", fresh_graph)

    assert first["duplicate"] is False
    assert second["duplicate"] is True
    assert second["execution_id"] == first["execution_id"]
    assert _owns_billing(fresh_graph, "id-engineer-9") == 1


def test_graph_change_after_approval_blocks_before_any_write(fresh_graph):
    plan_id = _approved_safe_plan(fresh_graph)
    graph(fresh_graph).query(
        "MATCH (identity:Identity {id: 'id-engineer-9'}) SET identity.eligible_owner = false"
    )
    result = execute_plan(plan_id, "exec-stale-1", fresh_graph)

    assert result["verdict"] == "BLOCK"
    assert result["execution_started"] is False
    assert _owns_billing(fresh_graph, "id-engineer-9") == 0


def test_timeout_with_unchanged_state_retries_and_verifies(fresh_graph):
    plan_id = _approved_safe_plan(fresh_graph)
    result = execute_plan(
        plan_id,
        "exec-timeout-retry",
        fresh_graph,
        injection={"action_type": "disable_identity", "mode": "timeout_unchanged"},
    )

    assert result["status"] == "VERIFIED"
    disable_steps = [step for step in result["steps"] if step["action_type"] == "disable_identity"]
    assert [step["adapter_outcome"] for step in disable_steps] == ["timeout", "accepted"]
    assert disable_steps[-1]["postcondition_verified"] is True


def test_timeout_with_failed_readback_is_unknown(fresh_graph):
    plan_id = _approved_safe_plan(fresh_graph)
    result = execute_plan(
        plan_id,
        "exec-timeout-unknown",
        fresh_graph,
        injection={"action_type": "disable_identity", "mode": "timeout_unknown"},
    )

    assert result["status"] == "UNKNOWN"
    assert result["review_required"] is True
    assert result["status"] != "VERIFIED"
    assert result["verification"]["contractor_status"] == "active"
    assert _owns_billing(fresh_graph, "id-engineer-9") == 1


def test_reconcile_unknown_timeout_from_readback(fresh_graph):
    plan_id = _approved_safe_plan(fresh_graph)
    unknown = execute_plan(
        plan_id,
        "exec-reconcile",
        fresh_graph,
        injection={"action_type": "disable_identity", "mode": "timeout_unknown"},
    )
    reconciled = reconcile_execution(unknown["execution_id"], fresh_graph)

    assert reconciled["status"] == "PARTIAL_FAILURE"
    assert reconciled["status"] != "VERIFIED"
    disable = [step for step in reconciled["steps"] if step["action_type"] == "disable_identity"][0]
    assert disable["readback"]["status"] == "active"
    assert disable["postcondition_verified"] is False


def test_adapter_success_without_a_write_is_not_verified(fresh_graph):
    plan_id = _approved_safe_plan(fresh_graph)
    result = execute_plan(
        plan_id,
        "exec-lie",
        fresh_graph,
        injection={"action_type": "disable_identity", "mode": "success_without_write"},
    )

    assert result["status"] == "PARTIAL_FAILURE"
    assert result["verification"]["invariant_holds"] is False
    disable = [step for step in result["steps"] if step["action_type"] == "disable_identity"][-1]
    assert disable["adapter_outcome"] == "accepted"
    assert disable["postcondition_verified"] is False
