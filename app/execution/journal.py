"""Persist approvals, executions, events, and observations in FalkorDB."""

import json
from datetime import datetime, timezone

from app.config import settings
from app.graph.client import graph


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _write(db, cypher, params):
    return graph(db).query(cypher, params=params, timeout=settings()["query_timeout_ms"])


def _read(db, cypher, params):
    return graph(db).ro_query(cypher, params=params, timeout=settings()["query_timeout_ms"]).result_set


def save_approval(plan_id, approver_id, reason, db=None):
    approval_id = "%s:approval:%s" % (plan_id, _now())
    _write(
        db,
        """
        MATCH (plan:Plan {id: $plan_id})
        MATCH (approver:Identity {id: $approver_id})
        CREATE (approval:Approval {
            id: $approval_id,
            approver_id: $approver_id,
            reason: $reason,
            created_at: $created_at
        })
        MERGE (approval)-[:APPROVES]->(plan)
        MERGE (approval)-[:APPROVED_BY]->(approver)
        SET plan.status = 'APPROVED'
        """,
        {
            "plan_id": plan_id,
            "approver_id": approver_id,
            "approval_id": approval_id,
            "reason": reason,
            "created_at": _now(),
        },
    )
    return approval_id


def latest_approval(plan_id, db=None):
    rows = _read(
        db,
        """
        MATCH (approval:Approval)-[:APPROVES]->(:Plan {id: $plan_id})
        RETURN approval.id, approval.approver_id, approval.reason, approval.created_at
        ORDER BY approval.created_at DESC
        LIMIT 1
        """,
        {"plan_id": plan_id},
    )
    if not rows:
        return None
    return {
        "approval_id": rows[0][0],
        "approver_id": rows[0][1],
        "reason": rows[0][2],
        "created_at": rows[0][3],
    }


def find_execution(plan_id, idempotency_key, db=None):
    rows = _read(
        db,
        """
        MATCH (:Plan {id: $plan_id})-[:HAS_EXECUTION]->(execution:Execution {idempotency_key: $idempotency_key})
        RETURN execution.id, execution.status, execution.response_json
        ORDER BY execution.started_at DESC
        LIMIT 1
        """,
        {"plan_id": plan_id, "idempotency_key": idempotency_key},
    )
    if not rows:
        return None
    body = json.loads(rows[0][2]) if rows[0][2] else None
    return {"execution_id": rows[0][0], "status": rows[0][1], "body": body}


def latest_execution(plan_id, db=None):
    rows = _read(
        db,
        """
        MATCH (:Plan {id: $plan_id})-[:HAS_EXECUTION]->(execution:Execution)
        RETURN execution.id, execution.status, execution.response_json
        ORDER BY execution.started_at DESC
        LIMIT 1
        """,
        {"plan_id": plan_id},
    )
    if not rows:
        return None
    body = json.loads(rows[0][2]) if rows[0][2] else None
    return {"execution_id": rows[0][0], "status": rows[0][1], "body": body}


def create_execution(plan_id, idempotency_key, db=None):
    execution_id = "%s:execution:%s" % (plan_id, _now())
    _write(
        db,
        """
        MATCH (plan:Plan {id: $plan_id})
        CREATE (execution:Execution {
            id: $execution_id,
            idempotency_key: $idempotency_key,
            status: 'EXECUTING',
            started_at: $started_at,
            response_json: ''
        })
        MERGE (plan)-[:HAS_EXECUTION]->(execution)
        SET plan.status = 'EXECUTING', plan.execution_started = true
        """,
        {
            "plan_id": plan_id,
            "execution_id": execution_id,
            "idempotency_key": idempotency_key,
            "started_at": _now(),
        },
    )
    return execution_id


def record_event(execution_id, action, outcome, observation, db=None):
    event_id = "%s:event:%s" % (action["action_id"], _now())
    observation_id = "%s:observation" % event_id
    _write(
        db,
        """
        MATCH (execution:Execution {id: $execution_id})
        MATCH (action:Action {id: $action_id})
        CREATE (event:ExecutionEvent {
            id: $event_id,
            idempotency_key: $idempotency_key,
            outcome: $outcome,
            observed_at: $observed_at
        })
        MERGE (event)-[:RESULT_OF]->(action)
        MERGE (execution)-[:RECORDED]->(event)
        SET action.status = $outcome
        """,
        {
            "execution_id": execution_id,
            "action_id": action["action_id"],
            "event_id": event_id,
            "idempotency_key": action["action_id"],
            "outcome": outcome,
            "observed_at": _now(),
        },
    )
    if observation is not None:
        _write(
            db,
            """
            MATCH (event:ExecutionEvent {id: $event_id})
            CREATE (observation:Observation {
                id: $observation_id,
                state_value: $state_value,
                observed_at: $observed_at,
                source: 'falkordb-readback'
            })
            MERGE (observation)-[:CONFIRMS]->(event)
            """,
            {
                "event_id": event_id,
                "observation_id": observation_id,
                "state_value": json.dumps(observation),
                "observed_at": _now(),
            },
        )
    return event_id


def verified_event_exists(action_id, db=None):
    rows = _read(
        db,
        """
        MATCH (event:ExecutionEvent {idempotency_key: $idempotency_key, outcome: 'verified'})
        RETURN count(event) AS hits
        """,
        {"idempotency_key": action_id},
    )
    return bool(rows and rows[0][0] > 0)


def finish_execution(execution_id, plan_id, status, body, db=None):
    _write(
        db,
        """
        MATCH (plan:Plan {id: $plan_id})
        MATCH (execution:Execution {id: $execution_id})
        SET plan.status = $status,
            execution.status = $status,
            execution.response_json = $response_json,
            execution.finished_at = $finished_at
        """,
        {
            "plan_id": plan_id,
            "execution_id": execution_id,
            "status": status,
            "response_json": json.dumps(body),
            "finished_at": _now(),
        },
    )
