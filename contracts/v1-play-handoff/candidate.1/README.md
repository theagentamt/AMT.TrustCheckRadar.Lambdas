# Modern Google Play handoff candidate.1

Proposed route: `POST /v1/purchases/google-play/verify`, API Gateway JWT and current active-device fingerprint header `x-device-binding-fingerprint`, account/age/deletion fences. This candidate is inactive and is not the retired purchase endpoint. Package/product/monthly base plan come from reviewed server configuration. All access still requires an account.

Request UUID identifies the immutable `(account, intent, purchaseToken)` attempt. Retries preserve all fields; a conflicting UUID never changes its meaning. PURCHASE covers purchase callbacks and ordinary foreground reconciliation. RESTORE is an explicit user action, never automatic account transfer. Existing Play binding is lowercase SHA256 of ASCII `TrustCheckRadar/account-namespace/v1` plus a zero byte, then UTF-8 exact authenticated subject; backend independently derives it. No new raw-token persistence is required on Android; new process recovery can query Play and use a new request UUID only because the backend's funded-period ledger prevents replenishment.

HTTP200 `committed` means the exact request has a durable ownership/paid-authority transaction. It is historical handoff evidence, not current access or guaranteed remaining allowance. Always read GET `/v1/access`; never infer or cache FREE/PRO entitlement from this response. `acknowledgment=pending` means server acknowledgment still requires retry/reconciliation; Android must not acknowledge itself or describe the subscription as fully synchronized. `pending` and `not_granted` perform no new grant and do not prove every other owned subscription lacks access. All successful responses require snapshot refresh.

The server freshly verifies Google state and the exact latest successful funded order; it never trusts client period/order/time/state claims. Funded-period counters remain immutable across retry, renewal ordering and restore. Postdeletion restore must preserve the owner-approved minimal purchase-linked usage through funded period end plus seven days (backups may retain historical copies up to35days longer); no deleted account ID, message or URL is retained in that ledger. Implementation and qualified activation of all admission/settlement/deletion paths are prerequisites, not implied by this contract.

Inactive transitions do not revoke a different/newer subscription based only on a submitted old token. SECUR4ALL-125 background lifecycle/reconciliation and SECUR4ALL-244 ingress remain required before paid launch. Grace/deferral extensions, unsupported offers, replacement modes and unverified catalog configurations are explicit unavailable/reconciliation outcomes until their accounting policy and provider evidence are qualified. Dev accepts only Google-recognized test purchases; no real purchase grant or acknowledgment is enabled.

Errors use fixed codes; no token/account/order/provider body is returned. Null requestId is allowed only when the server cannot establish request identity (including closed gate). Retryable failure does not prove no durable transaction occurred: retry the same request, and refresh the authoritative snapshot. Native UI must preserve owner/session fences and discard stale responses. No research participation grants purchase access or replenishes allowance.

## Fixed HTTP/error combinations

| Code | HTTP | Retryable |
| --- | --- | --- |
| INPUT_REJECTED | 400 | false |
| AUTHENTICATION_REQUIRED | 401 | false |
| ACCOUNT_UNAVAILABLE | 403 | false |
| ACTIVE_DEVICE_REQUIRED | 409 | false |
| REQUEST_CONFLICT | 409 | false |
| PURCHASE_ACCOUNT_MISMATCH | 409 | false |
| PURCHASE_OWNERSHIP_CONFLICT | 409 | false |
| PURCHASE_PLAN_UNSUPPORTED | 409 | false |
| PURCHASE_TRANSITION_UNSUPPORTED | 409 | false |
| PURCHASE_TEST_REQUIRED | 409 | false |
| PURCHASE_REJECTED | 409 | false |
| PURCHASE_VERIFICATION_UNAVAILABLE | 503 | true |
| PURCHASE_RECONCILIATION_REQUIRED | 503 | true |
| PURCHASE_SERVICE_UNAVAILABLE | 503 | false |

`requestId=null` is restricted to INPUT_REJECTED, AUTHENTICATION_REQUIRED and PURCHASE_SERVICE_UNAVAILABLE before valid request identity is established. Every nonnull requestId must equal the submitted UUID. A valid error body must agree with its HTTP status and fixed retryable value; mismatch is an untrusted/unavailable response. Successful replies use HTTP200 only. Network loss and malformed responses remain unresolved; they never authorize a purchase, account transfer or client acknowledgment.
