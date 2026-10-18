from fastapi.testclient import TestClient

from app.main import create_app


def test_demo_page_is_served_and_self_contained():
    response = TestClient(create_app()).get("/")

    assert response.status_code == 200
    assert "<title>ActionGraph Demo</title>" in response.text
    assert "<script src" not in response.text
    assert "This page decides nothing" in response.text


def test_demo_fixtures_and_reset(fresh_graph):
    client = TestClient(create_app())
    fixtures = client.get("/v1/demo/fixtures").json()
    summary = client.post("/v1/demo/reset").json()

    assert fixtures["unsafe"]["actions"][0] == {"type": "disable_identity", "target_id": "id-contractor-17"}
    assert fixtures["approver_id"] == "id-engineer-9"
    assert summary["graph_version"] == "seed-v1"
    assert summary["counts"]["Invariant"] == 2


def test_demo_ablation_shows_pass_then_block(fresh_graph):
    body = TestClient(create_app()).post("/v1/demo/ablation").json()

    assert body["edge"] == "id-engineer-9 -[OWNS]-> svc-billing"
    assert body["with_edge"]["verdict"] == "PASS"
    assert body["without_edge"]["verdict"] == "BLOCK"
    assert body["without_edge"]["reason_code"] == "CRITICAL_SERVICE_WOULD_BE_ORPHANED"


def test_preflight_records_when_it_was_evaluated(fresh_graph):
    client = TestClient(create_app())
    plan_id = client.post("/v1/plans", json=client.get("/v1/demo/fixtures").json()["unsafe"]).json()["plan_id"]
    verdict = client.post("/v1/plans/%s/preflight" % plan_id).json()

    assert verdict["evaluated_at"].endswith("Z")
    assert client.get("/v1/plans/%s" % plan_id).json()["latest_verdict"]["evaluated_at"] == verdict["evaluated_at"]


def test_demo_routes_fail_closed_when_the_graph_is_down(monkeypatch):
    monkeypatch.setenv("FALKORDB_PORT", "6391")
    client = TestClient(create_app())

    assert client.post("/v1/demo/reset").status_code == 503
    assert client.post("/v1/demo/ablation").status_code == 503
