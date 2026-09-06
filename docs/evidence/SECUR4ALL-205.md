# SECUR4ALL-205 Lambda Evidence

Status: **Implementation complete; story evidence blocked**

Implemented evidence:

- Strict, environment-bound five-field SQS envelope validation.
- Missing, deleted, suppressed, expired, and completed-replay no-op behavior.
- Image-baked model loading with `local_files_only=True`, `HF_HUB_OFFLINE=1`, and
  `TRANSFORMERS_OFFLINE=1`; runtime downloads are impossible.
- Revision-pinned Apache-2.0 multilingual MiniLM candidate with a maximum of 384
  normalized, finite, clipped vector dimensions.
- Feature records omit source text and direct identity while retaining bounded
  transient model provenance, taxonomy bucket, language, fingerprint, and token.
- Opaque cluster queue envelope and partial SQS batch failure response.

Automated reproduction:

```bash
python3 -m pytest -q tests/campaign_feature_extractor
python3 -m compileall -q src/campaign_feature_extractor tests/campaign_feature_extractor
```

Current result: 5 tests pass, including 2 parameterized negative subtests.

Completion blockers that must not be fabricated:

1. Docker Desktop is available and reports a Linux/ARM64 engine, but the build made
   no progress beyond resolving `public.ecr.aws/lambda/python:3.13-arm64` for two
   minutes and was canceled. An ARM64 image digest, runtime smoke test, and
   vulnerability scan therefore could not be produced from this host/network.
2. The approved English/Spanish labeled fixtures required to measure at least 95%
   precision and 80% recall are not present.
3. UAT memory, p95 latency, throughput, and measured cost require the immutable
   image running against those approved fixtures.

Until those artifacts exist, this story cannot truthfully be marked complete even
though its Lambda application boundary is implemented.
