from __future__ import annotations

import hashlib
import json
from difflib import SequenceMatcher

from .normalize import clean, normalize_name, normalize_phone, normalize_street, normalize_zip
from .scraper import Community
from .config import SOURCE_BASE_URL, SOURCE_NAME, TARGET_PARENT_ACCOUNT_NAME, TARGET_PARENT_NAME


def _ratio(left: str, right: str) -> float:
    return SequenceMatcher(None, left, right).ratio()


def score(community: Community, account: dict) -> tuple[float, list[str]]:
    evidence: list[str] = []
    street_equal = normalize_street(community.street) == normalize_street(account.get("billing_street"))
    zip_equal = normalize_zip(community.zip) == normalize_zip(account.get("billing_zip"))
    city_equal = clean(community.city) == clean(account.get("billing_city"))
    state_equal = clean(community.state) == clean(account.get("billing_state"))
    name_ratio = _ratio(normalize_name(community.name), normalize_name(account.get("name")))
    phone_equal = bool(normalize_phone(community.phone)) and normalize_phone(community.phone) == normalize_phone(account.get("phone"))
    total = name_ratio * 30
    if street_equal: total += 45; evidence.append("exact normalized street")
    if zip_equal: total += 10; evidence.append("same ZIP")
    if city_equal: total += 7; evidence.append("same city")
    if state_equal: total += 3; evidence.append("same state")
    if phone_equal: total += 5; evidence.append("same phone")
    evidence.append(f"name similarity {name_ratio:.0%}")
    return min(total, 100), evidence


def _fingerprint(payload: dict) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _proposal(kind: str, community: Community | None, account: dict | None, confidence: float,
              evidence: list[str], operations: list[dict], summary: str,
              source_context: dict | None = None) -> dict:
    item = {
        "kind": kind, "community": community.to_dict() if community else None,
        "account": account, "confidence": round(confidence, 1), "evidence": evidence,
        "operations": operations, "summary": summary, "source_context": source_context or {},
        "run_id": (source_context or {}).get("run_id", ""),
    }
    item["fingerprint"] = _fingerprint({"run_id": item["run_id"], "kind": kind, "community": item["community"], "account_id": (account or {}).get("account_id"), "operations": operations})
    return item


