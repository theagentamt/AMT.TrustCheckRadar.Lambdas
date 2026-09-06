# SECUR4ALL-208 Lambda Evidence

Status: **Core transitions implemented; story completion blocked for merge/split**

Verified behavior:

- Cognito group authorization is required before any campaign lookup or mutation.
- Confirmation and publication require at least 10 contributors.
- Invalid transitions fail with a conflict and make no writes.
- Publication adds the sparse publication-index keys.
- Emergency suppression removes publication-index keys.
- State/version condition and append-only audit creation share one transaction.
- Audit records contain only stable action/state/reason identifiers and the
  reviewer role; free-form reason text and reviewer identity are not persisted.
- Reasons containing obvious direct identifiers are rejected.
- Logs contain no campaign ID, reviewer identity, or reason text.

Reproduce:

```bash
python3 -m pytest -q tests/campaign_review
./scripts/build_lambda_zip.sh --function campaign_review --skip-dependencies
```

Blocking evidence: the approved product flow includes reviewer merge and split,
but finalized aggregate records deliberately contain no contributor tokens or
source ledger. Distinct-contributor overlap therefore cannot be calculated safely,
and the review role is explicitly denied transient-table access. The handler
returns `ACTION_REQUIRES_SOURCE_LEDGER` rather than inventing counts that could
violate privacy or accuracy. An approved overlap-safe aggregate contract is needed
before this story can be marked complete.
