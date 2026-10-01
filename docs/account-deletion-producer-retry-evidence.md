# Account-deletion producer retry and support evidence

This map covers the 12 components required by
[`account_data_api.config.required_components`](../src/account_data_api/config.py).
It records inspected source and test boundaries, not a new live run or inventory
approval. Source and test anchors were reviewed at Lambda commit
`de60324aac7616d05207ad46351c936fc61841eb`. The Lambda owner reported the six
simple-producer cases passing, the entitlements SDK suite passing (7 tests), and
the two focused Play lost-acknowledgment cases passing. This documentation review
checked their source but did not rerun them. These results are local evidence;
this increment adds no new AWS acceptance result. Bind this map to the final
release commit and recorded test output when linking it to completion.

SDK/Moto means real boto3 serialization and DynamoDB behavior in an isolated
emulator. It does not qualify AWS IAM, actual service outages or provider delivery.
The component unit suite uses test doubles; Cognito and KMS are injected in the
local all-component runner. Separate disposable cloud/Cognito qualification has
its own evidence and must not be inferred from a local test name.

## Receipt producers and retry owners

| Required component | Producer and durable retry owner | Fault/retry evidence inspected |
|---|---|---|
| SESSION_REVOCATION | Account-data `ensure_session_revoked`; eager post-fence attempt, deletion-ledger stream and account reconciliation | A: lost response after committed receipt, later retry reuses the receipt and does not call sign-out again; unit session idempotency |
| DEVICE_BINDINGS | Account-data `delete_device_bindings`; same stream/reconciler | A: committed-receipt lost response and unchanged command/receipt; B: bounded pages and resumed cleanup |
| DEVICE_RECOVERY | Account-data `delete_device_recovery_control`; same stream/reconciler | A: committed-receipt lost response; B: bounded minimization, unknown-family rejection and retention preservation |
| ANALYSIS_ABUSE | Account-data `delete_analysis_abuse_control`; same stream/reconciler | A: committed-receipt lost response; B: bounded families, legacy/policy refusal, approved deadline shortening and content-free retained tombstones |
| CAMPAIGN_OUTBOX | Account-data `delete_campaign_outbox`; same stream/reconciler | A: committed-receipt lost response; B: bounded locator/target cleanup, cross-account rejection and coverage refusal |
| USER_PROFILE | Account-data `delete_user_profile_state`; same stream/reconciler after all prerequisite receipts | A: committed-receipt lost response; B: prerequisites and consent-evidence preservation. A seeds prerequisite receipts for this isolated producer and does not itself prove their production |
| ENTITLEMENTS | Account-data `Lifecycle.entitlements` and ownership cleanup; same stream/reconciler | C: nonempty pass cannot receipt, verified empty pass/idempotent retry, account/purchase inventory races, ownership-fence races. committed-receipt ACK loss reconciles with the original command and receipt deadline |
| V1_AUTHORITY | `AuthorityDeletion`, driven by V1 deletion stream handler and scheduled `DeletionWorker.reconcile` | D: actual SDK transaction succeeds then response is lost at fence/delete/complete stages; retry finishes without recreating rows; stale recovery, writer races, key-inventory races and partial stream/scheduled recovery |
| PLAY_TOKENS | `TokenDeletion` subclass, Play token stream handler and scheduled deletion worker | E: bounded token/mapping erasure, exact receipt/idempotent repeat, command race, broken mapping, multiple retained namespaces and other-account preservation. dedicated subclass delete/receipt ACK-loss cases retry the original operation. These explicitly cover the override rather than relying on parent V1 tests |
| HISTORY | History deletion bridge creates/receipts a job; History lifecycle performs erasure. Bridge stream + reconciliation and lifecycle schedule recover work | F: lost receipt transaction response reuses original receipt; job/state/terminal races, restored pending-job suppression, and lifecycle completion race. All-component History retry case interrupts an erasure checkpoint |
| CAMPAIGN | Campaign deletion orchestration/completion, deletion stream and indexed recovery schedule | G: lost completion ACK, exact proof races, sealed-control atomic completion, expired receipt suppression, mixed batch replay and scheduled recovery; restored locator/new proof checks |
| IDENTITY | `Finalizer.finalize`, called by account-data stream/reconciliation only after complete upstream proof | H: lost Cognito delete response stays pending then confirmed absence reconciles; lost final DynamoDB transaction response reads terminal fence; proof/expiry races prevent false completion |

