"""Explicit, bounded impact paths for the synthetic offboarding scenario.

These queries read observed graph state. They do not apply a proposed plan.
"""

import re

from app.config import settings
from app.graph.client import graph, ping

_IDENTITY_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")

OWNED_CRITICAL_SERVICES = """
MATCH (i:Identity {id: $identity_id})-[rel:OWNS]->(s:Service)
WHERE s.criticality = 'critical' AND s.status = 'active'
RETURN i.id AS identity_id,
       i.status AS identity_status,
       i.eligible_owner AS eligible_owner,
       type(rel) AS relationship,
       s.id AS service_id,
       s.criticality AS criticality,
       s.status AS service_status
ORDER BY s.id
LIMIT $result_limit
"""

SERVICE_DEPENDENCIES = """
MATCH (i:Identity {id: $identity_id})-[:OWNS]->(s:Service)-[rel:DEPENDS_ON]->(r:Resource)
WHERE s.criticality = 'critical' AND s.status = 'active'
  AND r.criticality = 'critical' AND r.status = 'active'
RETURN i.id AS identity_id,
       s.id AS service_id,
       type(rel) AS relationship,
       r.id AS resource_id,
       r.resource_type AS resource_type,
       r.status AS resource_status,
       r.criticality AS resource_criticality
ORDER BY s.id, r.id
LIMIT $result_limit
"""

IGNORED_DEPENDENCIES = """
MATCH (i:Identity {id: $identity_id})-[:OWNS]->(s:Service)-[rel:DEPENDS_ON]->(r:Resource)
WHERE s.criticality = 'critical' AND s.status = 'active'
  AND r.criticality <> 'critical'
RETURN i.id AS identity_id,
       s.id AS service_id,
       type(rel) AS relationship,
       r.id AS resource_id,
       r.criticality AS resource_criticality
ORDER BY s.id, r.id
LIMIT $result_limit
"""

CO_OWNERS = """
MATCH (i:Identity {id: $identity_id})-[:OWNS]->(s:Service)<-[rel:OWNS]-(other:Identity)
WHERE s.criticality = 'critical' AND s.status = 'active'
  AND other.id <> $identity_id
RETURN other.id AS identity_id,
       other.status AS identity_status,
       other.eligible_owner AS eligible_owner,
       type(rel) AS relationship,
       s.id AS service_id
ORDER BY s.id, other.id
LIMIT $result_limit
"""

PROTECTING_INVARIANTS = """
MATCH (i:Identity {id: $identity_id})-[:OWNS]->(s:Service)<-[:PROTECTS]-(rule:Invariant)
WHERE s.criticality = 'critical' AND s.status = 'active' AND rule.enabled = true
RETURN rule.id AS invariant_id,
       rule.rule_type AS rule_type,
       s.id AS service_id
ORDER BY s.id, rule.id
LIMIT $result_limit
"""

WORKSPACE_VERSION = """
MATCH (w:Workspace {id: 'ws-synthetic-1'})
RETURN w.graph_version AS graph_version
LIMIT 1
"""

ISSUED_CREDENTIAL_ACCESS = """
MATCH (i:Identity {id: $identity_id})-[:OWNS]->(s:Service)-[:DEPENDS_ON]->(r:Resource)
MATCH (c:Credential)-[grant:GRANTS_ACCESS_TO]->(r)
MATCH (c)-[issued:ISSUED_TO]->(i)
WHERE s.criticality = 'critical' AND s.status = 'active'
  AND r.criticality = 'critical' AND r.status = 'active'
RETURN i.id AS identity_id,
       s.id AS service_id,
       r.id AS resource_id,
       c.id AS credential_id,
       c.status AS credential_status,
       c.scope AS credential_scope,
       type(grant) AS grant_relationship,
       type(issued) AS issued_relationship
ORDER BY c.id, r.id
LIMIT $result_limit
"""


