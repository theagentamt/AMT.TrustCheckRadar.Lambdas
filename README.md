# AMT.TrustCheckRadar.Lambdas

Python AWS Lambda application code for TrustCheckRadar. Infrastructure is owned by
[`AMT.TrustCheckRadar.Cloud.Infrastructure`](https://github.com/theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure);
this repository owns function code, tests, and immutable deployment packages.

## Functions

| Artifact | Invocation | Purpose |
|---|---|---|
| `age_attestation.zip` | `POST /v1/users/age-attestation` | Records the authenticated user's age-policy decision. |
| `campaign_cluster_aggregator.zip` | Cluster SQS queue | Applies bounded similarity and contributor caps to transient candidates. |
| `campaign_deletion_bridge.zip` | Account-deletion ledger stream | Deletes contributions and completes matching pending participation withdrawals. |
| `campaign_lifecycle.zip` | EventBridge Scheduler | Finalizes thresholded periods and creates/retires period HMAC keys. |
| `campaign_observation_publisher.zip` | Campaign outbox DynamoDB stream | Revalidates app-provided features, pseudonymizes the contributor, and enqueues opaque clustering work. |
| `campaign_participation.zip` | `GET`/`PUT /v1/users/campaign-participation` | Manages optional server-authoritative participation, quota, receipts, and withdrawal commands. |
| `campaign_review.zip` | Internal campaign transition API | Enforces reviewer authorization and audited publication state changes. |
| `campaign_trends.zip` | `GET /v1/scam-trends` | Returns localized, privacy-thresholded published campaign summaries. |
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
metadata so identical inputs produce identical archives. Full builds include the
15 Lambda packages and the immutable `campaign-contracts-1.0.0.zip` handoff from
`contracts/campaign/v1/`.

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

GitHub Actions can publish the exact artifacts from a successful main-branch CI
run with short-lived AWS OIDC credentials. Dev publishing can run automatically;
UAT and production promotion uses protected GitHub environments and the original
CI run so the ZIP bytes are not rebuilt. See
[GitHub Lambda Publishing](docs/GITHUB_PUBLISHING.md) for the required repository
variables, AWS role contract, and promotion flow.

## Repository boundaries

- Do not deploy CloudFormation/SAM stacks from this repository.
- Do not commit credentials, generated ZIPs, state files, or environment-specific
  configuration.
- Make infrastructure changes in the cloud-infrastructure repository.
- Keep runtime settings and artifact names synchronized with
  [the deployment contract](lambda_deployment_dependency_contract.md).

The `events/` directory contains sanitized examples for handler debugging. It does
not contain credentials or production identifiers.
