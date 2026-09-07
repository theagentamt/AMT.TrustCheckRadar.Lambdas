# Campaign Lambda Story Map

This map intentionally covers Lambda application work only. Infrastructure,
mobile clients, dashboards, and operational configuration are outside this
repository's scope.

| Story | Lambda scope | Current status |
|---|---|---|
| `SECUR4ALL-202` | Consume the approved taxonomy, privacy, retention, and schema decisions in Lambda contracts. | Dependency only. Product rules are available; the authoritative backend schemas and period-key addressing handoff remain pending. |
| `SECUR4ALL-203` | None. | Excluded: AWS infrastructure story. |
| `SECUR4ALL-204` | Publish opted-in sanitized observations, derive period pseudonyms, enforce input privacy, retain idempotency state, and enqueue opaque feature requests. | Lambda implementation complete. Analysis opt-in is fail-closed, the event ID survives transaction recovery, the outbox write is atomic with completion, and the publisher is packaged and tested. External contract/privacy approval remains a promotion gate rather than unfinished Lambda code. |
| `SECUR4ALL-205` | Run the pinned multilingual feature model, redact/bound its output, record model provenance, and enqueue opaque clustering work. | Lambda implementation present and unit-tested, but story remains blocked from completion: no local Docker engine or approved bilingual fixture set is available, so immutable image digest and required quality/performance evidence do not exist yet. |
| `SECUR4ALL-206` | Perform deterministic candidate scoring and bounded conditional aggregation with contributor caps and replay safety. | Lambda implementation complete with deterministic scoring, category conflicts, bounded candidate lookup, optimistic concurrency, one-vector/three-submission caps, replay safety, packaging, and automated evidence. End-to-end model quality remains a `SECUR4ALL-210` gate. |
| `SECUR4ALL-207` | Process consent withdrawal/account deletion, remove active contributions, recompute affected candidates, sweep retention, and retire period keys. | Key registry/rotation, threshold finalization, deletion tombstones, targeted purge, recomputation, and packaging are implemented. Story remains blocked because the table has no expiration index and the lifecycle role cannot scan, making explicit arbitrary transient sweeping impossible; the authoritative deletion-ledger schema is also absent. |
| `SECUR4ALL-208` | Enforce reviewer authorization and valid publication state transitions; create privacy-safe immutable audit records. | Confirm, publish, suppress, and emergency-suppress transitions are implemented with threshold checks and immutable standardized audit reasons. Story remains open because aggregate-only storage cannot implement overlap-safe merge/split semantics; those operations fail closed pending an approved contract. |
| `SECUR4ALL-209` | Implement the campaign trends and reviewer API Lambda handlers, including suppression, count bands, localization, filtering, and pagination. | Lambda portion (`SECUR4ALL-211`) complete: authenticated published-only query, suppression, bands, English/Spanish labels, filters, bounded pagination, packaging, and tests. Infrastructure/API deployment and dashboards remain outside this repository. |
| `SECUR4ALL-210` | Add Lambda privacy-negative, deletion, abuse, replay, quality, load, and cost-evidence tests. | Local evidence automation is implemented across all campaign Lambdas. Final story remains blocked by the missing approved bilingual fixtures/model image plus deployed UAT deletion, load, IAM, cost, rollback, and re-identification evidence. |
| `SECUR4ALL-216` | Wire successful opted-in conversation analyses to the campaign outbox with a retry-stable UUIDv4 and a strict sanitized record contract. | Lambda implementation complete. The event ID is persisted with `RESULT_READY`, reused by recovery, required by the atomic commit, and covered by service, consent, field-allowlist, retention, and failure-recovery tests. Infrastructure activation remains a separate handoff. |

## SECUR4ALL-204 implemented boundary

The observation publisher currently owns the first identity-separating boundary:

1. Accept only a strict V1 DynamoDB stream record for the deployed environment.
2. Treat declined consent and expired input as successful no-ops.
3. Derive a contributor token with the fixed-period KMS HMAC key.
4. Persist a sanitized observation without the account identifier.
5. Send only the approved five-field envelope to the feature queue.
6. Track `PENDING` and `PUBLISHED` dedupe state for recoverable at-least-once delivery.

The conversation-analysis request defaults campaign consent to `false` and accepts
only an explicit boolean opt-in. An opted-in completion creates its UUIDv4 event
identity before the atomic commit and stores that identity with `RESULT_READY`, so
a transaction retry cannot create a second campaign event. Declined requests do
not create an outbox record.
