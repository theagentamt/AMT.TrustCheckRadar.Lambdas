# Contained all-component deletion qualification

This extends the existing campaign qualification runner with four synthetic
cases. It changes no production source, runtime gate, retention policy, inventory
approval or deployed package. All eleven upstream receipts are produced by the
real cleanup functions; the fixture does not insert component receipts. The
shared finalizer then writes IDENTITY and the terminal fence using injected
Cognito. Actual Cognito deletion remains unperformed and separately scoped.

The fixture's full-account, purchase, HMAC and campaign inventory rows are explicit
synthetic prerequisites. Successful execution does not approve historical
coverage, lost-key coverage, restoration safety or any production inventory.
Paid usage and token ciphertext are synthetic schema fixtures: no store purchase,
provider verification, acknowledgment, token encryption or decryption is claimed.

## Cases and producer composition

- `handler_all_components`: 102 device records (including ACTIVE_BINDING),
  recovery rate/receipt/audit state, analysis request/rate/scan-rate/consumption,
  outbox event/locator, legacy entitlement and ownership lock/locator, 78 History
  records/locators over three generations, a paid pending CHECK/period/global
  counter, token/reverse binding pair, frozen campaign contribution, and profile.
  Real producers create SESSION_REVOCATION, DEVICE_BINDINGS, DEVICE_RECOVERY,
  ANALYSIS_ABUSE, CAMPAIGN_OUTBOX, ENTITLEMENTS, HISTORY, V1_AUTHORITY, PLAY_TOKENS,
  CAMPAIGN and USER_PROFILE before finalization. Campaign uses its actual stream
  handler; other components call their production cleanup functions against real
  SDK tables. This is not evidence for every deployed adapter, schedule or IAM role.
- `handler_all_components_lost_ack`: after the real session receipt Put commits,
  discard its acknowledgment. Retry must reuse that receipt without another
  sign-out or deadline change, then complete the same composition.
- `handler_all_components_history_retry`: after a real History erasure progress
  Update commits, interrupt the invocation's work and resume from a strongly read
  job. No HISTORY receipt may exist before every generation/stage completes.
- `handler_all_components_unknown_recovery`: unknown recovery family remains
  intact, no DEVICE_RECOVERY receipt is written and identity cannot finalize.

Device cleanup traverses a real 100-item page plus continuation. History uses
real 25-item pages per generation and current 120-day dedup/mutation settings.
Owned other-account rows must survive. Recovery receipt/audit retain their
original seven-/90-day deadlines; consent audit retains its original 400-day
expiry. The global purchase record retains seven used checks and its original
access-end-plus-seven-day expiry while one pending reservation becomes zero.
All upstream receipts retain their original 120-day clocks through replay.
Terminal replay of the real authority/token/History/campaign paths must not
recreate erased data or call identity again.

## Isolated AWS resource contract

Use the existing provisioner, event schema, source verification, exact run tags,
account/region checks and fixed `campaign_qualification.lambda_handler` entrypoint.
The existing three-table fixtures remain compatible and do not require extra env
values. Only the four new cases require these additional tables:

| Kind | Environment variable |
|---|---|
| devices | QUALIFICATION_DEVICES_TABLE |
| recovery | QUALIFICATION_RECOVERY_TABLE |
| abuse | QUALIFICATION_ABUSE_TABLE |
| outbox | QUALIFICATION_OUTBOX_TABLE |
| entitlements | QUALIFICATION_ENTITLEMENTS_TABLE |
| history-control | QUALIFICATION_HISTORY_CONTROL_TABLE |
| history-content | QUALIFICATION_HISTORY_CONTENT_TABLE |
| authority | QUALIFICATION_AUTHORITY_TABLE |
| tokens | QUALIFICATION_TOKENS_TABLE |

Every name must equal `amt-campaign-completion-qual-<12 lowercase hex run ID>-<kind>`.
All nine have only string PK/SK, no GSI, and exact existing qualification tags.
The original ledger alone has the existing CampaignRecoveryDueIndex. No real
store or account table may substitute. Resource preflight completes before any
fixture seeding/reset. Reset scans are strongly consistent and limited to two
100-row pages per table; incomplete discovery prevents reset mutation.

Existing fixture-role DynamoDB read/write/transaction permissions must cover
only these exact twelve synthetic tables, plus the existing exact ledger index.
No Cognito, Secrets Manager, provider or production-table permission is needed.
KMS remains the existing isolated HMAC fixture key. The new resource adapter
rejects foreign table names and checks remaining time before every SDK operation;
the old runner retains the same six-second cutoff. SDK timeout bounds do not
promise an invocation deadline. A 120-second isolated fixture timeout is
recommended for seeding and hundreds of bounded calls; no production timeout
change is implied. No batch writer or extra discovery index is introduced.

Build from a clean committed source with:

```
python scripts/build_campaign_qualification.py --source-sha <full SHA> --output-dir /tmp/<qualified-output>
```

The builder verifies every added source member against Git and includes additional
producer modules only in the qualification ZIP. The four emitted production ZIPs
remain unchanged by this test-only increment. Runtime dependencies for these
paths are Python standard library and Lambda-provided boto3/botocore; cryptography
and provider paths are not exercised. An actual Linux ARM64 run must still pass
before recording cloud acceptance.

Local validation: 83 SDK/Moto tests passed, including all 52 runner cases,
resource-name containment, source verification and sanitized failure cases.
Compilation and diff checks also passed. The same runner is exercised with:

```
AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q tests/campaign_deletion_bridge/test_qualification_dynamodb.py
```

Cloud fixture creation/invocation, actual identity acceptance, production marker
approval, backfill, package publication and live deletion activation are separate
reviewed steps. Five-minute Play checkpoint retention approval is not inferred;
these account-owned token deletion tests do not enable that worker checkpoint.
