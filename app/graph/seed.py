"""Synthetic offboarding workspace. No real identities, secrets, or credentials."""

from app.config import settings
from app.graph.client import connect, graph

# One statement. MERGE plus SET makes the seed overwrite known properties.
# Reset still deletes the graph first so leftover demo edges cannot survive.
SEED_QUERY = """
MERGE (w:Workspace {id: 'ws-synthetic-1'})
SET w.name = 'Synthetic Workspace',
    w.schema_version = '1',
    w.graph_version = 'seed-v1'

MERGE (contractor:Identity {id: 'id-contractor-17'})
SET contractor.status = 'active',
    contractor.identity_type = 'contractor',
    contractor.eligible_owner = true,
    contractor.display_name = 'Contractor 17'

MERGE (engineer:Identity {id: 'id-engineer-9'})
SET engineer.status = 'active',
    engineer.identity_type = 'employee',
    engineer.eligible_owner = true,
    engineer.display_name = 'Engineer 9'

MERGE (former:Identity {id: 'id-former-4'})
SET former.status = 'disabled',
    former.identity_type = 'employee',
    former.eligible_owner = false,
    former.display_name = 'Former 4'

MERGE (billing:Service {id: 'svc-billing'})
SET billing.name = 'Billing API',
    billing.criticality = 'critical',
    billing.status = 'active'

MERGE (payments:Service {id: 'svc-payments'})
SET payments.name = 'Payments API',
    payments.criticality = 'critical',
    payments.status = 'active'

MERGE (handbook:Service {id: 'svc-handbook'})
SET handbook.name = 'Team Handbook',
    handbook.criticality = 'low',
    handbook.status = 'active'

MERGE (database:Resource {id: 'db-billing-primary'})
SET database.resource_type = 'database',
    database.status = 'active',
    database.criticality = 'critical',
    database.name = 'Billing primary database'

MERGE (cache:Resource {id: 'cache-billing'})
SET cache.resource_type = 'cache',
    cache.status = 'active',
    cache.criticality = 'low',
    cache.name = 'Billing cache'

MERGE (credential:Credential {id: 'credential-deploy-4'})
SET credential.status = 'active',
    credential.scope = 'deploy',
    credential.expires_at = '2026-12-31T00:00:00Z'

MERGE (owner_rule:Invariant {id: 'INV-CRITICAL-SERVICE-OWNER'})
SET owner_rule.rule_type = 'critical_service_owner',
    owner_rule.severity = 'block',
    owner_rule.enabled = true,
    owner_rule.description = 'Every active critical service has at least one eligible active owner'

MERGE (w)-[:HAS_IDENTITY]->(contractor)
MERGE (w)-[:HAS_IDENTITY]->(engineer)
MERGE (w)-[:HAS_IDENTITY]->(former)
MERGE (w)-[:HAS_SERVICE]->(billing)
MERGE (w)-[:HAS_SERVICE]->(payments)
MERGE (w)-[:HAS_SERVICE]->(handbook)
MERGE (w)-[:HAS_RESOURCE]->(database)
MERGE (w)-[:HAS_RESOURCE]->(cache)

MERGE (contractor)-[:OWNS]->(billing)
MERGE (former)-[:OWNS]->(billing)
MERGE (engineer)-[:OWNS]->(payments)
MERGE (engineer)-[:OWNS]->(handbook)
MERGE (billing)-[:DEPENDS_ON]->(database)
MERGE (billing)-[:DEPENDS_ON]->(cache)
MERGE (payments)-[:DEPENDS_ON]->(database)
MERGE (credential)-[:GRANTS_ACCESS_TO]->(database)
MERGE (credential)-[:ISSUED_TO]->(contractor)
MERGE (owner_rule)-[:PROTECTS]->(billing)
MERGE (owner_rule)-[:PROTECTS]->(payments)
"""

COUNT_QUERY = """
MATCH (n)
RETURN labels(n)[0] AS label, count(n) AS count
ORDER BY label
"""

VERSION_QUERY = """
MATCH (w:Workspace {id: 'ws-synthetic-1'})
RETURN w.graph_version AS graph_version
LIMIT 1
"""


def delete_graph(db=None):
    client = db if db is not None else connect()
    try:
        graph(client).delete()
    except Exception as exc:
        message = str(exc).lower()
        if "invalid graph" in message or "does not exist" in message or "empty key" in message:
            return
        raise


def seed(db=None):
    client = db if db is not None else connect()
    graph(client).query(SEED_QUERY)
    return summary(client)


def reset_scenario(db=None):
    """Delete the selected graph and load the synthetic scenario again."""
    client = db if db is not None else connect()
    delete_graph(client)
    return seed(client)


def summary(db=None):
    client = db if db is not None else connect()
    selected = graph(client)
    result = selected.query(COUNT_QUERY)
    counts = {}
    for row in result.result_set:
        counts[row[0]] = row[1]
    version_rows = selected.query(VERSION_QUERY).result_set
    graph_version = version_rows[0][0] if version_rows else None
    return {
        "graph": settings()["graph"],
        "graph_version": graph_version,
        "counts": counts,
    }