# Replacement owners for a service a plan would orphan: active, eligible owners
# of another live service that depends on the same critical resource.
REPLACEMENT_CANDIDATES = """
MATCH (orphan:Service {id: $service_id})-[:DEPENDS_ON]->(shared:Resource)
      <-[:DEPENDS_ON]-(peer:Service)<-[:OWNS]-(candidate:Identity)
WHERE shared.criticality = 'critical' AND shared.status = 'active'
  AND peer.id <> orphan.id AND peer.status = 'active'
  AND candidate.status = 'active' AND candidate.eligible_owner = true
  AND NOT candidate.id IN $excluded_ids
  AND NOT (candidate)-[:OWNS]->(orphan)
RETURN candidate.id AS identity_id,
       collect(DISTINCT peer.id) AS via_services,
       collect(DISTINCT shared.id) AS shared_resources,
       count(DISTINCT shared) AS shared_count
ORDER BY shared_count DESC, identity_id
LIMIT $result_limit
"""


def validate_identity_id(identity_id):
    if not isinstance(identity_id, str) or not _IDENTITY_ID.match(identity_id):
        raise ValueError("identity_id must be 1-64 characters of letters, numbers, '.', '_' or '-'")
    return identity_id


def _params(identity_id):
    return {
        "identity_id": validate_identity_id(identity_id),
        "result_limit": settings()["result_limit"],
    }


def _query(g, cypher, params):
    return g.ro_query(cypher, params=params, timeout=settings()["query_timeout_ms"])


def _step(source_id, relationship, target_id):
    return "%s -[%s]-> %s" % (source_id, relationship, target_id)


def ownership_impact(identity_id, db=None):
    """Return critical-service ownership and the dependency paths behind it.

    Connection and query failures propagate. Callers must not treat a failure
    as an empty impact result.
    """
    ping(db)
    g = graph(db)
    params = _params(identity_id)
    limit = params["result_limit"]

    owned = _query(g, OWNED_CRITICAL_SERVICES, params).result_set
    dependencies = _query(g, SERVICE_DEPENDENCIES, params).result_set
    ignored = _query(g, IGNORED_DEPENDENCIES, params).result_set
    co_owners = _query(g, CO_OWNERS, params).result_set
    invariants = _query(g, PROTECTING_INVARIANTS, params).result_set
    credentials = _query(g, ISSUED_CREDENTIAL_ACCESS, params).result_set
    version_rows = _query(g, WORKSPACE_VERSION, params).result_set

    services = []
    paths = []
    for row in owned:
        identity_status = row[1]
        eligible_owner = bool(row[2])
        relationship = row[3]
        service_id = row[4]
        services.append(
            {
                "service_id": service_id,
                "criticality": row[5],
                "status": row[6],
                "owner_status": identity_status,
                "owner_eligible": eligible_owner,
                "counts_as_eligible_owner": identity_status == "active" and eligible_owner,
            }
        )
        paths.append(
            {
                "reason": "identity_owns_critical_service",
                "hops": 1,
                "steps": [_step(row[0], relationship, service_id)],
            }
        )

    for row in dependencies:
        paths.append(
            {
                "reason": "owned_service_depends_on_resource",
                "hops": 2,
                "steps": [
                    _step(row[0], "OWNS", row[1]),
                    _step(row[1], row[2], row[3]),
                ],
                "resource_type": row[4],
                "resource_status": row[5],
                "resource_criticality": row[6],
            }
        )

    ignored_dependencies = []
    for row in ignored:
        ignored_dependencies.append(
            {
                "reason": "noncritical_dependency_ignored",
                "hops": 2,
                "counts_as_impact": False,
                "steps": [
                    _step(row[0], "OWNS", row[1]),
                    _step(row[1], row[2], row[3]),
                ],
                "resource_criticality": row[4],
            }
        )

    for row in co_owners:
        identity_status = row[1]
        eligible_owner = bool(row[2])
        counts = identity_status == "active" and eligible_owner
        paths.append(
            {
                "reason": "co_owner_counts" if counts else "co_owner_not_eligible",
                "hops": 1,
                "counts_as_eligible_owner": counts,
                "steps": [_step(row[0], row[3], row[4])],
            }
        )

    invariant_records = []
    for row in invariants:
        invariant_records.append(
            {
                "id": row[0],
                "rule_type": row[1],
                "service_id": row[2],
            }
        )

    for row in credentials:
        paths.append(
            {
                "reason": "credential_issued_to_owner_grants_resource_access",
                "hops": 3,
                "steps": [
                    _step(row[0], "OWNS", row[1]),
                    _step(row[1], "DEPENDS_ON", row[2]),
                    _step(row[3], row[6], row[2]),
                    _step(row[3], row[7], row[0]),
                ],
                "credential_status": row[4],
                "credential_scope": row[5],
            }
        )

    truncated = (
        len(owned) >= limit
        or len(dependencies) >= limit
        or len(ignored) >= limit
        or len(co_owners) >= limit
        or len(credentials) >= limit
    )
    return {
        "graph": settings()["graph"],
        "graph_version": version_rows[0][0] if version_rows else None,
        "identity_id": params["identity_id"],
        "owns_critical_service": len(services) > 0,
        "critical_services": services,
        "evidence_paths": paths,
        "ignored_dependencies": ignored_dependencies,
        "invariants": invariant_records,
        "traversal": {
            "pattern": "explicit OWNS, critical DEPENDS_ON, GRANTS_ACCESS_TO, ISSUED_TO, PROTECTS",
            "max_hops": 3,
            "result_limit": limit,
            "truncated": truncated,
        },
    }


