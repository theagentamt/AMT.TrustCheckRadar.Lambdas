# Repository Migration

This repository succeeds `theagentamt/AMT.SecurityForAll.Lambdas` as the Lambda
application repository for TrustCheckRadar.

## Provenance

The complete legacy `main` history through commit `7e8f901` was imported before
the TrustCheckRadar refactor. The legacy repository remains configured locally as
the `legacy` remote for audit and comparison; new work is published only to
`theagentamt/AMT.TrustCheckRadar.Lambdas`.

## Refactor boundary

The migration intentionally keeps the deployed data and request contracts stable
while changing repository and delivery concerns:

- SecurityForAll repository and resource examples were renamed to TrustCheckRadar.
- The obsolete SAM stack and deleted Incognito writer references were removed.
- Packaging now emits every artifact expected by the Terraform stacks.
- Package paths, Python 3.13, and `app.lambda_handler` match the infrastructure
  defaults.
- Shared entitlement code is included only in functions that import it.
- Artifact uploads use immutable release keys and refuse overwrites.
- CI tests, compiles, shell-checks, and packages the full function set.
- Cognito post-confirmation accepts the users-table ARN currently supplied by the
  identity-workflows stack.

Infrastructure continues to be managed separately by
`theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure`.
