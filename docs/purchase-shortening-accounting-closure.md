# Verified purchase access shortening — inactive source increment

This change closes two accounting cases without resetting a funded period or
extending the approved latest verified access end plus seven-day deadline. It
adds no runtime, activation, provider call, retained identity or policy duration.

A freshly verified future deletion deadline may shorten while checks are pending.
The exact local and purchase-linked global counters are preserved in the writer's
transaction. An admission's copied access end is historical evidence; the current
verified PERIOD/global deadline controls settlement and recovery. It cannot be
used to prolong retention. Complete results alone charge, and existing settlement
idempotency remains unchanged.

When the new verified global deadline is already due and reservations remain,
`due_reservation_actions` strongly reads the account CHECK partition (at most eight
100-item pages), accepts at most 16 matching pending receipts, and requires their
count to equal both validated local and global reservations. It refuses unknown
matching receipt shapes, incomplete traversal, missing global evidence or counter
mismatches. Its actions are composed into the existing writer transaction:

- delete the observed global row, guarded by its observed fields;
- settle each unexpired matching receipt as failed with zero charge, retaining its
  original deadline and only the existing usage projection; delete an already
  expired receipt instead;
- decrement the observed account INFLIGHT count only by this period's count;
- write the observed local PERIOD with zero reservations and the verified access
  end, and update the paid source as inactive under the existing account,
  ownership, source and operation-identity fences.

No intermediate release is performed. Concurrent settlement, reservation changes,
receipt execution changes or account deletion invalidate transaction conditions.
A lost successful transaction response is reconciled through the existing exact
writer operation receipt. A late result cannot charge an already closed check.
The entire writer rejects more than 100 transaction actions before dispatch.
These are engineering bounds: exhaustion requires explicit reconciliation, never
truncation or a claim that cleanup completed. Expired/missing historical global
rows with unresolved reservations remain fail-closed; existing lease recovery may
release expired local reservations without reconstructing global accounting.

An old account's strictly validated zero-reservation PERIOD can now be deleted
under its durable deletion fence using only the guarded local row. A newer owner
may already have shortened, advanced or expired the global ledger; deleting old
local state neither reads nor changes that ledger. A nonzero local reservation
still requires matching pending receipt releases in the same deletion batch;
missing receipts cannot be ignored.

Runtime wiring must grant the lifecycle writer strongly consistent account
`Query`, and transactional global/receipt `DeleteItem` and account receipt/
INFLIGHT/PERIOD `PutItem` in addition to existing authority permissions. The
recovery usage contract must be packaged for the recovery receipt projection.
This does not authorize widening ordinary user handoff IAM or enabling workers.
The separate initial background ownership-claim writer is owned by the lifecycle
implementation and is outside this slice.

## Local validation

Synthetic SDK/Moto tests cover future shortening with complete/partial/failed
settlement, duplicate results, lease recovery, due zero-charge closure and writer
replay, lost commit responses, bounded multi-page/multi-reservation closure,
unknown/missing evidence, query exhaustion, expired receipt deletion, supported
URL/message/recovery projections, receipt/global/local/INFLIGHT/deletion races,
and old zero-reservation local erasure while a newer global ledger advances.
Existing purchase usage, access-window, writer and deletion suites are included.
Validation in this isolated worktree:

- `AMT_AUTHORITY_INTEGRATION=1 .../python -m pytest -q tests/shared_check_authority`:
  **335 passed**, including 23 new shortening cases.
- `.../python -m pytest -q`: **1,847 passed, 232 subtests passed, 29 skipped**.
- Changed production modules compile; `git diff --check` passes.

No live cloud/provider or physical device validation is claimed. Lifecycle
reconciler integration and updated lifecycle tests belong to the source owner;
this isolated slice deliberately does not edit those concurrently owned files.
