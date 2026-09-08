# SECUR4ALL-213 Canonical Contract Evidence

Status: **Canonical repository artifact complete; Android adoption and external approval pending**

- `contracts/campaign/v1/` contains immutable draft 2020-12 schemas for
  `appFeatures`, outbox, queue envelope, transient feature, aggregate, review
  transition, and app-facing trends response.
- Every schema has a `1.0.0` identifier and rejects unknown fields. The app feature
  schema bounds strings, lists, vector dimensions/range, identifiers, confidence,
  versions, and field presence; runtime validation additionally rejects booleans,
  non-finite values, non-UTF-8 data, and canonical encodings above 32 KiB.
- `taxonomy.en.json` and `taxonomy.es.json` have identical stable IDs across all
  ten required dimensions. Every dimension includes `other` and `unknown`.
- English/Spanish valid fixtures and prohibited-field, unsupported-version, and
  dimension-invalid fixtures are included and exercised against both JSON Schema
  and the production runtime validator.
- `campaign-contracts-1.0.0.zip` is deterministic, includes an internal
  `SHA256SUMS`, and is included in the release-level checksum manifest and
  immutable upload workflow.
- Compatibility rules and the prohibition on images, raw OCR/content, identities,
  precise timestamps, full identifiers, and unthresholded reviewed domains are
  documented in the artifact README.

Reproduce:

```bash
python3 -m pytest -q tests/campaign_contracts tests/shared_campaign_contracts
make package-no-deps
unzip -t dist/campaign-contracts-1.0.0.zip
python3 scripts/validate_campaign_lambdas.py --dist-dir dist
```

The Android repository must consume this version and provide extractor/runtime
fixture evidence. Legal/privacy and security approval of the contract remains an
external promotion gate.
