"""Read simulated state after an action. Adapter results are not evidence."""

from app.config import settings
from app.errors import ReadbackError
from app.graph.client import graph


def _read(db, cypher, params):
    try:
        return graph(db).ro_query(cypher, params=params, timeout=settings()["query_timeout_ms"]).result_set
    except Exception as exc:
        raise ReadbackError("read-back failed: %s" % exc) from exc


def observe_action(action, db=None, force_failure=False):
    if force_failure:
        raise ReadbackError("injected read-back failure")
    action_type = action["type"]
    if action_type == "assign_service_owner":
        rows = _read(
            db,
            """
            MATCH (owner:Identity {id: $owner_id})
            OPTIONAL MATCH (owner)-[rel:OWNS]->(service:Service {id: $service_id})
            RETURN owner.status, owner.eligible_owner, count(rel) AS owns
            """,
            {"owner_id": action["params"]["new_owner_id"], "service_id": action["target_id"]},
        )
        if not rows:
            raise ReadbackError("owner was not found during read-back")
        return {
            "owner_status": rows[0][0],
            "owner_eligible": bool(rows[0][1]),
            "owns": rows[0][2] > 0,
        }
    if action_type == "disable_identity":
        rows = _read(
            db,
            "MATCH (identity:Identity {id: $identity_id}) RETURN identity.status, identity.eligible_owner",
            {"identity_id": action["target_id"]},
        )
        if not rows:
            raise ReadbackError("identity was not found during read-back")
        return {"status": rows[0][0], "eligible_owner": bool(rows[0][1])}
    if action_type == "revoke_credential":
        rows = _read(
            db,
            "MATCH (credential:Credential {id: $credential_id}) RETURN credential.status",
            {"credential_id": action["target_id"]},
        )
        if not rows:
            raise ReadbackError("credential was not found during read-back")
        return {"status": rows[0][0]}
    if action_type == "delete_resource":
        rows = _read(
            db,
            "MATCH (resource:Resource {id: $resource_id}) RETURN resource.status",
            {"resource_id": action["target_id"]},
        )
        if not rows:
            raise ReadbackError("resource was not found during read-back")
        return {"status": rows[0][0]}
    raise ReadbackError("unsupported action for read-back")


def postcondition_holds(action, observation):
    action_type = action["type"]
    if action_type == "assign_service_owner":
        return (
            observation.get("owns") is True
            and observation.get("owner_status") == "active"
            and observation.get("owner_eligible") is True
        )
    if action_type == "disable_identity":
        return observation.get("status") == "disabled"
    if action_type == "revoke_credential":
        return observation.get("status") == "revoked"
    if action_type == "delete_resource":
        return observation.get("status") == "deleted"
    return False


def billing_verification(db=None):
    rows = _read(
        db,
        """
        MATCH (identity:Identity)-[:OWNS]->(:Service {id: 'svc-billing'})
        WHERE identity.status = 'active' AND identity.eligible_owner = true
        RETURN identity.id
        ORDER BY identity.id
        """,
        {},
    )
    contractor = _read(
        db,
        "MATCH (identity:Identity {id: 'id-contractor-17'}) RETURN identity.status",
        {},
    )
    owners = [row[0] for row in rows]
    contractor_status = contractor[0][0] if contractor else None
    return {
        "eligible_owners": owners,
        "contractor_status": contractor_status,
        "invariant_holds": owners == ["id-engineer-9"] and contractor_status == "disabled",
    }
