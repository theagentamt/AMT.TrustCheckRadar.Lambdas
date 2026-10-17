# SEC191 selective governed source reconciliation

This reconciles the source gap identified in the
[component manifest](SECUR4ALL-191-COMPONENT-MANIFEST.md). It brings the existing
governed History reader, its contracts and rules-only message support onto
`release-V01` without merging the divergent main tree or replacing newer billing
code. It changes no deployment, artifact selection, environment flags, selected
subjects, IAM, secrets, API routes or resources. The retained 21:22:59 service
snapshot remains historical evidence, not a fresh readiness receipt.

## Exact composition

The base is release `29138fffefe06a398ffb224d0de3cda42d7f73a4`. Selected source
comes from main `8ecc79678fd1f87c575c43f7f4ffb856869db48d`, which contains the
already qualified governed component history. The companion JSON lists each
exact copied Git object and each deliberate manual adaptation. This is a new
combined source tree, not a claim that any deployed archive was rebuilt from
one current commit.

Selected components include `governed_history`, `shared_governed_history`,
governed-History candidate1 contracts, the candidate3 rules-only profile,
message consumer/evaluator support, optional projected inventory reads and
build/import support. Existing main-only CI jobs gain the relevant test/build
commands. Their trigger, job, permission and release policy are unchanged;
no workflow was dispatched. The publisher workflow and SEC341 docs-only guard
remain byte-identical to the release baseline.

The release's newer entitlements, purchase-usage and prepare-auth diagnostics,
Play handoff, ownership, operations kernels and publication classifiers remain
preserved. The authority `_account`, `_record_prepare_auth_failure`, `_transact`,
account conditions and admission authority fences are unchanged. Main's older
entitlements and generic transaction-log changes are deliberately excluded.

## Default-off compatibility and the enabled component

The governed settlement setting defaults false. Its parsing is strict; unknown
Boolean text cannot turn it on. With the hook off, admission/settlement rows,
transaction payloads and ticking-clock sequence match the pinned release in
independent DynamoDB-emulator comparisons. Optional-setting lookup also
defaults off for the existing isolated archived-worker probes. No message
package is newly required by ordinary URL settlement/recovery.

When explicitly enabled by a later reviewed configuration, the hook follows
the existing governed contract: minimized source type and acceptance metadata
are attached on admission, and a content-free immutable projection is written
in the same settlement transaction as accounting. Governed History expires
seven days from settlement; replay does not renew it. That existing History
policy is distinct from the operations correction policy's at-most-seven-day
linkage from acceptance. No operations reference is added to these receipts by
this reconciliation.

The reader remains account/device/inventory-bound, checks canonical receipt
data after the sparse index, uses bounded cursor and response limits, and
rechecks ownership fences before return. It does not enable legacy History
mutations, recognition awards, export, account deletion or billing. The
candidate3 rules-only path requires its existing explicit Dev gates and cannot
fall through to AI or Google calls; AI and broader quality acceptance stay
separate.

## Source defects found before publication

Independent review reproduced two inherited reader defects using synthetic
local data. They are not reported live incidents:

1. A canonical `GetItem` timeout could be swallowed and returned as an empty
   list or missing result. Storage, clock and malformed service-envelope
   failures now produce fixed `SERVICE_UNAVAILABLE`/503. Successfully read
   missing, expired or corrupt rows remain suppressed. Validation and
   presentation use the same captured timestamp; raw service errors are not
   returned or logged.
2. A huge decimal limit string could reach Python's conversion guard and become
   a generic service error. The bounded page-limit parser now rejects it as
   `INVALID_REQUEST`/422 before integer conversion. Ordinary supported limits
   remain 1–50, default20.

The broader archived-worker regressions additionally caught direct access to a
new optional setting in old minimal settings probes. Using the existing main
pattern, absent optional settings now remain disabled. The two failing archive
cases are retained and rerun; test expectations are not weakened.

## Validation and artifact limits

The validation JSON records exact local outcomes and source-only ZIP hashes.
The retained suites exercise real local DynamoDB transactions through Moto,
replay, paid/complimentary accounting, deletion and index expiry, freshness,
rules-only no-provider behavior, auth/error handling and packaged worker
isolation. Independent tests compare actual default-off rows, transaction
payloads and clock behavior against the pinned release implementation, plus
protected source bytes and unchanged CI boundaries.

All eight backend contract anchors were independently byte-joined to Android
release `225cf63d5e62bcce4661aac617dfa2cca171be66`, including built-in recovery's
copy under `docs/contracts/`. This introduces no new mobile API or profile.
Native picker/QR/share/rotation changes and the separately approved 120-second
memory-only picker exception remain the Android worker's scope.

Local ZIPs use `--skip-dependencies`; they are inspection artifacts, not approved
deployment candidates. Isolated host import checks verify closed handlers and
archived URL-worker behavior without network access. They do not prove native
ARM64 runtime, actual AWS IAM/index configuration or live API behavior. No
signing, cloud publication, deployment, emulator or provider call occurs here.

## Remaining gates

- SEC191 stays open for fresh authorized target inventory, environment isolation,
  immutable artifact/configuration/rollback selection and assembled readiness.
  Checked-in tfvars defaults remain unsuitable as inferred live truth.
- The new reader fixes require an explicitly reviewed artifact/deployment path
  before they can be claimed in a runtime. Existing Dev component evidence is
  preserved; no new actual failure/recovery behavior was measured here.
- A combined Android Dev build remains a separate reviewed profile, with the
  billing-only canonical guard preserved. No source reconciliation unions
  account scopes, enables flags or authorizes a new store build or transaction.
- Operations trusted producers, durable ingestion/corrections and delivery stay
  unfinished SEC237/243 work. Main promotion/CI for SEC341 remains separately
  authorized work. URL-retention and provider-quality decisions remain their
  explicit gates; this source work does not answer or bypass them.
- Physical-only cases remain open ATCR148; paused iOS does not block the
  Android-first software work. Full release acceptance is not inferred from
  merged source, local fixtures or historical component deployment.
