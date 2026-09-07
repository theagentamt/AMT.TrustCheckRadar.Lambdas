# SECUR4ALL-216 Lambda Evidence

Status: **Lambda implementation complete; infrastructure activation pending**

This evidence covers only the conversation-analysis and observation-publisher
Lambda boundary. It does not claim creation or activation of AWS infrastructure.

| Requirement | Implementation evidence | Automated evidence |
|---|---|---|
| One outbox record for an opted-in successful analysis | The request-completion transaction contains one conditional `OBSERVATION_READY` put alongside request completion, entitlement consumption, and the consumption record. | `test_opted_in_analysis_atomically_writes_campaign_outbox` |
| Retry-stable UUIDv4 | The service generates the UUIDv4 before `store_result`, persists it with `RESULT_READY`, and passes the same value to the authoritative commit. Recovery reads that stored value and never generates another. | `test_opted_in_success_persists_uuid4_and_reuses_it_in_atomic_commit`, `test_result_ready_is_replayed_for_atomic_commit_recovery`, `test_opted_in_result_ready_recovery_reuses_persisted_event_id` |
| Fail closed without persisted identity | An opted-in commit rejects missing, malformed, or non-v4 event IDs before calling DynamoDB. The commit layer cannot silently mint a replacement during recovery. | `test_opted_in_commit_requires_the_persisted_uuid4` |
| Strict app feature contract | Opt-in requires the exact V1 `appFeatures` object. Shared validation enforces version, identifiers, 1–384 finite bounded vector values, bounded unique fingerprint/signal/indicator lists, confidence, and a 32 KiB canonical encoding cap. | Shared contract tests plus `test_opt_in_requires_app_features` and `test_rejects_malformed_app_features` |
| Server-authoritative participation | The app boolean is intent only. Publishing requires a stored `enrolled` state and retry-stable authorization provenance. The same transaction condition-checks state, version, and consent epoch before writing the outbox. | `test_app_intent_without_server_enrollment_does_not_write_outbox`, `test_changed_consent_epoch_does_not_publish_recovered_result`, `test_opted_in_analysis_atomically_writes_campaign_outbox` |
| Strict sanitized allowlist | The producer constructs the record from named fields only. Injected image, raw-OCR, and unknown payload fields do not cross the boundary; the publisher rejects missing, extra, prohibited, directly identifying, or malformed app-feature content. | Exact-field assertion in `test_opted_in_analysis_atomically_writes_campaign_outbox`; publisher contract tests |
| Bounded retention | Producer expiry is capped at 72 hours regardless of a larger configured value. | Expiry assertion in `test_opted_in_analysis_atomically_writes_campaign_outbox`; publisher expiry contract tests |
| No server extraction | The publisher revalidates app features, stores `FEATURE` directly without source text/account identity, and sends `campaign.cluster.requested` to `CLUSTER_QUEUE_URL`. No server extractor source, image, queue hop, ECR configuration, or publishing support remains. | `test_publishes_pseudonymous_app_feature_and_cluster_message`, `test_publisher_feature_record_satisfies_cluster_input_contract`, campaign evidence absence check |
| Clustering fail-closed boundary | Persisted feature routing and the full app-feature contract are revalidated before any candidate query or scoring. | `test_rejects_malformed_persisted_features_before_scoring` |
| Content-free telemetry | Analysis, publisher, and cluster log templates exclude account identity, app features, vectors, fingerprints, indicators, and sanitized text. | Handler log assertions and campaign evidence static checks |
| Safe failure recovery | Failure before `RESULT_READY` releases only the owned lease. Failure after storage leaves `RESULT_READY`; a retry skips model processing and retries only the atomic commit with the stored event ID. | `test_model_failure_releases_only_the_owned_processing_lease`, `test_commit_failure_keeps_result_ready_for_safe_replay`, `test_opted_in_result_ready_recovery_reuses_persisted_event_id` |

Reproduce:

```bash
python3 -m pytest -q tests/shared_campaign_contracts tests/campaign_participation tests/conversation_analysis tests/campaign_observation_publisher tests/campaign_cluster_aggregator
python3 -m compileall -q src tests
./scripts/build_lambda_zip.sh --function conversation_analysis --skip-dependencies
unzip -t dist/conversation_analysis.zip
unzip -Z1 dist/conversation_analysis.zip | grep -Fx app.py
shasum -a 256 dist/conversation_analysis.zip
```

After CI produces the immutable package, the Lambda release may be published to
the dev artifact bucket. Enabling the DynamoDB stream source or creating campaign
resources remains owned by the infrastructure handoff.
