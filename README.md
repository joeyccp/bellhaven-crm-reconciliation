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

## Approach

Two separate questions drive the reconciliation: **is it the same building, and is the owner right?** Keeping them apart prevents bad data.

### 1. Find every location

- Scrape all facilities from the website, including pages not linked from the directory (for example, Findlay).
- Label each location by how it was discovered.

### 2. Same place?

- Address and ZIP are the primary signals; name and phone provide supporting evidence. Buildings do not move, but names often change.
- Label each match **High**, **Medium**, or **Low confidence**, or **Manual review required**.

### 3. Right owner?

- The website shows what Bellhaven claims today; it does not prove that a differently owned CRM account is the same business.
- If the address matches but the owner, name, and phone all differ, require manual review.
- A matching Administrator is a helpful continuity clue, but is not enough on its own.

### 4. Safe, reviewable changes

- Show website and CRM records side by side, highlight differences, and explain every recommendation in plain English.
- Do not write anything to the CRM without approval, and allow approved changes to be undone.
- Bulk-approve low-risk updates; review ownership changes one by one.
- If an account has revenue history and an unpaid balance, preserve it, create a new account under the new owner, and link the two records.
- Mark duplicate records Inactive and point them to the surviving account.

### 5. Safe to run daily

- Remember past decisions so reruns show only new work.

## How I used AI

- I used an AI coding assistant as a pair programmer for speed and edge-case testing.
- I owned the business rules and verified the source records, every proposed change, the automated tests, and the approve/reject/undo flows.

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

- Run across all parent companies, prioritizing accounts with revenue or unpaid balances.
- Add a second ownership source, such as CMS ownership data or state licensing lists.
- Alert sales representatives when a facility changes owner.
- Track rejected suggestions to improve matching.

## Submission

See [SUBMISSION.md](SUBMISSION.md) for the concise writeup used with the assessment submission form.
