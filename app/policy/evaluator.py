"""Project a validated plan onto observed owners and return a deterministic verdict.

The graph is never mutated here. Proposed ownership stays in memory and is labeled
as a projection. Actions are applied in order, so a disable cannot pass on a
replacement that appears only in a later step.
"""

OWNER_RULE_ID = "INV-CRITICAL-SERVICE-OWNER"

_VERDICT_RANK = {"BLOCK": 0, "NEEDS_REVIEW": 1, "PASS": 2}


def _counts(identity):
    return identity["status"] == "active" and bool(identity["eligible_owner"])


def _is_critical_active(services, service_id):
    service = services.get(service_id)
    return bool(service) and service["criticality"] == "critical" and service["status"] == "active"


def _eligible(identities, owns, service_id):
    return {
        identity_id
        for identity_id, owned_service in owns
        if owned_service == service_id and _counts(identities[identity_id])
    }


def _ineligible_owners(identities, owns, service_id):
    return sorted(
        identity_id
        for identity_id, owned_service in owns
        if owned_service == service_id and not _counts(identities[identity_id])
    )


def _graph_path(state, service_id):
    steps = []
    for identity_id, owned_service in state["owns"]:
        if owned_service != service_id:
            continue
        steps.append("%s -[OWNS]-> %s" % (identity_id, service_id))
    for owned_service, resource_id in state["depends"]:
        if owned_service != service_id:
            continue
        resource = state["resources"].get(resource_id, {})
        if resource.get("criticality") == "critical" and resource.get("status") == "active":
            steps.append("%s -[DEPENDS_ON]-> %s" % (service_id, resource_id))
    return steps


def _owner_evidence(state, identities, owns, service_id, added, removed):
    current = _eligible(state["identities"], state["owns"], service_id)
    after = _eligible(identities, owns, service_id)
    return {
        "affected_service_ids": [service_id],
        "current_eligible_owners": sorted(current),
        "owners_removed_by_plan": sorted(removed.get(service_id, ())),
        "owners_added_by_plan": sorted(added.get(service_id, ())),
        "eligible_after": sorted(after),
        "ineligible_owners": _ineligible_owners(state["identities"], state["owns"], service_id),
        "graph_path": _graph_path(state, service_id),
    }


def _finding(verdict, reason_code, rule_id, evidence, action):
    return {
        "verdict": verdict,
        "reason_code": reason_code,
        "rule_id": rule_id,
        "evidence": evidence,
        "action_id": action["action_id"],
        "action_type": action["type"],
    }


def _apply_assign(action, state, identities, owns, added):
    service_id = action["target_id"]
    new_owner_id = action["params"]["new_owner_id"]
    service = state["services"].get(service_id)
    owner = identities.get(new_owner_id)
    if service is None or owner is None:
        return _finding(
            "NEEDS_REVIEW",
            "TARGET_NOT_FOUND",
            OWNER_RULE_ID,
            {"target_id": service_id, "new_owner_id": new_owner_id},
            action,
        )
    if not _counts(owner):
        return _finding(
            "BLOCK",
            "REPLACEMENT_NOT_ELIGIBLE",
            OWNER_RULE_ID,
            {
                "target_id": service_id,
                "new_owner_id": new_owner_id,
                "owner_status": owner["status"],
                "owner_eligible": owner["eligible_owner"],
                "graph_path": _graph_path(state, service_id),
            },
            action,
        )
    if (new_owner_id, service_id) not in owns:
        owns.add((new_owner_id, service_id))
        added.setdefault(service_id, set()).add(new_owner_id)
    return None


def _apply_disable(action, state, identities, owns, added, removed):
    identity_id = action["target_id"]
    identity = identities.get(identity_id)
    if identity is None:
        return _finding(
            "NEEDS_REVIEW",
            "TARGET_NOT_FOUND",
            OWNER_RULE_ID,
            {"target_id": identity_id},
            action,
        )
    affected = [
        service_id
        for owner_id, service_id in owns
        if owner_id == identity_id and _is_critical_active(state["services"], service_id)
    ]
    identity["status"] = "disabled"
    identity["eligible_owner"] = False
    for service_id in affected:
        if identity_id in _eligible(state["identities"], state["owns"], service_id):
            removed.setdefault(service_id, set()).add(identity_id)
        evidence = _owner_evidence(state, identities, owns, service_id, added, removed)
        if evidence["current_eligible_owners"] and not evidence["eligible_after"]:
            return _finding(
                "BLOCK",
                "CRITICAL_SERVICE_WOULD_BE_ORPHANED",
                OWNER_RULE_ID,
                evidence,
                action,
            )
    return None


def _apply_delete(action, state):
    resource_id = action["target_id"]
    if resource_id not in state["resources"]:
        return _finding(
            "NEEDS_REVIEW",
            "TARGET_NOT_FOUND",
            "INV-CRITICAL-DEPENDENCY",
            {"target_id": resource_id},
            action,
        )
    dependents = [
        service_id
        for service_id, depended_resource in state["depends"]
        if depended_resource == resource_id and _is_critical_active(state["services"], service_id)
    ]
    if not dependents:
        return None
    return _finding(
        "BLOCK",
        "CRITICAL_RESOURCE_HAS_ACTIVE_DEPENDENTS",
        "INV-CRITICAL-DEPENDENCY",
        {
            "affected_service_ids": sorted(dependents),
            "resource_id": resource_id,
            "graph_path": [
                "%s -[DEPENDS_ON]-> %s" % (service_id, resource_id) for service_id in sorted(dependents)
            ],
        },
        action,
    )


