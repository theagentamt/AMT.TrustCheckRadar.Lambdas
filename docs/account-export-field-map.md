# Account export candidate: approved scope and implemented readers

Owner approved current user-visible data with a scope manifest, observed values
rather than an immutable snapshot, no extra server copy, a fixed fifteen-minute
continuation window, and local cancellation without separate token revocation.
Accepting account deletion stops further export access. Candidate.3 includes the separately approved Play verification metadata. Export
activation remains an independently reviewed, subject-scoped Dev operation; source
completion is not activation or inventory acceptance.

The canonical typed field map is
`contracts/account-export/1.0.0-candidate.3/public-fields.json`. Each family below
uses an explicit allowlist; unknown stored attributes are never copied through.

| Public family | Implemented account-owned path | Public projection and boundary |
|---|---|---|
| profile | users `USER#subject/PROFILE` | Contact/name, age-attestation and profile status/times. Subject and database keys excluded. Owner requires completed onboarding (ACTIVE and ageVerified) for export; no payment or allowance requirement. |
| identity | Cognito AdminGetUser | Selected standard contact/name/verification attributes and custom:over_18 only. Exact username and unique sub must equal the authenticated subject. Unknown custom attributes, username, sub and session data excluded. |
| devices | devices `USER#subject/DEVICE#…` | Platform, OS version, status and lifecycle times; no fingerprints or binding controls. |
| recovery | recovery `USER#subject/RECOVERY#…` | Operation/result/status/completion time. Recovery AUDIT/RATE records are disclosed security/internal controls, not extra recovery receipt content. No operation IDs or fingerprints. |
| subscriptions | entitlements `USER#subject/ENTITLEMENT…` | Stored tier/status/product/platform, billing periods, verification time, access flag and allowance counts. No synthesized defaults or summing compatibility rows into duplicate grants. |
| usage | entitlements `USER#subject/USAGE#…` | Stored periodKey and usedCount where present. Legacy external-writer coverage remains an activation prerequisite. |
| purchases | validated USER purchase locators and exact TOKEN ownership targets | Exactly platform, productId and verifiedAtEpoch. Inventory revision and locator/target ownership must agree. Raw token/hash, account ID and anti-replay controls excluded. Legacy unindexed token rows require verified migration coverage. |
| access / allowance / trial | all verified retained authority-key account partitions | Explicit effective/source access state/validity, period limit/usage/reservations and trial activation metadata. No principal/store references, payload HMACs or grant/control revisions. |
| receipts | unexpired settled authority CHECK rows | Existing strict URL/message/recovery public summary validation, outcome, complete-only charge, assessment and original expiry; checkId capability removed. Approved embedded feedback exposes only category and receivedAt. No original submissions or operational execution data. |
| history / recognition | verified current History and recognition generations | Existing public History assessment schema; progress count and earned badge IDs. No original input, erasure jobs, cursor or acceptance controls. |
| participation / consent | owned participation row and CAMPAIGN_CONSENT rows | Observed consent state, policy/notice, event/time and approved public metadata. No consent/operation/account identifiers. |
| analysis_requests / scan_consumption | exact hashed owned analysis partitions | Status/type/time/expiry metadata only; no stored response, request body or authentication details. |
| research_observations | exact account outbox locator followed by strong owned event read | Observation time, source/risk/notice/signal categories and expiry. Missing live locator targets fail the export; expired rows are skipped. |
| play_verification | retained authority-owned Play token/binding partitions | Strict preparation or verification metadata only; no purchase token, digest, ciphertext, wrapped key, reverse binding or acknowledgment execution controls. Verification metadata is omitted at logical expiry even while its TTL row remains. |
| research_contributions | validated period key, derived contributor token and ContributorPeriodIndex followed by strong owned base reads | Language/taxonomy/signal/indicator categories, confidence, contribution count and expiry. No contributor token, vectors, lexical fingerprints, other contributors or campaign aggregates. |

