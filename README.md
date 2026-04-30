# AMT.SecurityForAll.Lambdas

Scaffold for SecurityForAll AWS Lambda functions, starting with the Incognito writer Lambda that stores incoming payloads in DynamoDB.

## What is included

- AWS SAM template (`template.yaml`)
- DynamoDB table (`SecurityForAllTable`)
- First Lambda (`IncognitoWriteFunction`) exposed via API Gateway `POST /incognito/write`
- Age attestation Lambda (`AgeAttestationFunction`) exposed via API Gateway `POST /identity/age-attestation`
- Post-confirmation Lambda source for Cognito profile creation (`src/post_confirmation/app.py`)
- Conversation analysis Lambda source for sanitized analysis requests (`src/conversation_analysis/app.py`)
- Local test event (`events/incognito-write.json`)
- Local test event (`events/age-attestation.json`)
- Local test event (`events/post-confirmation.json`)
- Local test event (`events/conversation-analysis.json`)
- Scripts and Make targets for build, deploy, local invoke, logs, zip packaging, and S3 artifact upload

## Prerequisites

- AWS CLI configured with credentials and target account
- AWS SAM CLI installed
- Docker (only needed for `sam local invoke`)

## Deploy with SAM

```bash
make deploy STACK_NAME=amt-security-for-all-lambdas AWS_REGION=us-east-1
```

This deploys:

- DynamoDB table named `<stack-name>-security-for-all`
- API endpoint for posting Incognito data
- Lambda with IAM permissions to read/write the DynamoDB table

## Local invocation

```bash
make local-invoke
```

```bash
make local-invoke-age
```

## Build Lambda ZIP artifact

```bash
make zip
```

Default output is `function.zip` at the repo root.

## Upload ZIP to foundation artifacts bucket

```bash
make upload-zip
```

Defaults:

- Bucket: `asecurityforall-dev-artifacts`
- Key: `identity/post-confirmation/v1.0.0/function.zip`

Override key/version when publishing a new release:

```bash
make publish-zip ARTIFACT_KEY=identity/post-confirmation/v1.0.1/function.zip
```

`publish-zip` runs build + upload. If S3 bucket versioning is enabled, the upload script prints the returned `VersionId` so you can pin it in downstream deployment configs.

## API usage

After deploy, get `ApiBaseUrl` from stack outputs, then call:

```bash
curl -X POST "$API_BASE_URL/incognito/write" \
  -H "Content-Type: application/json" \
  -d '{"sub":"user-123"}'
```

```bash
curl -X POST "$API_BASE_URL/identity/age-attestation" \
  -H "Authorization: Bearer <cognito-id-token>" \
  -H "Content-Type: application/json" \
  -d '{"agePolicyVersion":"v1.0"}'
```

The age-attestation flow only updates the existing user profile item at
`PK=USER#<sub>`, `SK=PROFILE`. If that profile does not exist already, the Lambda returns a friendly `404`
and makes no DynamoDB changes. When API Gateway is configured with a Cognito/JWT authorizer, the Lambda reads
`sub` and `custom:over_18` from the ID token claims instead of trusting those values from the request body.

## Conversation analysis guardrails

The conversation analysis Lambda enforces backend request limits so malformed or oversized payloads are rejected consistently even if a client bypasses local checks.

- `schemaVersion` must be `1.0`
- `localSanitizationApplied` must be `true`
- `sanitizedText` max: `8,000` characters
- `entities` max: `100`
- request body max: `64 KB`

## Conversation analysis abuse controls

The conversation analysis Lambda also applies MVP repeat-request protections.

- per-identity rate limiting uses a fixed `5` minute window
- default limit is `10` analysis requests per identity per window
- identical in-flight `requestId` values return a structured retryable `RATE_LIMITED` response
- recently completed duplicate `requestId` values return a structured retryable `RATE_LIMITED` response during the dedupe TTL
- request records are retained for `15` minutes by default with minimal metadata only

## Conversation analysis instruction-style abuse handling

The conversation analysis Lambda treats submitted sanitized text as untrusted user data.

- instruction-style abuse patterns are detected before the model call
- suspicious prompt-injection style content returns a safe low-confidence response instead of trusting the content as instructions
- the OpenAI prompt explicitly treats submitted content as data, not system or developer instructions
- model failures still return structured backend-safe errors without leaking raw provider text

## Adding future lambdas

1. Add a new folder under `src/<lambda_name>/`.
2. Add a new `AWS::Serverless::Function` resource in `template.yaml`.
3. Reuse shared resources (table, env vars, policies) as needed.
4. Add an event JSON file under `events/` for local testing.
