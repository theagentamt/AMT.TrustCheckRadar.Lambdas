# Restricted Dev synthetic acceptance operator helpers

Operator scripts only; production Lambda source unchanged. Root owns execution and independently
reviewed plans. No helper initializes approval from current state or test fixtures.

## Fresh identity helper

`scripts/qualify_dev_account_deletion.py` has explicit `prepare` and `authenticate-http`
stages, default local validation, and `--execute --plan-sha256` for reviewed
execution. A reviewed private prepare plan pins actual pool/client settings, writer
source/revision and exact Dev resources. Never repeat prepare or reuse its email
for another account. Existing operational journals remain external to Git.
A new HTTP-stage plan must pin the final activated API revision. The private
journal binds original subject and operation identity across these two plans.

Prepare uses suppressed AdminCreateUser with fixed synthetic given/family names,
requires exact returned Username/sub and actual readback, checks existing profile
and deletion absence, invokes the deployed fenced post-confirmation writer, then
verifies its genuine pending-age profile. This is administrative bootstrap and
explicit Lambda invocation, not native signup-trigger dispatch acceptance.

Authenticate sets a random permanent password only immediately before auth;
password and access/refresh tokens are never journaled/output. Actual GetUser
checks token identity, the HTTPS request targets only api-dev.andmorethings.net
without redirects, and the API JWT authorizer remains authoritative. GET must
report NOT_REQUESTED; POST is attempted at most once for the journaled UUID.
POST_ATTEMPTED ambiguity requires separate read-only reconciliation, not another
password reset or automatic new request. ACCEPTED is not complete erasure.
There is no direct AdminDeleteUser or DynamoDB write in this helper.

Journal creation is exclusive0600; updates use same-directory private temporary
file, file fsync, atomic replacement and directory fsync. The reviewed sixteen
local tests include failed replacement preserving the previous valid journal.

## Exact four-marker helper

`scripts/initialize_deletion_inventories.py` defaults offline. It imports the actual
validators from a clean exact `sourceCommit` checkout and accepts four marker
rows supplied by root. It creates no approval fields, epochs, manifests or rows.
Plan fields: schemaVersion, sourceCommit, tables, markers, manifests,
closedFunctions, approval. Tables are exact Dev pipeline/ledger name+TableId.
Marker map keys: locator,recovery,completion,account. Manifest entries each have
path and SHA256 of exact file bytes; that digest must equal its marker pin.
Manifests have account/region/environment, with arbitrary evidence content bound
by exact bytes. Candidate status is not approval.

External approval file exact fields:
- schemaVersion1, account107827791950, regionus-east-1, environmentdev;
- sourceCommit and manifestSha256 map of all four exact byte hashes;
- runtimeSnapshotSha256 = SHA256 canonical JSON(plan.closedFunctions);
- tableIdentitySha256 = SHA256 canonical JSON(plan.tables);
- approvedAtEpoch equal to all four supplied marker epochs;
- reviewReference and decision APPROVED_FOR_MARKER_INITIALIZATION.

Canonical JSON uses sorted keys and separators comma/colon. Plan.approval binds
path and SHA256 of this separate immutable file. The external reviewer/operator
must supply it after evidence review; these syntactic checks are not an audit or
substitute for historical/current-resource qualification. Full-account and
campaign inventories remain environment-wide, never fresh-subject shortcuts.

Source validators enforce exact marker fields, ordered component/writer lists,
completion-to-locator/recovery link pins and clocks. The locator minimum must
match the current period. All approval times must be strictly in the past; later
commands remain subject to production strict-after approval checks.

The writer checks exact account, region, active table ARNs/TableIds and pinned
function code/revision/environment hashes before and after. Four campaign
admission gates and bridge stream/recovery/completion gates stay false; account
API/finalizer/recovery writes stay false and HTTP scope empty. Root's independently
reviewed all-writer/role/trigger/drain/restore boundary remains necessary: Lambda
configuration and DynamoDB cannot be atomically fenced together by this helper.

All four markers absent: one four-Put transaction, each conditionally absent.
All four exact: idempotent readback only. Partial, unknown or differing state:
refuse without writes. Lost transaction acknowledgment: reread all four exactly,
recheck boundary, no automatic second transaction. If postcommit checks fail,
markers might exist; inspect exact readback and retry only the reviewed plan.
Existing HMAC/ownership markers, data, grants and retention are untouched.

Seventeen offline writer tests cover actual source validators, four-Put atomic
shape, lost acknowledgment, partial refusal, expired/future/malformed approval,
pin/invariant mismatch, exact DynamoDB type identity (BOOL cannot alias N), and gate drift. Synthetic approval fixtures are test
assumptions and are never eligible as real manifests or marker authorization.
