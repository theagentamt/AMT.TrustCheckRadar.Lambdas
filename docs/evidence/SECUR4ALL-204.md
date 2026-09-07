# SECUR4ALL-204 Lambda Evidence

Status: **Lambda implementation complete; promotion approvals remain external**

## Acceptance evidence

| Requirement | Implementation evidence | Automated evidence |
|---|---|---|
| Separate explicit opt-in | Analysis validation accepts only boolean `campaignConsentGranted`; omission defaults to `false`. | `test_accepts_explicit_campaign_opt_in`, `test_rejects_non_boolean_campaign_consent`, `test_parses_valid_payload` |
| Declining does not publish | The completion transaction omits the outbox write unless consent is exactly `true`; the publisher also suppresses a false record. | `test_declined_campaign_consent_does_not_write_outbox`, `test_declined_consent_is_successful_noop` |
| Random retry-stable event identity | A UUIDv4 is stored with `RESULT_READY` and reused by recovery before the atomic outbox commit. | `test_opted_in_success_persists_uuid4_and_reuses_it_in_atomic_commit`, `test_opted_in_result_ready_recovery_reuses_persisted_event_id`, `test_result_ready_is_replayed_for_atomic_commit_recovery`, publisher UUIDv4 contract tests |
| Atomic completed-analysis publication | Entitlement consumption, request completion, and the conditional outbox put share one DynamoDB transaction. | `test_opted_in_analysis_atomically_writes_campaign_outbox` |
| Period-scoped pseudonym | Publisher calls KMS `GenerateMac` with `HMAC_SHA_256` and domain-separated account bytes for the fixed 14-day UTC period. | `test_publishes_pseudonymous_observation_and_opaque_message`, `test_period_is_fixed_fourteen_day_utc_bucket` |
| No identity beyond boundary | Pipeline observation excludes account/request/device identifiers; precise event time is not persisted beyond the outbox. | Serialized-item assertions and prohibited-field contract tests |
| Sanitization defense | Strict field allowlist plus obvious email/phone rejection before persistence. | `test_rejects_unknown_and_prohibited_fields`, `test_rejects_obvious_unsanitized_identifiers` |
| Opaque queue envelope | Feature message contains exactly the approved five fields. | `test_feature_envelope_contains_only_approved_fields` |
| At-least-once replay safety | `PENDING`/`PUBLISHED` dedupe state recovers write-before-send failures and suppresses completed replay. | `test_pending_delivery_retries_without_rederiving_identity_token`, `test_completed_duplicate_is_successful_noop` |
| Retention | Observation TTL is capped at 72 hours and dedupe TTL at 21 days. | Service transaction assertions and bounded configuration validation |
| Content-free telemetry | Logs contain counts and low-cardinality outcomes only; malformed input emits a content-free metric. | `test_handler_uses_content_free_completion_log`, `test_malformed_record_emits_only_content_free_metric` |

## Reproduction

```bash
python3 -m pytest -q tests/conversation_analysis tests/campaign_observation_publisher
./scripts/build_lambda_zip.sh --function campaign_observation_publisher --skip-dependencies
unzip -l dist/campaign_observation_publisher.zip
```

The external schema, privacy, and security approvals referenced by the architecture
remain deployment gates. They do not change the implemented fail-closed Lambda
behavior and are not represented here as completed approvals.
