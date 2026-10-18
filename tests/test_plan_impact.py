from fastapi.testclient import TestClient

from app.graph.client import graph
from app.main import create_app
from app.planning.fixtures import DELETE_BILLING_DATABASE, SAFE_OFFBOARD, UNSAFE_OFFBOARD


def _impact(client, payload):
    plan_id = client.post("/v1/plans", json=payload).json()["plan_id"]
    response = client.get("/v1/plans/%s/impact" % plan_id)
    assert response.status_code == 200
    return response.json()


def _steps(target):
    return [step for path in target["paths"] for step in path["steps"]]


def test_unsafe_plan_impact_shows_owner_path_and_credential_reach(fresh_graph):
    body = _impact(TestClient(create_app()), UNSAFE_OFFBOARD)
    disable, revoke = body["targets"]

    assert disable["action_type"] == "disable_identity"
    assert "id-contractor-17 -[OWNS]-> svc-billing" in _steps(disable)
    critical_reach = [path for path in revoke["paths"] if path.get("counts_as_impact")]
    assert sorted(path["steps"][1] for path in critical_reach) == [
        "svc-billing -[DEPENDS_ON]-> db-billing-primary",
        "svc-payments -[DEPENDS_ON]-> db-billing-primary",
    ]
    assert "credential-deploy-4 -[ISSUED_TO]-> id-contractor-17" in _steps(revoke)
    assert body["traversal"]["truncated"] is False


def test_assign_impact_marks_the_proposed_owner_as_a_projection(fresh_graph):
    body = _impact(TestClient(create_app()), SAFE_OFFBOARD)
    assign = body["targets"][0]
    proposed = [path for path in assign["paths"] if path["reason"] == "proposed_owner"][0]
    owners = {path["steps"][0]: path["counts_as_eligible_owner"] for path in assign["paths"] if path["reason"] == "current_owner"}

    assert proposed["is_projection"] is True
    assert proposed["counts_as_eligible_owner"] is True
    assert owners == {
        "id-contractor-17 -[OWNS]-> svc-billing": True,
        "id-former-4 -[OWNS]-> svc-billing": False,
    }


def test_delete_impact_names_both_dependent_services(fresh_graph):
    target = _impact(TestClient(create_app()), DELETE_BILLING_DATABASE)["targets"][0]
    dependents = [path for path in target["paths"] if path["reason"] == "active_service_depends_on_resource"]

    assert [path["steps"][0] for path in dependents] == [
        "svc-billing -[DEPENDS_ON]-> db-billing-primary",
        "svc-payments -[DEPENDS_ON]-> db-billing-primary",
    ]
    assert all(path["counts_as_impact"] for path in dependents)


def test_impact_is_read_only_and_unknown_plan_is_404(fresh_graph):
    client = TestClient(create_app())
    before = graph(fresh_graph).query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    plan_id = client.post("/v1/plans", json=SAFE_OFFBOARD).json()["plan_id"]
    after_create = graph(fresh_graph).query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0]
    client.get("/v1/plans/%s/impact" % plan_id)

    assert graph(fresh_graph).query("MATCH ()-[r]->() RETURN count(r)").result_set[0][0] == after_create > before
    assert client.get("/v1/plans/plan-missing/impact").status_code == 404


def test_impact_reports_truncation_at_the_result_limit(fresh_graph, monkeypatch):
    client = TestClient(create_app())
    plan_id = client.post("/v1/plans", json=DELETE_BILLING_DATABASE).json()["plan_id"]
    monkeypatch.setenv("RESULT_LIMIT", "1")
    body = client.get("/v1/plans/%s/impact" % plan_id).json()

    assert body["traversal"]["result_limit"] == 1
    assert body["traversal"]["truncated"] is True
