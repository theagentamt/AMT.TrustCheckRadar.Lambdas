# SECUR4ALL-178 Lambda operations contract reconciliation

Status: proposed source contract, not a runtime publisher, storage design, API, alarm, or deployment.

Source baseline: `c9d95fd8cdc95b6843c5b8b0114fc7b53186d872` on Lambda `main`.

This note reconciles the operational-reporting draft in
`V1-OPERATIONS-CONTRACT.md` with the current V1 authority, message, URL,
QR, recovery-clarification, and governed-History contracts. Runtime delivery
belongs to SECUR4ALL-237 and infrastructure delivery belongs to SECUR4ALL-243.

## Source-backed policy

### One logical check

A logical check starts only when authority creates the `V1_CHECK_RECEIPT` in
`ADMITTED` state. Preparation is explicitly non-chargeable and admission is the
transaction that reserves capacity. HTTP requests, validation failures,
preparations, retries, provider calls, and the authority attempt counter are not
logical checks.

The operational `logicalCheckRef` is a future random server reference. It must
not be a subject, client check ID, operation proof, receipt ID, payload HMAC, URL
hash, or reversible derivative of any of those. SECUR4ALL-237 must bind that
reference to the existing authority receipt without creating a second subject
locator. It is potentially linkable while the receipt mapping exists.

An idempotent admission replay returns the existing receipt. It does not create
another accepted check. Reconciliation or backfill republishes the same event
identity and lifecycle revision. It does not create a separate "reconciled
check" count.

### Processing and accounting are independent

The authoritative processing outcomes are:

`complete`, `partial`, `inconclusive`, `failed`, `blocked`, `invalid_input`,
`unsupported`, and `unavailable`.

Only `complete` is chargeable. Trial and paid complete checks deduct one;
complimentary complete checks deduct zero. Every other settled outcome deducts
zero. Therefore the report must expose `processingOutcome`, `accountingState`,
and `chargedChecks` independently. It must not use "completed" as a synonym for
"charged" or fold inconclusive results into a chargeable-complete count.

The exact accepted-check equation is:

```text
accepted = pending
         + settled.complete
         + settled.partial
         + settled.inconclusive
         + settled.failed
         + settled.blocked
         + settled.invalid_input
         + settled.unsupported
         + settled.unavailable
```

`chargedChecks` is the sum of settled ledger deductions, not the count of
`complete` outcomes. `accountingState=unknown` and pending admission are never
treated as zero charge. An expired admitted lease settles as `failed` with zero
charge. A transaction-uncertain response remains reconciliation-required until
the authority receipt supplies a decision.

`check_terminal` is published only from an authority receipt that is actually
`SETTLED`. A terminal user response whose ledger read is still uncertain remains
reconciler-pending coverage; it does not create a second terminal fact, a new
charge, or an invented zero-charge decision.

There is no V1 backend `cancelled` outcome. Closing the client is not proof of
server cancellation. The V1 equation therefore excludes cancellation. Adding
it requires a new source state, contract version, and executable transition.

### Rejections and local intake

Authentication, account, device, access, allowance, rate, privacy, contract,
and input rejections that occur before successful admission stay outside the
accepted-check denominator. They may be counted only as `request_rejected`
fixed-code aggregates produced by trusted ingress. The aggregate has no event
ID, logical-check or attempt reference, lifecycle revision, access class,
input classification, user-derived field, or per-request timestamp. It uses a
five-minute server bucket and bounded count. The ingress increments the
aggregate directly; it must not create a per-request event, reconciliation row,
or seven-day linkable record.

There is no authorized client intake telemetry publisher today. A report must
show local-intake coverage as `unavailable`, not a zero failure count. SECUR4ALL-
237 may add a separately approved, minimized mobile contract later; this
projection does not authorize it.

### Internal attempts and cost

Attempts are a separate measure keyed by an opaque event identity. One logical
check may have no provider call (for example, message rules-only), one provider
call, or several bounded component/provider attempts. Attempt counts never
alter the logical-check denominator or ledger deduction.

