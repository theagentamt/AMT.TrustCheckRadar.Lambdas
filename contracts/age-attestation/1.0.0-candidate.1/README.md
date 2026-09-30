# Age-attestation contract 1.0.0-candidate.1

`POST /v1/users/age-attestation` records one authenticated adult
self-attestation. It does not verify age or identity. The V1 operation is
positive-only: declining the adult statement does not mutate server state.

The caller sends a Cognito **access token** in `Authorization: Bearer ...` and a
JSON body conforming to `request.schema.json`. The HTTP API JWT authorizer
validates issuer and client audience; the Lambda additionally requires the HTTP
API v2 event, `token_use=access`, and `sub`. Body subjects, ID-token custom
attributes and caller-selected policy versions are never authority.

Before accepting an operation, the Lambda uses Cognito `AdminGetUser` for the
authenticated subject. The returned `sub` must match, `phone_number_verified`
must be `true`, and libphonenumber metadata must classify the number as fixed,
mobile, fixed-or-mobile, or VoIP in an allowed account region. The default
regions are `US,PR,VI,GU,AS,MP`. Deployments may narrow or deliberately extend
that list through `AGE_ATTESTATION_ALLOWED_REGION_CODES`; prefix-only `+1`
classification is prohibited. Toll-free, premium, shared-cost, personal, pager,
voicemail, UAN, unknown and non-geographic categories are rejected.

Successful operations update only an existing matching `ACTIVE` or
`PENDING_AGE_GATE` profile while the fixed account-deletion fence is absent. A
missing legacy profile returns `PROFILE_NOT_FOUND`; this route never creates one.
Prelaunch/Dev operators must reconcile such accounts through a reviewed bounded
profile migration before enabling the client journey.

`operationId` is a canonical UUIDv4. A seven-day account-scoped receipt stores a
hash of the strict request and the authoritative result. Same-operation retries
return the stored result and original `attestedAt` with `replayed=true`; the same
ID with different content returns `IDEMPOTENCY_CONFLICT`. Replays atomically
recheck the deletion fence, current matching profile, policy and timestamp.
An already `ACTIVE` profile at the current policy accepts a new operation by
writing only its receipt and preserving the original `attestedAt`.

Required runtime configuration:

- `USERS_TABLE_NAME` (`TABLE_NAME` remains a compatibility alias)
- `DELETION_LEDGER_TABLE_NAME`
- `AGE_ATTESTATION_USER_POOL_ID`
- optional `AGE_ATTESTATION_ALLOWED_REGION_CODES`, a comma-separated list of
  uppercase ISO-style two-letter region codes

The users table requires consistent `GetItem` and transactional `PutItem`,
`UpdateItem`, and `ConditionCheckItem`. The deletion ledger requires
transactional `ConditionCheckItem`; Cognito requires `AdminGetUser` on the exact
user pool. Receipt TTL uses `expiresAt` and is fixed at 604800 seconds.

Errors conform to `error-response.schema.json`. Messages are safe display
fallbacks, but clients should localize from `error.code`. `retryable` is
authoritative; `RATE_LIMITED` also supplies `Retry-After`.

| HTTP | Error code |
|---:|---|
| 400 | `INVALID_REQUEST` |
| 401 | `AUTHENTICATION_REQUIRED` |
| 403 | `PHONE_NOT_VERIFIED`, `PHONE_REGION_NOT_ALLOWED`, `PHONE_NUMBER_UNSUPPORTED` |
| 404 | `PROFILE_NOT_FOUND` |
| 409 | `ACCOUNT_STATE_CONFLICT`, `IDEMPOTENCY_CONFLICT` |
| 429 | `RATE_LIMITED` |
| 503 | `SERVICE_UNAVAILABLE` |

`custom:over_18` alone is never sufficient. It is intentionally ignored by
this route; only the strict request acknowledgment, access-token subject,
Cognito phone eligibility, existing mutable profile, and deletion-fenced
transaction authorize the state change.

The event-shape checks prevent accidental legacy/trigger invocation from
becoming an authority path. They are not a signature over a direct Lambda
event. Infrastructure must keep `lambda:InvokeFunction` limited to the API
Gateway integration and approved operators, and must not expose an unauthenticated
function URL or grant app principals direct invocation rights.
