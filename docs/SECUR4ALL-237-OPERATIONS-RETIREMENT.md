# SEC237 selective operations-reference retirement

This source increment removes expired operations references from existing check
receipts using the bounded lease-recovery and expiry queues. It adds no resource,
subject locator, tombstone, schedule, permission or activation flag. The producer
remains default off. SEC237 and the actual deployment/operations acceptance stay
In Progress; this is source integration, not a deployed cleanup claim.

## Separate privacy and accounting deadlines

The previously approved operations deadline remains the earlier of original
receipt retention and acceptance plus seven days. A settled receipt is queued at
the earlier of that deadline and its full receipt expiry. Both the authenticated
settlement path and HMAC lease-recovery writer preserve this scheduling even
when producer creation is paused. No full TTL, History deadline or accounting
window is extended or shortened.

At the operations deadline, a conditional transaction removes only
`operationsReceipt` and `operationsTerminalReasonCode`. It leaves authoritative
outcome, accounting, History, receipt identity and other fields intact, and
reschedules a settled receipt for its original full expiry. Pending receipts keep
their recovery queue and counters. Recovery can then close the abandoned lease
through its existing zero-charge path without recreating operations metadata.
The scheduler exposes only an optional aggregate acknowledged-retirement count.

Selective erasure happens before full deletion is considered, including when
both deadlines are overdue. It permits removal while ACCESS is DELETING or
absent, conditioned on that exact observed state/absence. Full receipt deletion
still uses its existing stricter ownership rules. No account row is created and
no deletion restriction is broadened. Concurrent metadata, inventory, state,
full-expiry or settlement changes cannot rewrite another generation or restore
an erased reference. The update binds the captured metadata and index fields.

The helper requires acknowledged successful reads and writes, makes one SDK
write attempt, and returns finite failure classifications. A lost write response
followed by confirmed absence is an uncounted replay resolution, not an
acknowledged retirement. An unreadable resolution remains uncertain. Existing
scheduler continuation checks bound work and preserve the prior cursor when a
pause occurs between reference erasure and remaining accounting/full deletion.
No polling loop or new cost/cleanup timing guarantee is introduced.

## Independent findings and validation

Initial review reproduced two source gaps: full-expiry dispatch skipped selective
erasure for DELETING/missing ACCESS, and HMAC recovery queued a longer-lived
receipt only at its full expiry. Both were corrected and have actual Moto sweep
regressions. A final inventory-read counterexample also led to applying the
acknowledged-response validator to that helper's inventory path; ordinary full
expiry reads are unchanged. These are local counterexamples, not observed runtime incidents.
Tests also cover exact deadlines, longer History preservation, pending recovery,
replay, concurrent deletion/metadata/inventory/expiry/authority changes, lost or
malformed acknowledgments, malformed reads, and interrupted checkpointing.

One newly added pause fixture initially stopped at an earlier due preparation
row, before reaching its check. It was corrected to pause on the actual metadata
removal boundary; no production condition was weakened. The companion JSON
records final suite counts, inspection archive hashes and independent reviews.
Default-off behavior and existing auth/accounting/publisher preservation checks
remain in the combined local suite. Skip-dependency archives and network-blocked
host imports are source/package inspection, not deployable native ARM64 proof.

## Remaining activation dependencies

The strict paid-reservation closure path in `purchase_usage.due_reservation_actions`
still rejects operations or governed-History metadata with
`PURCHASE_USAGE_RECONCILIATION_REQUIRED`, preserving reservations and balances.
Two independent Moto counterexamples retain this explicit compatibility
obligation. This increment does not weaken that billing allowlist or claim all
writers are ready for activation. Reconcile that path before enabling these
producers.

Forward scheduling does not backfill already-settled rows from earlier code that
may have full-expiry-only index entries. Later deployment/activation must qualify
existing-row/index coverage; default-off source tests do not establish the
absence of such rows in an actual environment. No scan or migration ran here.

Trusted ingestion must still atomically fence the actual receipt, expiry and
account deletion; the in-process projection is not an attestation API. Backup,
restore and deletion enforcement, actual cleanup scheduling/timeliness, resources,
IAM, cost and Dev behavior are not qualified here. Logical expiry and successful
local physical removal do not prove a deployed sweep SLA or restored-backup
privacy. Provider attempts/finish/cost coverage, report dispatch and actual
report/alarm/recovery acceptance also remain open. No retention grace is invented.
See the earlier [producer handoff](SECUR4ALL-237-AUTHORITY-PRODUCER.md) for its
remaining coverage and [aggregate store](SECUR4ALL-237-AGGREGATE-STORE.md) for the
separate unlinkable ninety-day policy. Physical-only follow-ups remain ATCR148.

No AWS authentication/service call, application endpoint, provider, emulator,
purchase, flag, resource, deployment, permission or main promotion was performed.
