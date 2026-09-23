from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .config import (DATA_DIR, DATABASE_PATH, SOURCE_BASE_URL, SOURCE_NAME,
                     TARGET_PARENT_ACCOUNT_NAME, TARGET_PARENT_NAME)
from .crm import CRMClient
from .matcher import build_proposals
from .scraper import candidate_names_for_parent, scrape_communities
from .store import ReviewStore


def run() -> dict:
    crm = CRMClient()
    accounts, contacts = crm.accounts(), crm.contacts()
    crm_collected_at = datetime.now(timezone.utc).isoformat()
    parent = next(a for a in accounts if a.get("name") == TARGET_PARENT_ACCOUNT_NAME)
    communities = scrape_communities(
        candidate_names=candidate_names_for_parent(accounts, TARGET_PARENT_NAME)
    )
    website_collected_at = datetime.now(timezone.utc).isoformat()
    source_context = {
        "run_id": "legacy-bellhaven",
        "target_parent_name": TARGET_PARENT_NAME,
        "target_parent_account_name": TARGET_PARENT_ACCOUNT_NAME,
        "target_parent_account_id": parent["account_id"],
        "source_name": SOURCE_NAME,
        "source_base_url": SOURCE_BASE_URL,
        "website_collected_at": website_collected_at,
        "crm_collected_at": crm_collected_at,
        "created_at": website_collected_at,
    }
    proposals = build_proposals(communities, accounts, contacts, source_context)
    DATA_DIR.mkdir(exist_ok=True)
    Path(DATA_DIR / "communities.json").write_text(json.dumps([c.to_dict() for c in communities], indent=2), encoding="utf-8")
    Path(DATA_DIR / "crm_snapshot.json").write_text(json.dumps({"accounts": accounts, "contacts": contacts}, indent=2), encoding="utf-8")
    Path(DATA_DIR / "proposals.json").write_text(json.dumps(proposals, indent=2), encoding="utf-8")
    store = ReviewStore(DATABASE_PATH)
    store.ensure_legacy_run(source_context)
    store.create_run(source_context)
    store.backfill_source_context(source_context)
    store.sync(proposals, source_context["run_id"])
    return {"communities": len(communities), "accounts": len(accounts), "contacts": len(contacts), "proposals": len(proposals)}


if __name__ == "__main__":
    print(json.dumps(run(), indent=2))
