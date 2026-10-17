from app.graph.client import graph
from app.planning.fixtures import DISABLE_CONTRACTOR, SAFE_OFFBOARD, UNSAFE_OFFBOARD
from app.plans.service import create_and_preflight


def _count(db, cypher):
    return graph(db).query(cypher).result_set[0][0]


def test_blocked_offboard_suggests_owner_who_shares_the_database(fresh_graph):
    result = create_and_preflight(UNSAFE_OFFBOARD, fresh_graph)
    repair = result["suggested_repair"]

    assert result["verdict"] == "BLOCK"
    assert repair["service_id"] == "svc-billing"
    assert repair["is_projection"] is True
    assert repair["applied"] is False
    assert [c["identity_id"] for c in repair["candidates"]] == ["id-engineer-9"]
    candidate = repair["candidates"][0]
    assert candidate["via_services"] == ["svc-payments"]
    assert candidate["shared_resources"] == ["db-billing-primary"]
    assert candidate["evidence_path"] == [
        "id-engineer-9 -[OWNS]-> svc-payments",
        "svc-payments -[DEPENDS_ON]-> db-billing-primary",
        "svc-billing -[DEPENDS_ON]-> db-billing-primary",
    ]
    assert [a["type"] for a in repair["actions"]] == [
        "assign_service_owner",
        "disable_identity",
        "revoke_credential",
    ]
    assert repair["actions"][0]["params"] == {"new_owner_id": "id-engineer-9"}
    assert repair["preflight"]["verdict"] == "NEEDS_REVIEW"
    assert repair["preflight"]["reason_code"] == "CREDENTIAL_SERVES_CRITICAL_RESOURCE"


def test_disable_only_repair_rechecks_as_pass(fresh_graph):
    result = create_and_preflight(DISABLE_CONTRACTOR, fresh_graph)
    repair = result["suggested_repair"]

    assert result["verdict"] == "BLOCK"
    assert [a["type"] for a in repair["actions"]] == ["assign_service_owner", "disable_identity"]
    assert repair["preflight"]["verdict"] == "PASS"
    assert repair["preflight"]["status"] == "AWAITING_APPROVAL"


def test_suggesting_a_repair_writes_nothing(fresh_graph):
    plans_before = _count(fresh_graph, "MATCH (p:Plan) RETURN count(p)")
    create_and_preflight(UNSAFE_OFFBOARD, fresh_graph)

    assert _count(fresh_graph, "MATCH (p:Plan) RETURN count(p)") == plans_before + 1
    assert (
        _count(
            fresh_graph,
            "MATCH (:Identity {id: 'id-engineer-9'})-[rel:OWNS]->(:Service {id: 'svc-billing'}) RETURN count(rel)",
        )
        == 0
    )


def test_no_eligible_candidate_means_no_suggested_actions(fresh_graph):
    graph(fresh_graph).query(
        "MATCH (identity:Identity {id: 'id-engineer-9'}) SET identity.eligible_owner = false"
    )
    result = create_and_preflight(UNSAFE_OFFBOARD, fresh_graph)
    repair = result["suggested_repair"]

    assert result["verdict"] == "BLOCK"
    assert repair["candidates"] == []
    assert repair["actions"] is None
    assert "human must choose" in repair["note"]


def test_passing_plan_has_no_suggestion(fresh_graph):
    result = create_and_preflight(SAFE_OFFBOARD, fresh_graph)

    assert result["verdict"] == "PASS"
    assert "suggested_repair" not in result
