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

Infrastructure deployment and live smoke evidence are pending at publication time. The source implementation/deployment contract is [url-assessment-private-dev.md](url-assessment-private-dev.md). A follow-up evidence update must distinguish real Google/IAM/network acceptance from these synthetic local tests. The existing public legacy Dev analyzer remains unchanged by user instruction.