def build_proposals(communities: list[Community], accounts: list[dict], contacts: list[dict],
                    source_context: dict | None = None) -> list[dict]:
    requested_parent_id = (source_context or {}).get("target_parent_account_id")
    parent = next(
        a for a in accounts
        if (requested_parent_id and a.get("account_id") == requested_parent_id)
        or (not requested_parent_id and a.get("name") == TARGET_PARENT_ACCOUNT_NAME)
    )
    parent_id = parent["account_id"]
    context = source_context or {
        "target_parent_name": TARGET_PARENT_NAME,
        "target_parent_account_name": TARGET_PARENT_ACCOUNT_NAME,
        "target_parent_account_id": parent_id,
        "source_name": SOURCE_NAME,
        "source_base_url": SOURCE_BASE_URL,
    }
    target_parent_name = context["target_parent_name"]
    source_name = context["source_name"]
    source_checked_at = context.get("website_collected_at", "an unspecified time")
    def proposal(kind, community, account, confidence, evidence, operations, summary):
        return _proposal(kind, community, account, confidence, evidence, operations, summary, context)
    contact_by_account: dict[str, list[dict]] = {}
    for contact in contacts:
        contact_by_account.setdefault(contact.get("account_id", ""), []).append(contact)
    proposals: list[dict] = []
    matched_ids: set[str] = set()
    survivor_by_address: dict[str, str] = {}
    community_by_address = {
        normalize_street(c.street) + "|" + normalize_zip(c.zip): c for c in communities
    }

    for community in communities:
        ranked = sorted(((score(community, a), a) for a in accounts if "Parent Account" not in a.get("name", "")), reverse=True, key=lambda x: x[0][0])
        (best_score, evidence), account = ranked[0]
        active_admins = [
            c.get("name", "") for c in contact_by_account.get(account["account_id"], [])
            if clean(c.get("title")) == "administrator" and c.get("is_active")
        ]
        account = {**account, "active_administrators": active_admins}
        if best_score < 55:
            fields = {
                "name": community.name, "parent_id": parent_id, "billing_street": community.street,
                "billing_city": community.city, "billing_state": community.state, "billing_zip": community.zip,
                "care_type": " & ".join(community.care_offerings), "status": "Active", "phone": community.phone,
                "note": f"Created from {community.source_url}",
            }
            ops = [{"action": "create_account", "fields": fields, "administrator": community.administrator}]
            proposals.append(proposal("create", community, None, best_score, evidence, ops, f"Create CRM account for {community.name}"))
            continue

        address_key = normalize_street(community.street) + "|" + normalize_zip(community.zip)
        updates = {}
        desired = {
            "name": community.name, "billing_street": community.street, "billing_city": community.city,
            "billing_state": community.state, "billing_zip": community.zip,
            "care_type": " & ".join(community.care_offerings), "phone": community.phone,
        }
        # Identity matching tolerates aliases such as Rehab/Rehabilitation, but once
        # identity is established the website's official display name should win.
        normalizers = {"name": clean, "billing_street": normalize_street, "billing_city": clean,
                       "billing_state": clean, "billing_zip": normalize_zip, "care_type": clean, "phone": normalize_phone}
        for field, value in desired.items():
            if normalizers[field](value) != normalizers[field](account.get(field)):
                updates[field] = value

        ops: list[dict] = []
        wrong_parent = account.get("parent_id") != parent_id
        exact_full_address = all((
            normalize_street(community.street) == normalize_street(account.get("billing_street")),
            clean(community.city) == clean(account.get("billing_city")),
            clean(community.state) == clean(account.get("billing_state")),
            normalize_zip(community.zip) == normalize_zip(account.get("billing_zip")),
        ))
        core_identity_all_different = all((
            normalize_name(community.name) != normalize_name(account.get("name")),
            clean(desired["care_type"]) != clean(account.get("care_type")),
            normalize_phone(community.phone) != normalize_phone(account.get("phone")),
        ))

        # The operator website proves that the target parent currently claims this address,
        # but an exact address alone does not prove that a differently owned,
        # differently named facility is the same CRM entity. Escalate rather
        # than overwriting fields or inferring a CHOW without identity evidence.
        if wrong_parent and exact_full_address and core_identity_all_different:
            matched_ids.add(account["account_id"])
            matching_admin = next(
                (name for name in active_admins if clean(name) == clean(community.administrator)), None
            )
            continuity = ""
            review_evidence = [*evidence, "different parent", "name, care offerings, and phone all differ"]
            if matching_admin:
                continuity = (
                    f" The same administrator, {matching_admin}, appears in both sources, which makes an ownership "
                    "change more likely, but does not prove account identity."
                )
                review_evidence.append(f"same administrator: {matching_admin}")
            note = (
                f"{target_parent_name} currently lists a facility at this address, but the CRM account has a different "
                "parent, name, care offering, and phone."
                f"{continuity} Verify whether this is an ownership change or a different facility before updating."
            )
            review_fields = {"status": "Needs Review", "note": note}
            proposals.append(proposal(
                "update", community, account, best_score, review_evidence,
                [{"action": "update_account", "account_id": account["account_id"], "fields": review_fields}],
                f"Review ambiguous identity for {community.name}",
            ))
            continue

        matched_ids.add(account["account_id"])
        survivor_by_address[address_key] = account["account_id"]
        if wrong_parent and account.get("lifetime_revenue", 0) > 0 and account.get("outstanding_ar", 0) > 0:
            new_fields = {**desired, "parent_id": parent_id, "status": "Active", "note": f"CHOW successor created from {community.source_url}"}
            ops.append({"action": "chow_create", "old_account_id": account["account_id"], "fields": new_fields,
                        "administrator": community.administrator})
            updates = {}
        else:
            if wrong_parent:
                updates["parent_id"] = parent_id
            if updates:
                ops.append({"action": "update_account", "account_id": account["account_id"], "fields": updates})

            admins = [c for c in contact_by_account.get(account["account_id"], []) if clean(c.get("title")) == "administrator" and c.get("is_active")]
            matching_admin = next((c for c in admins if clean(c.get("name")) == clean(community.administrator)), None)
            if not matching_admin:
                for old_admin in admins:
                    ops.append({"action": "update_contact", "contact_id": old_admin["contact_id"],
                                "fields": {"is_active": False}})
                ops.append({"action": "create_contact", "fields": {"account_id": account["account_id"], "name": community.administrator,
                            "title": "Administrator", "phone": "", "is_active": True}})

        if ops:
            kind = "chow" if ops[0]["action"] == "chow_create" else "update"
            proposals.append(proposal(kind, community, account, best_score, evidence, ops, f"Reconcile {community.name} with {account['name']}"))

    # Exact-address alternatives are safe duplicate candidates. The account selected
    # by the website matcher survives; other copies are retained but made inactive.
    for key, survivor_id in survivor_by_address.items():
        same_address = [
            a for a in accounts
            if normalize_street(a.get("billing_street")) + "|" + normalize_zip(a.get("billing_zip")) == key
            and a.get("account_id") != survivor_id
            and a.get("status") != "Inactive"
            and not a.get("duplicate_of_account")
            and not a.get("chow_current_account")
        ]
        survivor = next(a for a in accounts if a["account_id"] == survivor_id)
        for loser in same_address:
            fields = {
                "status": "Inactive", "duplicate_of_account": survivor_id,
                "note": f"Duplicate facility record at the same normalized address; surviving account is {survivor_id} ({survivor['name']}).",
            }
            ops = [{"action": "update_account", "account_id": loser["account_id"], "fields": fields}]
            item = proposal("duplicate", community_by_address.get(key), loser, 95,
                         ["one website location maps to two CRM accounts", "exact normalized street and ZIP",
                          f"website-matched survivor: {survivor['name']}"],
                         ops, f"Keep {survivor['name']}; mark the second CRM record as duplicate")
            item["survivor"] = survivor
            proposals.append(item)

    website_addresses = {normalize_street(c.street) + "|" + normalize_zip(c.zip) for c in communities}
    bellhaven_children = [a for a in accounts if a.get("parent_id") == parent_id]
    for account in bellhaven_children:
        key = normalize_street(account.get("billing_street")) + "|" + normalize_zip(account.get("billing_zip"))
        if account["account_id"] not in matched_ids and key not in website_addresses:
            fields = {
                "status": "Needs Review",
                "note": (
                    f"This CRM account is currently assigned to {target_parent_name} but was not found in the "
                    f"{source_name} snapshot collected at {source_checked_at}. Verify ownership before changing the account."
                ),
            }
            ops = [{"action": "update_account", "account_id": account["account_id"], "fields": fields}]
            proposals.append(proposal("missing_from_website", None, account, 70, [f"{target_parent_name} child account", "no source location at this address"], ops, f"Flag {account['name']} for review"))

    return proposals