Historical backups, transient streams/queues, logs, security/internal controls,
non-user-linkable aggregates and external provider/store records are explicitly
excluded from the approved download scope. They are not claimed absent or erased.
Research index traversal is eventually consistent; strong base-row ownership checks
do not turn this into a cross-table snapshot.

## Transport and key handling

Authenticated POST `/v1/users/account-export` starts a new operation or continues
using an AES-GCM encrypted capability. Every page verifies current account ownership,
deletion fence, active device/version, fresh signed auth_time and source inventory.
The dedicated environment-bound cursor keyring comes from Secrets Manager AWSCURRENT;
old keys must remain available through outstanding token expiry. No export database,
archive, emailed link or provider lookup is introduced. No allowance is deducted.

The manifest is a scope set, not traversal order. Every family emits at least one
page, including empty families. Retained authority namespaces can repeat families.
Retrying a continuation can observe changed values; clients replace that page and
discard later pages. START retry explicitly creates a new download. The completed
local file excludes continuation credentials and authentication/device headers.

Bounds are 64 KiB per response, 8 MiB cumulative wire bytes, 256 pages, 25 source
rows per read and 900 seconds. Limits fail explicitly; no truncated COMPLETE file.

## Remaining activation and acceptance work

- Verify all actual legacy purchase/usage writers, locators, authority namespaces,
  research key retirement and retained/linkable source coverage. Missing keys or
  invalid inventory fail the entire export; age alone does not prove unlinkability.
- Qualify real upper-volume inventories against byte/page/time limits. Synthetic
  coverage does not prove all legitimate accounts fit.
- SEC200 and SEC207 now provide separately recorded Dev deletion and campaign
  lifecycle evidence. SEC236 must still qualify the selected export runtime against
  the actual compatible resource/inventory pins; these prior stories do not imply
  an export HTTP success or prove every legitimate account fits export limits.
- Owner explicitly chose to keep export blocked until onboarding is complete;
  active binding and fresh authentication remain required. This is now an intentional
  product rule, not an unresolved device-less path. Fresh authenticated pending-age
  owners can request deletion without age attestation.
- Recheck the existing cursor secret and genuine inventories, select the reviewed
  export-only artifact, and qualify a designated synthetic Dev account through the
  real JWT route. Never create approval markers to make a test pass. Release/device
  UAT is tracked separately by SECUR4ALL-331 (BackendV1-5), not a Dev acceptance
  substitute. No source commit activates general admission or production.

## Scoped Dev qualification

`ACCOUNT_EXPORT_HTTP_SUBJECTS_JSON` is required when export is enabled: one to ten
unique lowercase canonical UUID subjects, including Cognito UUIDv7. Missing, empty
or malformed configuration returns fixed 503 `SERVICE_UNAVAILABLE` before SDK
construction. A valid JWT outside the scope returns 503 `SERVICE_NOT_ENABLED`
before owned data reads. Scope changes also invalidate further continuation access.
The disabled feature keeps its existing 503 `SERVICE_NOT_ENABLED` precedence.

Final inventory verification now precedes the last account/device/authentication
check; the account deletion fence is reread after device reads. This closes the
reproduced deletion/device-change windows during source verification. It cannot
make distributed reads atomic with a deletion accepted after the final fence read.
No further source storage reads occur between that check and response release.

The SDK composition in `tests/account_export_api/test_nonempty_dynamodb.py` covers
all 20 nonempty families, 27-row purchase/research/outbox/Play pagination, modern
work markers and v2 outbox locators, nested summary refusal, whole-fixture read-only
state, as-read continuation replay, inventory/resource-generation changes, and the
899/900-second boundary with fresh authentication. Ownership, History and Play
fixtures use production builders; other rows are accepted projection fixtures.
Cognito, KMS and Moto's resource identity metadata are injected. This is reader
composition evidence, not all-producer or deployed AWS acceptance.

The immutable candidate.3 contract introduced at
`39cce61623794a123e61d25f5248b0c081eccf4a` is unchanged. Android's candidate.2 parser
must be updated to this existing candidate.3 contract before its export gate opens.
