# Submission writeup

## Matching approach

I built the matching logic as a staged process:

1. **Collect and normalize.** The pipeline scrapes each Bellhaven facility and standardizes names, street suffixes and directionals, ZIP codes, and phone numbers before comparing them with CRM records.
2. **Generate and rank candidates.** For each website location, it finds CRM candidates and ranks them using street address, ZIP, city/state, name similarity, and phone. Address and ZIP carry the most weight because a facility's branding may change while the physical location remains stable.
3. **Validate the relationship.** A strong location match identifies the likely facility, but the current CRM parent is checked separately. The Bellhaven website shows which facilities Bellhaven currently claims; it does not, by itself, prove that a record under a different parent is the same legal or operating entity.
4. **Route by evidence.** Same-parent, same-address differences are treated as stale CRM data and can be reviewed or bulk-approved. If the parent differs and the identity evidence is weak, the proposal is sent to manual review rather than automatically changing ownership.
5. **Explain the result.** The numeric score is used only to rank candidates. Reviewers see a plain confidence level—High, Medium, Low, or Manual Review Required—along with the matching and conflicting fields, source page, and proposed CRM changes.

This keeps record identity, ownership, and data freshness as separate questions instead of collapsing them into one similarity score.

## How I used AI

I used an AI coding assistant as a pair programmer to accelerate implementation, test edge cases, and iterate on the review app. I owned the business rules and validated the output myself by inspecting source records, reviewing proposed writes, running the automated test suite, and manually testing approval, rejection, rollback, filtering, and bulk actions.

## What I would build next

- Hosted storage instead of SQLite, with login and role-based approval.
- Safer multi-step CRM writes using transactions or an outbox.
- Monitoring for website layout changes and stronger address validation.
- An immutable audit log and metrics on reviewer overrides and match precision.
