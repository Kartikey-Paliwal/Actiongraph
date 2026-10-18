"""Demo-only helpers for the local demo page. They act on the synthetic graph only.

The page never decides a verdict. It calls the same API as any client; these
helpers only reset the scenario, hand out the fixed fixture plans, and run the
edge ablation in one call so it can be shown live.
"""

from app.errors import GraphUnavailable
from app.graph.client import connect, graph, ping
from app.graph.seed import reset_scenario
from app.planning.fixtures import DELETE_BILLING_DATABASE, DISABLE_CONTRACTOR, SAFE_OFFBOARD, UNSAFE_OFFBOARD
from app.plans.service import create_and_preflight

SECOND_OWNER_EDGE = "id-engineer-9 -[OWNS]-> svc-billing"

_ADD_SECOND_OWNER = """
MATCH (engineer:Identity {id: 'id-engineer-9'})
MATCH (billing:Service {id: 'svc-billing'})
MERGE (engineer)-[:OWNS]->(billing)
"""

_REMOVE_SECOND_OWNER = """
MATCH (:Identity {id: 'id-engineer-9'})-[rel:OWNS]->(:Service {id: 'svc-billing'})
DELETE rel
"""


def fixtures():
    return {
        "unsafe": UNSAFE_OFFBOARD,
        "safe": SAFE_OFFBOARD,
        "disable": DISABLE_CONTRACTOR,
        "delete_resource": DELETE_BILLING_DATABASE,
        "approver_id": "id-engineer-9",
    }


def _reachable(db):
    try:
        return ping(db if db is not None else connect())
    except Exception as exc:
        raise GraphUnavailable("FalkorDB is unavailable: %s" % exc) from exc


def reset(db=None):
    return reset_scenario(_reachable(db))


def ablation(db=None):
    """Reset, add a second owner edge, preflight a disable, delete only that edge, preflight again."""
    db = _reachable(db)
    reset_scenario(db)
    graph(db).query(_ADD_SECOND_OWNER)
    with_edge = create_and_preflight(DISABLE_CONTRACTOR, db)
    graph(db).query(_REMOVE_SECOND_OWNER)
    without_edge = create_and_preflight(DISABLE_CONTRACTOR, db)
    return {
        "edge": SECOND_OWNER_EDGE,
        "with_edge": with_edge,
        "without_edge": without_edge,
        "same_plan_actions": DISABLE_CONTRACTOR["actions"],
    }


_ISSUED = """
MATCH (c:Credential)-[:ISSUED_TO]->(i:Identity)
RETURN c.id, i.id
ORDER BY c.id, i.id
LIMIT 100
"""


def live_graph(db=None):
    """Nodes and edges of the workspace as FalkorDB holds them right now."""
    from app.config import settings
    from app.graph.state import load_observed_state

    db = _reachable(db)
    state = load_observed_state(db)
    issued = graph(db).ro_query(_ISSUED, timeout=settings()["query_timeout_ms"]).result_set
    nodes = []
    for node_id, record in state["identities"].items():
        nodes.append({"id": node_id, "label": "Identity", "status": record["status"], "eligible_owner": record["eligible_owner"]})
    for node_id, record in state["services"].items():
        nodes.append({"id": node_id, "label": "Service", "status": record["status"], "criticality": record["criticality"]})
    for node_id, record in state["resources"].items():
        nodes.append({"id": node_id, "label": "Resource", "status": record["status"], "criticality": record["criticality"]})
    for node_id, record in state["credentials"].items():
        nodes.append({"id": node_id, "label": "Credential", "status": record["status"]})
    edges = [{"from": a, "to": b, "type": "OWNS"} for a, b in state["owns"]]
    edges += [{"from": a, "to": b, "type": "DEPENDS_ON"} for a, b in state["depends"]]
    edges += [{"from": a, "to": b, "type": "GRANTS_ACCESS_TO"} for a, b in state["grants"]]
    edges += [{"from": row[0], "to": row[1], "type": "ISSUED_TO"} for row in issued]
    return {"graph_version": state["graph_version"], "nodes": nodes, "edges": edges}
