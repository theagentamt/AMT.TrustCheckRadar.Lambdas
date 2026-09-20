# URL resolver Dev deployment handoff — 2026-09-20

## Status and ownership

Lambda agent completed review, hardening, tests, packaging and a controlled smoke harness. **The immutable artifact is published, infrastructure reports the Dev alias Active/Successful on version 1, and all 22 live Lambda smoke cases passed on Python 3.13.** AWS profile `trustcheckradar` SSO was restored, and STS verified the intended account before publication. Infrastructure agent owns Terraform, IAM, networking, the temporary fixture host, deployment and cleanup. Lambda agent owns source, package publication and resolver smoke tooling. User authorized addressing gaps and attempting Dev deployment; analyzer/Google integration remains separate and unchanged.

## Published artifact

- Local ZIP: `/tmp/amt-url-resolver-artifacts/url_redirect_resolver.zip`.
- Verified bucket (versioning Enabled; us-east-1): `trustcheckradar-dev-107827791950-artifacts`.
- Published immutable key: `releases/url-resolver-dev-20260920-d107988b4412/url_redirect_resolver.zip`.
- Account/region: `107827791950` / `us-east-1`.
- SHA256 hex: `d107988b44125b485f439678b2d34ae7debc287a2f4f4d32a17ab532b0666e6c`.
- SHA256 base64: `0QeYi0QSW0hfQ5Z4stNK5968KHovT00yoXq1MrBmbmw=`.
- S3 version ID: `WEvxJt8eMNpmlnqfliCyw_SSncSAKaOT`; infrastructure must pin this exact version.
- Publication used `If-None-Match: *`; exact-version HEAD confirmed SHA256, metadata, 336116 bytes and AES256 encryption.
- Handler/runtime/architecture: `app.lambda_handler`, Python 3.13, ARM64; `dnspython==2.8.0` vendored.

The ZIP is a temporary local build. Recompute its checksum before publishing; if source changes, rebuild and use a new content-derived release key. Never assume an old ZIP matches current source.

## Review changes

- Preserve explicit empty queries in original URLs and relative redirects. Standard `urljoin` otherwise removes `?` or retains the old query, which can change server behavior.
- Reject leading double-slash paths that Python's HTTP client silently rewrites.
- Inspect up to three nested percent-encoding layers for sensitive paths/keys without rewriting the actual URL; reject further encoding layers conservatively.
- Recognize sensitive action paths with semicolon parameters and sensitive query keys separated with semicolons.
- Classify invalid Unicode as invalid input before network access.
- Add controlled fixture and bounded smoke harness, with retries disabled and no raw payload output.
- Add proposed English/Spanish inspection and blocked-link templates to `docs/url-redirect-resolver.md`; no mobile UI integrated. Consumer exposure still requires the documented privacy policy/notice integration.

IPv4 deny-list verified against infrastructure `terraform/url-resolver/main.tf`: all **15 CIDRs match**. Application additionally validates all DNS answers, excludes unsupported IPv6 encodings, and permits only validated IPv4 socket connections. IPv6-only destinations remain unsupported.

## Validation completed

- Full repository suite: **419 passed, 208 subtests passed**.
- Resolver suite: **77 passed** (included in full suite).
- Targeted packaged-handler import/invocation: metadata target blocked with zero requests; required source and vendored DNS modules present in ZIP.
- Fixture and harness compilation passed.
- Nine fixture routes verified with an actual local HTTP server, including explicit empty query behavior.
- Local interpreter is **Python 3.14**. Package build selected **Python 3.13 / ARM64** and pure-Python dnspython. The deployed smoke run has now validated Python 3.13 behavior for the 22 documented cases. These tests do not independently establish every network firewall behavior.

## Publication procedure (completed for this release)

Read-only verification first: caller account, bucket location, bucket versioning (must be Enabled). Upload only the one resolver artifact with `s3api put-object --if-none-match '*'`, SHA256 metadata and checksum, then read back object metadata and version. A preexisting object must not be overwritten; verify exact checksum and pinned version before reuse. Do not print credentials or load unrelated secrets.

Example (after account/bucket verification and local checksum verification):

