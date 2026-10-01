# Campaign cleanup recovery candidate (SECUR4ALL-207)

This source adds recovery discovery after DynamoDB stream expiry. Both command
producers and the scheduled consumer remain disabled by default. It does not
publish a CAMPAIGN receipt, seal a job control, approve an inventory, or claim
complete erasure. Existing retained-period cleanup still returns unverified
progress, including missing/retired selected keys. No deployment or data backfill
is part of this source increment.

## Storage and lifetime

The existing deletion ledger gains `CampaignRecoveryDueIndex`: HASH
`campaignRecoveryPartition` (String), RANGE `nextAttemptAtEpoch` (Number),
KEYS_ONLY projection. Exactly sixteen partition values are
`CAMPAIGN_RECOVERY#<environment>#00` through `#15`; UUID integer modulo 16 selects
the shard. The index copies only existing keys and due metadata, never content.

A sidecar lives at `ACCOUNT#<subject>` / `CAMPAIGN_RECOVERY#<operationUUID>`.
Its exact schema-1 fields are PK, SK, recordType=CAMPAIGN_RECOVERY, schemaVersion,
environment, operationId, commandSK, commandOccurredAtEpoch, revision,
nextAttemptAtEpoch, campaignRecoveryPartition. Command identity/time match the
owned ACCOUNT_DELETION or CAMPAIGN_WITHDRAWAL command exactly. No new accountId,
raw content, TTL, or longer retention is introduced. It exists only while that
owned cleanup remains unfinished. Due time is scheduling metadata, never a new
erasure deadline. Original deleteByEpoch remains unchanged.

The same account has `CAMPAIGN_RECOVERY_CONTROL`, exact fields PK, SK,
recordType=CAMPAIGN_RECOVERY_CONTROL, schemaVersion=1, environment, revision,
pendingJobs, state. OPEN requires a positive count; SEALED requires zero. Producers
atomically create a sidecar and create/increment this control with the original
command. A duplicate operation never increments twice. Missing control plus an
existing sidecar is refused, not reconstructed from a partial query. All writers
must participate in the same control CAS before counts become authoritative.

Qualified future withdrawal completion must remove its sidecar and decrement the
control in the same completion transaction, removing an OPEN zero-job control.
Qualified account CAMPAIGN completion must wait until its own sidecar is the last
job, then atomically delete that sidecar, change OPEN1 to SEALED0 and write the
exact CAMPAIGN receipt. Deleting the account job before recording the receipt
would strand recovery and is forbidden. Those completion builders remain
unimplemented/unwired pending full campaign cleanup proof; no runtime flag in
this increment enables them. The finalizer now requires exact SEALED0 evidence
before any identity call and deletes the control in the same terminal-fence /
IDENTITY-receipt transaction. Missing/open/malformed controls block finalization.

## Inventory and old commands

The recovery marker is at `INVENTORY#<environment>` /
`CAMPAIGN_RECOVERY_INVENTORY`. Its exact fields are PK, SK,
recordType=CAMPAIGN_RECOVERY_INVENTORY, schemaVersion=1, environment, revision,
coverage=VERIFIED_COMPLETE, manifestSha256 (64 lowercase hex), approvedAtEpoch,
legacyCoverageVerified=true, and writers in this exact order:
account_data_api, campaign_participation, campaign_recovery_backfill.

No marker writer or automatic upgrade exists. A reviewed manifest must enumerate
all current/historical command producers, frozen old artifacts, pending commands,
sidecars and controls, restore/backfill procedure, completed controls and cleanup
coverage. GSI emptiness is not inventory evidence. The reviewed full
ACCOUNT_DATA_INVENTORY schema remains unchanged, but finalization now requires a
newly reviewed manifest/pins establishing this recovery count authority. Older
approval does not automatically cover the new producer/control semantics.

`backfill_actions` only prepares a transaction; it never executes or provisions a
marker. It checks the exact still-pending command, a live exact-sub profile, any
pending account fence, receipt absence, and OPEN/absent control. The transaction
rechecks those observations. Missing/deleted owners, terminal account fences,
SEALED controls, or existing CAMPAIGN receipts are refused. Thus late backfill
cannot recreate work after finalization/receipt retirement. A restored unknown
state still needs separately reviewed restore proof; a count scan cannot bless it.

## Trigger, bounds and permissions

