# Lambda Deployment Dependency Contract

This document captures the deployment dependency contract for the Lambda functions currently used from the infrastructure repo.

## Campaign Observation Publisher

- Lambda path: `src/campaign_observation_publisher/`
- Artifact: `campaign_observation_publisher.zip`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible, ARM64
- Invocation mode: `INSERT` records from the campaign outbox DynamoDB stream

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `APP_ENVIRONMENT` | Yes | `dev` | Cross-environment validation and key selection cannot run |
| `CAMPAIGN_SCHEMA_VERSION` | Yes | `1` | Version validation cannot run; V1 is the only supported value |
| `PIPELINE_TABLE_NAME` | Yes | `trustcheckradar-dev-campaign-pipeline` | Transient observations and dedupe state cannot be written |
| `FEATURE_QUEUE_URL` | Yes | `https://sqs.us-east-1.amazonaws.com/...` | Opaque feature requests cannot be published |
| `CONTRIBUTOR_HMAC_KEY_ID_TEMPLATE` | Until the period-key discovery handoff is finalized | `alias/trustcheckradar-{environment}-campaign-contributor-{period_id}` | The publisher cannot address the period-specific KMS HMAC key |
| `CONTRIBUTOR_PERIOD_DAYS` | No | `14` | Defaults to 14 and rejects any other value |
| `OBSERVATION_RETENTION_HOURS` | No | `72` | Defaults to 72 and rejects values above the approved maximum |
| `TRANSIENT_RETENTION_DAYS` | No | `21` | Defaults to 21 and rejects values above the approved maximum |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Needs |
|---|---|
| Campaign outbox stream | `dynamodb:DescribeStream`, `dynamodb:GetRecords`, `dynamodb:GetShardIterator`, `dynamodb:ListStreams` |
| Campaign pipeline table | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:TransactWriteItems` |
| Current-period KMS HMAC key | `kms:GenerateMac` with `HMAC_SHA_256` |
| Feature queue | `sqs:SendMessage` |
| Campaign metrics namespace | `cloudwatch:PutMetricData` |

### Privacy and message contract

- The outbox input is strict, versioned, environment-bound, and requires an explicit `campaignConsentGranted` boolean.
- Declined consent and expired observations are successful no-ops.
- The account identifier is used only as input to `GenerateMac`; it is not written to the pipeline table, queue, logs, metrics, or handler response.
- Contributor tokens use `HMAC(period-key, b"campaign-contributor:v1\\0" + account-id)` and are scoped to fixed 14-day UTC periods.
- Sanitized observations expire within 72 hours; dedupe state expires within 21 days.
- Feature queue messages contain exactly `schemaVersion`, `eventType`, `environment`, `statisticsEventId`, and `recordVersion`.
- Standard-queue delivery is at least once. A `PENDING`/`PUBLISHED` dedupe record prevents completed replays while permitting recovery after a write-before-send failure.

The authoritative completed-analysis/outbox schema and lifecycle-created HMAC key
addressing convention remain pending campaign contract handoff. The parser and key
template are isolated so those details can be aligned without weakening the privacy
boundary.

---

## Age Attestation

- Lambda path: `src/age_attestation/app.py`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation modes:
  - API/event invocation for age attestation updates
  - Cognito-trigger style invocation is also supported by the code path

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `USERS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-users` | DynamoDB profile updates cannot run |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-users` | Same as above if `USERS_TABLE_NAME` is not provided |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB users table | `USERS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:UpdateItem` | Name required by code |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: users table from `USERS_TABLE_NAME` / `TABLE_NAME`
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = PROFILE`
- Item family updated:
  - user profile item
- Attributes updated:
  - `ageVerified`
  - `ageVerifiedAt`
  - `agePolicyVersion`
  - `updatedAt`
  - `status`
- GSI usage: none
- TTL fields: none

### Runtime assumptions

- Expects authenticated caller context to resolve the Cognito `sub` for API mode.
- Writes only to an existing user profile; it should not create the base profile record.
- Route expectation: POST-style API invocation if exposed through API Gateway.

### Compatibility aliases

- Table variable aliases accepted:
  - `USERS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require an ARN.

---

## Post Confirmation

