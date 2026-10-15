# SECUR4ALL-233: scoped synthetic Dev URL qualification

Status: local implementation and synthetic/offline validation only. No deployment,
AWS write, provider/endpoint contact, workflow dispatch, push, merge into release,
identity creation/sign-in, device/trial/allowance change, or runtime activation was
performed. This is not full SECUR4ALL-233 completion or live readiness. SEC334
and ITCR-98 remain the existing release-test follow-ups; no duplicate was created.

Source: feature branch `codex/sec233-scoped-url-qualification`, initially based on
`release-V01` `8cfa4c1a395942778a64f35d3dac566e6e8920e1`; current main
`8ecc79678fd1f87c575c43f7f4ffb856869db48d` was merged into the local feature
only (`2ad261d10c0f729159a6ef18a5178e7207a9519b`) to retain the SEC230 ledger,
current diagnostics and other current implementation. No main/release branch was
changed. The new delta changes no entitlement, Message, History, identity,
purchase, device, trial or account configuration. Retained appreview identity and
Android binding are untouched.

## Controls and boundaries

The public URL wire remains transport `1.0.0-candidate.2` with assessment
`0.3.0-candidate.1`. No public or private request/response fields were added.
Original-proof reconciliation requires the authenticated owning account and the
original check identity/proof, not a resubmitted URL. It does not require the
current device, trial, subscription, active qualification window, provider flag,
retention flag or cleanup heartbeat. The existing authority/account deletion and
receipt retention checks remain mandatory.

Every new boolean defaults OFF. Missing or malformed configuration fails closed
once any qualification environment key is present. Total absence of the new
qualification configuration preserves the retained legacy URL behavior; explicit
`URL_QUALIFICATION_ENABLED=false` cannot select legacy provider behavior.

| Environment | Meaning |
| --- | --- |
| `URL_QUALIFICATION_ENABLED` | Explicit scoped mode; defaults `false` |
| `URL_NEW_ADMISSION_ENABLED` | Prepare/new submit; defaults `false` |
| `URL_PROVIDER_ENABLED` | Resolver egress and Google lookup/cache path; defaults `false` |
| `URL_RECONCILIATION_ENABLED` | Original-proof URL recovery independent of global consumer/new/provider controls; defaults `false` |
| `URL_RETENTION_APPROVED` | Explicit owner retention approval; defaults `false`, **still pending** |
| `URL_SUBJECT_ALLOWLIST_JSON` | Separate validated JWT UUID list, exactly one scoped actor; never broadens engineering/Message/History gates |
| `URL_QUALIFICATION_MANIFEST_JSON` | Trusted public synthetic manifest below; no client override |
| `URL_QUALIFICATION_TABLE_NAME` | Dedicated store, never identity/authority tables |
| `URL_CLEANUP_ENABLED` | Scheduled cleanup only; defaults `false`; also requires exact `URL_RETENTION_APPROVED=true` |

Existing `STAGE=dev` and authority configuration remain required. Existing
`CONSUMER_ENABLED` remains a legacy gate and is not needed for explicitly enabled
scoped reconciliation. A safe future rollback closes new admission/provider and
retains URL reconciliation/authority access until original reservations settle or
expire; never delete receipts, reset counters, rewrite allowances, or erase locks
as a retry strategy. Existing customer accounting remains authoritative.

The trusted manifest has exactly `version:1`, `runId`, integer `startEpoch` and
`endEpoch`, `syntheticOnly:true`, and `targets:[{id,url}]`. Window length is at most
3,600 seconds, start inclusive/end exclusive. `runId` must be
`<10-digit startEpoch>_<suffix>` with suffix `[A-Za-z0-9_-]{1,32}` and must match
the supplied start. A run ID therefore cannot be reused for a later window after
its retained budget expires. Target IDs are `[A-Za-z0-9_-]{1,48}`; 1–16 unique IDs
and exact URLs are permitted. The manifest must be operator-reviewed public
synthetic targets only. Credentials, fragments, non-ASCII/control characters and
backslashes are rejected. Existing canonical URL/SSRF restrictions still apply.
The parser bounds manifest JSON to 12,000 bytes, while future infrastructure must
also enforce the **complete Lambda environment** 4-KiB service limit; the parser
bound is not a deployment allowance.

Manifest approval is checked before resolver DNS for every hop, and again before
resolver connect/GET and Google GET after DNS/TLS. A redirect to any URL outside
the trusted manifest is blocked before that target's DNS or HTTP contact. Resolver
source modification is the necessary extension of this slice to enforce this
boundary; all prior pinned DNS, TLS, URL, redirect, response-size and timing checks
remain. Consumer/assessment/resolver budgets retain their existing limits, with
assessment/resolver budget also bounded by the run deadline. No new provider,
AI, VirusTotal or Evaluate path exists; the source bound of six lookups remains.

