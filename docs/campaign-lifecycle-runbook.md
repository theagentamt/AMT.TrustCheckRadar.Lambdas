# Campaign Lifecycle Lambda Runbook

This runbook covers the Lambda-owned lifecycle and erasure behavior. It never
requires operators to inspect content, account IDs, contributor tokens, vectors,
or raw queue records. Infrastructure-owned alarms, schedules, IAM, backups, DLQs,
and table indexes must be verified in the environment repository and deployed
UAT evidence.

## Scheduled operations

`manage_keys` runs with `schemaVersion=1` and the exact environment. It creates
the current 14-day period HMAC key and retires an enabled key after its seven-day
recovery window. A successful result contains only created, retired, and unchanged
counts. Retry the same invocation after a transient AWS failure; all writes are
conditional and key retirement is state-checked.

`finalize_periods` runs after recovery. It re-reads every candidate and its current
contributions, suppresses cohorts below ten, emits only a thresholded aggregate,
and deletes transient candidate/contribution records. Language, tactic, and
channel values are included only when at least ten distinct contribution records
contain that value. A failed conditional aggregate write must be investigated by
state/version, not by retrieving feature content. Retry is safe after determining
whether the aggregate already exists.

`expire_transient` intentionally fails until CampaignPipeline exposes the
approved sparse expiry index and the lifecycle role can query it. DynamoDB TTL is
defense in depth and is not accepted as proof of the deletion deadline. Keep the
alarm open and do not mark SECUR4ALL-207 complete while this operation is blocked.

## Withdrawal and account deletion

The deletion bridge accepts only exact environment-bound deletion commands. It
derives active/recovery-period tokens with KMS, writes a tombstone before deleting
indexed features and contributions, removes each feature's unindexed `DEDUPE` and
`CLUSTERED` siblings, and recomputes affected candidates from survivors. A queued
clustering event sees the tombstone and cannot recreate the contribution.

A campaign withdrawal remains `withdrawal_pending` until deletion succeeds. Only
then does one transaction mark participation `withdrawn`, append the 400-day
privacy-safe completion receipt, and mark the deletion-ledger command complete.
Retry a failed stream batch normally. A matching completed command is a no-op and
does not loop.

## Privacy-safe diagnosis and repair

- Use Lambda error count, age-of-oldest-event, DLQ depth, and the low-cardinality
  result counters. Never copy queue bodies, feature records, tokens, or identities
  into tickets or chat.
- For a stuck withdrawal, use the protected ledger's operation/state metadata and
  deadline only. Re-drive the original event through the approved DLQ workflow;
  do not construct a replacement command manually.
- For a candidate version conflict, allow SQS redelivery. Candidate creation is
  serialized by the per-period/category creation-control version, so the retry
  re-queries the winner before creating another candidate.
- For a period-key failure, confirm key registry state and KMS state through the
  approved break-glass role. Never export key material or attempt to regenerate a
  retired token.
- Escalate any withdrawal approaching 24 hours or any transient record beyond its
  policy bound as a privacy incident.

## Verification

Local Lambda evidence:

```bash
python3 -m pytest -q tests/campaign_lifecycle tests/campaign_deletion_bridge tests/campaign_cluster_aggregator
make campaign-evidence
```

Environment completion additionally requires time-travel retention tests,
backup/restore non-resurrection tests, DLQ re-drive tests, alarm delivery, and an
authenticated UAT withdrawal through completion. Those checks are not simulated
or claimed by this repository.