- Lambda path: `src/post_confirmation/app.py`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: Cognito Post Confirmation trigger

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `USERS_TABLE_NAME` | Preferred | `trustcheckradar-dev-users` | New profile record cannot be created if no table identifier is provided |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-users` | Same as above if `USERS_TABLE_NAME` is not provided |
| `USERS_TABLE_ARN` | Infrastructure fallback | `arn:aws:dynamodb:us-east-1:123456789012:table/trustcheckradar-dev-users` | The table name is derived from the ARN when neither name variable is present |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB users table | `USERS_TABLE_NAME`, `TABLE_NAME`, or `USERS_TABLE_ARN` | `dynamodb:PutItem` | Name or ARN required by code |
| Cognito user pool trigger event | direct event payload | invoke permission only | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: users table from `USERS_TABLE_NAME` / `TABLE_NAME`, or derived from `USERS_TABLE_ARN`
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = PROFILE`
- Item family written:
  - user profile item
- Notable attributes:
  - `sub`
  - `email`
  - `given_name`
  - `family_name`
  - `phone_number`
  - `over_18`
  - `status`
  - `ageVerified`
  - `ageVerifiedAt`
  - `agePolicyVersion`
  - `createdAt`
  - `updatedAt`
- GSI usage: none
- TTL fields: none

### Runtime assumptions

- Triggered by Cognito after signup confirmation.
- Uses a conditional put to avoid overwriting an existing profile.

### Compatibility aliases

- Table variable aliases accepted:
  - `USERS_TABLE_NAME`
  - `TABLE_NAME`
  - `USERS_TABLE_ARN`
- The ARN fallback matches the identity-workflows Terraform stack contract.

---

## Device Registration

- Lambda path: `src/device_registration/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively POST `/device-registration`

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `DEVICE_BINDINGS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-device-bindings` | Device binding records cannot be read or written |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-device-bindings` | Same as above if `DEVICE_BINDINGS_TABLE_NAME` is not provided |
| `DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS` | No | `180` | Defaults are used for inactive cleanup horizon |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB device bindings table | `DEVICE_BINDINGS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:Query` | Name required by code |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: device bindings table
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = DEVICE#<bindingFingerprint>`
- GSI usage:
  - `GSI1PK`
  - `GSI1SK`
  - active lookup pattern uses `USER#<sub>#ACTIVE`
- TTL field:
  - `expiresAt` for inactive binding cleanup
- Item family written/read:
  - device binding items with fields such as:
    - `accountId`
    - `bindingFingerprint`
    - `platform`
    - `osVersion`
    - `status`
    - `firstSeenAt`
    - `lastSeenAt`
    - `deactivatedAt`

### Runtime assumptions

- Caller is authenticated and user `sub` is available in authorizer/JWT context.
- Request is expected to contain a stable device binding fingerprint generated by the client.
- Decision outcomes supported:
  - `NEW`
  - `KNOWN`
  - `SWITCH`

### Compatibility aliases

- Table variable aliases accepted:
  - `DEVICE_BINDINGS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require an ARN.

---

## Device Recovery

- Lambda path: `src/device_recovery/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `DEVICE_BINDINGS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-device-bindings` | Recovery logic cannot read or update bindings |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-device-bindings` | Same as above if `DEVICE_BINDINGS_TABLE_NAME` is not provided |
| `DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS` | No | `180` | Defaults are used for inactive retention handling |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB device bindings table | `DEVICE_BINDINGS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:Query` | Name required by code |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: device bindings table
- Primary key shape:
  - `PK = USER#<sub>`
  - `SK = DEVICE#<bindingFingerprint>`
- GSI usage:
  - `GSI1PK`
  - `GSI1SK`
  - active binding lookup
- TTL field:
  - `expiresAt` on inactive records
- Item families:
  - reads existing device binding items
  - writes reactivated/deactivated binding state

### Runtime assumptions

- Caller is authenticated.
- Supports device recovery operations such as reactivating an inactive device or resetting an active binding, depending on request action.

### Compatibility aliases

