# URL resolver Python 3.14 compatibility and release candidate

## Scope

The URL redirect resolver now targets Python 3.14 ARM64. Other functions retain their existing Python 3.13 build targets. The build script chooses the resolver target for both single-function and all-functions builds; an explicit `--python-version` override remains available. The CI workflow adds a dedicated Python 3.14 resolver test, ARM64 package build, and packaged-handler import check. Existing Python 3.13 CI coverage remains.

No resolver business logic, dependencies, request/response schema, retention, authorization, URL inspection, or analyzer code changed. Runtime/provider deployment and the live alias are owned by infrastructure. Historical Python 3.13 deployment evidence in `url-resolver-dev-deployment-handoff.md` and `url-resolver-dev-evidence-20260920/` remains historical.

## Published immutable candidate

- Bucket: `trustcheckradar-dev-107827791950-artifacts` (account `107827791950`, region `us-east-1`, versioning Enabled).
- Key: `releases/url-resolver-py314-dev-20260920-d107988b4412/url_redirect_resolver.zip`.
- Object version: `zKohCq1dfkcQb5f2IPscPfiYv9RoIFYB`.
- SHA256 hex: `d107988b44125b485f439678b2d34ae7debc287a2f4f4d32a17ab532b0666e6c`.
- SHA256 base64: `0QeYi0QSW0hfQ5Z4stNK5968KHovT00yoXq1MrBmbmw=`.
- Handler/runtime/architecture: `app.lambda_handler`, `python3.14`, `arm64`.
- Size/encryption: 336116 bytes, AES256.
- S3 metadata: `target-runtime=python3.14`, `target-architecture=arm64`, and exact hexadecimal SHA256.
- Local candidate: `/tmp/amt-url-resolver-py314-artifacts/url_redirect_resolver.zip`.

Publication used `If-None-Match: *`; exact-version HEAD confirmed checksum, metadata, size and encryption. This pure-Python package is byte-identical to the earlier Python 3.13 package because source and pinned `dnspython==2.8.0` did not change. Runtime selection happens in Lambda configuration, not inside the ZIP; identical hashes do not establish identical runtime behavior.

## Local compatibility evidence

- Python interpreter: **3.14.7**.
- Full repository suite: **419 passed, 208 subtests passed**.
- Resolver subset: **77 passed**.
- Resolver default build selected Python 3.14 ARM64 and included pinned dnspython.
- ZIP imported `app` and `dns.resolver` under Python 3.14 and rejected an AWS metadata destination before network activity.
- Representative unrelated function build retained Python 3.13; explicit resolver Python 3.13 override still worked. Those source-only target checks were not published.
- `shellcheck scripts/build_lambda_zip.sh`, `actionlint .github/workflows/ci.yml`, and `git diff --check` passed.

The new GitHub CI job is source configuration; local results do not claim a completed remote CI run. Feature-branch pushes alone do not run this repository's CI, which is triggered by pull requests and main-branch pushes.

## Infrastructure handoff and live validation

Infrastructure owns the reviewed Terraform provider/runtime migration and alias rollout. After deployment, the Lambda agent will run the existing narrow-role smoke harness with `--public-https` and no EC2 fixture: eight blocked/no-network cases plus one benign HTTPS request. These bounded checks must be distinguished from the earlier 22-case owned-fixture run on Python 3.13. No new fixture, customer URL, analyzer integration, UAT or production rollout is authorized by this candidate publication.

Live Python 3.14 results: **pending infrastructure rollout**.
