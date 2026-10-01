# Campaign deletion pagination candidate

Historical first increment: contributor traversal and repair-fence retention are
now superseded by [the strong locator candidate](campaign-contributor-locators-candidate.md).
The current candidate has no eventual-index cleanup fallback. The incomplete
coverage/activation warnings below remain relevant.

This source increment improves bounded contributor cleanup and queued-writer
fencing. It does **not** prove complete campaign erasure, activate campaign
processing, or complete the account/privacy stories. Both campaign completion
helpers now fail closed: no CAMPAIGN receipt and no withdrawal-completed state
can be written by this candidate.

## Implemented behavior

- Publisher pipeline writes and all aggregator mutation paths (new candidate,
  repeat contribution, capped dedupe) condition the transaction on absence of the
  exact contributor-period tombstone. A prior read alone did not close this race.
- Request-time contributor periods remain fixed across retries/rollover. Missing
  or retired period keys are a coverage failure, never skipped as proof of absence.
- Cleanup first validates the exact durable account/withdrawal command. Every
  cleanup transaction rechecks that command atomically. Exact completed-fence
  replay is a no-op; malformed or changed commands do not authorize cleanup.
- Contributor index traversal persists one bounded target before deletion and
  advances its cursor only after paired event siblings or candidate repair are
  handled. Deletes and the next phase are one transaction, so lost responses do
  not lose the repair target. Each invocation processes at most ten steps per
  eligible period (maximum two periods), with a remaining-time stop boundary.
- Withdrawal followed by account deletion can adopt the same contributor repair
  without discarding its pending recomputation. Concurrent workers use revision
  conditions; collisions retry rather than silently overwriting progress.
- Recompute reads 25 contributions per strongly consistent base-table page. Its
  bounded sums/counters/cursor live in `CANDIDATE#... / DELETION_RECOMPUTE`, capped
  to the candidate's existing transient deadline. Summary-version changes reset
  the pass. The centroid/counts update only after all pages, under that version
  fence. An empty candidate removes its summary; contributions without any usable
  vector fail closed instead of indexing an empty vector list.
- Tombstone progress contains operation/cursor/target metadata, never the account
  identifier or submitted content. It keeps the existing tombstone deadline;
  retries never extend it. Checkpoint loss/expiry cannot imply completion.
- A finished eventual-index pass returns `complete:false, coverage:UNVERIFIED`.
  The next invocation begins a new pass to discover delayed index entries. This
  repeated observation is useful cleanup, **not** authoritative absence proof.
- Known terminal component/account events are ignored. Pending/error batches
  raise a fixed error without provider exception details. The existing deletion
  mapping does not enable partial-batch responses, so returning a successful
  partial response would incorrectly acknowledge unfinished work.
- Lifecycle contribution reads now cover bounded pages; an over-limit candidate
  or truncated bucket fails before aggregate publication. Unprocessed batch
  deletes are detected. This is a safety guard, not a resumable lifecycle publisher.

## Required coverage design and remaining blockers

`ContributorPeriodIndex` is eventually consistent. Tombstone fencing prevents
new writes only from upgraded writers; it cannot prove that every preexisting
row has reached the index. No current complete, strongly consistent contributor
locator inventory exists, so the completion gate has no enabling flag.

The follow-on locator design must place exact target pointers under the
contributor-period base-table partition and create/update them in the **same
transaction** as every feature/contribution write. Event locators must cover
FEATURE and its DEDUPE/CLUSTERED siblings; candidate locators must cover the owned
contribution and required aggregate repair. All legacy writers, retries, stream
replays, restoration and lifecycle expiration must be inventoried and migrated.
Old data cannot become covered just because a new schema or flag exists.

A locator must survive until its target and required repairs are removed. Do not
use independent DynamoDB TTL expiration on locator and target as proof of paired
cleanup. Use the same approved transient deletion deadline with explicit paired
transactional cleanup and overdue-work reconciliation; no longer retention is
introduced. A logical deadline, an empty GSI, or elapsed TTL time is not physical
erasure evidence. Lost repair checkpoints need reconstruction under verified
coverage, not an assumed-success receipt.

Before activation also qualify:

1. Period-key inventory and retirement sequencing for pending requests, including
   historical periods and recovery deadlines. Current key retirement has no
   verified cleanup barrier; this patch does not extend key retention or certify
   old-key coverage.
2. Durable replay/reconciliation beyond the stream's finite record lifetime,
   pending-age/poison monitoring, command and candidate concurrency, and recovery
   after checkpoint expiry. Repeated stream errors alone are not that mechanism.
3. Candidate metadata beyond centroid/counts (including lexical/signal unions)
   and concurrent lifecycle publication/expiration. The recompute pass repairs
   observed counts; it is not a transactionally frozen snapshot or proof that all
   removed contributor influence is gone.
4. Lifecycle aggregate publication recovery: if aggregate Put succeeds and a
   subsequent cleanup batch fails, the existing conditional Put blocks replay
   before deletion can resume. This finalizer must remain inactive until an
   exact matching published aggregate or durable publication phase permits safe
   cleanup retry. No lifecycle resumability claim is made by this increment.
5. Receipt inventory and finalizer proof, backup/restoration suppression, the
   required CAMPAIGN_OUTBOX component and exact deadline acceptance.

## Infrastructure handoff

Do not deploy these artifacts against the previous IAM unchanged. Required
permissions remain table/key scoped and source disabled until reviewed:

- Publisher/cluster: pipeline `ConditionCheckItem` for contributor tombstones,
  restricted to transactions.
- Deletion bridge: pipeline `PutItem` and `ConditionCheckItem` for bounded repair
  checkpoints and summary guards, alongside existing Get/Query/Update/Delete;
  ledger GetItem and transaction ConditionCheckItem for the exact account command.
- No inventory writer, new retention, account mapping, public route or activation
  is introduced. Completion write permissions should be removed from the bridge
  while the completion gate is closed.

The fixed pending error works with the existing non-partial deletion stream
mapping. Any later partial-batch/reconciliation change needs its own tested
infrastructure/source contract. Source, deployment and activation are distinct.

## Local validation

Python 3.14, actual boto3 SDK with isolated Moto (no real AWS or KMS calls):
`AMT_AUTHORITY_INTEGRATION=1 PYTHONPATH=src /tmp/amt-check-authority-venv/bin/python -m pytest tests/campaign_deletion_bridge/test_progress_dynamodb.py -q`.
Thirteen tests cover multi-invocation traversal, three-page recomputation with no
partial centroid, lost delete acknowledgment, version restart, delayed index
rows, missing keys/expired state, time budget, ownership mismatch, terminal
replay, atomic tombstone rejection, command changes and overlapping operations.

Separate ordinary suites: deletion bridge 6 tests, cluster aggregator 15,
observation publisher 23, lifecycle 9. Compileall and diff whitespace checks.
These checks do not qualify real AWS IAM, KMS retirement, streams, Cognito,
legacy inventory, backlog convergence, deletion deadlines or physical backups.

Integrated on release-V01 with Python 3.14: full ordinary suite 1,757 passed,
233 subtests and 17 intentional isolated integration skips; the 13 SDK/Moto
progress cases passed independently again. Completion errors live in a pure
module so the fail-closed helpers do not initialize AWS dependencies. Four
campaign source ZIPs build; no native runtime/deployment qualification is claimed.