- Table variable aliases accepted:
  - `DEVICE_BINDINGS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require an ARN.

---

## Purchase Handoff

- Lambda path: `src/purchase_handoff/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively POST `/purchase-handoff`

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `ENTITLEMENTS_TABLE_NAME` | Yes, unless `TABLE_NAME` is set | `trustcheckradar-dev-purchase-entitlements` | Entitlement persistence fails |
| `TABLE_NAME` | Compatibility alias | `trustcheckradar-dev-purchase-entitlements` | Same as above if `ENTITLEMENTS_TABLE_NAME` is not provided |
| `PURCHASE_VERIFICATION_MODE` | No, but required for live verification behavior | `google_play` | If set to `stub`, Google Play is not called |
| `GOOGLE_PLAY_SECRET_NAME` | Required for live Google Play verification | `trustcheckradar/dev/google-play-service-account` | Live verification cannot fetch service account credentials |
| `GOOGLE_PLAY_PACKAGE_NAME` | Required for live Google Play verification | `com.andmorethings.trustcheckradar` | Android Publisher API calls cannot be scoped correctly |
| `GOOGLE_PLAY_PRO_PRODUCT_ID` | No | `trustcheck_radar_pro_monthly` | Defaults may be used, but product mapping may be wrong |
| `FREE_MONTHLY_SCAN_LIMIT` | No | `5` | Default free quota logic may be wrong if code assumes configured policy |
| `PRO_MONTHLY_SCAN_LIMIT` | No | `100` | Default pro quota logic may be wrong if code assumes configured policy |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB purchase entitlements table | `ENTITLEMENTS_TABLE_NAME` or `TABLE_NAME` | `dynamodb:GetItem`, `dynamodb:PutItem` | Name required by code |
| Secrets Manager Google Play service-account secret | `GOOGLE_PLAY_SECRET_NAME` | `secretsmanager:GetSecretValue` | Name required by code |
| Google Android Publisher API | runtime outbound call | outbound HTTPS | No AWS identifier |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: purchase entitlements table
- Primary key shapes:
  - Entitlement item:
    - `PK = USER#<sub>`
    - `SK = ENTITLEMENT#google_play#trustcheck_radar_pro_monthly`
  - Idempotency item:
    - `PK = TOKEN#<sha256(purchaseToken)>`
    - `SK = IDEMPOTENCY`
- Compatibility read shape:
  - `SK = ENTITLEMENT`
- GSI usage: none in the code path described here
- TTL fields:
  - none required by the purchase handoff logic itself
- Item families:
  - entitlement state
  - purchase token idempotency records

### Runtime assumptions

- POST-style API request with authenticated Cognito user context.
- For live verification, the code expects a full Google service-account JSON stored in Secrets Manager.
- This Lambda is the verification point against Google Play; downstream entitlement consumers trust DynamoDB state.

### Compatibility aliases

- Table variable aliases accepted:
  - `ENTITLEMENTS_TABLE_NAME`
  - `TABLE_NAME`
- Secret variable expected by code:
  - `GOOGLE_PLAY_SECRET_NAME`
- Important: code expects secret **name**, not secret ARN.

---

## Entitlement Snapshot

- Lambda path: `src/entitlement_snapshot/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively GET `/entitlements/snapshot`

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `ENTITLEMENTS_TABLE_NAME` | Yes, unless `USERS_TABLE_NAME` or `TABLE_NAME` is set | `trustcheckradar-dev-purchase-entitlements` | Snapshot cannot read entitlement state |
| `USERS_TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-purchase-entitlements` | Used only if `ENTITLEMENTS_TABLE_NAME` is not set |
| `TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-purchase-entitlements` | Used only if stronger names are not set |
| `ENTITLEMENT_PLATFORM` | No | `google_play` | Defaults are used for key resolution |
| `ENTITLEMENT_PRODUCT_ID` | No | `trustcheck_radar_pro_monthly` | Defaults are used for key resolution |
| `ENTITLEMENT_USAGE_PERIOD_MODE` | No | `billing_cycle` | Defaults are used in usage response logic |
| `FREE_MONTHLY_SCAN_LIMIT` | No | `5` | Free-tier remaining count may be incorrect |
| `PRO_MONTHLY_SCAN_LIMIT` | No | `100` | Pro-tier remaining count may be incorrect |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB purchase entitlements table | `ENTITLEMENTS_TABLE_NAME` or fallback aliases | `dynamodb:GetItem` | Name required by code |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: purchase entitlements table
- Primary key shapes:
  - entitlement item:
    - `PK = USER#<sub>`
    - `SK = ENTITLEMENT#google_play#trustcheck_radar_pro_monthly`
  - usage item:
    - `PK = USER#<sub>`
    - `SK = USAGE#<periodKey>`
- Compatibility read shape:
  - `SK = ENTITLEMENT`
- GSI usage: none
- TTL fields: none required by snapshot read path
- Item families read:
  - entitlement item
  - usage counter item

### Runtime assumptions

- Read-only entitlement/status endpoint.
- Authenticated caller required.
- Returns current entitlement state, usage counters, and restore guidance.

### Compatibility aliases

- Table variable aliases accepted:
  - `ENTITLEMENTS_TABLE_NAME`
  - `USERS_TABLE_NAME`
  - `TABLE_NAME`
