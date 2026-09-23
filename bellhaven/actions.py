from __future__ import annotations

from .crm import CRMClient


def execute(proposal: dict, crm: CRMClient) -> list[dict]:
    results = []
    prior = proposal.get("result") or {}
    prior_outcomes = prior.get("approval_result", []) if isinstance(prior, dict) else []
    for index, op in enumerate(proposal["operations"]):
        action = op["action"]
        if action == "update_account":
            current = crm.get_account(op["account_id"])
            if all(current.get(k) == v for k, v in op["fields"].items()):
                results.append({"action": action, "status": "already_applied", "account_id": op["account_id"]})
            else:
                results.append({"action": action, "status": "applied", "response": crm.update_account(op["account_id"], op["fields"])})
        elif action == "create_account":
            created = crm.create_account(op["fields"])
            account = created.get("data", created)
            contact_response = None
            if op.get("administrator"):
                contact_response = crm.create_contact({"account_id": account["account_id"], "name": op["administrator"], "title": "Administrator",
                                                       "phone": "", "is_active": True})
            results.append({"action": action, "status": "applied", "response": created, "contact_response": contact_response})
        elif action == "create_contact":
            # A withdrawn approval leaves the created contact in the CRM as
            # inactive because the API has no delete endpoint. On a retry,
            # reactivate that same contact instead of creating a duplicate.
            previous = prior_outcomes[index] if index < len(prior_outcomes) else {}
            contact_id = _created_id(previous, "contact_id") if isinstance(previous, dict) else ""
            if contact_id:
                fields = {**op["fields"], "is_active": True}
                crm.update_contact(contact_id, fields)
                results.append({"action": action, "status": "reactivated", "response": {"data": {"contact_id": contact_id}}})
            else:
                results.append({"action": action, "status": "applied", "response": crm.create_contact(op["fields"])})
        elif action == "update_contact":
            results.append({"action": action, "status": "applied", "response": crm.update_contact(op["contact_id"], op["fields"])})
        elif action == "chow_create":
            created = crm.create_account(op["fields"])
            account = created.get("data", created)
            crm.update_account(op["old_account_id"], {"chow_current_account": account["account_id"]})
            contact_response = None
            if op.get("administrator"):
                contact_response = crm.create_contact({"account_id": account["account_id"], "name": op["administrator"], "title": "Administrator",
                                                       "phone": "", "is_active": True})
            results.append({"action": action, "status": "applied", "new_account_id": account["account_id"], "contact_response": contact_response})
        else:
            raise ValueError(f"Unsupported action: {action}")
    return results


def _created_id(result: dict, key: str) -> str:
    if result.get(key):
        return result[key]
    response = result.get("response") or {}
    payload = response.get("data", response) if isinstance(response, dict) else {}
    return payload.get(key, "") if isinstance(payload, dict) else ""


def rollback(proposal: dict, crm: CRMClient) -> list[dict]:
    """Compensate an approved proposal without deleting CRM history."""
    applied = proposal.get("result") or []
    if not isinstance(applied, list) or len(applied) != len(proposal["operations"]):
        raise RuntimeError("Approval result is incomplete; automatic rollback is unsafe")
    account_before = proposal.get("account") or {}
    results = []
    for op, outcome in reversed(list(zip(proposal["operations"], applied))):
        action = op["action"]
        if action == "update_account":
            original = {field: account_before.get(field, "") for field in op["fields"]}
            results.append({"action": action, "response": crm.update_account(op["account_id"], original)})
        elif action == "update_contact":
            original = {field: (True if field == "is_active" else "") for field in op["fields"]}
            results.append({"action": action, "response": crm.update_contact(op["contact_id"], original)})
        elif action == "create_contact":
            contact_id = _created_id(outcome, "contact_id")
            if not contact_id: raise RuntimeError("Created contact id is missing; automatic rollback is unsafe")
            results.append({"action": action, "response": crm.update_contact(contact_id, {"is_active": False})})
        elif action == "create_account":
            account_id = _created_id(outcome, "account_id")
            if not account_id: raise RuntimeError("Created account id is missing; automatic rollback is unsafe")
            contact_id = _created_id({"response": outcome.get("contact_response")}, "contact_id")
            if contact_id: crm.update_contact(contact_id, {"is_active": False})
            results.append({"action": action, "response": crm.update_account(account_id, {
                "status": "Inactive", "note": "Approval withdrawn; account retained for audit because the API has no delete operation."
            })})
        elif action == "chow_create":
            new_id = _created_id(outcome, "new_account_id")
            if not new_id: raise RuntimeError("CHOW successor id is missing; automatic rollback is unsafe")
            contact_id = _created_id({"response": outcome.get("contact_response")}, "contact_id")
            if contact_id: crm.update_contact(contact_id, {"is_active": False})
            crm.update_account(op["old_account_id"], {"chow_current_account": ""})
            results.append({"action": action, "response": crm.update_account(new_id, {
                "status": "Inactive", "note": "CHOW approval withdrawn; successor retained for audit because the API has no delete operation."
            })})
        else:
            raise ValueError(f"Unsupported rollback action: {action}")
    return results
