# SECUR4ALL-207 Lambda Evidence

Status: **Lambda and infrastructure contract implemented; deployed UAT evidence remains**

Implemented and tested:

- Current-period `HMAC_256` key creation with the required project, environment,
  purpose, and period tags.
- A DynamoDB period-key registry consumed by the publisher and deletion bridge.
- Key disablement after the 14-day period plus seven-day recovery window and
  seven-day scheduled deletion.
- Threshold finalization based on a fresh contribution query; cohorts below 10 are
  suppressed, and qualifying output excludes contributor tokens and centroids.
- Consent-withdrawal/account-deletion token derivation only for active/recovery
  periods.
- Tombstone-before-delete, targeted `ContributorPeriodIndex` deletion, and
  feature/contribution/event-dedupe deletion plus centroid/count recomputation
  from surviving contributions.
- Cluster-side tombstone check prevents queued work from resurrecting a deleted
  contribution.
- Content-free lifecycle/deletion logs and environment/version validation.
- Exact pending campaign-withdrawal command validation, successful-deletion-only
  transition to `withdrawn`, a privacy-safe 400-day completion receipt, atomic
  ledger `COMPLETE` status, and completed-stream loop suppression.
- A privacy-safe normal/failure/manual-repair runbook in
  `docs/campaign-lifecycle-runbook.md`.
- Sparse, environment-bound expiration keys on features, publisher/cluster
  dedupe records, candidates, contributions, creation controls, and withdrawal
  tombstones.
- An hourly, index-query-only explicit expiration operation that deletes in
  bounded batches and fails on unprocessed writes; DynamoDB TTL remains a
  defense-in-depth fallback.

Reproduce:

```bash
python3 -m pytest -q tests/campaign_lifecycle tests/campaign_deletion_bridge tests/campaign_cluster_aggregator
./scripts/build_lambda_zip.sh --function campaign_lifecycle --skip-dependencies
./scripts/build_lambda_zip.sh --function campaign_deletion_bridge --skip-dependencies
```

Remaining environment evidence:

1. Backup/restore non-resurrection, DLQ re-drive, alarm delivery, and authenticated
   UAT withdrawal evidence require deployed infrastructure.
2. A deployed time-travel check must prove the hourly operation removes indexed
   records at or after their deadline without relying on DynamoDB TTL.
Because those are environment-level checks, this evidence does not claim the whole
story is complete.