An `attempt_started` fact is durably recorded before provider dispatch and
establishes that one bounded execution attempt was planned/reserved. It is not
proof that a provider request began; a policy or circuit gate may deny the call.
A later `attempt_finished` fact with the same `attemptRef` supplies exactly one outcome:
`succeeded`, `failed`, or `denied`. Timeout and provider throttling are fixed
failure reasons (`TIME_BUDGET_EXHAUSTED` and `PROVIDER_RATE_LIMITED`), not
additional outcome buckets. Therefore:

```text
attempts.started = attempts.awaiting_finish
                 + attempts.succeeded
                 + attempts.failed
                 + attempts.denied
```

Missing finish is `awaiting_finish`/unknown, never a fabricated failure or zero
cost. Identical replay is ignored. Start-to-finish enrichment for one
`attemptRef` is valid. Two facts for the same reference and same phase whose
immutable component/provider/start-time or finished units/cost/outcome differ
are a reconciliation fault; arrival order never selects a winner.

The attempt belongs to the reporting day containing its immutable start time.
A later finish enriches and may correct that original attempt-day aggregate; it
does not move provider units/cost into the finish day. `attempt_started` is the
pre-dispatch truth that prevents a crash after dispatch from disappearing as a
false zero.

Cost is nullable integer micro-USD on the finished attempt. `null` means
unknown; integer `0` means observed/configured zero and is not unknown. Reports
must publish `costKnownProviderAttempts`, `costUnknownProviderAttempts`,
`knownProviderRequestUnits`, and `requestUnitsUnknownProviderAttempts` before
any cost total. The cost-coverage denominator contains every started
provider-call attempt, including attempts still awaiting a finish. A started
provider attempt without a finish contributes unknown cost and unknown request
units; it must not disappear from coverage or become a known zero. Internal-
component attempts remain a separate operational count and have null provider
units/cost. No provider price, model price, budget, or currency conversion is
approved by this contract.

A dispatch denied before a provider request has proven request units `0` and
may have proven estimated cost `0`; those values are known zeros. A missing
finish or missing cost fact remains unknown and is not converted to that case.

The projection is explicitly `trafficClass=customer`. Owned readiness probes
remain outside this schema, customer accepted/accounting rates, and allowance.
They cannot acquire provider or allowance bypass through this document. A
deeper provider-using qualification needs a separately approved synthetic
contract and reports its cost separately.

### Message, URL, QR, and recovery coverage

Message source kinds are `pasted_text`, `ocr`, and `mixed`. URL source kinds are
`standalone_url`, `message_url`, and `qr_url`; QR is already preserved by the
authority/governed-History path and does not need to be guessed from content.

The built-in recovery selector is local, performs no provider call, and declares
`not_an_analysis_check`. It is outside the logical-check denominator. The
server recovery-clarification consumer is different: it admits an authority
controlled `recovery_clarification` check. A complete clarification can deduct
one for paid/trial access, while partial, inconclusive, failed, blocked,
unsupported, invalid, and unavailable outcomes deduct zero.

### Report correction semantics

Accepted checks remain in the cohort containing their authority acceptance
time. A late settled event revises that cohort. Report revision is an
infrastructure aggregate property; it does not rewrite the immutable receipt
or accounting decision. Identical event replay is ignored. A conflicting event
with the same identity and revision is rejected and increments fixed allowlisted
fault metadata/counters only. The conflicting body is not copied to a
quarantine, log, table, queue, or dead-letter queue.

The approved correction window is seven days, aligned with the minimized
authority receipt. Linkable event/check/attempt references must become
inaccessible at that same fixed deadline and must not renew on replay or report
generation. Fourteen days is approved for fixed-code diagnostic logs and 90
days for unlinkable daily aggregate revisions. These approvals do not activate
storage: SECUR4ALL-237/243 must verify expiry fences, physical cleanup and the
backup lifecycle first. The prior 30-day linkable proposal is not approved.

