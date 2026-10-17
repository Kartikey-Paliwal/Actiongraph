from app.graph.client import graph
from app.planning.fixtures import DISABLE_CONTRACTOR
from app.plans.service import create_plan, preflight_plan


def test_removing_the_second_owner_edge_flips_pass_to_block(fresh_graph):
    graph(fresh_graph).query(
        """
        MATCH (engineer:Identity {id: 'id-engineer-9'})
        MATCH (billing:Service {id: 'svc-billing'})
        MERGE (engineer)-[:OWNS]->(billing)
        """
    )
    created = create_plan(DISABLE_CONTRACTOR, fresh_graph)
    first = preflight_plan(created["plan_id"], fresh_graph)
    assert first["verdict"] == "PASS"
    assert first["evidence"]["eligible_after"] == ["id-engineer-9"]

    graph(fresh_graph).query(
        """
        MATCH (:Identity {id: 'id-engineer-9'})-[rel:OWNS]->(:Service {id: 'svc-billing'})
        DELETE rel
        """
    )
    second = preflight_plan(created["plan_id"], fresh_graph)

    assert second["verdict"] == "BLOCK"
    assert second["reason_code"] == "CRITICAL_SERVICE_WOULD_BE_ORPHANED"
    assert second["evidence"]["eligible_after"] == []
    assert second["execution_started"] is False
