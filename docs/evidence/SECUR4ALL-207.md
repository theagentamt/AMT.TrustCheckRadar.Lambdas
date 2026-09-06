# SECUR4ALL-207 Lambda Evidence

Status: **Partially implemented; completion blocked by deployed access contract**

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
  centroid/count recomputation from surviving contributions.
- Cluster-side tombstone check prevents queued work from resurrecting a deleted
  contribution.
- Content-free lifecycle/deletion logs and environment/version validation.

Reproduce:

```bash
python3 -m pytest -q tests/campaign_lifecycle tests/campaign_deletion_bridge tests/campaign_cluster_aggregator
./scripts/build_lambda_zip.sh --function campaign_lifecycle --skip-dependencies
./scripts/build_lambda_zip.sh --function campaign_deletion_bridge --skip-dependencies
```

Completion blockers proven against the current infrastructure contract:

1. `CampaignPipeline` has only contributor and candidate GSIs. It has no sparse
   expiration index, while the lifecycle role has no `dynamodb:Scan`. Therefore the
   hourly `expire_transient` invocation cannot discover arbitrary expired
   observation, feature, and dedupe items for explicit deletion. The handler fails
   this operation rather than falsely treating eventually consistent TTL cleanup as
   the required 24-hour evidence.
2. The foundation deletion ledger defines only `PK`/`SK`; no authoritative command
   schema has been handed off. The bridge parser is isolated and strict, but cannot
   be certified against the producer until that contract exists.

Because those are external access/schema inputs and the user restricted this work
to Lambda code, this evidence does not claim the whole story is complete.
