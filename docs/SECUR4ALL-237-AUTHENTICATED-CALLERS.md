# SEC237 authenticated consumer capture and older-row compatibility

This bounded increment wires the existing URL, message and recovery HTTP
consumers to receipt-local operations capture. It adds no endpoint, principal,
credential, permission, resource, provider action or reporting scope. The
operations producer remains default off. SEC237 remains In Progress; this is
reviewed source and local component validation, not deployment or real Dev
acceptance. It follows the trusted-ingestion and selective-retirement records.

## Existing authenticated caller boundary

Each consumer first performs its existing canonical request, authentication,
authority and public response validation. Only an ADMITTED or SETTLED actual
receipt may trigger the optional hook. A prepared token without a receipt,
unknown receipt or error path does not mint a capture. Existing URL Lambda
context time is supplied to the hook, as it already is for message and recovery.

`capture_authenticated` obtains the owner through the existing
`Authority._account` API Gateway authorizer-context contract. Account identities
in request bodies, evaluator responses or worker-like dictionaries cannot select
it. This inherits the existing gateway trust boundary; it does not independently
validate a JWT signature or establish deployed route/invocation permissions.
The selected owner is combined with the actual retained receipt proof. Fixed
server-side family, client check identifier, projection scope and known transport
version must match the actual row and operations metadata environment/family.
The binding object is internal type checking, not a network credential.

The underlying transaction retains the exact verified key inventory, matching
active/age-verified profile, deletion-marker absence, observed non-DELETING ACCESS,
receipt source and prior projection fences. An absent legacy version uses only
the already supported URL/message fallback; a missing client identifier is never
inferred. Missing optional source fields are also compared for absence, so a
concurrent legacy-version insertion cannot evade the original snapshot. Actual
client/version drift or deletion after authentication refuses the capture.

Capture occurs after the public response has been validated. Lazy import errors,
helper exceptions, conflicts and uncertain writes are contained; no capture
result or private reference is added to the response or logs. The validated
response, charge and provider dispatch behavior stay unchanged. Local tests
exercise all three real consumer implementations, including successful completion,
replay, prepared/unknown/invalid paths, and nonvacuous import/helper/write-failure
injection. They do not prove that a currently deployed provider or gateway uses
this code.

## Bounded optional work and fixed privacy deadline

Default off returns before new clock/account/service work. When enabled, the
hook requires at least 6000ms remaining and the existing one-attempt SDK client
with connect/read phase timeouts no greater than 0.2/0.3 seconds. A monotonic
four-second pre-dispatch window and at least two-second remaining reserve are
checked around owner qualification and before each additional receipt read or
transaction. These are dispatch guards, not hard end-to-end SDK completion or
latency guarantees. The existing `_account` has two reads between outer checks.
No capture retry is added and an unavailable budget skips optional work.

A successful capture or replay adds at most seven consistent item reads and one
transaction: two owner-profile/deletion reads, then receipt, inventory, ACCESS,
profile and deletion reads. Expired selective retirement takes a different
smaller path and uncertain retirement can require one extra read. This is an
SDK operation count, not a new IAM action name or a measured capacity, latency
or dollar-cost approval. Actual policy and execution/storage costs must be
qualified before enabling the producer.

The original seven-day operations deadline is unchanged. A seen expired receipt
with valid metadata, or a strictly validated expired orphan projection, now
invokes selective retirement after owner/proof/binding validation. Only the three
operations fields are removed; original full TTL, History, reservation/accounting
and pending lease queue are preserved. Known settled minimum-deadline and older
full-expiry index shapes are accepted for erasure. No missing metadata is reminted
and no raw source/client identity is inferred. A deliberate replay after erasure
is NOT_RECORDED. The prior paid-closure compatibility and full-delete authority
remain unchanged.

Seen-row repair and erasure do not discover or migrate every historical row.
Unseen older full-expiry index rows, mixed workers, actual sweep timeliness,
restored backups and deletion enforcement remain activation gates. This batch
introduces no full scan, account locator, migration resource or retention grace.

## Exact dependency handoff

`SECUR4ALL-237-DEPENDENCY-MANIFEST.json` pins the source base, 15 source-inspection
archives, 145 joined source files, existing contract files and requirements.
Thirteen archives carry the shared authority protocol; the two evaluators retain
only their existing contracts and cannot mint caller identity. All three HTTP
callers, lease/cleanup workers, paid closure and deletion carriers must be reviewed
as a coordinated set before any producer enablement. Their deployment need not
be inferred from this source manifest: actual alias versions and rollback remain
unset. The standalone reporting kernel is excluded from these archives.

The offline checker verifies ZIP bytes and exact protocol/member closure, and
negative tests reject missing cleanup/caller modules, old retirement or paid
closure code, source drift, an added reporting handler, and a false ready claim.
These checks are source compatibility, not a release approval. All archives were
built with `--skip-dependencies`, target Python3.14/ARM64, and remain explicitly
**not distribution-qualified**. The reviewed source can be used to build exact
native candidates later; dependency-complete artifact hashes, ARM64 runtime
imports and real route/authorizer/IAM qualification are still required.

No issuer, client, actor, table name, secret or HMAC material is invented or copied
into the handoff. It lists existing configuration names and default-off gates.
Actual table/index transaction permissions, reserved runtime, cost scope,
coordinated versions/rollback and older-row/backup coverage require a separate
reviewed deployment/activation plan and applicable owner approval. Neither
`AUTHORITY_ENABLED` nor `OPERATIONS_PRODUCER_ENABLED` is changed here.

## Evidence and remaining acceptance

The companion authenticated-callers JSON pins the exact source, tests, independent
review and manifest. The full local suite includes authority transactions,
URL/message/recovery integration, governed History, reporting and script checks;
network-blocked host imports inspect source archive layouts. Initial negative
fixture expectations are corrected to preserve the established unknown receipt
semantics; no existing implementation behavior was loosened to pass them.

No AWS authentication/service inspection, application endpoint, provider,
emulator, purchase, resource, permission, runtime flag or main promotion occurred
in this source batch. Source-only archives and Moto cannot prove deployed JWT
signature verification, actual request attribution, production latency, storage
or physical cleanup. Further complete attempt/cost/discovery/correction,
report/alarm/delivery work is outside this bounded increment. SEC243 real Dev
validation, SEC180 assembled release and physical-only ATCR148 retain separate
scope and open prerequisites. This source integration does not mark SEC237 Done.
