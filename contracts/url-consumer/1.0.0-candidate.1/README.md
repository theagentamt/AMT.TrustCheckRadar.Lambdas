# URL consumer transport 1.0.0-candidate.1

Engineering binding; not activated. Existing outcome contract remains
0.2.0-candidate.1. Client checkId is never replaced by the server operationProof.
All routes require a verified Cognito access JWT and active account. Prepare and
submit also require x-device-binding-fingerprint matching the authoritative
registered active device. Reconcile does not require active paid/device access.

| Method/route | Request | Effect |
|---|---|---|
| POST /v1/url-checks/prepare | Existing outcome-contract request plus transportVersion | Account/device/access/rate validation and proof; no provider, reservation or charge |
| POST /v1/url-checks | Same request plus operationProof | One atomic reservation, one bounded assessment, one complete-only settlement |
| POST /v1/url-checks/reconcile | transportVersion, checkId, operationProof only | Read/recover same operation; never resend a URL or repeat provider work |

Prepare uses client checkId as its idempotent preparation key. The proof binds
account, client checkId, complete projected intent, and expiration. Lost prepare
response: explicit same-request prepare may recover proof. Lost submit response:
reconcile only. Never automatically mint another check or replay provider work.

All responses use the transport response schema. outcome is null or the exact
existing public result/error contract. HTTP200=prepared/settled;202=pending;
401=authentication;403=account/device/access;409=conflict or expired proof;
422=invalid request/projection;429=operational rate/inflight limit;
503=unavailable/uncertain. Clients must parse the body even for non-2xx responses.
Unknown version/enum or incoherent identities fail closed with no retry/provider
action. No status/failure alone implies zero charge.

The current account's access snapshot is independent of an existing result.
Pending/unknown accounting remains null with requiresReconciliation=true.
Expired admitted leases recover to failed/zero; already-settled complete checks
are never refunded. Retention expiry returns unknown, never admission permission.

The fragment is always removed. full_url may include path/query only after user
review/disclosure; origin_only has withheld components and cannot establish full
link clearance. No request bodies, URLs, JWTs, proofs, account IDs or exception
text are logged. No URL/request payload is persisted.


### Admission expiry versus reconciliation retention

For a `prepared` envelope, `expiresAt` is the deadline to admit the operation; it is not a deadline to reconcile an already-admitted check. The backend accepts an expired execution proof for authenticated, same-account, URL-free reconciliation while the receipt remains inside its retention deadline. For a settled envelope, `expiresAt` is the receipt expiry when available. Missing/expired receipts return unknown accounting, never an assertion that no charge occurred. The owner approved seven-day minimized receipts on 2026-09-20. Clients may retain only the account-scoped check identity/proof and timestamps for at most seven days, removing them on logout/account change/deletion; never retain the original URL in this recovery record. This clarification does not change schema version or activate a route.

### Authoritative closure of a never-submitted preparation

After the signed admission deadline, reconciliation can return HTTP200, `state=rejected`, `errorCode=OPERATION_EXPIRED`, `outcome=null`, and accounting `not_started/0/receiptId=null/requiresReconciliation=false`. This exact response is emitted only after an atomic closure of the matching retained PREPARE row while CHECK is absent. Every consumer admission atomically requires that preparation to remain OPEN, so a request that verified its token before expiry cannot admit after closure. The retained preparation is the durable proof of non-admission. The client may clear that matching local recovery record and permit a new user-initiated check. Never infer this outcome from local time, a generic expired-proof error, missing preparation, service/auth failure or expired retention; those remain unknown accounting. See fixture `expired-unadmitted-closed`.