Stream delivery is bounded and not the durable recovery record. The existing
fixed command, progress records and scheduled reconciliation own retries after
stream expiry. A failed batch entry is not an instruction to generate a new
account-deletion request or operation ID. HTTP POST returns the committed
acceptance snapshot; its eager cleanup attempt does not own final identity
completion.

Verified scheduled event entry points in source are:

- Account-data: `{"schemaVersion":1,"operation":"reconcile-session-revocation"}`.
- History bridge: `{"schemaVersion":1,"operation":"reconcile"}`; History lifecycle: `{"schemaVersion":1,"operation":"sweep"}`.
- Campaign: `{"schemaVersion":1,"operation":"reconcile-campaign-cleanup"}`.
- V1: `{"schemaVersion":1,"operation":"reconcile-v1-authority-deletion"}`.
- Play tokens: `{"schemaVersion":1,"operation":"reconcile-play-token-deletion"}`.

These identify installed code paths; they are not permission to invoke arbitrary
accounts or change gates, schedules, allowlists or markers.

## Exact test anchors

A. [`test_component_retry_dynamodb.py`](../tests/account_data_api/test_component_retry_dynamodb.py),
`test_receipt_committed_response_lost_retry_preserves_original_deadline`, parameterized
for the six named simple producers. The injected exception occurs **after** a
receipt `put_item` succeeds. Retry at `now+15` keeps the exact receipt, fixed
command and other-account rows; it never fabricates an IDENTITY receipt. This does
not inject a failure before the receipt, during every page/checkpoint write, or
inside the real Cognito sign-out service.

B. [`test_service.py`](../tests/account_data_api/test_service.py):
`test_device_cleanup_is_bounded_resumable_and_receipted`,
`test_recovery_cleanup_is_bounded_minimizes_security_evidence_and_receipts`,
`test_analysis_abuse_cleanup_is_bounded_content_free_and_receipted`,
`test_approved_legacy_request_is_shortened_without_extending_retention`,
`test_campaign_outbox_cleanup_rejects_cross_account_target`,
`test_user_profile_cleanup_waits_for_prerequisites_then_preserves_consent`,
`test_hard_interruption_checkpoint_skips_to_next_account_but_keeps_pass_failure`.
These are unit/test-double evidence, not live AWS failures.

C. [`test_lifecycle_dynamodb.py`](../tests/account_data_api/test_lifecycle_dynamodb.py):
`test_only_verified_empty_pass_writes_guarded_receipt`,
`test_inventory_race_prevents_receipt`,
`test_entitlements_receipt_committed_ack_loss_reconciles_without_new_deadline`.
The latter raises after the SDK receipt transaction commits; reconciliation and
retry at `now+15` retain the exact receipt and command, including the original
120-day receipt deadline. Also see
[`test_lifecycle.py`](../tests/shared_purchase_ownership/test_lifecycle.py):
`test_owned_cleanup_removes_binding_before_new_account_can_claim`,
`test_deletion_race_atomically_rejects_old_queued_claim`,
`test_unverified_legacy_coverage_blocks_empty_completion_and_export`.

D. [`test_deletion.py`](../tests/shared_check_authority/test_deletion.py):
`test_ambiguous_committed_transaction_is_safe_to_retry` at three transaction
stages, `test_global_command_race_never_reports_completion`,
`test_inventory_rotation_race_during_delete_is_conditioned`; and
[`test_deletion_worker.py`](../tests/shared_check_authority/test_deletion_worker.py):
`test_stream_partial_has_retry_and_schedule_completes`,
`test_schedule_advances_after_poison_command`,
`test_terminal_fence_and_old_requested_stream_replay_never_restart_cleanup`.

