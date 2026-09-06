# AMT.TrustCheckRadar.Lambdas

Python AWS Lambda application code for TrustCheckRadar. Infrastructure is owned by
[`AMT.TrustCheckRadar.Cloud.Infrastructure`](https://github.com/theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure);
this repository owns function code, tests, and immutable deployment packages.

## Functions

| Artifact | Invocation | Purpose |
|---|---|---|
| `age_attestation.zip` | `POST /v1/users/age-attestation` | Records the authenticated user's age-policy decision. |
| `campaign_cluster_aggregator.zip` | Cluster SQS queue | Applies bounded similarity and contributor caps to transient candidates. |
| `campaign_deletion_bridge.zip` | Account-deletion ledger stream | Tombstones and removes active pseudonymous contributions. |
| `campaign_lifecycle.zip` | EventBridge Scheduler | Finalizes thresholded periods and creates/retires period HMAC keys. |
| `campaign_observation_publisher.zip` | Campaign outbox DynamoDB stream | Pseudonymizes opted-in completed analyses and publishes opaque feature work. |
| Campaign feature-extractor image | Feature SQS queue | Produces bounded multilingual embeddings from an image-baked offline model. |
| `conversation_analysis.zip` | `POST /analysis` | Analyzes sanitized conversation text with device, abuse, and entitlement controls. |
| `device_registration.zip` | `POST /device-registration` | Creates and updates account-to-device bindings. |
| `device_recovery.zip` | `POST /device-recovery` | Performs the optional protected device-recovery flow. |
| `entitlement_snapshot.zip` | `GET /entitlements/snapshot` | Returns the current subscription and scan-usage view. |
| `purchase_handoff.zip` | `POST /purchase-handoff` | Verifies Google Play purchases and updates entitlement state. |
| `web_risk_communication.zip` | `POST /web-risk-communication` | Evaluates the optional URL-risk flow. |
| `post_confirmation.zip` | Cognito PostConfirmation | Creates the initial user profile. |

All deployed handlers are `app.lambda_handler` on Python 3.13. Optional functions
and campaign workers are packaged with the required release set, but Terraform
decides whether to deploy them.

## Local development

Prerequisites:

- Python 3.13 or newer for local tests
- `pytest`
- `shellcheck` for shell validation
- AWS CLI only when publishing artifacts

Run the quality checks:

```bash
make check
```

Build all deployment packages without resolving third-party dependencies (fast
local packaging check):

```bash
make package-no-deps
```

Build release-ready packages, including Linux/ARM64 dependencies:

```bash
make package
```

Artifacts are written to `dist/`. The packager uses sorted paths and normalized ZIP
metadata so identical inputs produce identical archives.

The feature extractor is a container Lambda rather than a ZIP. Its Dockerfile
downloads a revision-pinned model during the image build and enables offline mode
at runtime; the deployed image must be referenced by its ECR digest.

Build one function or target x86_64 explicitly:

```bash
./scripts/build_lambda_zip.sh --function purchase_handoff
./scripts/build_lambda_zip.sh --all --arch x86_64
```

## Publishing a release

Choose the immutable release ID used by the infrastructure repository and the
environment-specific foundation artifact bucket:

```bash
make publish \
  RELEASE=2026.09.03-1 \
  ARTIFACT_BUCKET=trustcheckradar-dev-123456789012-artifacts
```

The upload script refuses to replace an existing S3 object. Packages are stored at
`releases/<release-id>/<function>.zip`, matching the Terraform release contract.
After publishing, set the same release ID in the infrastructure deployment.

Use `INCLUDE_OPTIONAL=false` to publish only the six functions required by the
default infrastructure configuration.

## Repository boundaries

- Do not deploy CloudFormation/SAM stacks from this repository.
- Do not commit credentials, generated ZIPs, state files, or environment-specific
  configuration.
- Make infrastructure changes in the cloud-infrastructure repository.
- Keep runtime settings and artifact names synchronized with
  [the deployment contract](lambda_deployment_dependency_contract.md).

The `events/` directory contains sanitized examples for handler debugging. It does
not contain credentials or production identifiers.