def replacement_candidates(service_id, excluded_ids, db=None):
    """Return graph-backed replacement owners for a service, best first.

    Failures propagate. A failed read must not look like "no candidates".
    """
    ping(db)
    params = {
        "service_id": validate_identity_id(service_id),
        "excluded_ids": [validate_identity_id(value) for value in excluded_ids],
        "result_limit": settings()["result_limit"],
    }
    rows = _query(graph(db), REPLACEMENT_CANDIDATES, params).result_set
    candidates = []
    for row in rows:
        identity_id, via_services, shared_resources = row[0], sorted(row[1]), sorted(row[2])
        path = []
        for peer_id in via_services:
            path.append(_step(identity_id, "OWNS", peer_id))
            for resource_id in shared_resources:
                path.append(_step(peer_id, "DEPENDS_ON", resource_id))
        for resource_id in shared_resources:
            path.append(_step(params["service_id"], "DEPENDS_ON", resource_id))
        candidates.append(
            {
                "identity_id": identity_id,
                "via_services": via_services,
                "shared_resources": shared_resources,
                "shared_count": row[3],
                "evidence_path": path,
            }
        )
    return candidates


# Bounded impact for each action target in a plan. Same explicit patterns,
# one query per target type, every query capped by RESULT_LIMIT.
SERVICE_OWNERS = """
MATCH (owner:Identity)-[:OWNS]->(s:Service {id: $target_id})
RETURN owner.id, owner.status, owner.eligible_owner, s.id, s.criticality
ORDER BY owner.id
LIMIT $result_limit
"""

SERVICE_CRITICAL_DEPENDENCIES = """
MATCH (s:Service {id: $target_id})-[:DEPENDS_ON]->(r:Resource)
WHERE r.criticality = 'critical' AND r.status = 'active'
RETURN s.id, r.id, r.resource_type
ORDER BY r.id
LIMIT $result_limit
"""

RESOURCE_DEPENDENTS = """
MATCH (s:Service)-[:DEPENDS_ON]->(r:Resource {id: $target_id})
WHERE s.status = 'active'
RETURN s.id, s.criticality, r.id, r.criticality
ORDER BY s.id
LIMIT $result_limit
"""

RESOURCE_CREDENTIALS = """
MATCH (c:Credential)-[:GRANTS_ACCESS_TO]->(r:Resource {id: $target_id})
RETURN c.id, c.status, r.id
ORDER BY c.id
LIMIT $result_limit
"""

CREDENTIAL_REACH = """
MATCH (c:Credential {id: $target_id})-[:GRANTS_ACCESS_TO]->(r:Resource)<-[:DEPENDS_ON]-(s:Service)
WHERE s.status = 'active'
RETURN c.id, r.id, r.criticality, s.id, s.criticality
ORDER BY r.id, s.id
LIMIT $result_limit
"""

CREDENTIAL_HOLDER = """
MATCH (c:Credential {id: $target_id})-[:ISSUED_TO]->(i:Identity)
RETURN c.id, i.id, i.status
ORDER BY i.id
LIMIT $result_limit
"""


