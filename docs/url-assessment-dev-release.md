# Private URL assessment Dev release evidence

Candidate source commit: `1f47f5b90fbff19d27a981206a70ef60e88ed267`.

- Bucket: `trustcheckradar-dev-107827791950-artifacts`
- Key: `releases/1f47f5b90fbff19d27a981206a70ef60e88ed267/url_assessment.zip`
- S3 version: `RfyEhgRrMSD3MMztj.VMwnNBOrZ1eWmA`
- SHA256: `938687a77264440cc8429ebdbe9411c51900c68b374201b24a5e1cb16f94bd54`
- Terraform source_code_hash: `k4aHp3JkRAzIQp69vpQRxRkAxos3QgGySl4csW+UvVQ=`
- Bytes: `339912`
- Runtime/architecture: Python 3.14 / ARM64; handler `app.lambda_handler`.

Publication used the authorized existing Dev credentials after verifying account `107827791950` and enabled artifact-bucket versioning. Conditional `If-None-Match: *` prevented overwriting the chosen key. A version-pinned HEAD verified VersionId, checksum, size and SHA256 metadata. Only the new assessment ZIP was uploaded. No secret value was read by publication tooling; no Lambda runtime, alias or IAM changes were performed by the Lambda agent.

Local Python 3.14 validation: **533 tests passed, 162 subtests passed**; actionlint, shellcheck and whitespace checks passed. Packaged handler import and metadata-block fixture passed directly from the ZIP without AWS or Google calls. Tests include fixed-host TLS/parameter behavior, malformed/truncated framing, provider redirect refusal, response limits, failed authentication/rate limiting, strict Lookup-versus-Evaluate parsing, SSRF/sensitive input blocking, resolver response validation, high-risk precedence, limited coverage, no consumer access, and log minimization.

Root deployed the pinned artifact as private Dev alias `trustcheckradar-dev-url-assessment:live`, version 1, Python 3.14 / ARM64, and verified Active/Successful before testing. Implementation PR [#9](https://github.com/theagentamt/AMT.TrustCheckRadar.Lambdas/pull/9) merged at `33669f1fb450f3de211cd4e5f2a65fc80e65a71c` after exact-head CI [35513407214](https://github.com/theagentamt/AMT.TrustCheckRadar.Lambdas/actions/runs/35513407214) passed. The source implementation/deployment contract is [url-assessment-private-dev.md](url-assessment-private-dev.md). The live acceptance below is distinct from synthetic local tests. The existing public legacy Dev analyzer remains unchanged by user instruction.

## Live Dev acceptance on September 20, 2026

All **8 live cases passed** through `arn:aws:iam::107827791950:role/trustcheckradar-dev-url-assessment-dev-test`, assumed for 15 minutes using existing authorized Dev credentials. Temporary credentials existed only in process memory/environment, never tool output or a file. Requests were direct synchronous calls to the qualified live alias; no automatic retry or log-tail retrieval.

- Metadata, private address, userinfo, encoded header injection, sensitive token and non-HTTP inputs: unknown/blocked or invalid-input; zero observed hops and zero provider calls.
- Explicit `https://example.com/`: complete supported checks, no known threat, one provider attempt. This is not a blanket safety guarantee.
- Google's documented synthetic malware fixture: high-risk match with MALWARE, one provider attempt. Partial/limited records intentional early stopping after a known match, not uncertainty that erases that match.

Sanitized case results are in [url-assessment-dev-smoke.json](url-assessment-dev-smoke.json). Root independently owns IAM-denial checks, log-content inspection, alarms, Terraform drift, and deployment evidence. These eight cases do not claim owned redirect-fixture, DNS-rebinding, production, paid consumer, trial/accounting or mobile UI acceptance; those remain separately scoped. No customer or real malicious URL was used.
