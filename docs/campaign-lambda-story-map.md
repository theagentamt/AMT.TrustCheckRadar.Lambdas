# Campaign Lambda Story Map

This map intentionally covers Lambda application work only. Infrastructure,
mobile clients, dashboards, and operational configuration are outside this
repository's scope.

| Story | Lambda scope | Current status |
|---|---|---|
| `SECUR4ALL-202` | Consume the approved taxonomy, privacy, retention, and schema decisions in Lambda contracts. | Dependency only. Product rules are available; the authoritative backend schemas and period-key addressing handoff remain pending. |
| `SECUR4ALL-203` | None. | Excluded: AWS infrastructure story. |
| `SECUR4ALL-204` | Publish opted-in sanitized observations with app-provided features, derive period pseudonyms, enforce input privacy, retain idempotency state, and enqueue opaque clustering requests. | Lambda implementation complete. Analysis opt-in is fail-closed, the event ID survives transaction recovery, app features are revalidated at each boundary, and the outbox write is atomic with completion. |
| `SECUR4ALL-205` | Enforce the app-provided multilingual feature contract at Lambda boundaries. | No server extractor exists. The shared V1 contract rejects malformed features before outbox persistence, publisher processing, or clustering; model quality and runtime evidence are owned by the Android handoff. |
| `SECUR4ALL-206` | Perform deterministic candidate scoring and bounded conditional aggregation with contributor caps and replay safety. | Lambda implementation complete with deterministic scoring, category conflicts, bounded candidate lookup, optimistic concurrency, one-vector/three-submission caps, replay safety, packaging, and automated evidence. End-to-end model quality remains a `SECUR4ALL-210` gate. |
| `SECUR4ALL-207` | Process consent withdrawal/account deletion, remove active contributions, recompute affected candidates, sweep retention, and retire period keys. | The authoritative campaign withdrawal command is now implemented, including pending-only deletion, conditional completion, a completion receipt, and loop suppression. Story remains blocked only by the missing expiration query access pattern needed for explicit arbitrary transient sweeping. |
| `SECUR4ALL-208` | Enforce reviewer authorization and valid publication state transitions; create privacy-safe immutable audit records. | Confirm, publish, suppress, and emergency-suppress transitions are implemented with threshold checks and immutable standardized audit reasons. Story remains open because aggregate-only storage cannot implement overlap-safe merge/split semantics; those operations fail closed pending an approved contract. |
| `SECUR4ALL-209` | Implement the campaign trends and reviewer API Lambda handlers, including suppression, count bands, localization, filtering, and pagination. | Lambda portion (`SECUR4ALL-211`) complete: authenticated published-only query, suppression, bands, English/Spanish labels, filters, bounded pagination, packaging, and tests. Infrastructure/API deployment and dashboards remain outside this repository. |
| `SECUR4ALL-210` | Add Lambda privacy-negative, deletion, abuse, replay, contract, load, and cost-evidence tests. | Local evidence automation is implemented across all campaign Lambdas, including proof that no server extractor remains. Final story still requires deployed UAT deletion, load, IAM, cost, rollback, and re-identification evidence. |
| `SECUR4ALL-216` | Wire successful opted-in conversation analyses and strict app-provided features to the campaign outbox with a retry-stable UUIDv4. | Lambda implementation complete. Publishing additionally requires the current server participation epoch, and the atomic outbox transaction condition-checks its state/version/epoch to close withdrawal races. |
| `SECUR4ALL-217` | Provide server-owned campaign participation enrollment, withdrawal, receipts, quota adjustment, deletion completion, and analysis authorization. | Lambda implementation complete and packaged as `campaign_participation.zip`; infrastructure routes, tables, IAM, configuration, and deployment remain with the server/infra handoff. |
| `SECUR4ALL-218` | Expose the optional participation choice and app intent. | No Lambda work. Android remains responsible for notice UI, explicit user action, feature extraction, and request intent. |

## SECUR4ALL-204 implemented boundary

The observation publisher currently owns the first identity-separating boundary:

1. Accept only a strict V1 DynamoDB stream record for the deployed environment.
2. Treat declined consent and expired input as successful no-ops.
3. Derive a contributor token with the fixed-period KMS HMAC key.
4. Revalidate and persist the app-provided feature without source text or account identifier.
5. Send only the approved five-field envelope to the clustering queue.
6. Track `PENDING` and `PUBLISHED` dedupe state for recoverable at-least-once delivery.

The conversation-analysis request defaults campaign intent to `false` and accepts
only an explicit boolean. Publishing also requires a current server-owned
`enrolled` state. An authorized completion creates its UUIDv4 event
identity before the atomic commit and stores that identity with `RESULT_READY`, so
a transaction retry cannot create a second campaign event. The stored consent
epoch/notice/version provenance is condition-checked in the same transaction as
the outbox write. Intent without server enrollment does not publish.