A delayed event, replay, backfill, or restore must not recreate linkable state
after account deletion or expiry. Ingestion must atomically verify the existing
admitted authority receipt and account-deletion fence before inserting or
updating linkable operations state. This contract does not authorize an
indefinite identity map or tombstone.

## Proposed projection

`operations-projection.schema.proposed.json` is candidate
`1.0.0-operations-candidate.1`. It has four authority/attempt event types:

- `check_accepted`
- `check_terminal`
- `attempt_started`
- `attempt_finished`

`rejection-aggregate.schema.proposed.json` is a separate non-linkable ingress
aggregate with the standardized type `request_rejected`. Separating it avoids
forcing pre-admission hostile traffic into a generic per-request event shape.
Its canonical key is the finite
`(schemaVersion, windowStartEpoch, environment, trafficClass, service,
reasonCode)` tuple. `rejectedCount` is the trusted ingress's authoritative
cumulative snapshot for that five-minute key, not a delta to add on arrival.
An identical snapshot is an idempotent no-op; a larger count replaces the
prior count; a smaller/stale count or conflicting same-revision value is a
fixed-code ingestion fault. None of those cases creates a per-request or
reconciliation row, stores the rejected body, or renews aggregate retention.

It deliberately has no generic tags, free text, account/device/client IDs,
content hashes, request bodies, provider responses, model explanations, dynamic
provider names, or metric dimensions. Unknown enum values require a new schema
version. Invalid/unknown payloads are not copied into logs, storage, queues, or
dead-letter queues; rejection emits fixed metadata/counters only.

`check_accepted` and `check_terminal` carry the same random
`logicalCheckRef`. A settled event carries the authority processing and
accounting decisions. A rejection aggregate has no per-request reference or
access classification. An attempt event cannot decide accounting.

The candidate event is source input for aggregation only. Routine reports and
emails contain aggregates and never contain `eventId` or `logicalCheckRef`.

## Synthetic fixture and test matrix

The fixtures in `fixtures.proposed.json` are synthetic fixed-code examples. No
fixture contains a user/account/device identifier, message, URL, QR payload,
token, proof, receipt, provider response, or content-derived value.

SECUR4ALL-237 should make the following executable before activation:

| Case | Expected result |
| --- | --- |
| accepted then complete, trial/paid | one accepted, one settled complete, one charged check |
| accepted then complete, complimentary | one accepted, one settled complete, zero charged checks |
| partial or inconclusive | one accepted, exact settled outcome, zero charged checks |
| failed/blocked/invalid/unsupported/unavailable | one accepted, exact settled outcome, zero charged checks; technical-failure numerator depends on a fixed source-backed cause, so a provider-caused partial may qualify while policy-disabled unavailable may not |
| admitted lease expires | settled failed, zero charged checks, no provider replay |
| duplicate accepted/settled event | no aggregate change |
| same identity/revision with different body | reject; retain fixed fault metadata/counter only; never store or echo the rejected body |
| duplicate rejection-window snapshot | no count change |
| larger rejection-window snapshot | replace the prior cumulative count; do not add it |
| stale/smaller rejection-window snapshot | fixed ingestion fault; no count rollback or raw retention |
| settled arrives before accepted | buffer/reconcile by identity and revision; one logical check after both facts are present |
| retry/replay/reconcile | same event identities and ledger decision; no second logical check or charge |
| transaction uncertainty | pending/accounting unknown until authoritative reconciliation; never assume zero |
| pre-admission auth/access/allowance/rate/input rejection | outside accepted denominator; fixed rejection reason only |
| local intake | report coverage unavailable until a separately approved mobile publisher exists |
| message rules-only | zero provider attempts is valid; outcome/accounting remain authoritative |
| URL redirect plus Google lookup | one logical check, multiple attempts, at most one settled deduction |
| provider 429 | provider attempt finishes failed with `PROVIDER_RATE_LIMITED`; request-level `RATE_LIMITED` is not substituted; terminal outcome remains the authority-settled partial/unavailable value |
| QR through URL consumer | input kind remains `qr_url`; no payload inspection or URL inference |
| recovery built-in selector | no analysis event and no deduction |
| recovery clarification complete | one accepted clarification and one charge for paid/trial; complimentary zero |
| recovery clarification non-complete | one accepted clarification, exact outcome, zero charge |
| unknown cost versus observed zero | null decreases cost coverage; integer zero increases known-cost coverage and is summed as zero |
| late settlement | original acceptance-day aggregate gets a new revision; today's accepted stays unchanged |
| Chicago DST/midnight | half-open UTC bounds derived from America/Chicago; 23/25-hour days remain valid |
| hostile string in every field | schema rejection and fixed counter only; no echo to logs, storage, metrics, or email |

