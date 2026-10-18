# History dependency read failure boundary

SECUR4ALL-345 is a bounded Dev bug under the existing open History qualification
story [SECUR4ALL-226](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-226).
A controlled SDK connection exception from the mutation receipt read previously
returned nonretryable500 and reached `LOGGER.exception`, including its synthetic
SDK diagnostic text in application logs. The public response was already fixed.
The retained reproduction establishes a source defect, not a deployed incident
or actual private-data exposure. No raw SDK logs were retained.

## Fixed behavior

Only the mutation API's unhandled-error boundary changes. The existing
`HistoryError` handler stays first. All transaction conditions and SEC344's
conditional-versus-unavailable mapping remain unchanged. Account, age, deletion,
active-device and legacy binding checks still run with their original arguments
and consistent reads. A read exception is never treated as an absent profile,
receipt, state or device record, and never authorizes a mutation.

Known botocore endpoint/connection-closed/connect-timeout/read-timeout exceptions
return the existing `503 SERVER_UNAVAILABLE`, `retryable=true`, with the fixed
message “The History dependency could not be read.” A transport exception does
not identify an account, authentication cause or exact SDK phase.

Typed `ClientError` qualifies as a transient read error only when its operation
is an exact plain string `GetItem` or `Query` and its error code is one of the
DynamoDB SDK model's documented read errors below. Response and Error metadata
must be ordinary dictionaries and Code an exact string. HTTP status, SDK text,
request IDs, returned items and duck-typed exception attributes are not evidence
of retryability.

| SDK code or exception | Fixed diagnostic category | HTTP / retryable |
| --- | --- | --- |
| ProvisionedThroughputExceededException, RequestLimitExceeded, ThrottlingException | SDK_READ_THROTTLED | 503 / true |
| InternalServerError | SDK_READ_INTERNAL | 503 / true |
| Four explicit connection/timeout exception types above | SDK_TRANSPORT_UNAVAILABLE | 503 / true |
| Other well-formed GetItem/Query ClientError, including permission, missing resource, validation and conditional errors | SDK_READ_REJECTED | 500 / false |
| Other/malformed SDK operation or metadata | SDK_FAILURE | 500 / false |
| Unexpected programming or other exceptions | UNEXPECTED_FAILURE | 500 / false |

Nontransient and unknown defects keep the existing `500 INTERNAL_ERROR`,
“An internal error occurred.”, `retryable=false`. A read error does not prove a
conditional write race and cannot become409. The finite diagnostic record has
only `failureClass` from the table: no exception formatting, stack trace,
arbitrary class name, SDK message/metadata, endpoint, item, key, account or token.
Existing operation metrics and no-store response headers remain unchanged.
This does not claim to change logging in other handlers or the SDK itself.

There is no new application retry or automatic mutation replay. SDK-internal
retry behavior remains unchanged. An explicitly retried action must keep its
original operationId and target; bootstrap retains account-scoped state
idempotency. A failure reading an already committed receipt does not prove that
the earlier mutation rolled back. Recovery rechecks current authorization and
binding before reconciling the original receipt; it does not extend receipts,
retention, jobs or counters.

## Local evidence and corrections

The companion validation JSON pins source, tests, archive and independent reviews.
Actual handler tests use controlled SDK failures with real local Moto account,
device, receipt/state and transaction fixtures. They cover all five mutation
routes, initial authority/deletion reads, current and legacy device checks,
bootstrap's repeated eligibility and conditional follow-up reads, state/locator
reads, zero mutation on read failure, post-commit read failure and explicit
same-action recovery/replay. Revoked/deleted/minor/inactive/device-changed
recovery stays denied. An unrelated account baseline remains unchanged.

The independent reviewer reproduced a malformed nonstring operation object
which could imitate set membership. The correction captures the operation once
and requires an exact string before comparison; retained adversarial tests
verify safe500 and no write. Unexpected exceptions with toxic string formatting
are never formatted. Two parent test assertions initially expected202 for a
completed clear-history receipt; they were corrected to its existing200 without
changing production behavior. The independent test's first isolated run lacked
its shared fixture import; that fixture-only correction and initial results are
retained separately. No failing run is presented as a pass.

Only `src/history_mutation_api/app.py` changes in production. All other production
files, transaction service, contracts, workflows, scripts and dependencies match
the integration base. The inspection ZIP contains source only, excludes
dependencies and is **not deployment-qualified**. Its import succeeds with socket
and SDK constructors blocked. Local synthetic/Moto tests do not prove deployed
IAM, target authorization, service retries, distributed races or live faults.

## Run locally and hand off to SEC226

With the existing pinned requirements-dev.txt installed, from the reviewed repo:

```sh
PYTHONPATH=src AWS_EC2_METADATA_DISABLED=true \
AWS_ACCESS_KEY_ID=synthetic AWS_SECRET_ACCESS_KEY=synthetic \
AWS_SESSION_TOKEN=synthetic AWS_DEFAULT_REGION=us-east-1 \
python -B -m pytest -q tests/history_mutation_api
```

These are local fixtures only; there is no live fault command. The companion
JSON records the larger disjoint preservation suites and their exact results.
Dev completion requires reviews, passing required local checks, committed and
pushed evidence, verified `release-V01` integration and tracker readback. The
existing SEC226 target qualification remains open under the owner's component
and release-test separation. No sprint/date is invented.

Later authorized assembled-target qualification must pin the dependency-complete
artifact, configuration and exact routes/role, preserve auth/binding/retention
and use reviewed disposable fixtures plus an unaffected-account baseline. Verify
normal and denied requests, fixed transient503, nontransient500, unchanged
conditional409, and explicit same-operation recovery with original receipt/job
metadata and no duplicate generation transition. Capture only finite outcomes,
counts and safe diagnostic enums. A live-safe fault harness is not established:
review and authorize a disposable-target-only harness before any fault case;
never throttle customer tables or falsify service responses to manufacture a pass.
Record cleanup and unresolved results; unperformed cases remain open in SEC226.
Physical-only acceptance stays in ATCR-148 where applicable.

No AWS read, endpoint call, auth/session lookup, fault injection, runtime/IAM/gate
change, deployment, mobile action, purchase or main promotion occurred here.