E. [`test_runtime_workers.py`](../tests/shared_play_lifecycle/test_runtime_workers.py):
`test_account_erasure_deletes_tokens_cursor_then_exact_component_receipt`,
`test_deletion_command_race_preserves_token_and_no_receipt`,
`test_mapping_pair_and_all_four_retained_namespaces_are_erased`,
`test_broken_binding_pair_preserves_account_rows_and_blocks_erasure_receipt`,
`test_token_deletion_lost_committed_ack_retries_original_operation`, parameterized
at `delete` and `receipt`. Each SDK transaction commits before the injected lost
response; the first call reports uncertainty and retry at `now+15` completes the
same operation. An already-committed receipt stays unchanged; a first receipt
uses its own occurrence time plus the existing retention period.

Related purchase-restoration evidence in
[`test_service.py`](../tests/v1_play_handoff/test_service.py) is
`test_deletion_then_restore_retains_used_releases_pending_and_supports_renewal`,
`test_unknown_deleted_account_usage_never_creates_fresh_allowance`, and
`test_new_owner_restore_waits_for_original_pending_reservation_cleanup`.
The Lambda owner reported these 3 focused tests passing on September 26, 2026.
They cover local no-reset/ownership behavior; they do not establish actual Google
Play restoration or provider verification.

F. [`test_terminal_dynamodb.py`](../tests/history_account_deletion_bridge/test_terminal_dynamodb.py):
`test_lost_ack_reads_original_receipt_without_extending_retention`,
`test_lifecycle_receipt_race_cannot_recreate_after_terminal`,
`test_restored_pending_job_with_terminal_fence_does_no_cleanup_or_receipt_write`;
also `handler_all_components_history_retry` in
[`account_cleanup.py`](../scripts/qualification/account_cleanup.py), exercised by
[`test_actual_qualification_runner_case`](../tests/campaign_deletion_bridge/test_qualification_dynamodb.py).

G. [`test_completion_dynamodb.py`](../tests/campaign_deletion_bridge/test_completion_dynamodb.py):
`test_lost_ack_recovers_exact_receipt_without_new_retention`,
`test_exact_transaction_proofs_refuse_concurrent_change`,
`test_expired_component_receipt_is_not_reissued`,
`test_restored_locator_after_completion_blocks_new_positive_replay`; and
[`test_worker_completion_dynamodb.py`](../tests/campaign_deletion_bridge/test_worker_completion_dynamodb.py):
`test_scheduled_completion_lost_ack_does_not_leave_due_work`,
`test_mixed_batch_failure_replays_completed_record_safely`.

H. [`test_finalizer.py`](../tests/shared_account_finalization/test_finalizer.py):
`test_lost_identity_delete_response_keeps_pending_then_absence_reconciles`,
`test_lost_final_transaction_acknowledgment_reads_completed_fence`,
`test_proof_race_during_provider_call_never_writes_false_completion`,
`test_component_expiry_during_identity_call_cannot_become_completion`,
`test_completed_suppression_fence_survives_informational_receipt_expiry`.
SDK/Moto DynamoDB is paired with an injected Cognito identity in this suite.

## Clocks, evidence retention and non-resurrection

Keep the original command's operation ID, request time and `deleteByEpoch`.
The public SLA remains request time plus 86,400 seconds; a retry does not restart
it. A component's first completion receipt uses the existing 120-day informational
retention policy. Reusing a valid receipt must not move its occurrence or retention
deadline. A failure, unknown family, stale inventory, changed command or incomplete
page never authorizes an invented completion receipt.

Preserve the existing component-specific exceptions: minimized recovery receipts
and audits retain their original 7/90-day boundaries; analysis dedupe/history
security evidence uses its already-approved source rules. A deletion retry is not
permission to renew them. Tests for approved legacy shortening check that the new
boundary never extends the existing deadline. The terminal suppression fence is
not removed merely because informational receipts reach their retention time;
its source deliberately has no TTL attribute. Restores are separately quarantined
and cannot be made safe solely by replaying a stale receipt.