```sh
aws --profile trustcheckradar --region us-east-1 s3api put-object \
  --bucket trustcheckradar-dev-107827791950-artifacts \
  --key releases/url-resolver-dev-20260920-d107988b4412/url_redirect_resolver.zip \
  --body /tmp/amt-url-resolver-artifacts/url_redirect_resolver.zip \
  --content-type application/zip \
  --metadata sha256=d107988b44125b485f439678b2d34ae7debc287a2f4f4d32a17ab532b0666e6c \
  --checksum-sha256 '0QeYi0QSW0hfQ5Z4stNK5968KHovT00yoXq1MrBmbmw=' \
  --if-none-match '*'
```

The exact version and checksum above have been returned to infrastructure. No infrastructure apply is owned by this agent.

## Controlled deployed smoke

Infrastructure provisions a temporary no-role HTTP fixture host running `scripts/url_resolver_http_fixture.py`. It listens on port 80 and must have ingress limited to resolver NAT EIP. Only static synthetic endpoints exist; no arbitrary redirect input. Optional `AMT_FIXTURE_PRIVATE_IP` environment variable is validated as private IPv4 and supplies `/private-ip`. IMDS may be needed by cloud-init bootstrap, but infrastructure will disable it after bootstrap. Infrastructure owns resource removal immediately after tests.

Run with a narrow assumed Dev test-role environment (preferred), or specify the authorized profile. Do not echo environment credentials.

```sh
python3 scripts/url_resolver_dev_smoke.py \
  --function arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live \
  --fixture-base http://OWNED_FIXTURE_PUBLIC_IP \
  --fixture-private-target \
  --public-https
```

Omit `--fixture-private-target` if the fixture was not bootstrapped with its private IP. Omit `--fixture-base` to run the eight blocked/no-network cases only; `--public-https` explicitly adds `https://example.com/`. The complete harness covers 8 blocked/no-network cases + 12 static HTTP fixture cases + optional private-IP redirect + optional public HTTPS fetch (22 total with both options). It validates status, reason, request count, completeness, HTTP warnings, and empty hop state for zero-request cases.

This covers owned standard redirects, nested redirects, loops, redirect limits, sensitive/private targets, duplicate headers, oversized headers, timeouts, Refresh headers and empty queries. It does **not** claim owned TLS/certificate/DNS-rebinding fixtures or independent firewall enforcement. TLS failure and pinned DNS are covered locally; infrastructure should separately inspect/test live IAM/network enforcement. Verify CloudWatch application events contain only outcome/reason/count/timing and no test URL/checkId/payload. Fixture logs intentionally omit request paths.

If any deployed smoke fails, retain a safe aggregate failure record and diagnose before enabling consumers; no automatic repeated fetching or analyzer integration.


## Completed live validation

Infrastructure confirmed alias `arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live`, version 1, Active/Successful, Python 3.13, and the published code hash. Lambda agent assumed only `arn:aws:iam::107827791950:role/trustcheckradar-dev-url-resolver-dev-test` for invocation; credentials stayed in process memory and were not printed or saved. All 22 harness cases passed. An additional attempt to invoke the unqualified function using the same role returned AccessDeniedException, confirming the alias-only authorization boundary for that principal.

The owned fixture used public address `184.193.21.224`, static private redirect destination `10.254.0.118`, source restriction to resolver NAT `98.82.242.45/32`, no instance role, and metadata disabled after bootstrap. Infrastructure confirmed fixture instance and security-group teardown after the successful tests. These addresses are historical test evidence; do not reuse them.

Read-only CloudWatch inspection found 22 application events, all with exactly the allowed event/status/reason/requestCount/hopCount/elapsedMs fields. No URL, smoke check ID, or metadata-address markers were found. Raw log records were not printed or persisted by the audit. This verifies the observed test invocations, not all possible future inputs.

Aggregate evidence:

- `/tmp/amt-url-resolver-dev-20260920/lambda-smoke.jsonl` — 22 passing cases.
- `/tmp/amt-url-resolver-dev-20260920/lambda-denied-invoke.json` — alias boundary denial passed.
- `/tmp/amt-url-resolver-dev-20260920/lambda-log-privacy.json` — 22 events, zero unexpected schemas or raw input markers.

Owned TLS-certificate failure and DNS-rebinding live fixtures remain outside this smoke run; those paths have local tests. Consumer notices, privacy-policy integration, analyzer/reputation orchestration, allowance enforcement and mobile access remain separate release work. No Google key or analyzer changes were made.