## Executable source anchors and current limits

All anchors refer to Lambda main `c9d95fd8cdc95b6843c5b8b0114fc7b53186d872`:

- `src/shared_check_authority/core.py:17-23` fixes paid/trial limits and the
  complete-only chargeable outcome.
- `src/shared_check_authority/core.py:332-370` makes preparation non-chargeable
  and idempotent.
- `src/shared_check_authority/core.py:380-429` creates one admitted receipt and
  reserves capacity transactionally.
- `src/shared_check_authority/core.py:516-651` performs idempotent settlement,
  zero-charge expired recovery, processing validation, and ledger settlement.
- `src/message_consumer/service.py:50-180`, `src/url_consumer/service.py:78-205`,
  and `src/recovery_consumer/service.py:45-165` expose pending/unknown/settled
  accounting and bounded reconciliation without content replay.
- `src/shared_governed_history/projection.py:37-99` preserves authoritative
  message/URL/QR source kind, processing outcome, accounting, and fixed expiry.
- `contracts/recovery-selection/1.0.0-candidate.1/reference_selector.py:49-70`
  marks built-in recovery selection as not an analysis check.
- `contracts/recovery-clarification/0.1.0-candidate.1/README.md:35-60`
  identifies the offline proposal as non-chargeable, provider-free simulation,
  not the server clarification consumer.

Current limitations:

1. No operations publisher, schema enforcement adapter, deduplication store,
   aggregator, daily delivery, or service-wide alert implementation exists.
2. Consumer logs currently expose only fixed event/status/state/reason fields;
   they do not form a complete lifecycle or provider-attempt record.
3. Broad adapter failures can return a fixed 503 before the current custom log
   statement, so log absence is not evidence of zero failures.
4. No authorized mobile intake publisher exists.
5. No cost catalog or source-backed micro-USD calculation exists.
6. No backend cancellation state exists.
7. The proposed random operations reference and monotonic lifecycle revision do
   not exist in current receipt data and require SECUR4ALL-237 implementation.
8. Schedule, retention, thresholds, correction cadence, retry/notification
   caps, and recipient are approved policy. Their runtime implementation and
   actual Dev report/alarm delivery evidence remain SECUR4ALL-237/243 work.

## Approved policy and remaining implementation evidence

1. Daily report at 08:00 `America/Chicago` for the previous local day.
2. Seven days for linkable reconciliation state, 14 days for fixed-code logs,
   and 90 days for unlinkable daily aggregate revisions, subject to tested
   expiry/deletion/backup enforcement before activation.
3. The alert thresholds, minimum traffic, event-time/lateness semantics,
   correction cadence, reminder/delivery caps, recovery policy, and bounded
   retry rules recorded in accepted infrastructure policy
   `1.0.0-policy.1`/`V1-OPERATIONS-CONTRACT.md`.
4. `support@andmorethings.com` is the approved operational recipient.

Policy acceptance does not establish runtime behavior. SECUR4ALL-243 must still
prove an actual report, alarm transition, recovery notification, suppression,
and delivery-failure path rather than relying on an SNS subscription test.
SECUR4ALL-237 must implement and qualify this proposed Lambda projection before
any publisher is enabled.