def _apply_revoke(action, state):
    credential_id = action["target_id"]
    if credential_id not in state["credentials"]:
        return _finding(
            "NEEDS_REVIEW",
            "TARGET_NOT_FOUND",
            "INV-CRITICAL-DEPENDENCY",
            {"target_id": credential_id},
            action,
        )
    resources = [resource_id for granted_credential, resource_id in state["grants"] if granted_credential == credential_id]
    dependents = []
    paths = []
    for resource_id in resources:
        resource = state["resources"].get(resource_id, {})
        if resource.get("criticality") != "critical":
            continue
        for service_id, depended_resource in state["depends"]:
            if depended_resource != resource_id or not _is_critical_active(state["services"], service_id):
                continue
            dependents.append(service_id)
            paths.append("%s -[DEPENDS_ON]-> %s" % (service_id, resource_id))
            paths.append("%s -[GRANTS_ACCESS_TO]-> %s" % (credential_id, resource_id))
    if not dependents:
        return None
    return _finding(
        "NEEDS_REVIEW",
        "CREDENTIAL_SERVES_CRITICAL_RESOURCE",
        "INV-CRITICAL-DEPENDENCY",
        {
            "affected_service_ids": sorted(set(dependents)),
            "credential_id": credential_id,
            "graph_path": paths,
        },
        action,
    )


def _rule_blocks_pass(state, findings):
    rule = state["invariants"].get(OWNER_RULE_ID)
    if rule and rule["enabled"]:
        return None
    if any(finding["verdict"] == "BLOCK" for finding in findings):
        return None
    return {
        "verdict": "NEEDS_REVIEW",
        "reason_code": "INVARIANT_NOT_IN_GRAPH",
        "rule_id": OWNER_RULE_ID,
        "evidence": {"graph_path": []},
        "action_id": None,
        "action_type": None,
    }


def evaluate_plan(plan, state):
    identities = {
        identity_id: dict(record) for identity_id, record in state["identities"].items()
    }
    owns = set(state["owns"])
    added = {}
    removed = {}
    findings = []

    for action in plan["actions"]:
        action_type = action["type"]
        if action_type == "assign_service_owner":
            finding = _apply_assign(action, state, identities, owns, added)
        elif action_type == "disable_identity":
            finding = _apply_disable(action, state, identities, owns, added, removed)
        elif action_type == "delete_resource":
            finding = _apply_delete(action, state)
        elif action_type == "revoke_credential":
            finding = _apply_revoke(action, state)
        else:
            finding = _finding(
                "BLOCK",
                "UNSUPPORTED_ACTION",
                OWNER_RULE_ID,
                {"action_type": action_type},
                action,
            )
        if finding is None:
            continue
        findings.append(finding)
        if finding["verdict"] == "BLOCK":
            break

    missing_rule = _rule_blocks_pass(state, findings)
    if missing_rule is not None:
        findings.append(missing_rule)

    if not findings:
        findings.append(
            {
                "verdict": "PASS",
                "reason_code": "OWNER_INVARIANT_HOLDS",
                "rule_id": OWNER_RULE_ID,
                "evidence": _pass_evidence(state, identities, owns, added, removed, plan),
                "action_id": None,
                "action_type": None,
            }
        )

    primary = sorted(findings, key=lambda finding: _VERDICT_RANK[finding["verdict"]])[0]
    rule = state["invariants"].get(primary["rule_id"], {})
    verdict = primary["verdict"]
    if verdict == "PASS" and plan["requires_approval"]:
        status = "AWAITING_APPROVAL"
    elif verdict == "PASS":
        status = "PREFLIGHTED"
    elif verdict == "BLOCK":
        status = "BLOCKED"
    else:
        status = "NEEDS_REVIEW"
    return {
        "plan_id": plan["plan_id"],
        "status": status,
        "verdict": verdict,
        "reason_code": primary["reason_code"],
        "invariant": {
            "id": primary["rule_id"],
            "description": rule.get("description"),
        },
        "evidence": primary["evidence"],
        "findings": findings,
        "graph_version": state["graph_version"],
        "execution_started": False,
    }


def _pass_evidence(state, identities, owns, added, removed, plan):
    service_ids = []
    for action in plan["actions"]:
        if action["type"] == "assign_service_owner":
            service_ids.append(action["target_id"])
        if action["type"] == "disable_identity":
            for owner_id, service_id in state["owns"]:
                if owner_id == action["target_id"] and _is_critical_active(state["services"], service_id):
                    service_ids.append(service_id)
    ordered = []
    for service_id in service_ids:
        if service_id not in ordered:
            ordered.append(service_id)
    if not ordered:
        return {"affected_service_ids": [], "graph_path": []}
    evidence = _owner_evidence(state, identities, owns, ordered[0], added, removed)
    evidence["affected_service_ids"] = ordered
    return evidence
