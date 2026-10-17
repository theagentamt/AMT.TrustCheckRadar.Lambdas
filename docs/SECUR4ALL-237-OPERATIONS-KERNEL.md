# SEC237 operations projection kernel

This implements the first source component of SEC237: bounded validation and
daily reduction of a supplied operations projection snapshot. It does **not**
implement a trusted publisher, runtime handler, durable ingestion, correction
store, alert engine or delivery. SEC237 and SEC243 retain their unfinished
acceptance; no deployment, new table, permission or client telemetry is enabled.

The accepted policy is SEC178 `1.0.0-policy.1`, including Chicago 08:00 prior-day
reporting, seven-day linkable reconciliation, 14-day fixed diagnostics and
90-day unlinkable aggregate revisions. That policy is already decided. This
change does not reopen the timezone, recipient or thresholds, approve a new
dollar budget or replace the physical/release follow-ups.

## Source boundary

`src/operations_reporting/reducer.py` is a pure calculation library. It has no
service clients, logs, account lookup, HTTP endpoint, Lambda handler or email
operation. The existing package builder's function list and all current
consumers are unchanged. `jsonschema==4.26.0` is pinned explicitly for this
library and is the existing development dependency version; no existing runtime
artifact is repackaged or distributed here.

Its two schemas are exact copies of the operations-candidate and rejection
proposal at Lambda main `8ecc79678fd1f87c575c43f7f4ffb856869db48d`, under
`docs/SECUR4ALL-178-operations-contract/`. The approved synthetic fixtures are
also copied unchanged as the accounting oracle. Copying these bounded contract
files does not merge main's other implementation or publisher workflow.

Input is a caller-supplied, already reconciled snapshot. Schema validity and
opaque-reference syntax are **not authority, random-reference generation or
deletion-fence proof**. No runtime caller exists yet. The future producer/store
must supply those guarantees before an aggregate can be delivered. Every result
therefore says `producerCoverage=not_qualified` and `deliveryPerformed=false`.
Empty input is not a healthy-service or zero-traffic attestation.

## Executable behavior

- One admitted check stays one logical check. Exact replay has no effect;
  contradictory event identity or same-phase facts abort with a fixed code.
  Terminal-before-accepted delivery is supported; a terminal missing admission
  is a reconciliation gap, not an accepted or charged check.
- All eight authoritative terminal outcomes remain separate. Processing,
  deducted/no-deduction and unresolved settlement are separate. Pending is
  unknown accounting, not zero charge. Complimentary complete use remains
  visible without an allowance deduction. No client cancellation or built-in
  recovery selection can become a server check outcome.
- Attempts stay separate from check counts. Finish-before-start arrival is
  supported; a missing start cannot prove dispatch or cost. A durable-start
  fact with no finish remains awaiting-finish with unknown units and cost.
  Start context, owner/check reference and ordinal cannot drift at finish.
  Internal components never enter provider-cost denominators. Known zero and
  unknown are different, and the known estimate is labeled a subtotal rather
  than total spend. No price catalog is invented.
- The cohort uses authority acceptance time; attempts use their start time.
  Late terminal/finish facts enrich the original day instead of today's total.
  Chicago local-midnight bounds are half-open UTC instants and cover both DST
  transitions. The kernel refuses a fresh whole-cohort calculation at the
  conservative seven-day boundary. This is not a physical deletion mechanism
  and must not erase a previously stored unlinkable aggregate revision.
- Rejected requests have a separate five-minute cumulative snapshot schema,
  with no per-request identifier or access class. The pure
  `reconcile_rejection_snapshot` helper treats identical replay as a no-op,
  accepts monotonic replacement and rejects a smaller count. A real store must
  apply that rule atomically; it is not a concurrency guarantee by itself.
  Daily calculation requires the reconciled head, rejects conflicting counts
  for a key, and never adds snapshots as deltas.
- No arbitrary tags, content, account/device IDs, provider responses or dynamic
  dimensions enter a report. Invalid fields yield only finite `ProjectionFault`
  codes; raw schema errors and rejected bodies are not returned or logged.
  The input bound is 10,000 facts and 4,096 serialized bytes per fact, a local
  calculation limit rather than a new cloud execution or storage budget.

The output includes processing rates with explicit accepted denominators and
`null` fractions for zero denominators. Fixed terminal reasons are retained as
counts. This slice deliberately does not infer a service-wide technical-failure
rate, alert state, retry recovery count, customer revenue or AWS cost from those
counts. Local-only intake is `not_collected`, not zero failures.

## Run the local component checks

From the repository root with its existing pinned development dependencies:

```sh
PYTHONPATH=src python -B -m pytest -q tests/operations_reporting
```

The suite executes the original expected accounting fixture, all terminal
categories, replay/order permutations, missing authority/start facts, conflicting
cost/ownership, late day corrections, DST, rejection snapshot monotonicity and
hostile values in every input field. It also verifies no service client or
runtime handler was introduced. Independent review and exact results are in
the companion validation/review records. These are local component tests, not
live Dev, provider, mailbox or production evidence.

## Remaining implementation and activation gates

1. Add server-generated random operation references and monotonic facts through
   the existing authoritative receipt boundary. Admission/replay must mint one
   reference atomically, not a second account locator or a content-derived key.
2. Record durable bounded attempt-start facts before dispatch and enrich them
   with authoritative finish/cost facts. Consumers, including partial and
   transaction-uncertain paths, need actual adapters and exhaustive accounting
   tests. Existing runtime consumers are unchanged in this slice.
3. Implement conditional ingestion and bounded reconciliation that atomically
   checks receipt admission, account deletion and the fixed original expiry.
   Cover concurrency, delayed replay, source absence, backup restore and crash
   recovery. No erased/expired linkable state may be recreated; no new indefinite
   identity map or tombstone is authorized.
4. Add unlinkable immutable daily revisions/deltas and a coalesced next-daily
   correction pipeline. Preserve already counted aggregate totals after linkage
   expires or is deleted, and disclose lost correction coverage. Enforce original
   retention boundaries without renewal on replay.
5. Implement the accepted alert/recovery and delivery state machines, bounded
   retries/caps, unknown-send handling, source-backed price/cost coverage and
   operator runbook. Cloud execution/storage cost and numeric infrastructure
   limits require review before activation; do not infer them from this kernel.
6. Under separate deployment scope, SEC243 must prove actual retained storage,
   deletion/backup lifecycle, least-privilege isolation, scheduling, suppression,
   recovery and actual report/alarm receipt at `support@andmorethings.com`.
   Provider acceptance alone is not verified inbox delivery. There is no runnable
   cloud acceptance command or operational HTTP endpoint to invent here.

Keep SEC237 and SEC243 open for this unfinished Dev work. SEC180 owns later
assembled release qualification, not a substitute for the above implementation.
The Android-first plan and ATCR148 physical-only deferral remain unchanged; no
mobile or physical-device blocker prevents the independent source work.