## Safe support diagnostics and recovery limits

Use deployment/source hashes, worker names, time windows, alarm state transitions
and fixed categories/counts. Do not copy account keys/subjects, purchase tokens,
HMAC values, message/URL content, raw exception strings or arbitrary DynamoDB rows
into support evidence. Do not reinterpret an HTTP 401/503, empty body, or a missing
metric as proof that deletion either completed or failed to commit.

| Surface | Existing diagnostic signals | What they establish |
|---|---|---|
| Account-data | `AccountDeletionReconciliationCommandFailures`, `AccountDeletionReconciliationPassFailures`, `SessionRevocationReconciliationSuccess`, `SessionRevocationReconciliationFullPassCompleted`, `SessionRevocationReconciliationFullPassAgeSeconds`; analysis/outbox/profile policy-blocked counters | Command/pass progress or a fixed policy block; a successful empty scan is not account erasure |
| History | `AccountDeletionReconciliationFailure`/`Success`, full-pass metrics; `LifecycleSweepFailure`/`Success`, `OverdueErasureJobs`, `StuckPendingCompletions`, `ExpirationCheckpointLagSeconds` | Bridge and lifecycle are distinct; later successful sweeps and alarm transitions are needed to establish recovery |
| Campaign | `RecoveryFailures`, `RecoveryTicks`, `CommandsUnverified`, `SidecarSchemaFailures`, `RecoveryBudgetExhausted`, `RecoveryShardTruncated`, `ObservedOverdueCommands` | Observed work/limits, not global inventory coverage or proof that an unverified period is empty |
| V1/Play | Fixed `v1_authority_deletion` / `play_token_deletion` events with `failed`, `pending`, `completed`, `overdue`, `fullPassAgeSeconds` when supplied; existing metric filters and Lambda errors | Bounded reconciliation outcome; no provider token or subject should appear. Do not invent separate per-family failure metrics |
| Final identity/entitlements | Existing account-data command/pass/stream failure signals and fixed completion proof | No dedicated per-step latency or “Cognito deleted” metric is claimed by this map; terminal proof is required |

Operational alerts target `support@andmorethings.com`. SNS action/delivery evidence
is distinct from verified inbox receipt. Investigate a failure with the exact
artifact and safe category, then let the existing bounded worker retry the same
command. Unknown inventory/shape/restore failures require owner-reviewed repair
or qualification; support must not edit a command, fabricate a receipt, reset a
retention clock or bypass a fence to clear an alarm.

## Remaining evidence boundaries and handoff

All 12 producers now have explicit local fault/retry evidence at the stages
listed above, including dedicated ENTITLEMENTS and PLAY_TOKENS lost-acknowledgment
cases. This is bounded coverage, not exhaustive failure testing: the six A cases
cover one committed-receipt boundary each, not every pre-receipt failure,
page/checkpoint write or service outage. Provider outages, IAM denials and actual
CloudWatch notification delivery require their separate component/Dev evidence;
this document cannot upgrade local mocks into those results. No new AWS acceptance
is claimed for the additional local tests.

Run gated SDK suites with `AMT_AUTHORITY_INTEGRATION=1` in the existing isolated
boto3/Moto development environment. Without that gate they skip, which is not a
pass. For example, the verified test entry point is
`python -m pytest tests/account_data_api/test_component_retry_dynamodb.py`.
Use bounded synthetic fixtures and record the selected release source, cases run,
result counts and cleanup; do not run a fixture against application tables.

[SECUR4ALL-329](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-329) owns pending
release/UAT qualification of the assembled journey. It does not hide unfinished
Dev tests. [SECUR4ALL-245](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-245)
owns restored-environment reopening: retain quarantine, verify copied data and
writer/inventory/source bindings before independently reviewed reopening. Neither
follow-up grants deployment, marker approval, new retention or public admission.
