"""Store plans and decisions in FalkorDB. Observed ownership edges are not modified."""

import json
from datetime import datetime, timezone

from app.config import settings
from app.graph.client import graph

_LABELS = ("Identity", "Service", "Credential", "Resource")


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _selected(db):
    return graph(db)


def _write(selected, cypher, params):
    return selected.query(cypher, params=params, timeout=settings()["query_timeout_ms"])


def plan_exists(plan_id, db=None):
    result = _selected(db).ro_query(
        "MATCH (p:Plan {id: $plan_id}) RETURN p.id LIMIT 1",
        params={"plan_id": plan_id},
        timeout=settings()["query_timeout_ms"],
    )
    return bool(result.result_set)


def save_plan(plan, graph_version, db=None):
    selected = _selected(db)
    _write(
        selected,
        """
        MERGE (p:Plan {id: $plan_id})
        SET p.status = 'PROPOSED',
            p.scenario = $scenario,
            p.requires_approval = $requires_approval,
            p.graph_version = $graph_version,
            p.created_at = $created_at,
            p.execution_started = false
        """,
        {
            "plan_id": plan["plan_id"],
            "scenario": plan["scenario"],
            "requires_approval": plan["requires_approval"],
            "graph_version": graph_version,
            "created_at": _now(),
        },
    )
    for action in plan["actions"]:
        if action["target_label"] not in _LABELS:
            raise ValueError("refusing to write an action for an unknown label")
        _write(
            selected,
            """
            MATCH (p:Plan {id: $plan_id})
            MERGE (a:Action {id: $action_id})
            SET a.action_type = $action_type,
                a.target_id = $target_id,
                a.target_label = $target_label,
                a.sequence = $sequence,
                a.status = 'validated',
                a.params_json = $params_json
            MERGE (p)-[:CONTAINS_ACTION]->(a)
            """,
            {
                "plan_id": plan["plan_id"],
                "action_id": action["action_id"],
                "action_type": action["type"],
                "target_id": action["target_id"],
                "target_label": action["target_label"],
                "sequence": action["sequence"],
                "params_json": json.dumps(action["params"], sort_keys=True),
            },
        )
        _write(
            selected,
            """
            MATCH (a:Action {id: $action_id})
            MATCH (n:%s {id: $target_id})
            MERGE (a)-[:AFFECTS]->(n)
            """
            % action["target_label"],
            {"action_id": action["action_id"], "target_id": action["target_id"]},
        )


def set_status(plan_id, status, db=None):
    _write(
        _selected(db),
        "MATCH (p:Plan {id: $plan_id}) SET p.status = $status",
        {"plan_id": plan_id, "status": status},
    )


def save_decision(result, db=None):
    selected = _selected(db)
    decision_id = "%s:decision:%s" % (result["plan_id"], _now())
    _write(
        selected,
        """
        MATCH (p:Plan {id: $plan_id})
        CREATE (d:Decision {
            id: $decision_id,
            verdict: $verdict,
            rule_id: $rule_id,
            reason_code: $reason_code,
            response_json: $response_json,
            created_at: $created_at,
            execution_started: false
        })
        MERGE (d)-[:EVALUATES]->(p)
        SET p.status = $status,
            p.graph_version = $graph_version,
            p.execution_started = false
        """,
        {
            "plan_id": result["plan_id"],
            "decision_id": decision_id,
            "verdict": result["verdict"],
            "rule_id": result["invariant"]["id"],
            "reason_code": result["reason_code"],
            "response_json": json.dumps(result),
            "created_at": _now(),
            "status": result["status"],
            "graph_version": result["graph_version"],
        },
    )
    _write(
        selected,
        """
        MATCH (d:Decision {id: $decision_id})
        MATCH (rule:Invariant {id: $rule_id})
        MERGE (d)-[:SUPPORTED_BY]->(rule)
        """,
        {"decision_id": decision_id, "rule_id": result["invariant"]["id"]},
    )
    return decision_id


def get_plan(plan_id, db=None):
    selected = _selected(db)
    rows = selected.ro_query(
        """
        MATCH (p:Plan {id: $plan_id})
        MATCH (p)-[:CONTAINS_ACTION]->(a:Action)
        RETURN p.id, p.status, p.scenario, p.requires_approval, p.graph_version,
               p.created_at, p.execution_started,
               a.id, a.action_type, a.target_id, a.target_label, a.sequence, a.params_json
        ORDER BY a.sequence
        """,
        params={"plan_id": plan_id},
        timeout=settings()["query_timeout_ms"],
    ).result_set
    if not rows:
        return None
    first = rows[0]
    actions = []
    for row in rows:
        actions.append(
            {
                "action_id": row[7],
                "type": row[8],
                "target_id": row[9],
                "target_label": row[10],
                "sequence": row[11],
                "params": json.loads(row[12] or "{}"),
            }
        )
    decisions = selected.ro_query(
        """
        MATCH (d:Decision)-[:EVALUATES]->(:Plan {id: $plan_id})
        RETURN d.response_json, d.created_at
        ORDER BY d.created_at DESC
        LIMIT 1
        """,
        params={"plan_id": plan_id},
        timeout=settings()["query_timeout_ms"],
    ).result_set
    latest = json.loads(decisions[0][0]) if decisions else None
    return {
        "plan_id": first[0],
        "status": first[1],
        "scenario": first[2],
        "requires_approval": bool(first[3]),
        "graph_version": first[4],
        "created_at": first[5],
        "execution_started": bool(first[6]),
        "actions": actions,
        "latest_verdict": latest,
    }
