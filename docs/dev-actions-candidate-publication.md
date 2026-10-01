# Dev candidate publication through GitHub Actions

The owner authorized infrastructure and Lambda deployment workflows on September 21, 2026. This permits the explicit manual deployment workflow below on `release-V01`; it does not change automatic CI timing, permit Android deployment, promote `main`, or authorize UAT/Prod. Local validation still precedes integration.

The registered `Publish Lambda release` workflow now accepts `mode=research_candidate`, `environment=dev`, and `source_sha=<exact release-V01 head>`. Leave `source_run_id` empty. The dispatch ref must be `release-V01` and its resolved SHA must match the supplied SHA. The existing `main_ci` mode continues to require successful main push CI; automatic triggers are unchanged.

The job checks source and isolated emulator suites, builds exactly the nine reviewed research/legacy-boundary packages with full dependencies for Python 3.14/arm64, and records SHA-256, dependency metadata, source commit and package sizes. Each ZIP passes offline source compilation and handler import. These checks do not execute native ARM64 extensions or call a provider. Dependencies are resolved during this new build; the resulting artifact versions and hashes, rather than a previous host-build hash, are the deployment inputs.

Only after those gates does it acquire the existing Dev publisher role. It conditionally publishes to `trustcheckradar-dev-107827791950-artifacts/releases/<source-sha>/<function>.zip`, refuses different existing content, requires versioning, and verifies each current object version, checksum and size. A partial failed run may leave verified immutable objects; it does not yield a complete publication manifest. An exact-byte retry reuses existing versions. A rebuild that resolves different bytes at the same source SHA fails closed and needs an explicitly reviewed new release/build decision, not an overwrite. Actions preserves build and publication evidence for 90 days.

This workflow never updates a Lambda function, alias, route, environment, IAM role or account item. No consent, purchase ownership, privacy deletion or campaign gate is enabled. Publication prepares immutable deployment inputs; it does not complete migration. Infrastructure owns the separate reviewed plan and deployment.

## Existing Dev readiness

Read-only metadata on September 21 confirmed the nine migration functions still use their legacy Python 3.13 artifacts. At that inspection the publisher's trust permitted only the `main` ref with its exact repository-ID/environment subject. A separately reviewed narrow Dev trust change was required before this release dispatch could obtain AWS credentials; its subsequent successful use is recorded below. The current publisher grants S3 release object publication and campaign-image ECR access, but no Lambda update or account-data access; this workflow uses only its S3 and caller-identity operations.

The [migration handoff](research-consent-access-migration.md) remains authoritative for runtime cutover. Qualify current paid/trial/complimentary consumers and purchase/restore first, or obtain review of temporary unavailability; replacing the old handlers with retirement responses does not preserve working paid service. Inventory old consent epochs, grants and in-flight accounting, drain old publishers and cached credentials, establish writer/cleanup coverage and preserve campaign kill switches. Merely uploading packages satisfies none of those gates.

## Local validation for this workflow change

- `actionlint .github/workflows/publish.yml` passed.
- Publisher tests: 22 passed (14 candidate and eight existing resolver cases), covering exact scope/checksum rejection before cloud access, Dev target binding, conditional publication, exact retry and concurrent object replacement.
- Existing nine full-dependency packages from the integrated source passed the new Python 3.14 offline compile/import verifier; no handler invocation occurred.
- Compile checks and `git diff --check` passed. A fresh Actions build will produce its own source/run/version evidence; no Actions success or live publication is claimed by this local record.


## Completed publication

[Actions run 35675976296](https://github.com/theagentamt/AMT.TrustCheckRadar.Lambdas/actions/runs/35675976296) succeeded at exact merged release commit `d98ffd65b42d54953ad83e980e58846b6fc02c5d`. The separately reviewed Dev OIDC trust change was effective and the workflow assumed the existing publisher role. All nine candidate ZIPs were published to versioned Dev S3 objects with no runtime update.

The [durable publication manifest](evidence/dev-actions-publication-35675976296.json) records every bucket/key/version/hash, resolved dependency set, source/run identity and validation result. The nine downloaded Actions packages were independently hashed and matched all publication records. Eight source-only package hashes match the previous local build; the purchase ZIP differs, so this manifest is the authoritative input for this candidate's infrastructure plan. Do not substitute the earlier host-build purchase hash.

Actions validation passed: 1,769 ordinary tests, 232 subtests, 22 skips; 73 isolated consent/campaign emulator cases; 40 isolated replay/inventory cases; nine offline package compile/import checks. No provider invocation or native ARM64 execution was performed. The main-promotion job was correctly skipped.

Candidate publication is complete. Runtime migration is not: paid/trial/complimentary and purchase/restore availability, old in-flight accounting, old-epoch cleanup, inventory/marker qualification and coordinated campaign drain remain acceptance gates. Routes, functions, aliases, stored account data and activation flags were unchanged by this run. Infrastructure may use these exact pins in a plan without treating publication as approval to activate them.
