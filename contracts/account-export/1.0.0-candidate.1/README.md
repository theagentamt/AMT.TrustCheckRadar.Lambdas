# Observed account export — Android contract candidate

Owner approved direct authenticated export of current user-visible data with a scope
manifest; observed values rather than a snapshot; no extra server copy; fixed
15-minute continuation; deletion fence stops access; cancel only stops the local
download and does not separately revoke an issued stateless token. This candidate
implements that scope and remains disabled pending complete inventory qualification.

POST `/v1/users/account-export`, API Gateway JWT access-token authorizer; exact
current `x-device-binding-fingerprint` and signed auth_time within five minutes.
The existing one-minute future clock tolerance applies. Refresh alone is not
reauthentication. No paid entitlement, allowance deduction or provider lookup.
Request bodies are ordinary bounded JSON with no duplicate fields, base64 or query.

START_EXPORT returns a new server-generated operationId and first page. A lost
START acknowledgment cannot be recovered without server storage: explicit retry
starts a new download. Do not imply the previous start failed or charge for either.
CONTINUE_EXPORT sends only the opaque encrypted cursor. Every continuation binds
account, active-device version, operation, immutable start/expiry, source inventory,
family position, page number and cumulative wire bytes. Rotation retains old keys
through outstanding token expiry; missing keys fail, never fall back unsigned.

All successful pages are HTTP200 JSON, private/no-store. Exact envelope and limits
are pinned here. Items are bounded public projections, not raw database rows; use
public-fields.json plus existing nested assessment contracts. This JSON Schema
checks the envelope; it is not a replacement for projection/semantic validation.
The synthetic fixtures' cursor strings are placeholders, not usable credentials.

A zero-item IN_PROGRESS page is valid. Continue until COMPLETE with null cursor.
Same-cursor retry rereads observed values and may differ; replace that page by its
pageNumber and discard any previously assembled later pages. Never append it twice.
Page numbers start at0 and increment by1. Every page must have the same operation,
start/expiry, manifest and transport version. The response family must be in the
manifest; repeated family pages and multiple retained authority namespaces are valid.
Require expiry=start+900; start<=observed<expiry. Unknown fields/states/version,
conflicting page numbers or invalid shapes are errors, never partial completion.

Keep download assembly in bounded private memory. Only a validated COMPLETE sequence
may be offered as a finished file. The saved artifact contains the reviewed manifest,
operation/observation metadata and public records; **omit nextCursor**, all auth/device
headers and server diagnostic data. Do not persist an export outbox or auto-resume
across account changes. Background/cancel drops local cursor/partial bytes; it cannot
retract bytes already delivered or separately revoke the server's short-lived token.

64KiB/page,8MiB cumulative wire bytes,256pages,25source rows/page and900seconds are
fail-closed bounds, not proof every legitimate account fits. EXPORT_LIMIT_EXCEEDED
must never produce a truncated COMPLETE file. Large-inventory qualification remains
required. An expired export requires an explicit new start, not silent extension.

The manifest covers profile/Cognito identity, devices/recovery, subscriptions and
usage/purchase projections, V1 allowance/trial/minimized receipts/private feedback,
History/recognition, consent and account-linkable research/analysis metadata.
Operational raw controls, research vectors/lexical fingerprints and other people's
records are excluded explicitly. Historical backups and external provider copies
are not claimed absent or immediately erased. Research GSI values are observed under
its eventual-index consistency and strong owned base-row rechecks, not a point-in-time
cross-table snapshot. Missing readers/keys/inventory fail the whole export; they are
never silently substituted with empty families. COMPLETE means this bounded approved
scope was traversed, not that every external processor or backup was exported.

After any failed page, no partial download is labeled complete. Authentication,
account/device change, deletion fence, expiry and SOURCE_CHANGED require appropriate
reauthentication/restart; never automatically replay a start into a different account.
Map fixed error codes to local EN/ES text; do not display arbitrary server strings.
