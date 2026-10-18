# SEC237 trusted receipt capture and paid closure

This bounded source slice completes a trusted, conditional capture boundary for
check facts and repairs typed-metadata compatibility in paid-reservation closure.
The capture is stored on the **same owning authority receipt**, not in a second
account locator, table or durable identity map. It adds no endpoint, handler,
worker authentication adapter, notification, schedule, permission or resource.
The producer remains default off. SEC237 remains In Progress; merged component
source and local tests are not deployment or full operations acceptance.

## Actual source and deletion fences

`ReceiptIngestion.capture` takes only the existing server-created
`TrustedWorkerContext` and check proof. It never accepts caller-supplied rows,
events, account partitions or snapshots as authority. It derives the account
partition from the retained HMAC key and verifies the proof against the actual
strongly read receipt. Environment, record type, policy, identity, state,
accounting and fixed operations deadline are validated. Historical capture does
not require a currently spendable grant and cannot create one.

Inventory, receipt, ACCESS, profile and deletion-ledger reads require successful
SDK response metadata. One atomic transaction checks the complete verified key
inventory, the observed ACCESS generation in ACTIVE/INACTIVE/REVOKED state,
active/age-verified matching profile, deletion-marker absence, exact receipt
source fields and prior projection presence/value. Missing source/profile/ACCESS
or any deletion marker refuses capture. No upsert can recreate a deleted receipt.
The typed context is an internal capability, not an IAM or HTTP authentication
proof; a future actual worker adapter must authenticate it and keep this method
unreachable from client JSON.

`operationsProjection` contains only the closed content-free event schema,
source state, coverage and the original opaque reference metadata. A strict
validator rejects free-text extensions and malformed/contradictory accounting.
It is schema validation, not provenance authentication. All captured facts are
derived again from the actual receipt before the transaction. The pure
in-process projection helper remains insufficient as an ingestion credential.
No account/device/client/check/receipt identity, content, payload hash, execution
proof, SDK body or provider response is copied into this captured projection.

An admitted capture advances to the authoritative terminal facts, preserving the
original accepted fact and random identities. An already captured terminal event
cannot be replaced by conflicting or accepted-only facts. Unknown terminal cause
continues to disclose `TERMINAL_REASON_NOT_RECORDED`, not a fabricated technical
cause, healthy zero or confirmed deduction. The stored capture may lag a later
settlement until the next deliberate capture; it is not an independently current
read API. The aggregate producer-coverage marker remains `not_qualified`.

Replay revalidates the source, deletion and expiry fences in a transaction even
when no field update is needed. Competing first captures, delayed accepted facts,
metadata retirement, terminal settlement, profile revocation, inventory drift
and account deletion cannot silently overwrite another observed generation.
There is one SDK write attempt; failed conditions are conflicts, while malformed,
mixed, transport or lost write acknowledgments remain unknown. No automatic
retry or speculative success reconciliation occurs. A deliberate later capture
can reconcile an actual committed write, still subject to all fences.

## Unextended privacy deadline and deletion

Both the original metadata and its captured copy use the already approved
operations deadline: no later than acceptance plus seven days, and no later than
the original receipt deadline. Capture, replay and enrichment do not renew it.
A trusted clock is checked before and after SDK dispatch; crossing expiry never
returns facts. The transaction compares the fixed deadline against dispatch
time. This is not a DynamoDB server-clock or in-flight physical-deletion SLA.

Capture preserves a pending receipt's canonical lease-recovery index. For a
settled receipt it accepts only the known earlier-deadline or original full-
expiry index shapes and atomically repairs a seen older full-only index to the
earlier operations deadline. Full TTL and History remain unchanged. This is a
bounded repair of a seen row, not discovery, scan or migration of all older rows.

Selective retirement now removes metadata, terminal reason and projection
atomically. An orphan projection left by an older worker is erasable using its
strict validated captured deadline; no missing metadata is rebuilt from it.
The known older full-expiry index shape is accepted for direct erasure. Existing
account-partition deletion erases the receipt and its projection together, with
its retained-key inventory and accounting release fences unchanged. A restored
stale receipt/profile/ACCESS cannot bypass an existing deletion-ledger marker.
No new operations tombstone or longer-lived identity retention is introduced.

Mixed old workers can still leave an orphan indexed at full History expiry;
unseen older-row/index coverage and deployment-version coordination must be
qualified before activation. Backup/restore exclusion, deletion enforcement and
actual sweep timeliness remain deployment/acceptance prerequisites. Local
restoration/race fixtures do not prove deployed backup safety.

## Paid closure compatibility with original accounting

The prior increment documented that paid closure rejected all new metadata.
`due_reservation_actions` now accepts only the complete, typed governed-History
admission group and validated operations metadata/projection. Unknown fields,
partial groups, wrong source/access class, future acceptance, contradictory
reference metadata and unexpired orphan projections still fail closed.

The existing caller-owned transaction continues to bind the local period,
verified global usage, inflight count, every exact pending receipt, account and
deletion fences. A first projection inserted after the closure read prevents
that older snapshot from replacing the receipt. Already-expired full receipts
are deleted without new retention. For a surviving receipt, expired operations
fields are erased; unexpired references keep their original deadline and queue.
Closure records failed/zero with `OPERATION_EXPIRED` because the authoritative
paid authorization/accounting window has expired. The worker lease can still be
future in that case; this is **not evidence that its lease elapsed**, a provider
failure, cancellation cause or new funding decision. No result narrative or
History projection is reconstructed from a failed closure.

Used checks are never refunded or reset. Other owners' funded periods remain
isolated. Late completion, replay and lost-acknowledgment reconciliation cannot
charge a closed reservation again. All other original purchase-usage statements
and existing entitlement/auth/ownership/diagnostic/publisher sources remain
protected. Eight legacy comparisons cover four projection scopes with both expired and
still-retained receipts, matching actual closure action lists exactly against
pinned release. The old two metadata-rejection tests are
explicitly updated to prove accepted typed planning without mutation; unknown-
field rejection and actual transactional/accounting tests remain present.

## Validation and next prerequisites

The companion JSON records the frozen combined tests, focused and independent
adversarial checks, exact source/review hashes and inspection archive pins.
Actual local Moto transactions cover concurrency, replay, owner isolation,
closure preservation, deletion/restoration fencing, uncertainty and exact
expiry. Existing consumer, governed History, operations reducer/revision/store,
script and billing preservation suites are included. No actual AWS, application
endpoint, provider, emulator, authentication, purchase or runtime action ran.
Skip-dependency archives and network-blocked host imports remain source/package
inspection, not deployable native ARM64 or actual cloud qualification.

No reporting scope is widened. Remaining source work includes complete trusted
component attempt/finish/cost producers, bounded discovery/reconciliation and
aggregate/correction consumption, plus alert/delivery state machines. To deploy
or activate this slice, qualify the actual authenticated worker/caller wiring,
coordinated authority/capture/cleanup versions and all older rows, package/runtime
imports, existing table/index/transaction IAM, approved execution/storage cost,
physical deletion and backups/restores. Then SEC243 must prove actual retained
storage, cleanup, report/alarm/recovery/failure and delivery. No new permission,
policy, retention grace or monetary budget is inferred. SEC180 and physical-only
ATCR148 keep their separate release/testing scope; SEC237 is not Done.
