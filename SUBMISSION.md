# Submission writeup

## Matching approach

I approached the reconciliation in three layers:

1. **Is it the same facility?** I compared each website location with CRM records using address and ZIP as the strongest signals, with name and phone as supporting evidence. This accounts for facilities whose branding has changed while the physical location stayed the same.
2. **Does the ownership relationship make sense?** After finding the likely facility, I checked whether its CRM parent matched Bellhaven. The website shows what Bellhaven currently claims, but a matching address alone is not enough to prove an ownership change.
3. **Is there enough evidence to act?** Clear, same-parent matches can be updated confidently. Conflicting ownership or weak identity evidence is routed to manual review instead of being changed automatically.

The review app presents the supporting and conflicting fields side by side so the reviewer can quickly understand each recommendation before writing anything to the CRM.

## How I used AI

I used an AI coding assistant as a pair programmer to accelerate implementation, test edge cases, and iterate on the review app. I owned the business rules and validated the output myself by inspecting source records, reviewing proposed writes, running the automated test suite, and manually testing approval, rejection, rollback, filtering, and bulk actions.

## What I would build next

- Hosted storage instead of SQLite, with login and role-based approval.
- Safer multi-step CRM writes using transactions or an outbox.
- Monitoring for website layout changes and stronger address validation.
- An immutable audit log and metrics on reviewer overrides and match precision.
