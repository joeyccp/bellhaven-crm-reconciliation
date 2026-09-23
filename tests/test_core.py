import unittest
import tempfile
import json
from pathlib import Path

from bellhaven.actions import execute, rollback
from bellhaven.matcher import build_proposals, score
from bellhaven.normalize import normalize_name, normalize_phone, normalize_street
from bellhaven.scraper import Community, candidate_names_for_parent, parse_community
from bellhaven.source_validation import validate_public_https_url
from bellhaven.store import ReviewStore
from app import _is_safe_bulk_match, _is_safe_missing_review


class CoreTests(unittest.TestCase):
    def test_crm_assisted_discovery_only_uses_target_parent_brand_names(self):
        accounts = [
            {"name": "Bellhaven Meadows of Findlay"},
            {"name": "Bellhaven Senior Living (Parent Account)"},
            {"name": "Unrelated Meadows"},
        ]
        self.assertEqual(
            candidate_names_for_parent(accounts, "Bellhaven Senior Living"),
            ["Bellhaven Meadows of Findlay"],
        )

    def test_source_url_rejects_non_https_before_fetching(self):
        with self.assertRaises(ValueError):
            validate_public_https_url("http://example.com")
        with self.assertRaises(ValueError):
            validate_public_https_url("https://example.com/communities")

    def test_runs_keep_proposals_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory) / "review.db")
            for run_id in ("run-a", "run-b"):
                store.create_run({"run_id": run_id, "target_parent_name": run_id, "created_at": f"2026-01-0{1 if run_id == 'run-a' else 2}T00:00:00+00:00"})
                store.sync([{"fingerprint": f"fp-{run_id}", "run_id": run_id, "kind": "update"}], run_id)
            rows = store.list()
            self.assertEqual({p["run_id"] for p in rows}, {"run-a", "run-b"})
            store.sync([], "run-a")
            self.assertEqual([p["run_id"] for p in store.list()], ["run-b"])

    def test_decided_proposal_is_not_reproposed_when_note_timestamp_changes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = ReviewStore(Path(directory) / "review.db")
            base = {
                "run_id": "run-a", "kind": "missing_from_website",
                "account": {"account_id": "a1"}, "community": None,
                "operations": [{"action": "update_account", "account_id": "a1", "fields": {
                    "status": "Needs Review", "note": "Snapshot at time one"
                }}],
            }
            store.sync([{**base, "fingerprint": "first"}], "run-a")
            store.decide("first", "approved", {})
            changed_note = json.loads(json.dumps(base))
            changed_note["fingerprint"] = "second"
            changed_note["operations"][0]["fields"]["note"] = "Snapshot at time two"
            store.sync([changed_note], "run-a")
            rows = store.list()
            self.assertEqual([(p["fingerprint"], p["decision"]) for p in rows], [("first", "approved")])

    def test_bulk_approval_requires_same_parent_full_address_and_safe_fields(self):
        proposal = {
            "decision": "pending", "kind": "update",
            "account": {"parent_id": "parent"},
            "source_context": {"target_parent_account_id": "parent"},
            "evidence": ["exact normalized street", "same ZIP", "same city", "same state"],
            "operations": [{"action": "update_account", "account_id": "a1", "fields": {"name": "New"}}],
        }
        self.assertTrue(_is_safe_bulk_match(proposal))
        proposal["operations"][0]["fields"]["parent_id"] = "other"
        self.assertFalse(_is_safe_bulk_match(proposal))

    def test_missing_bulk_approval_only_allows_needs_review_and_note(self):
        proposal = {
            "decision": "pending", "kind": "missing_from_website",
            "operations": [{"action": "update_account", "account_id": "a1", "fields": {
                "status": "Needs Review", "note": "Verify ownership."
            }}],
        }
        self.assertTrue(_is_safe_missing_review(proposal))
        proposal["operations"][0]["fields"]["parent_id"] = "other"
        self.assertFalse(_is_safe_missing_review(proposal))

    def test_normalization(self):
        self.assertEqual(normalize_street("980 West Michigan Avenue"), "980 w michigan ave")
        self.assertEqual(normalize_street("4850 Northwest Sylvania Avenue"), "4850 nw sylvania ave")
        self.assertEqual(normalize_street("3313 Wilmington Pk"), "3313 wilmington pike")
        self.assertEqual(normalize_name("Bellhaven Rehab & Nursing"), "bellhaven rehabilitation nursing")
        self.assertEqual(normalize_phone("+1 (614) 250-9447"), "6142509447")

    def test_parse_detail(self):
        html = """<h1>Bellhaven Test</h1><dl class='detail'><dt>Address</dt><dd>1 Main Street<br>Akron, OH 44313</dd><dt>Care Offerings</dt><dd><span class='badge'>Assisted Living</span></dd><dt>Administrator</dt><dd>Jane Doe</dd><dt>Phone</dt><dd>(330) 555-0100</dd></dl>"""
        item = parse_community(html, "https://example.test/community")
        self.assertEqual(item.administrator, "Jane Doe")
        self.assertEqual(item.city, "Akron")

    def test_exact_address_dominates_name_difference(self):
        community = Community("New Name", "55 Ridgewood Avenue", "Akron", "OH", "44313", ["Assisted Living"], "Jane", "330-555-0100", "url")
        account = {"name": "Old Name", "billing_street": "55 Ridgewood Ave", "billing_city": "Akron", "billing_state": "OH", "billing_zip": "44313", "phone": ""}
        value, evidence = score(community, account)
        self.assertGreater(value, 65)
        self.assertIn("exact normalized street", evidence)

    def test_chow_preserves_revenue_account_with_open_ar(self):
        community = Community("Bellhaven Test", "1 Main St", "Akron", "OH", "44313", ["Assisted Living"], "Jane", "330-555-0100", "url")
        accounts = [
            {"account_id": "parent", "name": "Bellhaven Senior Living (Parent Account)", "parent_id": "", "billing_street": "", "billing_zip": ""},
            {"account_id": "old", "name": "Bellhaven Test", "parent_id": "other", "billing_street": "1 Main Street", "billing_city": "Akron", "billing_state": "OH", "billing_zip": "44313", "care_type": "Assisted Living", "phone": "330-555-0100", "status": "Active", "lifetime_revenue": 100, "outstanding_ar": 10},
        ]
        proposals = build_proposals([community], accounts, [])
        chow = next(p for p in proposals if p["kind"] == "chow")
        self.assertEqual(chow["operations"][0]["old_account_id"], "old")
        self.assertEqual(chow["operations"][0]["fields"]["parent_id"], "parent")

    def test_different_parent_and_all_core_fields_different_requires_review(self):
        community = Community("Bellhaven New", "1 Main St", "Akron", "OH", "44313", ["Memory Support"], "Jane", "330-555-0100", "url")
        accounts = [
            {"account_id": "parent", "name": "Bellhaven Senior Living (Parent Account)", "parent_id": "", "billing_street": "", "billing_zip": ""},
            {"account_id": "old", "name": "Legacy Care Center", "parent_id": "other", "parent_name": "Other Operator", "billing_street": "1 Main Street", "billing_city": "Akron", "billing_state": "OH", "billing_zip": "44313", "care_type": "Skilled Nursing", "phone": "330-555-9999", "status": "Active", "lifetime_revenue": 100, "outstanding_ar": 10},
        ]
        proposal = next(p for p in build_proposals([community], accounts, []) if p["account"] and p["account"]["account_id"] == "old")
        self.assertEqual(proposal["kind"], "update")
        self.assertEqual(proposal["operations"][0]["fields"]["status"], "Needs Review")
        self.assertNotIn("parent_id", proposal["operations"][0]["fields"])
        self.assertIn("name, care offerings, and phone all differ", proposal["evidence"])

    def test_bellhaven_parent_allows_stale_core_fields_to_update(self):
        community = Community("Bellhaven New", "1 Main St", "Akron", "OH", "44313", ["Memory Support"], "Jane", "330-555-0100", "url")
        accounts = [
            {"account_id": "parent", "name": "Bellhaven Senior Living (Parent Account)", "parent_id": "", "billing_street": "", "billing_zip": ""},
            {"account_id": "old", "name": "Legacy Care Center", "parent_id": "parent", "parent_name": "Bellhaven Senior Living (Parent Account)", "billing_street": "1 Main Street", "billing_city": "Akron", "billing_state": "OH", "billing_zip": "44313", "care_type": "Skilled Nursing", "phone": "330-555-9999", "status": "Active", "lifetime_revenue": 0, "outstanding_ar": 0},
        ]
        proposal = next(p for p in build_proposals([community], accounts, []) if p["kind"] == "update")
        fields = proposal["operations"][0]["fields"]
        self.assertEqual(fields["name"], "Bellhaven New")
        self.assertEqual(fields["care_type"], "Memory Support")
        self.assertEqual(fields["phone"], "330-555-0100")

    def test_same_administrator_is_noted_as_ownership_continuity_evidence(self):
        community = Community("Bellhaven New", "1 Main St", "Akron", "OH", "44313", ["Memory Support"], "Jane Doe", "330-555-0100", "url")
        accounts = [
            {"account_id": "parent", "name": "Bellhaven Senior Living (Parent Account)", "parent_id": "", "billing_street": "", "billing_zip": ""},
            {"account_id": "old", "name": "Legacy Care Center", "parent_id": "other", "parent_name": "Other Operator", "billing_street": "1 Main Street", "billing_city": "Akron", "billing_state": "OH", "billing_zip": "44313", "care_type": "Skilled Nursing", "phone": "330-555-9999", "status": "Active", "lifetime_revenue": 0, "outstanding_ar": 0},
        ]
        contacts = [{"contact_id": "c1", "account_id": "old", "name": "Jane Doe", "title": "Administrator", "is_active": True}]
        proposal = next(p for p in build_proposals([community], accounts, contacts) if p.get("account", {}).get("account_id") == "old")
        self.assertIn("same administrator: Jane Doe", proposal["evidence"])
        note = proposal["operations"][0]["fields"]["note"]
        self.assertIn("makes an ownership change more likely", note)

    def test_rollback_restores_updated_account_fields(self):
        class FakeCRM:
            def __init__(self): self.calls = []
            def update_account(self, account_id, fields):
                self.calls.append((account_id, fields)); return {"account_id": account_id, **fields}
        crm = FakeCRM()
        proposal = {
            "account": {"account_id": "a1", "name": "Old Name", "phone": "111"},
            "operations": [{"action": "update_account", "account_id": "a1", "fields": {"name": "New Name", "phone": "222"}}],
            "result": [{"action": "update_account", "status": "applied", "response": {"account_id": "a1"}}],
        }
        rollback(proposal, crm)
        self.assertEqual(crm.calls, [("a1", {"name": "Old Name", "phone": "111"})])

    def test_name_alias_matches_identity_but_updates_official_name(self):
        community = Community("Bellhaven Rehabilitation & Nursing", "1 Main St", "Akron", "OH", "44313", ["Short-Term Rehabilitation & Nursing"], "Jane", "330-555-0100", "url")
        accounts = [
            {"account_id": "parent", "name": "Bellhaven Senior Living (Parent Account)", "parent_id": "", "billing_street": "", "billing_zip": ""},
            {"account_id": "a1", "name": "Bellhaven Rehab and Nursing", "parent_id": "parent", "parent_name": "Bellhaven Senior Living (Parent Account)", "billing_street": "1 Main Street", "billing_city": "Akron", "billing_state": "OH", "billing_zip": "44313", "care_type": "Skilled Nursing", "phone": "330-555-0100", "status": "Active", "lifetime_revenue": 0, "outstanding_ar": 0},
        ]
        proposal = next(p for p in build_proposals([community], accounts, []) if p["kind"] == "update")
        update = next(op for op in proposal["operations"] if op["action"] == "update_account")
        self.assertEqual(update["fields"]["name"], "Bellhaven Rehabilitation & Nursing")

    def test_retry_reactivates_withdrawn_contact_instead_of_creating_duplicate(self):
        class FakeCRM:
            def __init__(self): self.updated = []; self.created = []
            def update_contact(self, contact_id, fields):
                self.updated.append((contact_id, fields)); return {"contact_id": contact_id}
            def create_contact(self, fields):
                self.created.append(fields); return {"contact_id": "new"}
        crm = FakeCRM()
        fields = {"account_id": "a1", "name": "Jane Doe", "title": "Administrator", "phone": "", "is_active": True}
        proposal = {
            "operations": [{"action": "create_contact", "fields": fields}],
            "result": {"approval_result": [{"action": "create_contact", "response": {"data": {"contact_id": "c1"}}}]},
        }
        result = execute(proposal, crm)
        self.assertEqual(crm.created, [])
        self.assertEqual(crm.updated, [("c1", fields)])
        self.assertEqual(result[0]["status"], "reactivated")


if __name__ == "__main__": unittest.main()
