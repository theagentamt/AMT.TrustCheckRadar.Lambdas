# Unwired stable campaign completion (SECUR4ALL-207)

`campaign_deletion_bridge/completion.py` is an internal candidate. Neither a
handler, environment variable nor the existing hard-disabled completion helpers
can call it. It contains no approval writer. No marker, backfill, data mutation,
publication or deployment was performed in the environment for this increment.

The earlier read-only coverage assessor remains diagnostic and always reports
receiptEligible=false. This candidate never accepts its counts, a caller's
qualified boolean, GSI emptiness or a completed traversal cursor as erasure proof.

## Separate completion inventory

A new, explicitly versioned metadata-only marker is required in the deletion
ledger at PK `INVENTORY#<environment>`, SK `CAMPAIGN_COMPLETION_INVENTORY`.
Its exact fields are PK, SK, recordType=CAMPAIGN_COMPLETION_INVENTORY,
schemaVersion=1, environment, revision, coverage=VERIFIED_COMPLETE,
manifestSha256, approvedAtEpoch, locatorManifestSha256,
locatorInventoryRevision, recoveryManifestSha256, recoveryInventoryRevision,
and invariants. The invariants list is exactly, in order:

- fenced_writers
- legacy_locators
- orphan_repairs
- publication_anonymity
- restore_non_resurrection
- prior_period_erasure

The constructor requires independently reviewed completion manifest/revision
pins, plus exact locator and recovery manifest/revision pins. The new marker must
link those same observed markers. Every marker approval must strictly precede
the original command and no timestamp may be in the future. Every whole marker
is checked again in the completion transaction. The published locator/recovery
marker schemas remain unchanged. Existing approval is not automatically upgraded;
older pending commands remain blocked pending separately reviewed requalification,
never an edited original command timestamp.

These fields name required evidence; writing them would not manufacture that
evidence. Qualification must prove all historical/current writers use atomic
locator creation and tombstone absence, all account-linked transient records
have complete locators, orphan candidate repair is absent or resolved, publication
retains only approved non-linkable thresholded aggregates, restore cannot resurrect
untracked state, and periods below minimumPeriodId are erased. It must also prove
recovery sidecar/control counts and all backfills. Those actual environment and
historical facts remain unqualified. Synthetic test markers explicitly assume them
so the mechanical proof/transaction can be tested; they are not operational approval.

## Stable retained-period proof

For every period from the pinned minimumPeriodId through the immutable request
period, the candidate requires an exact ENABLED registry record and bounded KMS
HMAC derivation with exact account/region/key response identity. Missing, retired,
unavailable or malformed keys fail; age, a retirement timestamp or TTL does not
substitute for proof.

The corresponding strong-read contributor partition must contain exactly its
validated persistent tombstone. A strongly consistent base-table Query with
Limit2 must return only that same tombstone, with no LastEvaluatedKey. Any locator,
unknown row, pending repair, non-null repair cursor or different repair operation
fails. The tombstone is checked again in the final transaction with the exact
period key. Under the separately qualified invariants, every possible producer
must condition its insertion on tombstone absence, so it cannot insert behind
this proof. An arbitrary privileged writer ignoring the fence invalidates the
inventory; this is not a DynamoDB transactional range-lock claim.

The range defaults to eight periods, hard maximum32. All SDK/KMS calls and the
final transaction require at least six seconds remaining before dispatch. There
is no remote hard deadline guarantee after dispatch. The transaction must fit
DynamoDB's100-action bound. No proof state or token is newly persisted. Existing
tombstones, their deadlines and the read-only assessor are unchanged.

## Atomic consumption and replay

Every path matches the exact pending command, recovery sidecar/control, live owner
profile and proof set. An OPEN1 control additionally requires a strong account
sidecar-prefix Query Limit2 to return only the exact own sidecar/no continuation;
a contradictory count cannot hide another job. Qualified sidecar producers
serialize any later insertion by changing that same control revision.

