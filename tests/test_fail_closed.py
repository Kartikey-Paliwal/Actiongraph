from fastapi.testclient import TestClient

from app.errors import GraphUnavailable, PlanValidationError
from app.graph.client import graph
from app.main import create_app
from app.plans.service import create_plan


def _plan_count(db):
    return graph(db).query("MATCH (p:Plan) RETURN count(p) AS count").result_set[0][0]


def test_unsupported_action_is_rejected_before_a_plan_is_stored(fresh_graph):
    client = TestClient(create_app())
    before = _plan_count(fresh_graph)

    response = client.post(
        "/v1/plans",
        json={
            "scenario": "synthetic_identity_offboarding",
            "actions": [{"type": "run_shell", "target_id": "id-contractor-17"}],
        },
    )

    assert response.status_code == 422
    assert _plan_count(fresh_graph) == before


def test_unknown_target_is_rejected(fresh_graph):
    try:
        create_plan(
            {
                "scenario": "synthetic_identity_offboarding",
                "actions": [{"type": "disable_identity", "target_id": "id-missing-1"}],
            },
            fresh_graph,
        )
    except PlanValidationError as exc:
        assert "unknown target" in str(exc)
    else:
        raise AssertionError("unknown target was accepted")


def test_graph_unavailable_does_not_return_a_verdict(monkeypatch):
    monkeypatch.setenv("FALKORDB_PORT", "6391")
    try:
        create_plan(
            {
                "scenario": "synthetic_identity_offboarding",
                "actions": [{"type": "disable_identity", "target_id": "id-contractor-17"}],
            }
        )
    except GraphUnavailable:
        return
    raise AssertionError("unavailable graph produced a plan")
