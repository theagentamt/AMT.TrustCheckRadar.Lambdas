# SECUR4ALL-205 Lambda Evidence

Status: **No server-side feature-extraction Lambda; app contract enforced**

Product direction supersedes the former server feature extractor. Multilingual
feature extraction always occurs in the app and the server does not download,
load, or execute an embedding model.

The Lambda-owned boundary is limited to defensive contract enforcement:

- `conversation_analysis` validates the exact V1 `appFeatures` object before an
  opted-in analysis can enter the campaign outbox.
- `campaign_observation_publisher` revalidates the same object, derives the
  period-scoped contributor HMAC, stores a source-text-free transient `FEATURE`
  record, and sends an opaque `campaign.cluster.requested` envelope.
- `campaign_cluster_aggregator` revalidates the persisted feature and its routing
  metadata before candidate lookup or scoring.
- Missing, unknown, malformed, oversized, duplicate-list, unsupported-version,
  non-finite, boolean-as-number, and out-of-range values fail closed.
- Server extractor source, tests, container build, feature queue, model image,
  ECR settings, and image-publishing workflow support have been removed.

Reproduce:

```bash
python3 -m pytest -q tests/shared_campaign_contracts tests/conversation_analysis tests/campaign_observation_publisher tests/campaign_cluster_aggregator
make campaign-evidence
```

Model quality, language coverage, extractor provenance, and app runtime
performance evidence now belong to the Android implementation handoff rather
than any Lambda or server runtime.
