"""Load observed workspace state. Callers must not treat a failed load as an empty graph."""

from app.config import settings
from app.errors import GraphUnavailable
from app.graph.client import graph, ping
from app.observability import emit

_STATE_LIMIT = 500

IDENTITIES = """
MATCH (i:Identity)
RETURN i.id AS id, i.status AS status, i.eligible_owner AS eligible_owner
ORDER BY i.id
LIMIT $result_limit
"""

SERVICES = """
MATCH (s:Service)
RETURN s.id AS id, s.criticality AS criticality, s.status AS status
ORDER BY s.id
LIMIT $result_limit
"""

RESOURCES = """
MATCH (r:Resource)
RETURN r.id AS id, r.criticality AS criticality, r.status AS status
ORDER BY r.id
LIMIT $result_limit
"""

CREDENTIALS = """
MATCH (c:Credential)
RETURN c.id AS id, c.status AS status
ORDER BY c.id
LIMIT $result_limit
"""

OWNS = """
MATCH (i:Identity)-[:OWNS]->(s:Service)
RETURN i.id AS identity_id, s.id AS service_id
ORDER BY i.id, s.id
LIMIT $result_limit
"""

DEPENDS = """
MATCH (s:Service)-[:DEPENDS_ON]->(r:Resource)
RETURN s.id AS service_id, r.id AS resource_id
ORDER BY s.id, r.id
LIMIT $result_limit
"""

GRANTS = """
MATCH (c:Credential)-[:GRANTS_ACCESS_TO]->(r:Resource)
RETURN c.id AS credential_id, r.id AS resource_id
ORDER BY c.id, r.id
LIMIT $result_limit
"""

PROTECTS = """
MATCH (rule:Invariant)-[:PROTECTS]->(s:Service)
RETURN rule.id AS invariant_id,
       rule.description AS description,
       rule.enabled AS enabled,
       s.id AS service_id
ORDER BY rule.id, s.id
LIMIT $result_limit
"""

VERSION = """
MATCH (w:Workspace {id: 'ws-synthetic-1'})
RETURN w.graph_version AS graph_version
LIMIT 1
"""


def _rows(selected, cypher):
    try:
        result = selected.ro_query(
            cypher,
            params={"result_limit": _STATE_LIMIT},
            timeout=settings()["query_timeout_ms"],
        )
    except Exception as exc:
        emit("graph.unavailable", reason="query_failed")
        raise GraphUnavailable("graph query failed: %s" % exc) from exc
    rows = result.result_set
    if len(rows) >= _STATE_LIMIT:
        emit("graph.unavailable", reason="state_limit_reached", limit=_STATE_LIMIT)
        raise GraphUnavailable("graph read reached the state limit and cannot be used for a verdict")
    return rows


def load_observed_state(db=None):
    try:
        client = ping(db)
    except GraphUnavailable:
        raise
    except Exception as exc:
        emit("graph.unavailable", reason="connect_failed")
        raise GraphUnavailable("FalkorDB is unavailable: %s" % exc) from exc
    selected = graph(client)
    identities = {}
    for row in _rows(selected, IDENTITIES):
        identities[row[0]] = {"status": row[1], "eligible_owner": bool(row[2])}
    services = {}
    for row in _rows(selected, SERVICES):
        services[row[0]] = {"criticality": row[1], "status": row[2]}
    resources = {}
    for row in _rows(selected, RESOURCES):
        resources[row[0]] = {"criticality": row[1], "status": row[2]}
    credentials = {}
    for row in _rows(selected, CREDENTIALS):
        credentials[row[0]] = {"status": row[1]}
    owns = [(row[0], row[1]) for row in _rows(selected, OWNS)]
    depends = [(row[0], row[1]) for row in _rows(selected, DEPENDS)]
    grants = [(row[0], row[1]) for row in _rows(selected, GRANTS)]
    invariants = {}
    for row in _rows(selected, PROTECTS):
        rule = invariants.setdefault(
            row[0],
            {"description": row[1], "enabled": bool(row[2]), "protects": []},
        )
        rule["protects"].append(row[3])
    try:
        version_rows = selected.ro_query(VERSION, timeout=settings()["query_timeout_ms"]).result_set
    except Exception as exc:
        raise GraphUnavailable("graph query failed: %s" % exc) from exc
    return {
        "graph_version": version_rows[0][0] if version_rows else None,
        "identities": identities,
        "services": services,
        "resources": resources,
        "credentials": credentials,
        "owns": owns,
        "depends": depends,
        "grants": grants,
        "invariants": invariants,
    }
