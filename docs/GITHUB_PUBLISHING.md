# GitHub Lambda Publishing

The `Publish Lambda release` workflow promotes the exact 20 Lambda ZIP packages
and the campaign/history contract ZIPs produced by a successful `CI` push run
on `main`. It does not rebuild deployment artifacts during promotion. The
source commit SHA is the immutable S3 release ID.

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

## Independent resolver publication

CI on a main-branch push records `dist/release-scope.json` from the entire push's before/head commit range. Resolver-only changes, including reviewed packaging/docs/workflow support files, receive `scope=resolver`; a change to any other Lambda or shared runtime code retains the existing all-functions scope. Pull-request CI does not produce a promotable main-push manifest.

The publishing workflow requires a successful main-push CI run and an exact matching scope manifest. A resolver-only release invokes `scripts/publish_url_resolver.py`, which verifies the resolver checksum, conditionally uploads only `url_redirect_resolver.zip`, checks the current object and requires its version/checksum/size to match the published artifact, and saves `url-resolver-publication.json` as a GitHub artifact. No unrelated Lambda ZIP or contract package is published in this path. The result contains the bucket/key/version/base64 hash for the separately authorized infrastructure promotion. It never calls Lambda or changes a runtime/alias.

Broader releases retain the existing all-functions uploader. CI runs created before this scope manifest existed cannot be promoted with this workflow; run fresh main CI after merging the reviewed source. The OIDC role and permissions are unchanged. Resolver-only publication uses the same already-authorized versioned artifact bucket and S3 release prefix.

The publisher uses existing `s3:GetObject` and `s3:PutObject` permissions. It does not request `GetObjectVersion`; a concurrent latest-version replacement fails verification. Infrastructure independently verifies pinned-version bytes with its existing deploy permissions before rollout.


Contract-only changes under `contracts/url-assessment/v1-draft/` and their tests, with narrowly listed release-scope support files, receive `scope=contracts`. The publishing workflow validates the main CI evidence but skips AWS credential assumption and every upload step. These draft handoffs are versioned in Git only; they do not publish or activate runtime artifacts. Any runtime-code change prevents contract-only scope.