Account completion requires DELETION_REQUESTED with the exact deletion operation
and OPEN pendingJobs1. One transaction removes the own sidecar, increments the
control revision and seals pendingJobs0, and writes the existing exact CAMPAIGN
component receipt with its120-day policy. The account command stays REQUESTED;
this never claims whole-account completion. Older withdrawal jobs prevent sealing.
The existing finalizer can later consume SEALED0 with its independently required
full-component/identity proof. No sidecar is removed in an earlier transaction.

Withdrawal completion requires the exact current withdrawal_pending consent epoch,
operation/revision and immutable requested/deadline times. It atomically marks
that command COMPLETE, consumes its sidecar, decrements the control (deleting the
OPEN zero-job control), updates only that consent state to withdrawn and appends
one completion audit. A pending account deletion may coexist under its exact
profile/fence guard. A newer or malformed consent epoch is preserved and blocked,
not overwritten or treated as already cleaned.

The completion audit reuses the existing400-day event shape accepted by
account_data_api's CONSENT_COMPLETION_FIELDS and _validate_consent_audit. The
collision-safe append-only key is
`CAMPAIGN_CONSENT#<epoch>#<completedAt>#<operation>#COMPLETED`; the suffix avoids a
same-second collision with the original withdrawal request audit. Existing export
prefix/projection logic accepts this event and does not export the key or IDs.
Original request/audit retention is never refreshed. Positive withdrawal replay first validates the current pinned completion and
linked inventories, requires its own job absent and its exact unexpired completion
audit; unrelated remaining jobs may coexist. Component receipt/seal replay also
requires current qualified markers. A full-account terminal fence alone produces
only terminalAcknowledged=true with campaignComplete=false, including after
receipt retirement; legacy terminal state is not new erasure proof. Replay
performs no new write and does not recreate a retired receipt,
update a later consent epoch or extend a previous completion audit. Lost response
or concurrent completion can resolve only from exact durable terminal evidence.

## Future IAM and activation dependencies

No new completion permission is granted by this source. A separately reviewed
future caller would require: ledger GetItem on ACCOUNT#* and INVENTORY#env; strong
base-ledger Query for the account's CAMPAIGN_RECOVERY# prefix; pipeline GetItem,
strong base Query for derived CONTRIB# partitions and GenerateMac under existing
period-key restrictions; users GetItem for the exact owner profile/participation.
The transaction requires check-only exact pipeline inventory/PERIOD#/CONTRIB#
proofs, ledger inventory/account proof and users profile proof; ledger transactional
PutItem for the existing receipt/terminal withdrawal, DeleteItem for own job/zero
control, UpdateItem for count/seal; users transactional PutItem for exact
participation and append-only completion audit. Runtime, IAM, schedule and provider
activation are explicitly outside this increment.

Remaining full207 acceptance includes actual reviewed inventory/restore/publication
and retired-key evidence, candidate deployment and bounded retry/monitoring tests,
then deliberate handler integration and activation. No new retention policy,
physical-device requirement, or unrelated Play checkpoint approval is introduced.

## Validation for this source increment

The final isolated real-SDK/Moto run passed217 tests:46 new completion cases plus
existing recovery, finalizer, retained-period, locator progress, coverage and
metadata repair cases. The new cases include real guarded producer rejection
during absence proof, whole-proof/control/owner/epoch races, overlapping jobs,
concurrent completion, lost acknowledgment, audit/receipt expiry, legacy terminal
recognition and the composed CAMPAIGN receipt → profile cleanup → finalizer seal
consumption. Other component proofs and completion inventories in that composition
are explicitly synthetic preconditions, not live acceptance.

The unchanged public bridge service checks passed six tests and nine subtests.
Compileall and git diff --check passed. The full local Python3.14/ARM64 bridge
archive contains20 distinct verified/compiled Python source members; its hash and
size are in campaign-completion-local-package.json. It has no extra dependency
requirements. An initial harness text search matched the word completion in an
existing comment; the corrected AST import check confirms no handler wiring.
No AWS import, upload, deployment, CI dispatch, physical-device test, customer
operation or real-provider request was performed.
