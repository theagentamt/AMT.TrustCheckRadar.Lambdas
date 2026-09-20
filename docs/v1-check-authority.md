# V1 check authority — undeployed runtime core

Status: backend authority for SECUR4ALL-230/233, now included in disabled consumer/entitlement/recovery candidate packages. No protected mobile endpoint is activated and no live table rows are seeded by this increment. See url-consumer-dev-handoff.md for the implemented synchronous lifecycle and remaining activation gates. Existing legacy Web Risk remains unchanged. Canonical mobile contract `0.2.0-candidate.1` remains inactive and its client check ID ownership is unchanged.

## Approved policy

Owner decisions on 2026-09-20 are represented by `owner-2026-09-20-v1`:

- Paid: 200 completed checks per monthly **subscription period supplied by verified store authority**, not a calendar-month or 30-day reset invented here.
- Trial: explicit activation, seven days or ten completed checks, whichever is reached first.
- Complete outcomes deduct one check; partial, failed, blocked, invalid, unsupported, and unavailable outcomes deduct zero. A confirmed threat with partial inspection stays partial and deducts zero.
- Complimentary authority has no subscription allowance deduction. Account/device gates and operational attempt/inflight limits still apply.
- VirusTotal is disabled pending a commercial quote.

Attempt limits, inflight caps, operation validity, worker settlement deadline, reconciliation horizon, counter/receipt retention, and HMAC lifecycle have **no production defaults or approval in this change**. Test numbers are synthetic configuration. Missing, invalid, disabled, or mismatched policy fails closed. A failure response alone never proves settlement: the eventual mobile adapter must carry an authoritative receipt or preserve pending/unknown accounting.

## Existing authorities and missing writers

`shared_history.security.jwt_subject` consumes verified API Gateway Cognito JWT context, checking issuer, client, access-token use, scope and expiration. This is not independent signature verification: an authenticated gateway adapter must supply the context. `users` profile and deletion-ledger tombstone fences are reused. Admission requires the actual `device_registration` `ACTIVE_BINDING` pointer/stateVersion and the matching active device row; no eventually consistent GSI or legacy pointer fallback grants access.

`shared_entitlements` and `conversation_analysis/scan_access` use legacy FREE/PRO/credit/research balances. They are deliberately not V1 authority. Existing `purchase_handoff` does not create this new V1 authority. Transactional writer interfaces, explicit trial activation/eligibility history, server-only complimentary administration and revocation are implemented in shared_check_authority/entitlements.py and disabled v1_entitlements handlers. Trusted store verification adapters, operator IAM administration integration, migration and activation remain incomplete; see v1-entitlement-writers.md. No client request, research flag, positive legacy balance, or mobile-supplied receipt creates authority here.

Future authority writers must update ACCESS atomically, monotonically increasing `revision` for **every** state, basis, period, policy, activation or validity change. Period rows are immutable identities with mutable counters only. Renewal creates a new period; never repurpose an old reserved period. The admission transaction rechecks current account/deletion/device state, grant revision/basis/policy/validity, the original period counter snapshot, and inflight capacity. Concurrent revocation or quota consumption fails the transaction without creating a partial reservation.

## Storage contract (proposal, no new resources)

Use the existing purchase-entitlements table's PK/SK and `expiresAt` TTL capability with a disjoint namespace. The future adapter owns table names; no hard-coded AWS identifiers exist in the core.

Partition: `V1#<retained-key-id>#<HMAC-SHA256(account subject)>`.

| Sort key | Content |
| --- | --- |
| `ACCESS` | `recordType=V1_ACCESS_AUTHORITY`, schemaVersion=1, policyVersion, ACTIVE state, basis, monotonic revision, validFromEpoch/validUntilEpoch, periodId for paid/trial; explicit activationKind/activatedAtEpoch for trial |
| `PERIOD#<id>` | `recordType=V1_ALLOWANCE_PERIOD`, policyVersion, original grantRevision, limit (200/10), startEpoch/endEpoch, usedChecks/reservedChecks |
| `PREPARE#<preparation-id>` | Candidate idempotent preparation receipt: signed check ID, exact payload HMAC, expiresAt; no admitted work |
| `CHECK#<signed-check-id>` | Exact payload HMAC; ADMITTED/SETTLED; original basis/revision/policy/periodSK; private executionToken; settleByEpoch; chargedChecks/receiptId/processingOutcome; expiresAt |
| `ATTEMPT#<window-start>` | attempts and expiresAt, separate from customer allowance |
| `INFLIGHT` | activeCount and expiresAt, separate from customer allowance |

No raw URL, QR, message, Google response, JWT or raw account ID is stored in these rows. HMACs are pseudonymous/linkable control data, **not anonymous research data**. Execution tokens and HMAC material are secrets; this library emits no logs. Internal worker context excludes account identity from repr. The future adapter must not log events, context, credentials or rows.

Receipt retention must cover at least operation validity + worker settlement + reconciliation horizons. Counter retention must cover receipt retention and the attempt window. These relations are validated, but values are unapproved. ACCESS/PERIOD retention is deliberately not guessed or set by this library. They must survive original-period settlement and required reconciliation. DynamoDB TTL is delayed cleanup, not an authorization clock: the core explicitly checks proof, worker and receipt deadlines.

`deletion_partitions(account)` enumerates the account partition for **every retained HMAC key**. A deployment requires the account deletion bridge to remove ACCESS, all periods, preparations, receipts, attempts and inflight rows across that inventory, including any archived keys/migrated namespaces, without reintroducing writes after the account-deletion fence. Key rotation must retain verification keys through proof/settlement/reconciliation/deletion horizons. Disable new admission while migrating grants/counters; never create independently spendable allowance in both namespaces. Old-key unadmitted proofs cannot enter a new active-key authority. Key inventory, deletion/rekey process and retention approval are activation gates.

