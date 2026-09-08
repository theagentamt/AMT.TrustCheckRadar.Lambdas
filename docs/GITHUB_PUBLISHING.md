# GitHub Lambda Publishing

The `Publish Lambda release` workflow promotes the exact 15 Lambda ZIP packages
and `campaign-contracts-1.0.0.zip` produced by a successful `CI` push run on
`main`. It does not rebuild deployment artifacts during promotion. The source
commit SHA is the immutable S3 release ID.

## GitHub repository configuration

Create GitHub environments named `dev`, `uat`, and `prod`. Configure these
nonsecret environment variables in each environment:

| Variable | Required | Value |
| --- | --- | --- |
| `AWS_REGION` | Yes | AWS region containing the environment, such as `us-east-1` |
| `AWS_ROLE_ARN` | Yes | Lambda artifact-publisher IAM role trusted only by this repository and GitHub environment |
| `ARTIFACT_BUCKET` | Yes | That environment's foundation artifact bucket |

Set repository variable `ENABLE_DEV_PUBLISH=true` only after the `dev`
environment and AWS role are ready. Thereafter, every successful `CI` push run on
`main` publishes its exact ZIP artifacts to dev. Without that variable, publishing
is manual and CI remains read-only.

Require a reviewer for the `uat` and `prod` environments and restrict all three
environments to the `main` branch. Do not store AWS access keys in GitHub.

## AWS OIDC contract

Each `AWS_ROLE_ARN` must trust GitHub's OIDC provider and the exact environment
subject for this repository:

```text
repo:theagentamt/AMT.TrustCheckRadar.Lambdas:environment:<dev|uat|prod>
```

If the AWS account uses GitHub's immutable owner/repository-ID OIDC subject form,
use that exact form instead. The role needs only:

- `sts:GetCallerIdentity`;
- `s3:ListBucket`/bucket metadata access for its configured artifact bucket;
- `s3:PutObject` for `releases/*` in that bucket.

The workflow requests short-lived credentials with GitHub OIDC. An IAM role that
trusts only the infrastructure repository cannot be reused without changing its
trust boundary; use a dedicated, least-privilege Lambda publisher role.

## Release flow

1. Merge Lambda code to `main` and let `CI` finish successfully.
2. Dev publishes automatically when `ENABLE_DEV_PUBLISH=true`. The workflow
   summary records the source CI run ID, release SHA, and S3 prefix.
3. To promote the identical ZIP bytes, run `Publish Lambda
   release` manually from `main`, choose `uat` or `prod`, and enter the successful
   source CI run ID.
4. Pass the recorded release SHA to the infrastructure deployment workflow.

S3 uploads use `If-None-Match: *`; a release cannot overwrite an existing object.
A retry accepts an existing object only when its stored SHA-256 metadata matches
the CI artifact. `SHA256SUMS` is verified before upload and published beside the
ZIP files.
