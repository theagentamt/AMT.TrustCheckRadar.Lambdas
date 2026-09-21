# Account export candidate: approved scope and implemented readers

Owner approved current user-visible data with a scope manifest, observed values
rather than an immutable snapshot, no extra server copy, a fixed fifteen-minute
continuation window, and local cancellation without separate token revocation.
Accepting account deletion stops further export access. Candidate.2 implements this
scope but is disabled; source completion is not activation or inventory acceptance.

The canonical typed field map is
`contracts/account-export/1.0.0-candidate.2/public-fields.json`. Each family below
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
- Finish whole-account deletion integration, guarded ENTITLEMENTS cleanup and
  campaign pagination before claiming complete lifecycle support.
- Owner explicitly chose to keep export blocked until onboarding is complete;
  active binding and fresh authentication remain required. This is now an intentional
  product rule, not an unresolved device-less path. Fresh authenticated pending-age
  owners can request deletion without age attestation.
- Provision keys/verified inventory only after acceptance, wire authorized routes,
  and perform staging/device/security/end-to-end qualification. No current source
  commit activates the candidate or changes production.
