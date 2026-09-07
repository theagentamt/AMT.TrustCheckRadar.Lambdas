# SECUR4ALL-217 Lambda Evidence

Status: **Lambda implementation complete; infrastructure activation pending**

The Lambda contract keeps participation optional and off when no current record
exists. `campaign_participation.zip` serves authenticated HTTP API v2 GET/PUT
requests using only Cognito JWT `claims.sub`.

Implemented and tested:

- Exact V1 join/withdraw request validation, configured notice/policy ownership,
  UUIDv4 operation idempotency, and rejection of body identity/unknown fields.
- One-read idempotency through a transactionally written
  `CAMPAIGN_OPERATION#<operationId>` item; retries never scan or filter the
  400-day consent receipt history.
- Current `not_enrolled`, `enrolled`, `withdrawal_pending`, and `withdrawn` state
  responses without internal keys or account identity.
- New UUIDv4 consent epochs for every re-enrollment and append-only transition
  receipts retained for exactly 400 days.
- Atomic participation, receipt, entitlement, and withdrawal-command writes.
- Base free 10, participating free 15, bonus 5, unchanged Pro, and used-count
  preservation across join/withdraw cycles.
- Server-authoritative entitlement reads and analysis outbox authorization.
- Withdrawal remains pending until the deletion bridge succeeds; completion then
  atomically condition-updates the same epoch/operation, appends a privacy-safe
  receipt, and marks the ledger command complete. Failed deletion remains
  retryable and completed stream updates do not loop.

Reproduce:

```bash
python3 -m pytest -q tests/campaign_participation tests/conversation_analysis tests/entitlement_snapshot tests/campaign_deletion_bridge tests/campaign_observation_publisher
python3 -m compileall -q src tests
./scripts/build_lambda_zip.sh --function campaign_participation --skip-dependencies
unzip -t dist/campaign_participation.zip
```

Infrastructure must supply the routes, JWT authorizer, table names/ARNs, transaction
permissions, notice/policy versions, retention/SLA values, and quota values listed
in `lambda_deployment_dependency_contract.md`. No AWS resource was changed or
deployed by this evidence.

Latest local evidence (2026-09-07):

- Full suite: 177 tests and 73 parameterized subtests passed.
- Content-free static check: 62 log templates passed.
- `campaign_participation.zip` SHA-256:
  `6fa83bbed6e1f9bd8ba1d0b5a10a7ba562de9db0b8f4a8230bb6a73849aa05d8`.
- Campaign package inspection and server-extractor absence checks passed.
