"""Day 1 proof: FalkorDB relationships show who owns a critical service."""

import pytest

from app.graph.client import connect, graph, ping
from app.graph.queries import ownership_impact
from app.graph.seed import reset_scenario


@pytest.fixture(scope="module")
def db():
    try:
        client = ping(connect())
    except Exception as exc:
        pytest.skip("FalkorDB is not reachable: %s" % exc)
    reset_scenario(client)
    return client


def test_contractor_owns_critical_billing_service(db):
    impact = ownership_impact("id-contractor-17", db)

    assert impact["owns_critical_service"] is True
    assert impact["critical_services"][0]["service_id"] == "svc-billing"
    assert impact["critical_services"][0]["counts_as_eligible_owner"] is True

    reasons = [path["reason"] for path in impact["evidence_paths"]]
    assert "identity_owns_critical_service" in reasons
    assert "owned_service_depends_on_resource" in reasons
    assert "credential_issued_to_owner_grants_resource_access" in reasons

    ownership = next(path for path in impact["evidence_paths"] if path["reason"] == "identity_owns_critical_service")
    assert ownership["steps"] == ["id-contractor-17 -[OWNS]-> svc-billing"]

    dependencies = [path for path in impact["evidence_paths"] if path["reason"] == "owned_service_depends_on_resource"]
    assert len(dependencies) == 1
    assert "svc-billing -[DEPENDS_ON]-> db-billing-primary" in dependencies[0]["steps"]
    assert impact["graph_version"] == "seed-v1"
    assert {"id": "INV-CRITICAL-SERVICE-OWNER", "rule_type": "critical_service_owner", "service_id": "svc-billing"} in impact["invariants"]

    co_owners = [path for path in impact["evidence_paths"] if path["reason"] == "co_owner_not_eligible"]
    assert co_owners[0]["steps"] == ["id-former-4 -[OWNS]-> svc-billing"]
    assert co_owners[0]["counts_as_eligible_owner"] is False


def test_noncritical_cache_is_ignored(db):
    impact = ownership_impact("id-contractor-17", db)
    evidence_steps = [step for path in impact["evidence_paths"] for step in path["steps"]]
    assert "svc-billing -[DEPENDS_ON]-> cache-billing" not in evidence_steps
    ignored_steps = [step for path in impact["ignored_dependencies"] for step in path["steps"]]
    assert "svc-billing -[DEPENDS_ON]-> cache-billing" in ignored_steps


def test_low_criticality_service_is_not_in_contractor_impact(db):
    impact = ownership_impact("id-contractor-17", db)
    service_ids = [service["service_id"] for service in impact["critical_services"]]
    assert "svc-handbook" not in service_ids


def test_engineer_owns_payments_and_shares_the_database(db):
    impact = ownership_impact("id-engineer-9", db)
    service_ids = [service["service_id"] for service in impact["critical_services"]]
    assert service_ids == ["svc-payments"]
    assert impact["critical_services"][0]["counts_as_eligible_owner"] is True
    dependency = next(path for path in impact["evidence_paths"] if path["reason"] == "owned_service_depends_on_resource")
    assert "svc-payments -[DEPENDS_ON]-> db-billing-primary" in dependency["steps"]
    assert {"id": "INV-CRITICAL-SERVICE-OWNER", "rule_type": "critical_service_owner", "service_id": "svc-payments"} in impact["invariants"]


def test_disabled_co_owner_does_not_count_as_eligible(db):
    impact = ownership_impact("id-former-4", db)
    assert impact["owns_critical_service"] is True
    assert impact["critical_services"][0]["service_id"] == "svc-billing"
    assert impact["critical_services"][0]["counts_as_eligible_owner"] is False


def test_reset_restores_contractor_ownership_after_edge_removal(db):
    graph(db).query(
        """
        MATCH (i:Identity {id: 'id-contractor-17'})-[rel:OWNS]->(s:Service {id: 'svc-billing'})
        DELETE rel
        """
    )
    removed = ownership_impact("id-contractor-17", db)
    assert removed["owns_critical_service"] is False

    reset_scenario(db)
    restored = ownership_impact("id-contractor-17", db)
    assert restored["owns_critical_service"] is True
    assert restored["critical_services"][0]["service_id"] == "svc-billing"


def test_unknown_identity_has_no_path_and_bad_id_is_rejected(db):
    impact = ownership_impact("id-missing-1", db)
    assert impact["owns_critical_service"] is False

    with pytest.raises(ValueError):
        ownership_impact("contractor 17; MATCH (n) DETACH DELETE n", db)
