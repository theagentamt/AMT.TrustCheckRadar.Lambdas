# SECUR4ALL-338 governed result History source boundary

This source increment defines a content-free, seven-day History projection over
authoritative V1 check receipts. It adds no runtime, route, index, role, provider
capability, subject, customer access, or deployment by itself.

## Write boundary

`GOVERNED_HISTORY_SETTLEMENT_ENABLED` defaults to `false` on every authority
writer and accepts only the exact strings `true` or `false`. When enabled in an
otherwise qualified runtime, message candidate.3 and URL candidate.2 admission
capture only the accepted epoch, result type, and bounded source type.
Settlement validates a typed user-visible summary and writes the projection,
digest, fixed expiry, and sparse GSI2 keys in the same DynamoDB transaction as
the original accounting result. Pending, pre-admission, missing-summary, legacy,
or malformed rows are never guessed or backfilled.

The deadline is exactly seven days from the original settlement. Replays return
the original receipt and do not rewrite or extend it. Account-deletion fences
block a late settlement, the existing authority deletion worker removes the
entire owned receipt, and the existing explicit expiry worker physically removes
expired receipts. Existing account export continues to project the original
receipt through its allowlist and does not expose the History index or digest.

## Read boundary

The versioned contract is
`contracts/governed-history/1.0.0-candidate.1`. It defines:

- `GET /v1/users/analysis-history`
- `GET /v1/users/analysis-history/{resultId}`

The reader has independent default-off gates:

- `GOVERNED_HISTORY_LIST_ENABLED=false`
- `GOVERNED_HISTORY_DETAIL_ENABLED=false`

It also requires the existing Dev stage, authority, Cognito, exact engineering
subject, active-account, adult, active-device, deletion, complete HMAC inventory,
and key-rotation controls. The device fingerprint and generation are rechecked
after data reads. List cursors are HMAC-signed, expire after 900 seconds, and
bind the account, device fingerprint/generation, HMAC inventory revision,
contract version, and page boundary. Direct lookup accepts only the public
`gh1_...` result identity; operation proofs are never lookup keys.

GSI2 is sparse. Its partition is the existing HMAC account partition and its
sort key is `GOVERNED#<12-digit-settled-epoch>#<32-hex-receipt-id>`. The index
INCLUDE projection is only `recordType`, `state`, `governedHistory`, and
`expiresAt`, plus DynamoDB's automatic table and index keys. Each candidate is
then verified through a strongly consistent canonical `GetItem`, which obtains
the digest and retention deadline. A stale, deleted, expired, or corrupt index
candidate is filtered without failing the page. Canonical reads are capped at
20 per response and a signed continuation cursor advances over examined rows,
including pages containing only stale candidates.

The reader role requires bounded GSI2 `Query`, canonical authority `GetItem`,
account/device/deletion fence `GetItem`, and the existing HMAC key secret read.
It needs no Scan, write/delete, export, recognition, mutation, provider secret,
URL-assessment invocation, or entitlement-operator permission.

Identity fence reads are reader-specific and minimized. IAM should require
`dynamodb:Select=SPECIFIC_ATTRIBUTES` for GSI2 Query and require a non-null
`dynamodb:Attributes` list for each GetItem statement. The source projections
are: profile `PK,SK,sub,status,ageVerified`; device pointer/binding
`PK,SK,recordType,bindingFingerprint,stateVersion,accountId,status`; deletion
`PK,SK`; inventory `PK,SK,recordType,schemaVersion,revision,coverage,issuedKeys`;
and the canonical receipt fields documented above. No profile contact,
demographic, purchase-token, or full-row read is used.

Exact reader settings are:

```text
GOVERNED_HISTORY_INDEX_NAME=GSI2
GOVERNED_HISTORY_CURSOR_TTL_SECONDS=900
GOVERNED_HISTORY_RETENTION_SECONDS=604800
GOVERNED_HISTORY_DEFAULT_PAGE_SIZE=20
GOVERNED_HISTORY_MAX_PAGE_SIZE=50
GOVERNED_HISTORY_MAX_RESPONSE_BYTES=262144
```

## Privacy and accounting

The public item contains typed outcome codes, message keys, source type,
accepted/settled times, stable History identity, original accounting, and the
fixed expiry. It never contains submitted or sanitized text, screenshots,
entities, URLs, check IDs, operation proofs, free-form model explanations,
current allowance, or entitlement snapshots. `outcome` remains the original
strict typed settlement result, including its original provider observation and
deadline. The separate `presentation` object carries effective display verdict,
processing state, coverage, fixed reason codes, and freshness status. An expired
URL match is therefore still preserved as the original high-risk observation
while its presentation becomes historical, unknown, and partial. Reads never
invoke a provider or charge a check. The stored outcome, original charge,
digest, and deadline are unchanged.

## Source verification

Use Python 3.14 with pinned development dependencies:

```text
AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q \
  tests/shared_check_authority/test_governed_history.py \
  tests/governed_history

bash scripts/build_lambda_zip.sh --function governed_history \
  --python-version 3.14 --arch arm64
```

These checks are source, contract, package, and isolated DynamoDB evidence. They
do not claim a deployed Dev index/route/role, activated subject, assembled
mobile flow, UAT, provider, or customer qualification.
