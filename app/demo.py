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
