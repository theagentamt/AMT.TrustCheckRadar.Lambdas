# Shared URL and QR-link assessment — V1-0 draft handoff

Version: **0.1.0-draft.1**. Status: **review proposal and synthetic fixtures; no consumer endpoint or implementation is activated**.

This handoff supports SECUR4ALL-76/103/110/190 and Android ATCR-97/98 refinement. It does not close those contract gates or make ATCR-124/125 ready by itself. The source review used the retained V1 tracker specification and the subsequent approved resolver decisions. Historical Free/Pro/credits pricing in older text is superseded by the V1 decision below. Operations event/retention/reporting policy is owned separately by SECUR4ALL-178; its draft values are not copied or treated as approved here.

## What exists and what does not

| Boundary | Current state |
| --- | --- |
| Private redirect resolver | Deployed in Dev on Python 3.14. Direct backend IAM invocation, schemaVersion integer `1`, no API Gateway envelope. `private-resolver-*.schema.json` describe this caller/result boundary. Mobile must never call it or receive AWS credentials. |
| Shared consumer URL assessment | **Not implemented.** `consumerEndpoint` is null. Consumer schemas in this folder propose field names and semantics, not a network adapter or activation contract. |
| Legacy `POST /web-risk-communication` | Separate Evaluate-era implementation. It does not implement these schemas, shared access/charging, or resolver orchestration; do not bind Android to it as a substitute. |
| Trial/paid/complimentary authority | Existing code has legacy Free/Pro balances and participation incentives. SECUR4ALL-230/231/232/241 must replace/migrate them before shared external checks activate. |
| Google Lookup and final verdict | Lookup is the selected first provider; the adapter and combined verdict pipeline are not implemented by this handoff. Resolver completion never implies a no-threat result. |

The private resolver receives `{schemaVersion:1, checkId, url}` and returns redirect observations and limitations. `httpChainComplete=true` means a supported HTTP chain ended at a 2xx response; it does not establish the ultimate browser destination or any reputation verdict. Its `hops` and `lastObservedUrl` are transient sensitive backend data, not automatically a mobile result projection. See `docs/url-redirect-resolver.md` for the actual deployed limits and side-effect warning.

## Proposed consumer boundary

- Authenticated account and active-device checks precede external work. The consumer route, HTTP status/error binding, device proof transport, API version negotiation and exact legacy compatibility behavior remain pending SECUR4ALL-190. No mobile-supplied account ID, subscription, trial, complimentary or research flag can grant access.
- `contractVersion` is an explicitly draft wire discriminator. It is not the old message schema string `1.0` or the private resolver's integer `1`. Unsupported versions must be explicitly rejected, never silently treated as the legacy route.
- `checkId` is the proposed logical-check identity shared by a message/link/QR URL and its internal services. Replaying an accepted request preserves identity and payload; reusing an ID for different input must conflict. The bridge to the existing message `requestId`, operation receipts and reconciliation transport remains unimplemented. Authenticated account controls and status/receipt reconciliation remain available after subscription or allowance expiry; they must not trigger fresh provider work. Access to a previously completed result is a separate cached/replay policy gate. A lost response does not prove that no website/provider was contacted or that no charge occurred.
- `entryPoint` is descriptive provenance (`standalone_url`, `message_url`, `qr_url`), not an authorization switch. `language` selects EN/ES presentation; it does not change evidence or access.
- `target.url` is an eligible, reviewed HTTP/HTTPS request URL, excluding its fragment. `target.scope=origin_only` explicitly declares reduced input and `withheldComponents` names what was removed; the original values/replacement map are never transmitted. Do not silently rewrite a full URL and claim to have checked it. `full_url` means the complete HTTP request target, not browser fragment behavior. `withheldComponents` may contain only `fragment` for that scope: fragments are never sent in the HTTP request, remain local, and are outside assessment coverage. Removing a path or query requires `origin_only` in this draft. `full_submitted_url` evidence describes only the submitted fragment-free HTTP target, never any omitted browser behavior. Client flags are untrusted and require independent API validation. Full-value submission rules remain a privacy gate; schema validity alone authorizes no network request.
- Requests contain no screenshots, QR images, contact data, headers, cookies, provider keys or callback URL. Non-URL QR payloads remain local. A URL found inside another parameter is not a separate instruction to fetch it.
- Draft result fields separate `verdict`, `processingOutcome`, `uncertainty`, `evidence`, `reasonCodes`, `transportWarnings` and `nextAction`. `assessedAt` is observation time, not a guarantee that a later destination is unchanged. Results contain no automatic navigation instruction or provider-generated executable action.
- Google Lookup no-match can support `no_known_threat_detected` only when the required supported checks for the submitted eligible URL are complete. Origin-only evidence, unavailable providers, blocked destinations and unsupported processing cannot clear the full link. High-risk evidence remains high risk if later checks are incomplete or conflicting. A Google match requires `high_risk` with `avoid_link`; an incomplete secondary check is `partial`, not a global `unavailable`/`blocked` state that could erase existing evidence.
- `suspicious` is present only to review the semantic contract. No heuristic or moderate-risk provider mapping is approved by its fixture. Lookup does not provide a moderate-risk confidence grade; a validated AMT rule would need its own evidence and approval.
- HTTP adds an unencrypted-connection warning. It is not an automatic scam verdict; HTTPS does not prove safety. Retain insecure steps and HTTPS-to-HTTP downgrade warnings from the observed chain.
- Synthetic fixture message keys and translations are draft copy for accessibility review. They are not customer-service activation or legal/privacy approval.

