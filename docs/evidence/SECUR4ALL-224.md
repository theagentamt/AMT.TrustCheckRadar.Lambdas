# SECUR4ALL-224 Lambda Evidence

Status: **safe disabled implementation complete; story activation pending**

Implemented Lambda scope:

- Strongly consistent, newest-first History reads from the account's current
  server generation.
- Detail lookup through a tenant-owned durable locator; another tenant's key is
  never accepted from the request.
- Progress reads fail closed when recognition is disabled or uninitialized.
- Random server-side cursor handles bound to the JWT subject by HMAC; no raw
  key, subject, or assessment is present in the returned cursor.
- Verified Cognito JWT plus active device binding on all routes.
- Exact issuer/client, access-token type, expiry, and required-scope claim
  validation; legacy claims and principal fallbacks are rejected.
- Strong authoritative profile and account-deletion-fence checks on every read,
  including requests using tokens issued before deletion.
- Versioned bootstrap and paginated export routes; list/progress responses
  include both generations, contract version, and server time.
- Private no-store response headers and strict response-size/page bounds.
- Deterministic `history_read_api.zip` packaging.
- Canonical `history-contracts-1.0.0.zip` source with exact limits, errors,
  routes, badge keys, and EN/ES text.

Automated evidence is in `tests/history_read_api` and `tests/shared_history`.

The story must remain open until infrastructure routing/IAM is accepted and the
authenticated Dev contract suite passes against deployed resources.
