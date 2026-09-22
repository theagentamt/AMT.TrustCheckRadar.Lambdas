# Legacy access retirement candidate

This source increment closes the old analysis, snapshot, direct Web Risk and purchase write paths during the consent/access cutover. It does not migrate customer records, activate current authority, enable a trial, allocate checks, deploy a service, or establish production readiness. Existing paid records and usage remain untouched; paid-service and purchase/restore availability require a separate reviewed rollout.

## Mandatory public boundaries

All four packages keep `app.lambda_handler`.

- `conversation_analysis` authenticates the verified API Gateway Cognito access-token claims, validates the existing bounded request, and only attempts an owned replay. A new request returns `LEGACY_MIGRATION_REQUIRED`; ambiguous old work returns `LEGACY_RECONCILIATION_REQUIRED`. Neither starts, settles, retries a provider, deducts, renews a period, or writes an outbox. No environment flag restores its former dispatcher.
- `entitlement_snapshot` always returns HTTP 409 `LEGACY_MIGRATION_REQUIRED`. It cannot synthesize a free grant from a missing or malformed row. Its handler imports neither SDK nor historical service.
- `web_risk_communication` always returns HTTP 410 `LEGACY_ENDPOINT_RETIRED`; it does not parse submitted URLs, load secrets, or call providers.
- `purchase_handoff` checks store configuration and authoritative account/deletion state, then returns `LEGACY_MIGRATION_REQUIRED` unless the separately qualified ownership candidate is enabled. The legacy unconditional entitlement writer has been removed. The migration infrastructure hard-disables that ownership candidate and denies legacy writes/provider access. This source change alone does not qualify the ownership candidate or make purchases/restores available.

`shared_entitlements.apply_campaign_participation_allowance` retains its compatibility name but returns an unchanged copy and never reads research participation. Existing call sites, including verified purchase period handling, cannot reapply a research bonus. Tests preserve the same verified token, current billing period, 37 remaining checks and two credits. Existing legacy rows with a 15-check limit also remain unchanged: this is preservation, not conversion into approved current access. Historical normalization helpers remain private to old service code and the independently gated purchase candidate; the retired analysis/snapshot handlers do not call them.

`load_campaign_participation` only recognizes enrolled eligibility with notice `research-consent-2026-09-21-v2`, policy `independent-research-v1`, a valid original epoch/version and matching configured environment. Reading older enrollment cannot authorize new publication and does not rewrite its evidence. Current authority is unchanged by this slice.

## Replay evidence and limitations

Replay requires an exact payload hash under the authenticated account's hashed request partition, a strict known `COMPLETED` row shape, original ordered timestamps and a live original retention deadline (at most the existing 900-second completion window). Unknown extra fields/schema combinations fail closed. The public response passes the existing strict minimized assessment projection. The original owned consumption receipt must exactly match request, completion timestamp, consumption type and expiry; no missing accounting is reconstructed.

`PROCESSING`, `RETRYABLE` and `RESULT_READY` are preserved for reconciliation. An expired processing lease is not evidence that a provider was never called. A cached result does not establish the original accounting basis or period. Completed-erased and unavailable evidence never fall back to new analysis.

An active, age-verified account with no fixed deletion fence is required before and after replay. Device authority requires strong reads of `ACTIVE_BINDING` and its exact active `DEVICE#...` row; missing pointer fails closed rather than falling back to the eventual GSI. Both records must remain identical across validation. A pointer/version change or revocation blocks release.

When History table names are configured, History state, locator and content must all exist, match the current generation/request/response, and remain unchanged across strong reads, even if History activation flags are false. Configured-but-empty History blocks replay because visibility cannot be established; this is **not evidence that the result was erased**. Partial configuration also blocks. With neither table configured, rows carrying History authorization are rejected. Deployment must preserve original table configuration; removing table names is not a supported way to bypass visibility checks.

The before/after reads are not a transaction-wide snapshot, and cannot revoke a response already released before a later deletion. No general historical replay or new result availability is promised. Unknown evidence is preserved for an operator decision rather than interpreted as a successful charge or an unused allowance.

## Least-privilege read contract

The replay handler needs DynamoDB `GetItem` only on:

- Users: `USER#*` (profile).
- Deletion ledger: `ACCOUNT#*` (fixed account fence).
- Analysis abuse: `ANALYSIS#REQUEST#*` and `ANALYSIS#CONSUMPTION#*`.
- Device bindings: `USER#*` (pointer and device; no GSI query).
- If configured, History control `USER#*` and History content `USER#*#HISTORY#*`.

It needs no writes, transactions, secrets, SSM, provider access or Lambda invocation. Snapshot and direct Web Risk fixed handlers need none of these reads. Purchase needs only its configured account/fence reads while the ownership candidate is off. It no longer performs the synthetic `health-check` entitlement read.

## Offline inventory report

`tools/legacy_access_inventory/plan.py --input <protected-json>` accepts at most 8 MiB and 10,000 records. The envelope is `{ "schemaVersion": 1, "records": [{ "family": "request", "item": {} }] }`; input items use ordinary decoded JSON, not DynamoDB AttributeValue wrappers. Duplicate JSON keys, invalid envelope, non-finite literals, oversized input and malformed JSON fail with a fixed error. Do not place customer inventory in source control or print it into logs.

The output contains aggregate preservation buckets, classifier version and SHA-256 of the exact input bytes. The included synthetic input/report uses no customer records. These are preliminary preservation classifications, **not schema qualification**: a `COMPLETED` classification still requires independent handler proof; current authority and audit records are deliberately not interpreted. Unknown fields and unrecognized families remain unknown or separately reviewed, never grants.

Required coverage families are requests, consumption, entitlements, consent state, consent operations, consent audit, withdrawal commands, purchase tokens, purchase locators, current authority and deletion fences. Missing recognizable families are reported. Presence of one record cannot establish complete pagination or coverage: `inventoryComplete`, `replayQualified`, `migrationApproved` and `applyAvailable` are always false. The planner has no SDK, collector, credentials, apply command or mutation path. It does not inspect backups or prove a complete live inventory.

## Remaining rollout acceptance

A later concrete rollout must preserve exact in-flight retry identity and original evidence, drain old invocations and cached credentials, confirm all writers and provider entry points are fenced, qualify any separate current access/purchase availability, gather the real read-only inventory and resolve unknown/in-flight records without manufacturing history. Rollback must keep retirement and consent/account-deletion fences; selecting an older artifact must not revive legacy grants or publication. Historical backup/replay and already-dispatched provider obligations remain separate acceptance work. No account deletion or campaign cleanup is declared complete here.

## Local evidence

The source slice has focused ordinary handler/helper tests plus isolated real-SDK/Moto replay tests covering original consumption, expiry/shape mismatch, History erasure and account/device races with unchanged stored items. The offline CLI tests verify deterministic input binding, ambiguous/unknown preservation, missing families, bounded malformed input and fixed redacted errors. These tests use synthetic data; they are not a live Dev/production inventory or provider qualification.

Validation on this isolated source branch: ordinary `python -m pytest -q` passed **1,753 tests and 238 subtests**, with 21 intentionally skipped optional SDK suites. The separately run `AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q tests/legacy_retirement/test_boundary_dynamodb.py` passed **32** real-SDK/Moto cases. `tests/legacy_retirement/test_inventory_cli.py` passed **8** CLI cases (also included in the ordinary total). Changed Python source compiled successfully; `git diff --check` passed. Combined consent/pipeline package validation is owned by the integrating Lambda agent and must be reported separately.

The retired analysis composition also pins `COGNITO_ISSUER`, `COGNITO_APP_CLIENT_ID`, `COGNITO_REQUIRED_SCOPE=aws.cognito.signin.user.admin`, `APP_ENVIRONMENT` and the original table names independently of whether a History deployment object is selected. Its existing assessment bounds are `HISTORY_MAX_SUMMARY_BYTES=4096`, `HISTORY_MAX_LIST_ITEMS=20`, and `HISTORY_MAX_TEXT_FIELD_BYTES=1024`; protect these from generic environment overrides. HistorySettings defaults those bounds when absent, but does not provide the issuer/client identity. The replay handler does not invoke paged History response/cursor processing or require activation flags. Infrastructure composition must supply the verified auth binding before replay can be called runnable.