## Access and accounting decisions

Approved direction: all features require an account; built-in checks and recovery guidance remain available without paid external access; the V1 individual subscription is $4.99. External AI and reputation checks require eligible paid, capped-trial or complimentary authority and available allowance where applicable. Complimentary bypasses subscription/quota only, not account/device checks or abuse controls. One logical user check must not receive multiple deductions because of internal services or technical retries. Research and demographic participation cannot grant cloud access or restore allowance.

No charge amounts, remaining balance, cached-result entitlement, renewal boundary or charge-on-partial rule is invented here. Fixtures deliberately do not report a charge decision. A client must not decrement quota from the number of provider calls, resolver hops, result label or HTTP transport status. SECUR4ALL-230 must provide the authoritative accounting projection and reconciliation semantics.

## QR and destination handoff

| Decoded payload | V1-0 refinement behavior |
| --- | --- |
| HTTP/HTTPS, including web App Store/Play Store links | Local editable preview; explicit request for shared URL assessment after access/privacy gates. Never open automatically. |
| Plain text | Local inert display. Whether an explicit user action may copy it into message analysis remains an acceptance decision in SECUR4ALL-103. |
| `tel:`, `sms:`, `mailto:`, `intent:`, custom schemes, payment, Wi-Fi, geo, vCard | Unsupported/unassessed local state in this draft. No dialing, launch, payment, joining, server classification or implicit cloud submission. Owner must confirm the final supported payload table. |
| Malformed/unknown data | Explicit invalid/unsupported/unassessed presentation; no low-risk fallback. |

Cancellation is a client journey state, not a claim that a dispatched Lambda or website request was canceled. No cancellation/status endpoint exists for this consumer flow. Do not automatically retry under a new ID, refund, or charge because the screen closes. Reopening or manually opening a previously checked URL requires explicit review; an old result is not a current guarantee, and no background re-scan or fresh charge is authorized by the old result. Android owns Back, camera permission, import alternatives, TalkBack, large-text, offline and confirmation fixtures.

## Retention and privacy boundary

No new server storage, cache, History write or research contribution is authorized by this draft. Submitted URLs and resolver chains are transient sensitive processing data; logging them is prohibited. Provider cache keys/results, logical-check receipts, minimized results, account/deletion linkage and audit retention require explicit accepted schemas and durations before implementation. Cache hits must not bypass access checks. Existing History/research systems retain their separate gates; the draft does not silently enroll a check in either. Refer to SECUR4ALL-169/178/190/241 for the authoritative retention, deletion and migration work rather than copying a guessed period.

## Decisions still needed before implementation readiness

1. **SECUR4ALL-76:** paid allowance, capped-trial count/duration/start event, renewal/reset boundaries, store identifiers, and exact partial/inconclusive/technical-failure/cached-result charging and replay-access policy. No recurring free cloud allowance or credits are part of the new V1 model.
2. **SECUR4ALL-103:** final local QR payload table, especially optional explicit plain-text-to-message handoff and any supported action beyond inert display. Nothing opens automatically.
3. **SECUR4ALL-110/169:** the field-level URL projection permitted to providers; treatment of withheld path/query data; exact retained fields, purposes, periods and deletion behavior. The existing sensitive-link heuristic is not proof that every other link is non-sensitive.
4. **SECUR4ALL-190/230/233:** accepted route/auth/version/error binding, requestId/checkId compatibility, result-engine/provider provenance, same-check reconciliation, accounting projection, validation before provider calls, and migration/rollback compatibility. These are backend design deliverables, not mobile guesses.
5. **SECUR4ALL-232:** complimentary operator surface, audit retention and effective access/revocation contract; no huge numeric quota represents unlimited access.

## Validation

`fixtures/manifest.json` lists synthetic examples. Schema tests reject permissive client access flags, unsupported versions, unknown-as-safe results and origin-only no-match conclusions; they also verify EN/ES key coverage and high-risk-plus-partial validity. Separate tests validate actual resolver responses against the private schema. These are refinement/contract tests, not live consumer API, provider integration, Android UI, billing sandbox, accessibility or physical-camera evidence.
