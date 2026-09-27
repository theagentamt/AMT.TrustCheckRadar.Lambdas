# Backend retention and disclosure attestation — 27 September 2026

This is the Lambda owner's source/evidence attestation for ATCR-95, based on
`release-V01` commit `a1eafcecaead9cd1fe7bdaecc6a76e5ce6ac1e4a`. It changes no
collection, retention policy, consent, runtime flag, data or deployment. Earlier
inventory documents remain historical records; their lists of missing workers and
legacy research incentives are not a current product specification.

Source capability, isolated synthetic qualification, scoped live Dev acceptance,
current activation and production availability are separate claims. Infrastructure
owns the current installed hashes, permissions, gates, log/backup metadata and
restore inventory. Android owns its actual entry-point wiring and phone storage.
This attestation is not provider-contract verification, legal approval, a store
submission or a certification of anonymity or complete physical erasure. The paired
[infrastructure retention reconciliation](https://github.com/theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure/blob/f7e9233a9d8d3239cd72a1b972d0cdcb0a1615f9/docs/ATCR-95-RETENTION-RECONCILIATION.md)
records current metadata and public-policy checks; its evidence directory preserves
the dated observations rather than implying these settings can never change.

## Corrections to the September 21 disclosure blockers

| Earlier statement | Current supported fact | Boundary that remains |
| --- | --- | --- |
| Research opt-in changes 10/15 free quota | Current participation uses independent research consent and cannot read/write entitlements. The retired legacy analysis/snapshot/purchase paths cannot restore the bonus. The coordinated Dev retirement was installed September 21. | New Join and general research processing remain separately gated. SECUR4ALL-217/218 are still In Progress; their old bonus acceptance text is superseded by the V1 section. Neither consent nor source delivery grants paid/trial/complimentary access. |
| No full-account export or identity finalizer | Candidate.3 protected direct export and all 12 account-erasure component producers/finalizer exist. SECUR4ALL-200/236 and ATCR-94 have scoped actual Dev evidence. | This is not general availability or a promise to erase provider/store/backups/user-downloaded copies. Current route/scope restoration must be read from infrastructure, not inferred from a successful temporary test. |
| Campaign cleanup cannot complete | SECUR4ALL-207 includes deadline cleanup, paired work inventory, seal/retired-proof consumers, explicit aggregate expiry and guarded KMS retirement. Actual isolated AWS composition exercised computed seal → actual KMS DisableKey/ScheduleKeyDeletion → account and withdrawal completion. | Synthetic clocks/inventories/captured transport in those fixtures are disclosed. A real key is never retired merely because its age elapsed; current existing-key state is infrastructure evidence. |
| Post-confirmation logs have unlimited retention; recovery store is absent | Infrastructure reports the September 23 log correction applied, and September 27 metadata confirms all 33 observed log groups have 14 days; the recovery-control table now exists with seven-day PITR. | These counts describe the audited Dev resources, not every future log group, provider log, UAT or Production. Expiration configuration is not a content audit or synchronous deletion proof. |
| Every nonempty export family already had actual producer/live proof | Local SDK composition covers nonempty approved projections across all 20 families; actual Dev and Android export used genuine identity/profile/device while the other families were empty. | Do not present the local injected-control/projection fixtures as a live populated all-family test. |

The latest tracker descriptions/comments for SECUR4ALL-217/218 were inspected on
September 27. Their deployed-retirement update supersedes the earlier pending
installation comments, but does not close their remaining broader acceptance.

## Input and external-processing boundary

Raw screenshots/images and OCR extraction belong to the mobile inventory. The
modern message Lambda contract accepts reviewed `sanitizedText`, language and
speaker role; it does not accept image uploads or arbitrary attachment fields.
This source fact supports the reviewed screenshot-local flow only when the mobile
entry point actually uses that contract. It is not a claim about arbitrary SDKs,
all external applications or a future enabled endpoint.

Sanitization is data minimization, **not anonymization**. Message text can still
identify people through context or imperfect masking. URLs can contain sensitive
path/query information; a hash, random event ID or period HMAC does not alone make
retained data anonymous. Account/profile/device identifiers, purchase digests and
research linkage remain purpose-bound personal or pseudonymous service data.

| Boundary | Source behavior | What must not be claimed |
| --- | --- | --- |
| Modern message evaluation / OpenAI | The optional proposer sends reviewed sanitized message text, language and role to the fixed Responses endpoint; the request sets `store:false`, `stream:false`, `background:false` and no tools. It requests bounded structured output; proposals are separately validated. Source does not persist or log model proposal content. The consumer retains the approved minimized result/accounting summary rather than the submitted text in its authority receipt. | `store:false` is not proof of provider zero retention, abuse-monitoring exclusion, geographic processing, training terms, a DPA, or deletion of external copies. No account-specific provider contractual attestation was supplied to this audit. Enabled model/route settings require infrastructure evidence. |
| Retired conversation analysis | The installed retirement entry point is owned historical replay only; it cannot initiate a new model dispatch. Old analysis-client code remains in the tree as historical implementation. | Do not describe that old code as an active new-analysis pipeline, or equate retiring a route with erasing its retained records. |
| URL resolution / Google Web Risk | The lookup implementation sends the URI to Google's fixed Web Risk endpoint. Resolution can contact a destination host using bounded network requests, exposing ordinary network/request metadata to that host. | Do not promise that a full URL never leaves the device/backend, that destination requests are invisible, or that Google/destination retention is verified by our cache TTL. |
| VirusTotal | No VirusTotal endpoint/integration was found in the current Lambda source audit. | A generic legacy vendor list is not evidence that VirusTotal processes this product's requests. An Android SDK or another repository would need its own attestation. |
| Google Play | The verifier sends purchase credentials to Google to obtain current subscription/order proof and, where enabled and qualified, acknowledge a purchase. Background notifications request fresh authoritative verification; they do not grant access by themselves. | Our deletion removes/minimizes approved local copies, not Google's independent account, order or payment records. License-test/provider/RTDN readiness is separate from source tests and closed-handler deployment. |

Source anchors: [message proposer](../src/message_evaluator/proposer.py),
[message consumer](../src/message_consumer/service.py),
[retired analysis entry point](../src/conversation_analysis/app.py),
[Web Risk lookup](../src/url_assessment/lookup_provider.py),
[Play verification](../src/shared_play_verification/proof.py).
No new provider-policy lookup is represented as an account-specific contractual
verification; unresolved processor claims stay explicitly unverified.

## Retained data and deletion boundaries

These are approved/source-enforced logical bounds. Asynchronous TTL and backup
expiry are not instantaneous physical erasure guarantees. Explicit deletion and
reconciliation enforce the applicable service deadlines; unknown/mismatched
records fail closed and require operational reconciliation rather than false
completion.

| Family | Purpose and lifetime | Access/deletion qualifications |
| --- | --- | --- |
| Cognito/profile/active binding | Identity, onboarding and current device authority while the account exists. Inactive device rows use their separately configured expiry. | Deletion atomically fences the original operation, revokes sessions, drains owned device rows and removes profile/identity only after qualified receipts. No subscription gate. Delayed profile/age writers check the terminal fence. |
| Device recovery | Seven-day minimal retry receipt, 24-hour rate state, 90-day security audit. | Account cleanup removes rate state and minimizes receipt/audit fields without refreshing original deadlines. Device fingerprints and sensitive payload are removed from retained minima. |
| Legacy analysis replay/abuse | Ordinary deduplication is 900 seconds; request/rate/consumption state is operational data. | Account deletion strips response/authorization/event linkage while preserving only the approved minimal dedupe fields, caps ordinary dedupe to the original deletion request plus 900 seconds without extending a shorter deadline, and deletes rate/consumption rows. Valid History dedupe follows its separately approved 120-day bound. |
| Modern check authority | Seven-day minimized request/accounting receipts and attempt counters; receipts can include the approved result summary and are still account-associated. | No permission to retain submitted raw or sanitized text in place of the public summary. Logical expiry/reconciliation, owned deletion and provider-attempt budgets remain separate from charged completed checks. |
| History/recognition | 90-day bounded assessment content; no raw text, OCR/image, sanitizedText, original entities or snippets in the backend History schema. Current dedupe is 120 days; mutation receipts seven days; History cursor capabilities 15 minutes. | Delete-one/clear/reset invalidate the appropriate generations and schedule explicit cleanup. Account erasure traverses retained generations. Minimal suppression/control records can remain; do not say every stored row vanishes at logout or deletion. Native History live mutation release qualification remains with its existing follow-ups. |
| Consent | Account-lifetime current state; exact minimal consent/operation audits 400 days from the applicable event, with original deadlines preserved on replay. | Withdrawal blocks future eligibility through authoritative state/epoch/deletion checks and creates a 24-hour cleanup command. Rejoining never cancels old cleanup or grants allowance. No separate commercial or demographic permission is granted by research consent. |
| Research outbox | Observation content has the original 72-hour bound. Its authoritative modern locator has the original event deadline plus 24 hours, expressed as logical expiry rather than independently expiring TTL. | Explicit paired event/locator/work removal and receipt coverage are required. Removing TTL from the locator prevents lost discovery, not permission to retain it indefinitely. Delayed queue/stream delivery rechecks consent, owner/deletion and source evidence. |
| Research transient pipeline/work | New transient deadlines are the minimum of original/earlier bounds, the 21-day maximum where applicable and the 14-day period end plus seven-day recovery boundary. WORK/lookup references share the target deadline and are removed atomically with the target. | These references are paired discoverability records, not anonymous logs. Due work is cleaned before period end where required. Poison/unknown rows keep completion unverified and emit operational signals; clocks cannot authorize false seals. |
| Contributor suppression / period proof | Bounded contributor tombstones protect delayed processing; modern work tracks their exact original deadlines. Period controls/qualified retired-prefix evidence are metadata, not user content. | Cleanup requires exact generation/resource/inventory proof. SEALED precedes key retirement; DisableKey and scheduled deletion are distinct from eventual key destruction. Proof-only later account/withdrawal cleanup must not generate a new MAC from a retired key. |
| Published aggregate | Approved thresholded public projection, retained for at most 400 days with explicit expiry traversal. | It excludes account IDs, contributor tokens, per-user vectors and raw text. The approved aggregate policy may retain it after individual withdrawal. That policy does not make all precursor data anonymous or prove anonymity of arbitrary historical rows. |
| Play recoverable token | Dedicated encrypted purpose-limited token, linked to a live account; deadline is latest authoritative verified access expiry plus seven days. Fresh authoritative shorter expiry shortens retention. | Explicit account/expiry cleanup; no raw token in export, logs, request receipts, usage ledger or an extra AWS queue. Dedicated store must remain outside PITR/on-demand/AWS Backup. This is different from the minimized purchase usage record below. |
| Purchase ownership/usage | Ownership and account references are erasable. Minimized global purchased-period usage can survive account deletion until the latest verified access end plus seven days, preserving funded dates and used/reserved counts. | Contains no account ID, raw token/order, message or URL. Fresh store-verified restore can preserve remaining purchased checks but cannot reset used checks or automatically relink a same-email new account. Its separately approved backup window may retain historical copies up to 35 additional days. |
| Account deletion evidence | Minimal component receipts record the approved 120-day retention boundary. Fixed terminal fence is durable suppression evidence. | `retainUntilEpoch` is metadata, not an enabled ledger TTL. Fence/receipt retirement is not automatic merely because 120 days elapsed; backup/replay qualification is required. Never publish “all deletion records disappear after 120 days.” |

Source anchors: [History contracts](../src/shared_history/contracts.py),
[History configuration](../src/shared_history/config.py),
[account workers](../src/account_data_api/service.py),
[finalizer](../src/shared_account_finalization/service.py),
[research consent contract](../contracts/campaign/research-consent-v2/README.md),
[period deadline](../src/shared_campaign_locators/period.py),
[paired outbox](../src/shared_campaign_work/outbox.py),
[work transactions](../src/shared_campaign_work/transactions.py),
[aggregate publication](../src/campaign_lifecycle/publication.py),
[Play tokens](../src/shared_play_lifecycle/tokens.py),
[purchase usage](../src/shared_check_authority/purchase_usage.py).

## Protected export, erasure and restore evidence

Candidate.3 provides approved minimized public fields across 20 families, not a
raw dump of every storage attribute. Explicit START fixes the 900-second window;
CONTINUE cannot extend it. Each page requires current signed authentication,
`auth_time` freshness within 300 seconds, completed onboarding, the active owned
device, current approved inventories and no deletion fence. Renewed authentication
does not reset the export's original deadline. There is no subscription condition.
Pages are direct private/no-store JSON with authenticated encrypted continuation;
there is no server S3 export object, export job/cursor table or download copy.
Reads are declared **as-read**, not a database snapshot. Acceptance of account
deletion invalidates continuation immediately; already saved user documents are
outside the service's ability to revoke.

Reused evidence, without repeating or upgrading its scope:

- [SEC200 producer retry map](account-deletion-producer-retry-evidence.md) and
  [Dev closure evidence](evidence/account-deletion-dev-closure-2026-09-26/restore-runtime.json):
  all-component qualification, actual disposable Cognito deletion/lost response,
  same-email new-subject isolation, retained purchase counters, and composed
  restored-copy suppression. Residual restored records remain quarantined;
  this is not successful re-opening of a native backup restore.
- [SEC207 work protocol](campaign-period-work-candidate.md) and
  [actual qualification evidence](evidence/campaign-period-lifecycle-2026-09-27/README.md):
  actual SDK/DynamoDB/KMS qualification, with synthetic inventory/clock and
  captured transport limits stated. Scheduled key deletion is not destruction.
- [SEC236 export qualification](account-export-dev-qualification.md): populated
  SDK reader/projection evidence and no mutation. The separate infrastructure
  [Dev acceptance](https://github.com/theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure/blob/release-V01/docs/SECUR4ALL-236-DEV-ACCEPTANCE.md)
  records actual authenticated direct/HTTP export and same-cursor deletion denial.
- Infrastructure [ATCR94 engineering closure](https://github.com/theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure/blob/release-V01/docs/ATCR-94-ENGINEERING-CLOSURE.md)
  records actual native export and native single-operation deletion separately
  from backend 12-receipt observation, including failed observations and later
  read-only reconciliation. It is not evidence of actual nonempty History
  delete/clear/reset in Dev; those existing live matrices remain separately scoped.

Current Dev infrastructure metadata reported September 27: all 13 inventoried
tables exist; foundation/ledger/intelligence PITR is 35 days, History and recovery
PITR seven days, campaign outbox/pipeline and Play token PITR disabled. Ledger TTL
is disabled. All 33 observed log groups have 14-day retention. These are
infrastructure-owner observations, not new AWS reads by the Lambda author.
PITR-disabled does not by itself prove absence of manual exports or copies;
consult the resource-specific backup inventory and restore quarantine. Restored
resources cannot serve traffic just because their old inventory marker says
approved: resource/generation pins, current suppression and requalification apply.
SECUR4ALL-245 retains native restore/re-opening work; release and physical-device
follow-ups remain distinct from completed Dev component evidence.

## Disclosure decisions still requiring an owner

No source fix or broader activation is warranted merely to make disclosure copy
sound more complete. The following remain explicit:

- External provider processing/data-use/retention, contractual roles, geography
  and support/deletion responsibilities need account-specific owner evidence.
  Code request flags cannot supply it.
- Current public EN/ES privacy/deletion policy and Google Play Data Safety mapping
  need SECUR4ALL-92/94 reconciliation. Infrastructure reports public policy bytes
  unchanged since September 21; they are not approved by this attestation.
- General research enrollment/production, paid store lifecycle and every deployed
  mobile variant must be described according to its actual gates. A scoped Dev
  test is not permission to advertise availability or a universal 24-hour erasure
  guarantee covering logs, backups, stores and downstream processors.
- Runtime logs must remain content/credential-free with fixed diagnostic codes;
  source/static checks and sampled failures do not prove that every SDK/provider
  error in every deployed path has never exposed data. No new log-content audit
  was performed here.

Android can now remove stale claims that the source lacks independent consent,
full export, component deletion or identity finalization. It must retain accurate
availability wording, masking limitations, user-saved-copy privacy, external-store
boundaries and the distinction between operational evidence and legal approval.