The existing campaign_deletion_bridge accepts exactly
`{"schemaVersion":1,"operation":"reconcile-campaign-cleanup"}`. The worker gate is
CAMPAIGN_RECOVERY_ENABLED=false; both producers use the separate
CAMPAIGN_RECOVERY_WRITES_ENABLED=false. Configuration pins are
CAMPAIGN_RECOVERY_INDEX_NAME=CampaignRecoveryDueIndex,
CAMPAIGN_RECOVERY_MANIFEST_SHA256 (empty default), and
CAMPAIGN_RECOVERY_INVENTORY_REVISION (zero default).

A scheduled tick starts at `(UTC epoch // 300) % 16`, rotates through sixteen
shards, reads at most two pages of eight GSI candidates per shard, and attempts at
most four commands. It stops before further calls below six seconds remaining.
GSI rows are discovery only: the worker strongly rereads each actual job, exact
pending command/control and recovery inventory. A guarded transaction increments
job revision and sets due time to now+60 before bounded retained-period work.
Timeout/lost acknowledgment may skip one attempt, but the job remains due later;
there is no deletion or receipt generation from a retry. Inventory, command,
control and sidecar races abort the transaction. Pending account deletion may
coexist with older withdrawal jobs; terminal account deletion is refused.

Malformed/obsolete candidates are preserved and skipped. One poisoned oldest row
cannot block a later valid row in the same shard. More than sixteen poisoned
candidates in one shard can still require operator reconciliation; the cap is
reported and does not imply full coverage. Valid attempts advance their due time,
so later due work becomes visible. This is not a full-backlog or deadline SLA
qualification. Existing SDK timeouts bound individual calls, but a remote call
can consume time after its pre-call clock check.

Producer ledger permissions: GetItem, strong base-table Query on ACCOUNT#*;
transactional PutItem (new control/job) and UpdateItem (existing control);
check-only ACCOUNT#* receipt absence. Existing owner/profile and command guards
remain. Worker permissions: Query only the exact GSI/shards, GetItem and
ConditionCheckItem on ACCOUNT#* and exact INVENTORY#environment, transactional
UpdateItem on ACCOUNT#*. Worker needs no new Put/Delete completion grants.
Finalizer reuses its account-ledger transactional Delete permission for SEALED0.
Backfill would additionally require exact owner Get/Condition and command/control
transaction grants, but no live backfill tool or role is provisioned here.

## Metrics and integration

Fixed EMF namespace TrustCheckRadar/Campaign, sole dimension Environment:
RecoveryTicks, RecoveryFailures, CommandsAttempted, CommandsUnverified,
SidecarSchemaFailures, IndexCandidatesObserved, ObservedPendingAgeSeconds,
ObservedOverdueCommands, RecoveryBudgetExhausted, RecoveryShardTruncated.
Pending age is measured from the original command time, not the moved due time;
observed age/overdue counts are lower-bound observations, never entire backlog
coverage. RecoveryTicks indicates a successful bounded tick, not erasure success.
No subject, operation ID, contributor token, or raw SDK error enters metric data.

Changed artifacts: account_data_api, campaign_participation,
campaign_deletion_bridge; finalizer dependency copies also affect
v1_authority_deletion, history_account_deletion_bridge, history_lifecycle and the
three Play lifecycle packages that include that shared module.
No package is uploaded/deployed by this increment. Root owns the separate index,
selected producer IAM preparation, disabled schedule and alarms. Activation still
requires producer/backfill coverage, reviewed inventory pins, exact package
qualification, outstanding period/key/legacy/restore proof, and implemented
atomic completion. The unrelated five-minute Play checkpoint policy remains
pending and is not used by campaign recovery.

## Local validation for this increment

Final isolated real-SDK/Moto run: 171 tests passed across the new recovery suite
(31), finalization, retained-period traversal, locator progress, coverage and
metadata reconstruction. Separately, eight actual account admission cases,
four actual withdrawal transaction cases, and 22 Play-token/privacy composition
cases passed. Those fixtures are synthetic; existing other-component receipts
and recovery seals in the composition tests are explicit test preconditions,
not newly implemented campaign completion proof.

Ordinary affected service/handler checks passed: account-data 39 tests plus
18 subtests, participation 10 tests, and campaign bridge six tests plus nine
subtests. Compileall and git diff --check passed. No GitHub CI dispatch, provider
calls, customer writes or physical-device testing were performed.

The nine archive records in campaign-recovery-local-packages.json were checked
against every distinct Python member's source bytes and compiled locally.
Campaign bridge and participation have no extra dependency requirements and
were fully packaged; the seven shared-finalizer dependency archives intentionally
omit external dependencies. These are local source/package checks, not Linux
native imports, immutable S3 publication or deployed-runtime qualification.
