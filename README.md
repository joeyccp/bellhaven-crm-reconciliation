# Bellhaven CRM Reconciliation

A review-first pipeline that scrapes Bellhaven's public community directory, matches locations to CRM accounts, proposes explainable changes, and writes only reviewer-approved changes.

## Safety and business rules

- No CRM mutation happens during scraping or proposal generation.
- Every mutation requires an explicit click in the review app.
- Rejected decisions can be returned to Pending. Approved decisions can be withdrawn through compensating CRM updates.
- Decisions are stored by stable proposal fingerprint, so reruns do not re-propose decided work.
- Facility pages omitted from directory pagination can be recovered through constrained CRM-assisted URL discovery; these records are clearly labeled in the review UI.
- For a parent change where `lifetime_revenue > 0` **and** `outstanding_ar > 0`, the pipeline preserves the old account, creates a successor under the configured target parent, and links the old record through `chow_current_account`.
- Otherwise, a wrong parent is corrected directly.
- Website administrator data is modeled as a CRM Contact with title `Administrator`; facility phone remains on the Account.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Export the values from `.env` in your shell. Never commit the personal CRM token.

## Run

Generate a dry-run review queue and snapshots:

```bash
python -m bellhaven.pipeline
```

Start the review application:

```bash
flask --app app run
```

Open `http://127.0.0.1:5000`. Rejecting changes only records a decision. Approving applies the displayed operations to the CRM and records the result.

For an approved item, **Undo & revert CRM** restores prior field values. Because the sandbox API has no delete endpoint, rollback of a newly created account or CHOW successor marks that new record Inactive, deactivates its created Administrator contact, clears the CHOW link where applicable, and retains an audit record. The proposal then moves to **Withdrawn** rather than pretending the write never occurred.

## Matching approach

Candidates are ranked using normalized street address, ZIP, city, state, facility name similarity, and phone. Address is weighted most heavily because facility names commonly change after acquisitions. Low-confidence locations become create proposals rather than being silently attached to a weak candidate. Each proposal includes the source URL, matching evidence, original CRM record, and exact operations.

The numeric heuristic is used only for internal candidate ranking; it is not presented as a probability. The review UI uses evidence-based levels instead: **High** for exact normalized address + ZIP (or ZIP + phone), **Medium** for one strong identifier, and **Low** otherwise. New-account proposals say **No reliable CRM match**, while stale website findings say **Manual review required**.

Every existing CRM account panel includes its `updated_at` timestamp. Recency is supporting evidence for identifying stale ownership or branding, but it never overrides stronger evidence such as normalized address, the current website listing, billing history, or the CHOW rule.

The target parent and operator evidence source are configuration-driven (`TARGET_PARENT_NAME`, `TARGET_PARENT_ACCOUNT_NAME`, `SOURCE_NAME`, and `SOURCE_BASE_URL`). Each proposal snapshots that context and both collection timestamps so historical decisions retain the source and parent used at the time. The operator website is treated as evidence for facilities claimed by that parent—not as proof that a differently owned CRM record is the same entity. When parent, name, care offering, and phone all differ at an otherwise exact address, the proposal is limited to `Needs Review` plus an explanatory note. A matching active Administrator is shown as continuity evidence that makes an ownership change more likely, but it does not bypass manual review.

## Demo: start another parent reconciliation

Use **New reconciliation** in the review app to select any CRM Parent Account, name an evidence source, and enter its public HTTPS base URL. **Test source** scrapes and previews extracted facilities without creating proposals or writing CRM data. **Run reconciliation** creates an isolated run with its own context, snapshots, proposals, filters, decisions, and audit history. The run selector switches between operators without reinterpreting earlier decisions.

The demo scraper intentionally supports the assessment site's structure only: a `/communities` directory linking to detail pages with the expected fields. Unsupported layouts fail before a run is created. Source URLs must be public HTTPS origins; credentials, paths, queries, fragments, localhost, private, link-local, and reserved addresses are rejected. A production version would add reviewed source-specific adapters rather than claiming that arbitrary websites share one schema.

Normalization handles punctuation, case, ZIP+4, directional words, common street abbreviations, and common name variants such as `Centre/Center` and `Rehab/Rehabilitation`.

## Tests

```bash
python -m unittest discover -s tests -v
```

## Daily operation

`.github/workflows/daily.yml` contains a daily schedule. In a production deployment, the SQLite decision store should be replaced by durable database storage; the included local database is appropriate for this take-home demonstration.

## What I would build next

- Durable hosted decision storage and authenticated reviewer access.
- Transactional/outbox handling for multi-step CHOW operations.
- Address validation and better unit/suite parsing.
- Alerts for ambiguous matches and API write failures.
- Full audit history, rollback support, and metrics on match precision.

## Submission

See [SUBMISSION.md](SUBMISSION.md) for the concise writeup used with the assessment submission form.