- Code does not require a table ARN.

---

## Conversation Analysis

- Lambda path: `src/conversation_analysis/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: protected API route, effectively POST analysis endpoint

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `ANALYSIS_ABUSE_TABLE_NAME` | Yes, unless fallback aliases are set | `trustcheckradar-dev-analysis-abuse-control` | Request dedupe/rate controls fail |
| `TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-analysis-abuse-control` | Used only if primary name is missing |
| `USERS_TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-analysis-abuse-control` | Used only if stronger names are missing |
| `DEVICE_BINDINGS_TABLE_NAME` | Yes | `trustcheckradar-dev-device-bindings` | Device binding validation fails and request is rejected/unavailable |
| `ENTITLEMENTS_TABLE_NAME` | Yes for entitlement enforcement, unless fallback aliases are set | `trustcheckradar-dev-purchase-entitlements` | Monthly/credit entitlement gating fails |
| `RATE_LIMIT_WINDOW_SECONDS` | No | `60` | Default request throttling windows are used |
| `RATE_LIMIT_MAX_REQUESTS` | No | `10` | Default request throttling caps are used |
| `REQUEST_ID_TTL_SECONDS` | No | `86400` | Dedupe retention defaults are used |
| `SCAN_RATE_LIMIT_WINDOW_SECONDS` | No | `3600` | Default scan abuse window is used |
| `SCAN_RATE_LIMIT_MAX_REQUESTS` | No | `20` | Default scan abuse cap is used |
| `FREE_MONTHLY_SCAN_LIMIT` | No | `5` | Free-tier quota math may be wrong |
| `PRO_MONTHLY_SCAN_LIMIT` | No | `100` | Pro-tier quota math may be wrong |
| `OPENAI_SECRET_NAME` | Required for real model calls | `trustcheckradar/dev/openai` | OpenAI API key cannot be loaded |
| `OPENAI_SECRET_FIELD` | No | `apiKey` | Defaults are used when omitted |
| `OPENAI_RESPONSES_ENDPOINT` | No | `https://api.openai.com/v1/responses` | Defaults are used when omitted |
| `OPENAI_MODEL` | No | `gpt-4.1-mini` | Defaults are used when omitted |
| `OPENAI_TIMEOUT_SECONDS` | No | `20` | Defaults are used when omitted |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB abuse-control table | `ANALYSIS_ABUSE_TABLE_NAME` or fallback aliases | `dynamodb:GetItem`, `dynamodb:PutItem`, `dynamodb:UpdateItem`, `dynamodb:DeleteItem` | Name required by code |
| DynamoDB device bindings table | `DEVICE_BINDINGS_TABLE_NAME` | `dynamodb:Query` | Name required by code |
| DynamoDB entitlements table | `ENTITLEMENTS_TABLE_NAME` or fallback aliases | `dynamodb:GetItem`, `dynamodb:PutItem` | Name required by code |
| Secrets Manager OpenAI secret | `OPENAI_SECRET_NAME` | `secretsmanager:GetSecretValue` | Name required by code |
| OpenAI Responses API | runtime outbound call | outbound HTTPS | No AWS identifier |
| API Gateway JWT authorizer / Cognito identity | request context | invoke + authorizer context | No env var |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Abuse-control table item families:
  - request idempotency / dedupe:
    - `PK = ANALYSIS#REQUEST#<sha256(identity)>`
    - `SK = <requestId>`
  - request rate limiting:
    - `PK = ANALYSIS#RATE#<sha256(identity)>`
    - `SK = <windowStart>`
  - scan abuse cap:
    - `PK = ANALYSIS#SCAN_RATE#<sha256(accountId)>`
    - `SK = <windowStart>`
- TTL fields:
  - `ttl`
  - `expiresAt`
- Entitlements table item families read/written:
  - entitlement item
  - usage/remaining scan metadata such as:
    - `remainingMonthlyScans`
    - `remainingCredits`
    - `lastScanAt`
    - `lastScanRequestId`
    - `lastScanConsumptionType`
- Device binding table usage:
  - query active binding by user

### Runtime assumptions

- Expects authenticated request.
- Expects `Authorization: Bearer <id-token-or-jwt>`.
- Expects device-binding header:
  - `X-Device-Binding-Fingerprint`
- Expects JSON payload for analysis request.
- This Lambda is linked to a real OpenAI call when `OPENAI_SECRET_NAME` is configured and verification mode is not stubbed away in code.

### Compatibility aliases

