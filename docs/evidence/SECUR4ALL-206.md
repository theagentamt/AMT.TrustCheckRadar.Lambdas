# SECUR4ALL-206 Lambda Evidence

Status: **Lambda implementation complete**

| Requirement | Evidence |
|---|---|
| Approved weighted score | Unit test proves 45% semantic, 25% lexical, 20% tactics, and 10% indicators. |
| Category conflict | Conflicting stable taxonomy buckets always score zero. |
| Match thresholds | Only scores at or above 0.82 update an existing candidate; lower results create a separate UUID candidate. |
| Bounded search | Candidate retrieval uses `CandidateBucketIndex` and a hard limit of 500. |
| Concurrency | Candidate summary replacement is conditioned on its prior version. New-candidate creation is serialized by an optimistic period/category control record, so a losing SQS delivery re-queries the winner before creating another candidate. |
| One vector per contributor | An existing contribution never updates the centroid. |
| Three submissions per contributor | A fourth submission creates only a dedupe outcome and cannot change candidate statistics. |
| Replay/deletion races | Missing, suppressed, expired, and previously clustered events are successful no-ops. |
| No automatic publication | Aggregator writes only transient `UNFINALIZED` candidates. |
| Retention | Candidates, contributions, and dedupe records receive a maximum 21-day expiry. |
| Queue isolation | Strict five-field schema and environment checks; handler returns partial batch failures. |
| Persisted input contract | The app-provided feature and routing metadata are revalidated before candidate lookup or scoring. |
| Thresholded dimensions | Contribution records carry language and bounded tactic/channel signals; finalization includes a dimension only after ten distinct contributions support it. |

Reproduce:

```bash
python3 -m pytest -q tests/campaign_cluster_aggregator
./scripts/build_lambda_zip.sh --function campaign_cluster_aggregator --skip-dependencies
```

The canonical package supplies bilingual fixtures. Android still owns extractor
runtime adoption and the measured 95% precision/80% English-Spanish recall gate.
