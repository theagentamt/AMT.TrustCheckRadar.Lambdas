# Account deletion: existing disabled producer transport

This candidate documents the existing schemaVersion 1 source; it does not add an
export API, a new persistence surface, finalization, or permission to enable it.
Android must remain gated until the account-wide producer's activation prerequisites
are satisfied. The transport has no paid/provider/allowance dependency.

- POST `/v1/users/account-deletion`: exact request schema, at most 1,024 UTF-8 bytes,
  ordinary JSON with no duplicate keys, no base64 proxy body or query parameters.
  A verified access-token subject is the only target. The signed `auth_time` must
  be within 300 seconds (the existing 60-second future clock tolerance applies).
  Refreshing an access token alone does not constitute reauthentication.
- Successful POST is **202 Accepted**; GET on the same route is **200**. Both are
  private/no-store JSON. GET accepts no body/query and requires the verified
  subject but not a still-active profile. It is best-effort before identity removal.
- Same account/operation ID retry refers to the original fixed deletion command.
  A different ID gets IDEMPOTENCY_CONFLICT. Never automatically generate a new ID
  after timeout, app restart, or ambiguous acknowledgment.
- `REQUESTED` is pending. `completionEligible=true` means all configured component
  receipts validate; it is not proof of global finalization or backup erasure.
  The current producer **never emits overall COMPLETE**. Unknown overall states,
  unexpected fields, duplicate components, inconsistent completion flags, or a
  deadline other than requestedAtEpoch + 86,400 must fail closed in the client.
- `NOT_REQUESTED` is an authoritative current read; it does not authorize a client
  to silently retry deletion or infer that a racing/ambiguous POST failed.
- A 401/403, network failure or feature-disabled response is not deletion success
  or deletion failure proof. A lost response may follow a committed fence. Keep
  the user's pending intent scoped to its account, offer explicit recovery, and
  do not clear local data on an ambiguous or generic accepted operation.
- Once Cognito identity removal occurs, ongoing login/export/status access is not
  promised. Accepting deletion cancels unfinished account exports. Download an
  export before requesting deletion. These are existing approved policy boundaries,
  not authorization to pretend the export/finalizer already exists.
- Errors use the existing `{error:{code,message,retryable,details?}}` envelope.
  Localized UX maps fixed codes; never display arbitrary server/exception strings.
  `details` (when supplied) is an array of fixed `field`/`issue` pairs.

JSON Schema does not express all semantic checks above, duplicate object-key
rejection, or a JSON numeral's exact integral lexical representation. Fixtures
are synthetic and contain no real account IDs or submission content.