- Abuse table aliases accepted:
  - `ANALYSIS_ABUSE_TABLE_NAME`
  - `TABLE_NAME`
  - `USERS_TABLE_NAME`
- Entitlements table aliases accepted:
  - `ENTITLEMENTS_TABLE_NAME`
  - fallback aliases in shared entitlement config
- Device binding table expects the specific name:
  - `DEVICE_BINDINGS_TABLE_NAME`
- Secret variable expected by code:
  - `OPENAI_SECRET_NAME`
- Important: code expects secret **name**, not ARN.

---

## Web Risk Communication

- Lambda path: `src/web_risk_communication/`
- Handler: `app.lambda_handler`
- Runtime expectation: Python 3.13 compatible
- Invocation mode: API/event invocation with URL input

### Required environment variables

| Variable | Required | Example | What breaks if missing |
|---|---|---:|---|
| `WEB_RISK_TABLE_NAME` | Yes, unless fallback aliases are set | `trustcheckradar-dev-web-risk-cache` | Cache reads/writes fail |
| `TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-web-risk-cache` | Used only if primary name is missing |
| `USERS_TABLE_NAME` | Compatibility fallback | `trustcheckradar-dev-web-risk-cache` | Used only if stronger names are missing |
| `WEB_RISK_SECRET_NAME` | Yes | `trustcheckradar/dev/web-risk-api-key` | Google Web Risk API key cannot be loaded |
| `WEB_RISK_SECRET_FIELD` | No | `apiKey` | Defaults are used when omitted |
| `WEB_RISK_ENDPOINT` | No | `https://webrisk.googleapis.com/v1eap1:evaluateUri` | Defaults are used when omitted |
| `LOG_LEVEL` | No | `INFO` | Only logging verbosity is affected |

### External resources

| Resource | Env/config key used | Needs | ARN, name, or both |
|---|---|---|---|
| DynamoDB web-risk cache table | `WEB_RISK_TABLE_NAME` or fallback aliases | `dynamodb:GetItem`, `dynamodb:PutItem` | Name required by code |
| Secrets Manager Web Risk secret | `WEB_RISK_SECRET_NAME` | `secretsmanager:GetSecretValue` | Name required by code |
| Google Web Risk API | runtime outbound call | outbound HTTPS | No AWS identifier |
| CloudWatch Logs | Lambda runtime default | `logs:CreateLogGroup`, `logs:CreateLogStream`, `logs:PutLogEvents` | Managed by Lambda execution role |

### Storage contract

- Table: web-risk cache table
- Primary key shapes:
  - full URL cache item:
    - `PK = WEBRISK#FULL_URL#<sha256(normalizedUrl)>`
    - `SK = RESULT`
  - domain cache item:
    - `PK = WEBRISK#DOMAIN#<sha256(domain)>`
    - `SK = RESULT`
- TTL fields:
  - `ttl`
  - `expiresAt`
- GSI usage: none
- Item families:
  - cached full-url assessment
  - cached domain assessment

### Runtime assumptions

- Input event contains a URL string or JSON body with URL.
- Lambda normalizes and strips unsafe URL components before hashing/evaluation.
- Code does not need an S3 artifact env var at runtime.

### Compatibility aliases

- Table variable aliases accepted:
  - `WEB_RISK_TABLE_NAME`
  - `TABLE_NAME`
  - `USERS_TABLE_NAME`
- Secret variable expected by code:
  - `WEB_RISK_SECRET_NAME`
- Important: code expects secret **name**, not ARN.

---

## Shared Entitlements Module

- Module path: `src/shared_entitlements/`
- This is not a standalone Lambda, but it defines shared table/env expectations used by multiple Lambdas.

### Environment aliases used by shared entitlement code

- `ENTITLEMENTS_TABLE_NAME`
- `USERS_TABLE_NAME`
- `TABLE_NAME`
- `FREE_MONTHLY_SCAN_LIMIT`
- `PRO_MONTHLY_SCAN_LIMIT`

### Shared storage assumptions

- Default entitlement fallback item:
  - `PK = USER#<sub>`
  - `SK = ENTITLEMENT`
- Google Play specific entitlement item:
  - `PK = USER#<sub>`
  - `SK = ENTITLEMENT#google_play#trustcheck_radar_pro_monthly`

---

# Terraform gaps to fix

## High-confidence gaps

