# SECUR4ALL-216 Lambda Evidence

Status: **Lambda implementation complete; infrastructure activation pending**

This evidence covers only the conversation-analysis and observation-publisher
Lambda boundary. It does not claim creation or activation of AWS infrastructure.

| Requirement | Implementation evidence | Automated evidence |
|---|---|---|
| One outbox record for an opted-in successful analysis | The request-completion transaction contains one conditional `OBSERVATION_READY` put alongside request completion, entitlement consumption, and the consumption record. | `test_opted_in_analysis_atomically_writes_campaign_outbox` |
| Retry-stable UUIDv4 | The service generates the UUIDv4 before `store_result`, persists it with `RESULT_READY`, and passes the same value to the authoritative commit. Recovery reads that stored value and never generates another. | `test_opted_in_success_persists_uuid4_and_reuses_it_in_atomic_commit`, `test_result_ready_is_replayed_for_atomic_commit_recovery`, `test_opted_in_result_ready_recovery_reuses_persisted_event_id` |
| Fail closed without persisted identity | An opted-in commit rejects missing, malformed, or non-v4 event IDs before calling DynamoDB. The commit layer cannot silently mint a replacement during recovery. | `test_opted_in_commit_requires_the_persisted_uuid4` |
| Consent withdrawal at the producer boundary | Omitted consent defaults to false, only a boolean is accepted, and a declined request completes without an outbox operation. | `test_parses_valid_payload`, `test_rejects_non_boolean_campaign_consent`, `test_declined_campaign_consent_does_not_write_outbox` |
| Strict sanitized allowlist | The producer constructs the record from named scalar fields only. Injected image, raw-OCR, and unknown payload fields do not cross the boundary; the publisher rejects missing, extra, prohibited, or directly identifying content. | Exact-field assertion in `test_opted_in_analysis_atomically_writes_campaign_outbox`; publisher contract tests `test_rejects_unknown_and_prohibited_fields` and `test_rejects_obvious_unsanitized_identifiers` |
| Bounded retention | Producer expiry is capped at 72 hours regardless of a larger configured value. | Expiry assertion in `test_opted_in_analysis_atomically_writes_campaign_outbox`; publisher expiry contract tests |
| Downstream isolation | The request path writes only the transactional outbox record. Feature extraction and clustering run from asynchronous stream/queue handlers and are not invoked by the analysis request. | Service call-boundary tests plus campaign-observation-publisher partial/retry tests |
| Safe failure recovery | Failure before `RESULT_READY` releases only the owned lease. Failure after storage leaves `RESULT_READY`; a retry skips model processing and retries only the atomic commit with the stored event ID. | `test_model_failure_releases_only_the_owned_processing_lease`, `test_commit_failure_keeps_result_ready_for_safe_replay`, `test_opted_in_result_ready_recovery_reuses_persisted_event_id` |

Reproduce:

```bash
python3 -m pytest -q tests/conversation_analysis tests/campaign_observation_publisher
python3 -m compileall -q src tests
./scripts/build_lambda_zip.sh --function conversation_analysis --skip-dependencies
unzip -t dist/conversation_analysis.zip
unzip -Z1 dist/conversation_analysis.zip | grep -Fx app.py
shasum -a 256 dist/conversation_analysis.zip
```

After CI produces the immutable package, the Lambda release may be published to
the dev artifact bucket. Enabling the DynamoDB stream source or creating campaign
resources remains owned by the infrastructure handoff.
