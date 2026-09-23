# Submission writeup

## Matching approach

I built a review-first reconciliation workflow rather than a one-off spreadsheet diff. The pipeline collects the operator's facility pages, normalizes names, street suffixes and directionals, ZIP codes, and phone numbers, then ranks CRM candidates using address, location, name similarity, and phone evidence. Address and ZIP carry the most weight because facility names and branding can change after an acquisition. The score is used only to rank candidates; the reviewer sees explainable confidence levels and the specific evidence instead of a misleading probability.

The workflow distinguishes routine updates, safe matches, new accounts, possible changes of ownership (CHOW), duplicates, and CRM locations missing from the operator website. The website is treated as evidence for facilities currently claimed by the selected parent—not as proof that a differently owned CRM record is the same legal entity. Same-parent, same-address updates can be bulk-approved; ambiguous ownership cases are routed to manual review. For CHOW cases with both historical revenue and open AR, the old billing account is preserved, a successor is created, and the records are linked. All CRM writes require explicit approval and can be reversed through compensating updates.

## How I used AI tools

I used an AI coding assistant as a pair programmer to accelerate implementation, test edge cases, and iterate on the review experience. I remained responsible for the business rules and verified the output by inspecting source records, comparing proposed mutations with the assignment requirements, running the automated test suite, and manually exercising approve, reject, withdraw, reopen, filtering, and bulk-action flows. I also used AI to challenge ambiguous assumptions—for example, separating candidate-ranking scores from reviewer confidence and limiting the operator website's authority when the CRM parent differs.

## What I would build next

For production, I would replace SQLite with durable hosted storage, add authentication and role-based approval, and use transactional or outbox-based execution for multi-step CRM writes. I would add source-specific scraping adapters with monitoring for website layout changes, stronger address/unit validation, and a complete immutable audit log. I would also measure match precision and reviewer overrides over time, use those outcomes to calibrate thresholds, and add alerts for ambiguous matches, stale source snapshots, and partial CRM failures.

