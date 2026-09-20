# Shared URL and QR-link assessment — mobile engineering candidate

Version **0.2.0-candidate.1** replaces the earlier 0.1.0 draft in this canonical directory. This is a concrete engineering contract and reproducible mapping reference for SECUR4ALL-76/103/110/190/230/233 and Android ATCR-97/98. **Consumer activation remains false; analysis and reconciliation endpoints are null.** Merging these files is not approval of commercial policies or deployment of an API.

## Current boundaries

| Component | Status |
| --- | --- |
| Private HTTP redirect resolver | Deployed Dev, IAM-only, Python 3.14, private integer schemaVersion 1. No reputation verdict. |
| Private URL assessment | Deployed Dev, IAM-only, Python 3.14, private integer schemaVersion 1. Bounded resolver + Google Lookup; minimized result, no account authority or ledger. |
| This mobile contract | Engineering candidate schemas, EN/ES copy, public fixtures and reference mapping. String contractVersion 0.2.0-candidate.1; **not the private DTO**. |
| Consumer route/auth/device adapter | Not implemented or activated. No route, HTTP binding, app credentials or legacy-route substitution is authorized. |
| Authoritative paid/trial/complimentary access and accounting | Not implemented for this new flow. Independent snapshots, receipts and explicit same-check replay authority are required before activation. |
| Legacy POST /web-risk-communication | Existing Evaluate flow, left unchanged by user direction. It cannot substitute for this consumer contract or new paid gate. |

## Canonical files

- `request.schema.json`: explicit URL assessment input with logical check identity, entry point, language and reviewed URL projection.
- `reconciliation-request.schema.json`: the same check identity and operation `reconcile`; **no URL or new analysis input**.
- `result.schema.json` / `error.schema.json`: public response kinds `assessment_result` / `request_error`.
- `access.schema.json`, `accounting.schema.json`, `retry.schema.json`: independent authoritative control facts, inlined identically in both response schemas.
- `messages.json`: bounded EN/ES display copy; clients never render raw provider or exception text.
- `MOBILE-HANDLING.md`: consumer handling matrix, receipt/retry rules and compatibility behavior.
- `reference_mapping.py`, `private-reason-map.json`, `mapping-cases.json`: explicit private-to-public adapter reference and expected outputs. This code is **not packaged or deployed**. The mapper requires trusted operation identity/projection, access snapshot, accounting snapshot and explicit replay authorization; it provides none of them itself.
- `fixtures/manifest.json`: consumable public examples. Mapping-case private inputs are backend test material, not mobile wire payloads.

## Approved constraints preserved

Every feature requires an account. Built-in checks and recovery guidance remain available after paid external access ends. V1 individual pricing is $4.99; paid allowance, trial length/count/start event, billing boundaries and partial/inconclusive charging remain unset. Research/demographic participation cannot grant external access or refill allowance. Complimentary access bypasses subscription/allowance only, while account/device and abuse controls remain required.

One logical check may invoke several internal services and must not incur multiple deductions from technical retries or internal calls. No consumer may decrement allowance from provider counters, HTTP status or a verdict. An error does not prove zero charge. Existing account controls and status/receipt reconciliation remain available after subscription or allowance expiry without fresh provider work. Access to an old result remains a separate privacy/retention policy gate.

The URL projection remains explicit: `full_url` means the complete HTTP target, never browser fragment behavior; only `fragment` may be listed as withheld in that scope. `origin_only` declares path/query reduction and cannot clear the original full link. API-side validation is still required. Original omitted values, replacement maps, screenshots, QR images, credentials, headers, callbacks and access flags are not consumer request fields.

## What the candidate does not decide

No initial trial amount, paid monthly limit, default allowance, numeric unlimited workaround, renewal/reset rule, charge-on-partial decision, cache entitlement, refund, TTL or retry cooldown is approved here. A fixture `remainingChecks: 1` is an intentionally synthetic **current balance**, not a trial/plan limit. Fixture `chargedChecks: 0/1/null` demonstrates independently supplied ledger states, not a pricing policy. The same private failure is shown with charged, not-charged and unknown receipts to prevent clients inferring billing from the result.

No new server cache, History/research write, contribution, receipt storage or retention policy is authorized. These schemas describe the minimized fields a future authority must supply; SECUR4ALL-169/178/190/230/241 must define permitted persistence and deletion. No URL or hop chain appears in a public response.

## Remaining activation gates

1. SECUR4ALL-76/230/231/241: approved plan/trial configuration, authoritative account/store/allowance lifecycle, idempotent single-check ledger, migration and rollback.
2. SECUR4ALL-190/233: consumer route/auth/version/device binding, accepted requestId/checkId bridge, replay authorization, reconciliation transport, mobile compatibility rollout and error binding. Implement the mapping against actual authority services; this reference alone grants no access.
3. SECUR4ALL-110/169: accepted provider projection, minimized result/receipt/cache retention, account/deletion linkage and old-result access. Recognized sensitive-link heuristics do not establish that all other data is non-sensitive.
4. SECUR4ALL-103: approved local QR payload table and explicit plain-text-to-message handoff. Unsupported schemes remain inert; nothing opens, dials, pays, joins Wi-Fi or starts another analysis automatically.
5. SECUR4ALL-232: complimentary operator grant/revoke/audit surface and effective revocation behavior.

## Validation scope

Schema and reference-mapping tests verify closed enums, version/kind rejection, high-risk precedence, coherent copy/actions, origin-only limitations, independently supplied charging, explicit replay grants, expired-access reconciliation and EN/ES coverage. Live private Dev acceptance is documented separately in `docs/url-assessment-dev-release.md`. These tests do not claim a deployed consumer endpoint, mobile network integration, billing sandbox, physical-camera, accessibility or production acceptance.