## Durable caps and cache

The new table has string `PK/SK`, TTL `expiresAt`, and `GSI1` on
`GSI1PK/GSI1SK` projecting `expiresAt` plus its automatic key attributes. It must
have PITR and backups OFF. No new table row or log contains an account identifier,
URL, check/client ID, original proof, payload hash or device binding. A random
lock-owner token is a coordination nonce, not a consumer proof.

| Key | Fields and lifetime |
| --- | --- |
| `RUN#<runId>` / `BUDGET` | `newAdmissions`, `providerAttempts`, `startEpoch`, `endEpoch`, `manifestDigest`, expiry at window end + 7 days |
| `RUN#<runId>` / `MATCH#<targetId>` | allowlisted `threatTypes`, original `observedAt/validUntil`, `manifestDigest`; physical expiry is the exact Decimal ceiling of original provider expiry; logical validity remains the exact provider deadline |
| `RUN#<runId>` / `LOCK#<targetId>` | random `owner`, expiry at run end; no takeover |
| `CONTROL` / `CLEANUP` | operational `lastSweepEpoch`; no identity data, no expiry index |

`manifestDigest` is SHA256 of run ID/window and sorted ID/public synthetic URL
pairs only. It binds configuration integrity and excludes subjects, client/account
IDs, credentials, bindings and proofs. It cannot be used to authorize a target.
All table retention, including this integrity metadata and the cleanup clock,
remains subject to pending owner approval; the implementation gate stays closed.

Hard constants are 10 NEW submissions and 60 Google-attempt permits per run and
cannot be configured upward. The NEW counter update participates in the **same
existing Authority.admit transaction** that creates the receipt and reserves the
allowance, including existing account/device/grant/global purchase-use guards.
Known replay is returned before the hook, and a conflicting/failed transaction
cannot spend a NEW slot. Partial/failed new checks still spend a NEW submission
slot; this is separate from customer charging.

The Google counter is conditionally incremented before a request and never
refunded. It conservatively counts permits even when DNS/TLS/secret lookup or a
lost transaction response prevents an actual GET. An uncertain permit response
never authorizes a call. DynamoDB conditional atomic writes establish the global
cap under concurrency; local Moto evidence does not substitute for future live
IAM/service qualification.

The cache stores only matched threats. No-match responses retain no provider
validity and are not persisted. Match reads preserve the original provider clocks
exactly; expired evidence is never made current. A match lifetime over seven days
is not cached: the confirmed match is returned once and the target stays locked
for the rest of the run. This avoids downgrading a real match due solely to cache
retention policy.

Single flight is a conditional lock. Contention returns unavailable without
waiting, extra attempts or a new wire state. A second read after lock acquisition
prevents redundant calls when another worker has just published a match. Known
completed calls release their own lock conditionally. Provider ambiguity, uncertain
budget responses, abandoned workers and failed/ambiguous cache persistence retain
the lock through run end. There is no timed takeover or automatic unknown refetch.
A confirmed match survives cache-write failure in the current assessment result;
the lock prevents persistence failure from causing extra provider calls. This can
reduce availability for the rest of a short synthetic run and requires a new,
separately authorized run for further qualification; it does not authorize retries
with fresh proofs to recover the old accounting result.

## Cleanup and IAM interface

All indexed rows use `GSI1PK=URL_QUALIFICATION_EXPIRING` and
`GSI1SK=<12-digit expiresAt>#<runId>`. The cleanup artifact is
`url_qualification_cleanup.zip`, private scheduled handler `app.lambda_handler`.
The intended schedule is every five minutes, disabled by default. It queries up
to four pages of 50 expired rows with `Select=SPECIFIC_ATTRIBUTES`, never scans,
and deletes only if the observed expiry still matches and is due. It advances
`CONTROL/CLEANUP.lastSweepEpoch` only after a complete sweep with no uncertain
conditional deletion; backlog/exception keeps the heartbeat stale. The enabled
handler raises the fixed content-free `URL_QUALIFICATION_CLEANUP_INCOMPLETE`
error unless sweep completion is exactly boolean true, so existing Lambda Errors
alarms detect repeated incomplete sweeps without extra metrics or record logs. No unexpired
lock can be deleted to reopen a target within a run. TTL alone is not the promised
15-minute cleanup mechanism.

