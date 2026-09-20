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