1. Provide table names, not only ARNs.
- Several Lambdas read DynamoDB tables by environment variable name only.
- If Terraform currently exports only ARNs, the runtime still needs the matching `*_TABLE_NAME` env vars.
- Affected Lambdas:
  - `age_attestation`
  - `post_confirmation`
  - `device_registration`
  - `device_recovery`
  - `purchase_handoff`
  - `entitlement_snapshot`
  - `conversation_analysis`
  - `web_risk_communication`

2. Provide secret names, not only ARNs, where code expects names.
- The current code paths expect secret names for these integrations:
  - `GOOGLE_PLAY_SECRET_NAME`
  - `OPENAI_SECRET_NAME`
  - `WEB_RISK_SECRET_NAME`
- If Terraform currently injects only ARNs such as `GOOGLE_PLAY_SECRET_ARN`, the code will not resolve the secret unless compatibility support is added in code.

3. Ensure `DEVICE_BINDINGS_TABLE_NAME` is present for `conversation_analysis`.
- This is a hard dependency for device binding validation.
- If missing, the Lambda returns service-unavailable style failures during the device validation stage.

4. Ensure `ENTITLEMENTS_TABLE_NAME` is present for all entitlement-aware Lambdas.
- Affected Lambdas:
  - `purchase_handoff`
  - `entitlement_snapshot`
  - `conversation_analysis`
- Fallback aliases exist in some code paths, but relying on them is brittle and makes Terraform less explicit.

5. Ensure `ANALYSIS_ABUSE_TABLE_NAME` is present for `conversation_analysis`.
- This is required for dedupe, rate limiting, and abuse controls.

## IAM permissions Terraform must provide

### Age Attestation
- `dynamodb:UpdateItem`

### Post Confirmation
- `dynamodb:PutItem`

### Device Registration
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `dynamodb:Query`

### Device Recovery
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `dynamodb:Query`

### Purchase Handoff
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `secretsmanager:GetSecretValue`
- outbound internet/NAT access if Lambda is inside a VPC and still needs Google access

### Entitlement Snapshot
- `dynamodb:GetItem`

### Conversation Analysis
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `dynamodb:UpdateItem`
- `dynamodb:DeleteItem`
- `dynamodb:Query`
- `secretsmanager:GetSecretValue`
- outbound internet/NAT access if Lambda is inside a VPC and still needs OpenAI access

### Web Risk Communication
- `dynamodb:GetItem`
- `dynamodb:PutItem`
- `secretsmanager:GetSecretValue`
- outbound internet/NAT access if Lambda is inside a VPC and still needs Google Web Risk access

### Common to all Lambda execution roles
- `logs:CreateLogGroup`
- `logs:CreateLogStream`
- `logs:PutLogEvents`

## App/backend outputs Terraform should expose clearly

- API route URL for each protected endpoint
- Cognito authorizer / issuer details expected by the client
- Device binding header requirement for conversation analysis:
  - `X-Device-Binding-Fingerprint`
- Exact table names for:
  - users
  - device bindings
  - purchase entitlements
  - analysis abuse control
  - web risk cache
- Exact secret names for:
  - OpenAI API key secret
  - Google Play service-account secret
  - Google Web Risk API key secret
- Config values that are part of behavior, not just infrastructure:
  - `FREE_MONTHLY_SCAN_LIMIT`
  - `PRO_MONTHLY_SCAN_LIMIT`
  - `SCAN_RATE_LIMIT_WINDOW_SECONDS`
  - `SCAN_RATE_LIMIT_MAX_REQUESTS`
  - `RATE_LIMIT_WINDOW_SECONDS`
  - `RATE_LIMIT_MAX_REQUESTS`
  - `REQUEST_ID_TTL_SECONDS`
  - `DEVICE_BINDINGS_INACTIVE_RETENTION_DAYS`
  - `ENTITLEMENT_PLATFORM`
  - `ENTITLEMENT_PRODUCT_ID`
  - `ENTITLEMENT_USAGE_PERIOD_MODE`

## Nice-to-fix clarity gaps

1. Standardize on `*_TABLE_NAME` and `*_SECRET_NAME` everywhere.
- Today the codebase still carries compatibility aliases in a few places.
- Terraform will be easier to reason about if each Lambda gets a single canonical env var contract.

2. Decide whether code should support secret ARN aliases.
- Right now several Lambdas clearly expect secret names.
- If infra prefers passing ARNs, add explicit code support instead of depending on naming assumptions.

3. Publish runtime contract per endpoint alongside infra outputs.
- Especially for:
  - request headers
  - request body shape
  - authenticated identity requirements
  - expected HTTP method
