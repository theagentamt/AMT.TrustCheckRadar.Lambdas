# Authoritative access snapshot integrity

SECUR4ALL-230 / Android ATCR-92, September 22, 2026. This increment hardens the existing modern access snapshot and trial eligibility readers. It does not recreate the retired FREE/PRO/research-bonus service or add a paid-store/operator adapter.

## Implemented behavior

`GET /v1/access` and `POST /v1/access/trial` retain schema version 1 and the existing error codes. The canonical schema and fixture bytes in `contracts/v1-access/v1` are unchanged. New reads use existing strongly consistent `GetItem` permissions; no new table, secret, retained field, permission or provider call is introduced.

The service detects observed device, authority or trial-history changes across a snapshot read and returns the existing retryable `AUTHORITY_SNAPSHOT_CHANGED` conflict. A snapshot that crosses the grant/period deadline is also rejected. This is bounded consistency checking, not a claim that a snapshot authorizes later work: every admission still repeats its atomic account/device/grant/counter checks.

Retained trial eligibility must describe an explicit, nonfuture activation under the approved policy. Contradictory activation clocks across retained HMAC namespaces, unexpected record fields, invalid numeric types, missing eligibility history for an existing trial source, or an inconsistent seven-day trial period fail closed. Identical retained historical activation evidence remains reusable without granting a new trial or extending its clock. Corrupt rows are preserved for reconciliation, never normalized or deleted by this reader. The same validation protects explicit activation retries.

Retained ACCESS and source states must match the closed forms emitted by the trusted writers. Unknown states/fields, invalid numeric metadata or inconsistent active projections fail before refresh or trial mutation; they are preserved for reconciliation. Valid inactive paid subscriptions without a funded period remain supported.

Counter corruption (`completed + reserved > limit`) no longer looks like ordinary exhaustion. The shared authority rejects it before new admission or snapshot display. Valid usage, original-period settlement, renewals, complete-only charging and complimentary precedence are unchanged.

## Mobile interpretation

- `periodEndsAtEpoch` is the allowance PERIOD row's end, not a promised renewal/reset and not a separate grant validity timestamp. Display “Current allowance period ends.” Paid period dates require verified store authority; do not invent a calendar or 30-day boundary.
- Paid limit is 200; trial limit is 10. Counters are nonnegative, `completedUsed + reserved <= limit`, and `remaining = limit - completedUsed - reserved`. Reservations reduce current availability but are not completed charges.
- A trial-basis snapshot has retained explicit activation, expiry exactly 604800 seconds later, and allowance period end equal to that expiry. Trial dates can remain after access expires; they do not create access or permit reactivation.
- `basis=none` does not identify why paid access ended. Use neutral unavailable wording; do not infer paid expiry, cancellation, refund or store status from this schema.
- Complimentary snapshots have null allowance fields. Account/device and operational limits still apply. `externalChecksAllowed` describes current entitlement eligibility, separately from endpoint/provider availability.
- A changed snapshot or lost activation response does not prove trial activation failed. Reconcile with GET or the same explicit activation; do not silently activate a trial or switch/register a device.

## Source and deployment boundary

The already completed nine-function Dev research/legacy runtime migration is separate. Modern authority consumers and their qualified activation controls are not made ready merely by installing the retirement handlers. The deployed modern URL/access roots still need their own immutable same-source selection, protected inventory, scheduling/deletion, JWT/device and authenticated acceptance.

Changed shared modules are packaged in `url_consumer`, `url_lease_recovery`, `v1_entitlements`, `v1_authority_deletion`, `message_consumer`, `recovery_consumer`, `result_feedback` and `account_export_api`. The first four must retain the infrastructure root's same-source pin. Their wire contracts remain compatible; no unrelated runtime is automatically selected or enabled.

Actual Google Play lifecycle verification, immutable funded-period data, ownership reconciliation and verified restoration remain separate SECUR4ALL-125/195 dependencies. The typed paid writer interface is not that provider adapter. Complimentary administration still requires an audited server-derived operator entrypoint. No positive legacy balance or research participation supplies authority.

No AWS mutation, paid/provider request or feature activation is performed by the local source tests. The overall SECUR4ALL-230 acceptance still includes real provider budgets, all protected endpoint fences, verified writers, original-period accounting and recovery under authenticated deployment conditions. Emulator fixtures are not live store, physical-device or customer acceptance.

## Local validation

- Real SDK/Moto authority suite: 263 passed, including 39 snapshot/history/device/counter/source preservation regressions.
- Ordinary repository suite: 1,769 passed, 232 subtests passed, 23 explicitly skipped integration groups.
- All eight affected full-dependency Python 3.14/arm64 archives built; their packaged source imported in isolated local processes using host-compatible cryptography/rpds wheels because Linux native wheels cannot load on macOS; the four modern root handlers returned their disabled responses before any runtime authority/provider operation. Local imports do not replace Linux runtime or authenticated deployment qualification.
- Compile and whitespace checks passed. The four schema/fixture hashes are pinned in `docs/evidence/v1-access-contract-pin.json`; no public schema bytes changed.