Consumer/assessment/resolver strongly read the heartbeat and close new
admission/provider work when it is missing, in the future or older than 900
seconds. Startup needs one successful enabled cleanup sweep before runtime can
open. Reconciliation never reads this store. Cleanup requires exact
`URL_RETENTION_APPROVED=true` before any SDK client or sweep, and remains
independent of runtime mode/window/provider controls during rollback. Retain
retention approval for authorized cleanup of existing data; revoking it closes
cleanup as well as new/provider operations and needs an explicit retention
disposition, while original-proof reconciliation remains available.
Future deployment/retention approval must authorize its operational heartbeat
writes and validate observed deletion lag/alarms; these checks were not performed
against AWS.

New-table permissions, in addition to unchanged existing authority dependencies:

- Consumer: `GetItem` only `CONTROL/CLEANUP`; transaction-enclosed `UpdateItem`
  only `RUN#<runId>/BUDGET` for the aggregate NEW counter.
- Assessment: `GetItem` on the heartbeat and its run's match keys; standalone
  conditional `PutItem/DeleteItem` on its run's lock keys; transaction-enclosed
  `UpdateItem` on BUDGET; transaction-enclosed `ConditionCheckItem` on its lock
  and `PutItem` on the corresponding match key.
- Resolver: `GetItem` only `CONTROL/CLEANUP`.
- Cleanup: `Query` only the expiry GSI partition, conditional `DeleteItem` on
  run rows, and `GetItem/UpdateItem` only the cleanup control row. No authority,
  identity, secrets, providers, Scan, backup or bulk-write permissions.

AWS transaction IAM must grant underlying item actions using the appropriate
`dynamodb:EnclosingOperation` restriction; naming `TransactWriteItems` alone is
not the authorization design. Exact IAM is owned by the infrastructure slice.

## Local evidence and remaining gates

The final broad impacted suite passed **715 tests in 47.59 seconds** against
implementation head `a83d5b208949c17c709759bcae561b6254bb0f4b`. This rerun
includes all cleanup retention/alarm fixes and real authority-loading rollback
coverage; it supersedes the earlier 701-test evidence. Shellcheck, compileall and
whitespace checks also passed against this source. Source stories remain open;
owner retention approval has not been granted.

The 715-test suite includes the 44 scoped tests: absent/false/malformed retention
consent prevents cleanup SDK/client creation; incomplete or missing sweep
completion raises the fixed alarm error without exposing response data; original
proof reconciliation works with mode OFF/authority ON and retention/provider/new
admission OFF; and the real keyring, inventory, Settings validation and Authority
loader accept that rollback configuration using read-only fake SDKs. No account
or table write occurs in the loader test.

An isolated Python environment `/tmp/sec233-offline-venv` points to already cached
Python dependencies. No network install was performed. Cached versions: pytest
9.1.1, Moto 5.2.3, boto3 1.43.108, jsonschema 4.26.0, dnspython 2.8.0. Moto
intercepts AWS in fixture tests; provider/DNS/HTTP tests use local fakes. Four
source-archive imports are included in the passing suite. The packaged files use
`--skip-dependencies`; neither those archives nor these tests establish deployment
artifact or live Lambda runtime readiness.

Representative commands:

```sh
AMT_AUTHORITY_INTEGRATION=1 PYTHONPATH=src /tmp/sec233-offline-venv/bin/python -m pytest -q tests/url_qualification tests/url_assessment tests/url_redirect_resolver tests/shared_check_authority tests/contracts/test_url_consumer_transport.py tests/scripts/test_lambda_release_scope.py
shellcheck scripts/build_lambda_zip.sh
python3 -m compileall -q src/shared_url_qualification src/url_qualification_cleanup src/url_consumer src/url_assessment src/url_redirect_resolver src/shared_check_authority tests/url_qualification
git diff --check
```

Coverage includes original-proof/account ownership, accounting reservation and
settlement, cap/replay/lost-response behavior, concurrent single flight, stale or
future cleanup clocks, original match validity, no-match behavior, >7-day lifetime
rejection from storage, cache persistence failure, unknown-lock retention,
redirect rejection before DNS/HTTP, bounded cleanup/backlog/conditional deletion,
closed defaults/partial config, and isolated four-artifact imports.

Pending gates: owner retention answer; independently reviewed disposable actor
onboarding and explicit GET/trial/access admission under SEC230; existing
`v1_entitlements` live17/current-source compatibility (unchanged here); independent
URL subject approval; approved public synthetic manifest/run window; strict IAM
and service conformance; startup cleanup/actual deletion lag; future explicit
publication/deployment/provider/endpoint approval; release integration and linked
release-test evidence. Existing single-subject appreview trial/History gates
cannot be replaced or widened to prepare the new actor. Physical-device work is
not performed, per the standing instruction. Nothing here waives those gates.
