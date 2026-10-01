# SECUR4ALL-221 source acceptance handoff

This increment supplies the legacy wire correction and a separate governed
sanitizer supplement. It is not a deployment. SECUR4ALL-222 retains legacy Dev
verification; Android ATCR-79 and iOS ITCR-13/92 retain platform implementation and
acceptance. Root orchestration records the immutable source/fixture links in those
stories before declaring the shared handoff complete.

| Requirement | Source and evidence |
| --- | --- |
| Legacy 65536 actual UTF-8 bytes; strict base64/UTF-8; no JSON reserialization sizing | conversation_analysis/validation.py; test_wire_validation at 65535/65536/65537, raw/escaped6000emoji, bad encodings, scalar rejection |
| Legacy 8000 code points after existing trim, no normalization | 7999/8000/8001 Unicode tests; combining/ZWJ/whitespace compatibility |
| Legacy entities, source, consent/features, structured errors | Existing full conversation_analysis tests plus versioned request schema, feature reference, real response fixtures and 0/100/101 entity tests |
| Reject before downstream work and do not log raw content | Handler tests mock identity/device/service for invalid wire; log and response sentinel assertions |
| Preserve logical request/source/idempotency/outbox | Equal parsed payload for raw/escaped/base64/dict representations; unchanged service/abuse suites cover hashes, source and campaign paths |
| Shared nine-type masking semantics | Versioned profile and 41 curated EN/ES raw-to-token fixtures; deterministic normalization/overlap/reuse, OCR/mixed, punctuation and unsupported examples |
| Governed exact reviewed projection and limits | Existing immutable candidates, supplemental malformed/reuse/cardinality/code-point tests and real consumer integration identity/32768-byte tests |
| API second privacy boundary | Additive residual IPv6 guard before check allocation/provider work; authenticated abuse counting retained; direct evaluator/vendor tests |
| Historical receipt compatibility | v1/v2 old IPv6 raw replay is rejected without new charge; contentless original-proof reconcile returns the unchanged stored outcome/accounting |
| Versioned access for both mobile clients | contracts/sanitizer/v1 SHA256SUMS; Android byte-for-byte mirror/parity handled in its PR; iOS tracker handoff recorded by orchestrator |
| Provider/evaluation integrity | New source profile pins, unchanged prompt/schema, historical packets retained; empty registries and no execution grant from this artifact |

The supplemental client profile has a 12000-code-point preparation cap and no
silent truncation. It is format masking, not all-PII recognition or ownership
inference. The server cannot verify normalized original-value equality once
originals are removed. Backend tests validate structural projections and rejection
boundaries; Android executes the same raw fixtures. Mobile performance/device
acceptance is separately evidenced by that agent, not inferred from Python tests.

Local validation uses Python 3.14.7, no provider calls:

```
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/sanitizer_contract tests/conversation_analysis
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/message_evaluator tests/message_ai_evaluation tests/message_ai_controlled
AMT_AUTHORITY_INTEGRATION=1 PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider tests/message_consumer tests/shared_check_authority
```

Keep legacy tests separate from Moto suites because existing legacy fixtures stub
AWS modules. Source-only ARM64/Python3.14 archives built using the unchanged build
script include exact guard/validation bytes and exclude evaluation tooling; these
are packaging checks, not dependency-qualified deployable artifacts. No Terraform,
IAM, workflows, immutable candidate artifacts, deployment or activation changed.

SEC221 source criteria can be completed after reviewed release integration and
both mobile handoff receipts. Do not equate that with SEC222 deployment, iOS
implementation, physical-device acceptance or provider quality qualification.
