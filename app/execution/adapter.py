"""Mock side effects against the FalkorDB graph, which is the simulated system of record.

A timeout returns before any write. A lying success returns without writing.
The caller must not treat either result as verified until read-back.
"""

from app.config import settings
from app.graph.client import graph

_MODES = ("timeout_unchanged", "timeout_unknown", "success_without_write")


def apply_action(action, db=None, mode=None):
    if mode is not None and mode not in _MODES:
        raise ValueError("unsupported failure injection: %s" % mode)
    if mode in ("timeout_unchanged", "timeout_unknown", "success_without_write"):
        outcome = "timeout" if mode.startswith("timeout") else "accepted"
        return {"outcome": outcome, "wrote": False, "mode": mode}
    selected = graph(db)
    action_type = action["type"]
    if action_type == "assign_service_owner":
        selected.query(
            """
            MATCH (owner:Identity {id: $owner_id})
            MATCH (service:Service {id: $service_id})
            MERGE (owner)-[:OWNS]->(service)
            """,
            params={"owner_id": action["params"]["new_owner_id"], "service_id": action["target_id"]},
            timeout=settings()["query_timeout_ms"],
        )
    elif action_type == "disable_identity":
        selected.query(
            """
            MATCH (identity:Identity {id: $identity_id})
            SET identity.status = 'disabled', identity.eligible_owner = false
            """,
            params={"identity_id": action["target_id"]},
            timeout=settings()["query_timeout_ms"],
        )
    elif action_type == "revoke_credential":
        selected.query(
            """
            MATCH (credential:Credential {id: $credential_id})
            SET credential.status = 'revoked'
            """,
            params={"credential_id": action["target_id"]},
            timeout=settings()["query_timeout_ms"],
        )
    elif action_type == "delete_resource":
        selected.query(
            """
            MATCH (resource:Resource {id: $resource_id})
            SET resource.status = 'deleted'
            """,
            params={"resource_id": action["target_id"]},
            timeout=settings()["query_timeout_ms"],
        )
    else:
        raise ValueError("unsupported action: %s" % action_type)
    return {"outcome": "accepted", "wrote": True, "mode": None}