## Internal lifecycle, not a public route

1. `prepare(verified_event, projected_intent, preparation_id)` checks account, active device, current access, attempt cap, and exact intent shape. It issues a signed, bounded operation proof. Repeating a retained preparation ID with the same intent recovers the same proof. It performs no provider work, reservation, or charge.
2. `admit(verified_event, projected_intent, proof)` atomically reserves one allowance slot and one inflight slot, and creates one receipt. Only the successful admission response carries an execution token. Retries return status, never a second execution permission. A changed URL origin, query ordering, scope, withheld-components projection, entry point or language conflicts with the original proof.
3. `settle(TrustedWorkerContext(account), proof, execution_token, outcome)` is **internal only**. The future worker adapter must authenticate the worker and bind account/outcome to trusted dispatch and validated provider results; it must never construct this context from unauthenticated client JSON. Fresh client JWTs are unnecessary. The receipt/execution proof and account/deletion fences remain required. Settlement releases the original period reservation and charges that period only for complete results, even after renewal. Repeated settlement returns the first recorded decision.
4. `reconcile(verified_event, proof)` reads only minimal control status. Account identity/deletion checks remain; current device, paid access, expired subscription, or exhausted allowance do not prevent an authenticated account from checking its existing operation. It performs no provider call or mutation. Missing/expired receipts return UNKNOWN, not zero charge or permission to resubmit.

The projected intent is the single-URL structural payload `{entryPoint, language, target:{url, scope, withheldComponents}}`. The hash preserves exact URL semantics; it is **not** URL security validation. The future request adapter must apply the canonical request schema, sanitizer/projection policy and URL validator before any external dispatch. The provider/resolver must independently enforce SSRF/HTTP protections. This slice does not prove parent-message/multiple-URL/AI fanout accounting: future orchestration must reserve once for the parent logical check and share its provider budget, not charge each extracted link.

The signed proof/preparation mechanism is a **candidate internal lifecycle change**, not a modification to the published mobile contract or a client instruction to replace check IDs. A public lifecycle requires a separately reviewed contract version. Candidate POST `/v1/url-checks` and URL-free POST `/v1/url-checks/reconcile` bindings are unimplemented and unfinalized; no prepare route is approved. Android continues local review/decoding with external submit unavailable.

## Retry, crash and deadline boundaries

The injected DynamoDB resource must use `Config(retries={"total_max_attempts": 1})`; construction rejects SDK retry configurations that could silently replay attempt increments. There are no application transaction retries. Ambiguous writes read the same receipt. An ambiguous successful admission never returns a second execution token. This avoids duplicate provider work but can leave an admitted reservation without delivered work: **a transactional outbox/durable dispatch and recovery mechanism is required before deployment**. No guessed refund/retry or charge is performed by this library.

Expired signed operation proofs cannot create new work even if TTL has removed the receipt. Missing/expired status remains unknown. Worker deadline expiry refuses ordinary settlement and requires a reviewed recovery path; reservation release after worker loss and counter repair are not implemented. A permanently abandoned admitted operation is therefore an activation blocker, not a hidden completeness claim.

The existing HTTP API supports at most 30 seconds while the private assessment currently uses a 35-second Lambda/32-second internal deadline. It cannot be wired synchronously without redesign. Admission/worker/status architecture or a newly budgeted provider path remains a dependency. No provider runtime, private execution role, public API, mobile transport or charging path is activated here.

## Infrastructure handoff

No infrastructure changes are needed for this library-only increment. A future separate consumer/worker role would need strongly consistent `GetItem` and `TransactWriteItems` for the exact users, devices, deletion-ledger and purchase-entitlements tables. Core uses no Scan, GSI query, S3, or new Secrets Manager access. Cross-key deletion would additionally need a separately reviewed partition-query/delete design. Do not broaden the private assessment role (which intentionally denies database access). Future authenticated consumer, trusted worker, result/dispatch storage, least-privilege invocation, HMAC provisioning, retention and recovery are separate reviewed plans.

## Validation and release behavior

Run isolated DynamoDB emulator tests (no AWS credentials or requests):

```sh
python3.14 -m venv /tmp/amt-authority-tests
/tmp/amt-authority-tests/bin/python -m pip install -r tests/shared_check_authority/requirements.txt
AMT_AUTHORITY_INTEGRATION=1 /tmp/amt-authority-tests/bin/python -m pytest -q tests/shared_check_authority
```

The normal suite explicitly skips this module to avoid interference from existing application `sys.modules` mocks. CI runs it in a separate Python 3.14 job using Moto and explicit synthetic AWS credentials. Emulator transaction/race coverage is useful runtime evidence, not live AWS concurrency qualification. Deployment must test real IAM, transactional fencing, TTL policy and dispatch recovery after the remaining gates are resolved.

`authority_manual` publication scope validates CI artifacts but skips AWS credentials and every upload. Only the new disabled V1 packages include the core. No runtime or alias changes, broad artifact upload, or deploy follows this merge. Historical library-only validation below describes the prior increment; current handoff supersedes the prior outbox recommendation with bounded synchronous work and zero-charge expired-lease recovery.

Local Python 3.14 validation on 2026-09-20: 44 isolated transaction regressions
passed; normal suite 650 tests and 162 subtests passed (one intentional isolated
module skip). Coverage includes duplicate/ambiguous writes, revocation races,
expired JWT worker settlement, original-period renewal, partial/failed zero
charge, caps, expired-proof replay, missing legacy authority, privacy and key
inventory. Compilation, actionlint and shellcheck passed.