def _rows(g, cypher, target_id, limit):
    rows = _query(g, cypher, {"target_id": validate_identity_id(target_id), "result_limit": limit}).result_set
    return rows, len(rows) >= limit


def _service_paths(g, service_id, limit):
    owners, cut_owners = _rows(g, SERVICE_OWNERS, service_id, limit)
    deps, cut_deps = _rows(g, SERVICE_CRITICAL_DEPENDENCIES, service_id, limit)
    paths = [
        {
            "reason": "current_owner",
            "hops": 1,
            "steps": [_step(row[0], "OWNS", row[3])],
            "counts_as_eligible_owner": row[1] == "active" and bool(row[2]),
            "service_criticality": row[4],
        }
        for row in owners
    ]
    paths += [
        {"reason": "service_depends_on_critical_resource", "hops": 1, "steps": [_step(row[0], "DEPENDS_ON", row[1])]}
        for row in deps
    ]
    return paths, cut_owners or cut_deps


def _resource_paths(g, resource_id, limit):
    dependents, cut_dependents = _rows(g, RESOURCE_DEPENDENTS, resource_id, limit)
    grants, cut_grants = _rows(g, RESOURCE_CREDENTIALS, resource_id, limit)
    paths = [
        {
            "reason": "active_service_depends_on_resource",
            "hops": 1,
            "steps": [_step(row[0], "DEPENDS_ON", row[2])],
            "service_criticality": row[1],
            "counts_as_impact": row[1] == "critical",
        }
        for row in dependents
    ]
    paths += [
        {"reason": "credential_grants_access", "hops": 1, "steps": [_step(row[0], "GRANTS_ACCESS_TO", row[2])], "credential_status": row[1]}
        for row in grants
    ]
    return paths, cut_dependents or cut_grants


def _credential_paths(g, credential_id, limit):
    reach, cut_reach = _rows(g, CREDENTIAL_REACH, credential_id, limit)
    holders, cut_holders = _rows(g, CREDENTIAL_HOLDER, credential_id, limit)
    paths = [
        {
            "reason": "credential_reaches_service_dependency",
            "hops": 2,
            "steps": [_step(row[0], "GRANTS_ACCESS_TO", row[1]), _step(row[3], "DEPENDS_ON", row[1])],
            "resource_criticality": row[2],
            "service_criticality": row[4],
            "counts_as_impact": row[2] == "critical" and row[4] == "critical",
        }
        for row in reach
    ]
    paths += [
        {"reason": "credential_issued_to", "hops": 1, "steps": [_step(row[0], "ISSUED_TO", row[1])], "holder_status": row[2]}
        for row in holders
    ]
    return paths, cut_reach or cut_holders


def target_impact(action, db=None):
    """Bounded graph paths for one validated plan action. Failures propagate."""
    ping(db)
    g = graph(db)
    limit = settings()["result_limit"]
    action_type = action["type"]
    target_id = action["target_id"]
    if action_type == "disable_identity":
        impact = ownership_impact(target_id, db)
        paths = impact["evidence_paths"]
        truncated = impact["traversal"]["truncated"]
    elif action_type == "assign_service_owner":
        paths, truncated = _service_paths(g, target_id, limit)
        owner = _query(
            g,
            "MATCH (i:Identity {id: $owner_id}) RETURN i.status, i.eligible_owner LIMIT 1",
            {"owner_id": validate_identity_id(action["params"]["new_owner_id"])},
        ).result_set
        paths.append(
            {
                "reason": "proposed_owner",
                "hops": 1,
                "steps": [_step(action["params"]["new_owner_id"], "OWNS (proposed)", target_id)],
                "counts_as_eligible_owner": bool(owner) and owner[0][0] == "active" and bool(owner[0][1]),
                "is_projection": True,
            }
        )
    elif action_type == "delete_resource":
        paths, truncated = _resource_paths(g, target_id, limit)
    elif action_type == "revoke_credential":
        paths, truncated = _credential_paths(g, target_id, limit)
    else:
        raise ValueError("unsupported action: %s" % action_type)
    return {
        "action_id": action.get("action_id"),
        "action_type": action_type,
        "target_id": target_id,
        "paths": paths,
        "truncated": truncated,
    }
