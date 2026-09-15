# Account-data inventory and deletion/export gap contract

Status: **source inventory complete for the Lambda repository; policy approval and
runtime activation remain pending**

This document inventories every persistent or asynchronously retained data family
that the Lambda source reads or writes. It is evidence for completing the account
data design; it is not privacy/legal approval, an authorization to deploy, or proof
that expired records have been physically removed.

## Owner decisions recorded 2026-09-14

The following decisions are approved for Dev and are now reflected in the source
contract where applicable:

- device-recovery security audit: 90 days;
- device-recovery retry receipt: 7 days;
- device-recovery rate state: 24 hours;
- device-recovery table PITR: 7 days once the table is provisioned;
- post-confirmation CloudWatch log retention: 14 days;
- minimal account-deletion component receipts: 120 days, containing only the
  operation/status/timestamps, necessary account linkage and no contact or
  submission content; the fixed fence cannot retire until backup/replay coverage
  is verified;
- existing minimal consent evidence: 400 days;
- account export occurs before deletion; accepting deletion cancels every unfinished
  export, and there is no ongoing consumer login or status access after Cognito
  identity removal;
- billing is handled by app stores and no separate local billing-retention period is
  required.

The billing decision does **not** authorize deleting or reassigning token-keyed
anti-replay records, inventing a retention duration, or assuming there are no
existing transactions. Entitlement, usage, purchase-token ownership and legacy
locator coverage therefore remain blocked.

The product-wide deletion producer remains disabled by all of these independent
gates:

- `ACCOUNT_DELETION_ENABLED=false`
- `ACCOUNT_DELETION_POLICY_STATUS=pending`
- `ACCOUNT_DATA_INVENTORY_STATUS=pending`
- `ACCOUNT_DELETION_COMPLETION_STATUS=incomplete`
- `COGNITO_USERNAME_IS_SUB=false`

Do not change those values from their safe defaults until every blocking decision
and worker in this document is accepted and environment evidence exists.

## Linkage vocabulary

- **Direct**: the Cognito `sub`, an email/phone/name, or another user identifier is
  stored in the key or item.
- **Deterministic derived**: the key contains an unkeyed SHA-256 of `sub`. The
  service can reproduce it for deletion; it is still user-associated data.
- **Rotating pseudonym**: a period-specific KMS HMAC token can be linked to a
  subject only while the corresponding key remains enabled. It is still in scope
  during that window.
- **Event-linked**: a random event/request ID is not a subject identifier by itself,
  but another retained record links the event to the subject.
- **Aggregate**: thresholded output contains no account ID, contributor token, raw
  text, or per-user vector. It is not currently reversible to a user through this
  repository.

TTL timestamps are logical expiry inputs and defense in depth. DynamoDB TTL, queue
retention, and log expiration are asynchronous and do not prove physical erasure
at the deadline. Explicit bounded deletion plus reconciliation is required wherever
the product promises an erasure SLA.

## Inventory matrix

### Cognito identity and users profile

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| Cognito user | User Pool username and `sub`; standard/custom attributes may include email, phone, name and age acknowledgement | Cognito creates/authenticates it. `post_confirmation`, `age_attestation`, and every protected API consume claims/trigger attributes. `account_data_api` currently calls global sign-out only. | No `AdminDeleteUser` finalizer. No account export implementation. | **Erasable identity.** Session revocation is not deletion. Decide username/sub mapping, final deletion ordering, retry behavior, attribute export, and how status remains available after the identity is deleted. |
| Users `PROFILE` | `PK=USER#<sub>`, `SK=PROFILE`; `sub`, email, given/family name, phone, age acknowledgement/status/timestamps, and deletion operation metadata | Created by `src/post_confirmation/app.py`; updated by `src/age_attestation/app.py` and `src/account_data_api/service.py`; read/condition-checked by protected account, device, History and analysis paths. Profile creation and age updates now condition-check absence of the fixed deletion fence in the same transaction. | Deletion request atomically changes status to `DELETION_REQUESTED`, but no final profile minimizer/deleter exists. No full export. | Direct identifiers are **erasable**. A minimal account-deleted tombstone may be necessary, but its exact fields, location and retention are not approved. Do not retain contact/profile attributes in that tombstone. |

The Dev metadata audit found the existing post-confirmation log group has no
retention while 16 other matching Lambda/API groups have 14 days. The approved
post-confirmation target is 14 days, but applying it remains infrastructure work.

### Device binding and recovery

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| Active device pointer | Device table `PK=USER#<sub>`, `SK=ACTIVE_BINDING`; fingerprint or `NONE`, version and timestamp | `device_registration` and `device_recovery` write it transactionally; analysis and History APIs read it | `account_data_api.delete_device_bindings` queries the full user partition and deletes it. No export. | **Erasable.** Existing device component receipt covers it. |
| Device records | Device table `PK=USER#<sub>`, `SK=DEVICE#<bindingFingerprint>`; direct account ID, fingerprint, platform, OS version, status and timestamps | Registration/recovery write; device-authorized APIs read. Inactive rows have `expiresAt` (currently 180 days). | Same bounded partition deletion as the pointer. TTL is supplementary. No export. | **Erasable.** Existing component worker is implemented, but deployed bounded retry/reconciliation evidence is still required. |
| Self-recovery idempotency receipt | Recovery control table `PK=USER#<sub>`, `SK=RECOVERY#<operationId>`; request payload hash, fingerprint, result and completion time | `device_recovery` reads/writes; approved seven-day logical retention | `account_data_api` now removes payload hash/fingerprint, preserves only the bounded operation/result/status/times through the original expiry, explicitly deletes already-expired rows and writes an exact `DEVICE_RECOVERY` receipt after bounded completion. | **Minimal seven-day retry receipt after deletion-time minimization.** Native TTL remains supplementary. |
| Self-recovery rate state | Recovery control table `PK=USER#<sub>`, `SK=RATE#<window>`; request count/time | `device_recovery` transactionally updates; approved 24-hour logical state | The new recovery component explicitly deletes it regardless of remaining TTL. | **Erasable operational state.** No tombstone is retained. |
| Self-recovery audit | Recovery control table `PK=USER#<sub>`, `SK=AUDIT#<epoch>#<operationId>`; action/result/fingerprint and time | `device_recovery` writes; Dev policy now requires exactly 90 days | The new recovery component removes the fingerprint and preserves only record type/version, operation, actor type, action, result, occurrence and original expiry. Already-expired rows are explicitly deleted. | **Minimal 90-day security audit.** Seven-day PITR is approved when the currently absent Dev recovery table is provisioned; restore reconciliation evidence remains required. |

### Entitlements, purchases and usage

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| Entitlement state | Entitlements table `PK=USER#<sub>`, `SK=ENTITLEMENT#google_play#trustcheck_radar_pro_monthly`; compatibility `SK=ENTITLEMENT`; account ID, platform/product, billing periods, status, order/token hashes, access and quota state | `purchase_handoff`, `conversation_analysis`, and `campaign_participation` write; `entitlement_snapshot`, analysis and participation read | No account deletion worker or full export. | Mixed **erasable service state** and potentially **necessary purchase/audit evidence**. Finance/legal must approve exact retained receipt fields and duration before a minimizer is implemented. Raw entitlement state must not survive merely because some billing evidence is retained. |
| Purchase-token idempotency | `PK=TOKEN#<sha256(purchaseToken)>`, `SK=IDEMPOTENCY`; plaintext `accountId`, product/platform, verification and billing-period values; no TTL | `src/purchase_handoff/idempotency.py` reads/writes by token hash | A `USER#<sub>` query cannot discover these rows. No account locator, deletion worker or export path exists. | **Blocking discovery gap.** Preferred proposal: atomically add a `USER#<sub>/PURCHASE_TOKEN#<hash>` locator in the existing entitlements table for new writes, without a new table/GSI. Deletion can enumerate locators while the token-keyed record continues to prevent cross-account token reuse. Exact minimization/retention and legacy backfill remain unapproved. |
| Usage counters | `PK=USER#<sub>`, `SK=USAGE#<periodKey>`; usage counts/period | `entitlement_snapshot` reads. No Lambda writer exists in this repository, so the external producer must be identified. Infrastructure reports a current 548-day default. | No deletion/export worker. | **Inventory-owner blocker.** Identify the writer and authoritative retention basis. Do not silently replace 548 days with the unrelated History retention. |

Google Play is an external source/processor for purchase verification. Its account,
order and subscription records cannot be erased by these Lambdas; the policy must
separate locally erasable copies from records Google or the merchant must retain.

### Conversation analysis, abuse controls and model processing

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| Analysis request/replay | Abuse table `PK=ANALYSIS#REQUEST#<sha256(sub)>`, `SK=<requestId>`; payload hash, lease/status, response, optional campaign/history authorization and event ID | `conversation_analysis/abuse_controls.py`, `scan_access.py` and History integration | The `ANALYSIS_ABUSE` component queries this deterministic partition in strongly consistent pages of 100, deletes expired rows, and replaces unexpired rows with the exact content-free `PK`/`SK`/`COMPLETED_ERASED`/payload-hash/original-expiry allowlist. It accepts the separately approved 120-day History tombstone written by History-first cleanup and removes any remaining authorization/event metadata without shortening its expiry. | Response, leases, authorizations and event IDs are **erasable**. The ordinary analysis writer's prior source default is 900 seconds; an older deployment example says 86400 seconds, but no approved Dev value or legacy-row inventory was supplied. Exact ordinary-request dedupe retention remains an activation decision. |
| Request rate | `PK=ANALYSIS#RATE#<sha256(sub)>`, `SK=<windowStart>`; count/time with logical TTL | `abuse_controls.enforce_rate_limit` | The `ANALYSIS_ABUSE` component deletes the whole deterministic partition in bounded pages. | **Erasable operational state.** TTL remains supplementary, not proof. |
| Scan rate | `PK=ANALYSIS#SCAN_RATE#<sha256(sub)>`, `SK=<windowStart>`; count/time with logical TTL | `scan_access._enforce_scan_rate_limit` | The `ANALYSIS_ABUSE` component deletes the whole deterministic partition in bounded pages. | **Erasable operational state.** No tombstone is retained. |
| Scan consumption | `PK=ANALYSIS#CONSUMPTION#<sha256(sub)>`, `SK=<requestId>`; hashed account marker, consumption type/time with logical TTL | `scan_access.commit_scan_and_request` writes it atomically with request/entitlement state | The worker can deterministically query this partition, but deletion is policy-blocked and no `ANALYSIS_ABUSE` receipt is written while `ANALYSIS_CONSUMPTION_DELETION_POLICY_STATUS=pending`. All request, result, rate and consumption writers now condition-check the active profile and fixed deletion fence in the same transaction. | **Proposed erasable operational state, not approved.** App-store billing does not by itself prove that local quota/dedup/security evidence has no independent purpose. Owner/security must approve delete-versus-minimize fields and duration. |
| OpenAI request/response processing | Source text is sent to the configured Responses API; the API key is in Secrets Manager | `conversation_analysis/analysis_client.py` | No repository-controlled provider deletion/export mechanism | External-processing blocker: approve vendor data-use/retention configuration and incident/export responsibilities. No paid/model call is authorized by this inventory. |

The conversation-analysis Lambda now requires the canonical
`ANALYSIS_ABUSE_TABLE_NAME`; the unsafe `TABLE_NAME`/`USERS_TABLE_NAME` fallback
was removed. Dedicated environment tables remain mandatory before account-data
activation so item-family IAM and backup/retention boundaries can be proven.

### History and recognition

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| History content | Content table `PK=USER#<sub>#HISTORY#<generation>`, `SK=COMPLETE#<completedEpochMs>#<requestId>`; bounded assessment summary/result metadata | Analysis completion writes; History list/detail/export reads; lifecycle deletes | Delete-one, clear, retention and account-deletion jobs explicitly delete content. `GET /v1/users/history/export` is paginated but is History-only. | **Erasable.** Current contract is 90-day content and 24-hour purge, with PITR policy separately gated. Deployed physical deletion/reconciliation/restore evidence remains required. |
| `STATE` | Control `PK=USER#<sub>`, `SK=STATE`; account status, generations, accepted sequence | Bootstrap/completion/mutations/deletion bridge/lifecycle write; all History paths read | Account deletion fences state and ultimately marks it deleted; it is not fully removed. | **Minimal tombstone/control candidate.** Approve exact post-delete fields and duration; direct subject linkage remains. |
| Request locator/tombstone | Control `PK=USER#<sub>`, `SK=REQUEST#<requestId>`; payload hash, generation, content locator/status and lifecycle fields | Analysis completion and History lifecycle/mutations | Content locator becomes content-free and is retained for dedupe, then explicitly swept; default contract is 120 days where approved | **Minimal replay tombstone** after `contentSortKey` and replay response are removed. Retention must cover content retention plus erasure SLA, and restore must not resurrect content. |
| Recognition progress | Control `PK=USER#<sub>`, `SK=PROGRESS#<generation>`; qualifying count and badge IDs | Bootstrap/analysis/reset write; progress API reads | Account History erasure covers recognition generations; exact post-delete state handling is tied to the erasure job | **Erasable.** No full-account export currently includes it outside the History API contract. |
| Mutation receipt | Control `PK=USER#<sub>`, `SK=MUTATION#<operationId>`; operation/target/status; configured seven-day expiry | Mutation API writes/reads | Explicit control-table lifecycle sweep; account deletion does not immediately remove every receipt | **Minimal short-lived idempotency receipt.** Keep only approved bounded fields and duration. |
| Completion job | Control `PK=USER#<sub>`, `SK=COMPLETION#<requestId>`; acceptance/generation/status/lifecycle data | Analysis reserve/completion and lifecycle | Lifecycle completes/reschedules and later expires it | **Minimal work/idempotency record**, never exportable content. Must finish or be reconciled before final identity deletion. |
| Erasure job | Control `PK=USER#<sub>`, `SK=ERASURE#<operationId>`; deletion stages/continuations/deadline | Mutation/deletion bridge creates; lifecycle resumes/completes | It is the explicit erasure mechanism and produces the ledger `HISTORY` component receipt | **Necessary work record**, then a bounded minimal completion receipt. Do not remove while work can retry. |
| Cursor | Control `PK=CURSOR#<sha256(randomHandle)>`, `SK=CURSOR`; HMAC subject binding and last sort key; 15-minute expiry | History read/export creates and consumes | Explicit control expiration plus TTL | **Erasable short-lived capability state.** A full account export must invalidate subject cursors at/following the deletion fence. |
| Lifecycle checkpoints | Control `PK=LIFECYCLE#<environment>`, `SK=EXPIRATION#...` / `RECONCILIATION#...`; no subject | History lifecycle | Operational, not per-user export/erasure | **Necessary environment control.** Retain independently of a user deletion. |

The History tables have an approved seven-day PITR infrastructure setting in the
source-task handoff, but application approval flags intentionally remain false.
A restored table must be isolated, checkpoints rewound, and all deletion fences,
expiry lanes and replay redactions reconciled before traffic is admitted.

### Campaign participation, consent and deletion ledger

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| Current participation | Users `PK=USER#<sub>`, `SK=CAMPAIGN_PARTICIPATION`; state/version, notice/policy, consent epoch and effective/withdrawal timestamps | `campaign_participation` writes; entitlement, analysis and publisher read/condition-check | Withdrawal transitions to `withdrawn` only after contribution deletion. Full account deletion invokes campaign cleanup but does not minimize/remove current state. | Active consent state is **erasable** after withdrawal/account deletion. A minimal withdrawal proof may remain, but current direct-sub record policy is unapproved. |
| Participation operation | Users `PK=USER#<sub>`, `SK=CAMPAIGN_OPERATION#<operationId>`; action, epoch, resulting state/time | Participation API writes/reads for replay | 400-day `expiresAt`; no explicit full-account cleanup/export | **Minimal idempotency/audit candidate**, but 400-day retention is policy-specific and cannot be silently changed. Approve fields, duration, physical sweep and export treatment. |
| Consent audit | Users `PK=USER#<sub>`, `SK=CAMPAIGN_CONSENT#<epoch>#<time>#<operationId>` and completion variant; notice/policy/state/limit/time | Participation and deletion bridge write | 400-day `expiresAt`; no product-wide export or explicit users-table sweep | Likely **necessary minimal consent audit**. Direct subject linkage and 400-day retention need privacy/legal approval plus backup non-resurrection rules. |
| Campaign withdrawal command | Ledger `PK=ACCOUNT#<sub>`, `SK=CAMPAIGN_WITHDRAWAL#<operationId>`; direct account, epoch, deadline/status | Participation writes; deletion bridge consumes/updates | Remains as protected operation evidence; no TTL in Lambda contract | **Necessary work record then minimal audit.** Retention and final minimization are unresolved. |
| Account deletion command | Ledger `PK=ACCOUNT#<sub>`, `SK=ACCOUNT_DELETION`; operation/status/deadline | `account_data_api` writes; all protected writers fence against it; deletion bridges/reconcilers read | Fixed fence intentionally persists; overall completion/finalizer is absent | **Necessary deletion fence/tombstone.** Exact retained fields and duration must be approved. It must survive long enough to prevent restored/queued work from recreating data. |
| Component/progress receipts | Ledger `PK=ACCOUNT#<sub>`, `SK=ACCOUNT_DELETION#<COMPONENT>` and bounded progress items; request binding, completion time and `retainUntilEpoch` | Account, History and campaign workers | Session, device, device-recovery, History and campaign receipts exist. The analysis-abuse receipt shape exists in source but cannot be emitted until its ordinary-request, legacy-request and consumption policy gates are approved. New receipts use the approved 120-day minimal contract. Progress is removed after completion. | **Necessary work/idempotency evidence.** Ledger TTL remains disabled; controlled retirement is blocked until verified backup/replay coverage and overall finalization exist. |
| Ledger reconciliation checkpoints | `PK=LIFECYCLE#<environment>`, fixed reconciliation sort keys | Account and History reconcilers | Operational, no per-user content | **Necessary environment control**, not user export data. |

The 120-day component-receipt schema is intentionally strict. A pre-existing
receipt without `retainUntilEpoch` is not accepted as complete, and a producer's
conditional create must not overwrite or silently bless it. The safe release
contract is therefore: perform a read-only inventory of exact
`ACCOUNT_DELETION#<COMPONENT>` rows before activating the paired workers; activate
only if no legacy receipts exist, or after a separately approved, audited
migration defines their provenance and retention. No Lambda in this repository
mutates a live legacy receipt in place. Until that compatibility gate is proven,
overall deletion remains incomplete.

The read-only Dev audit verified 11 default-name DynamoDB tables: all six existing
foundation tables and campaign intelligence have 35-day PITR; the History pair has
7-day PITR; outbox/pipeline PITR is disabled. Ledger TTL is disabled and its stream
uses `NEW_IMAGE`; the other ten existing tables use `expiresAt` TTL. The recovery
control table is not yet provisioned. Those facts do not prove purge or approve
fence retirement.
The restore runbook must apply the fixed deletion ledger before any restored table,
stream, outbox or queue can serve traffic.

### Campaign outbox, pipeline, queues and aggregates

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| Observation outbox | Outbox `PK=EVENT#<statisticsEventId>`, `SK=OBSERVATION_READY`; direct `accountId`, consent epoch/notice, sanitized text, bounded features/signals and assessment metadata; maximum 72-hour `expiresAt` | Analysis transaction writes; publisher stream reads and marks delivery state through its dedupe protocol | Publisher rechecks consent before downstream persistence, but account deletion cannot find/delete event-keyed rows; no account index or explicit expiry sweeper is present | **Erasable content. Critical gap:** add an existing-table subject access pattern or approved registry and a bounded outbox cleanup/reconciliation component. TTL/stream expiry is not proof. |
| Pipeline feature | `PK=EVENT#<eventId>`, `SK=FEATURE`; period HMAC contributor token, vector and bounded features; no direct account ID; 21-day transient expiry index | Publisher writes; clusterer reads; deletion bridge queries by contributor GSI | Active/recovery token derivation deletes it plus siblings and writes a tombstone first | **Erasable rotating-pseudonymous contribution.** Deployed backup/DLQ non-resurrection and expiration-sweep evidence is still required. |
| Publisher/cluster dedupe | `EVENT#<eventId>/DEDUPE` and `EVENT#<eventId>/CLUSTERED`; event outcome/state and transient expiry | Publisher/clusterer | Deletion bridge deletes feature siblings; campaign lifecycle explicitly expires indexed transient records | **Minimal transient idempotency records**, but event linkage stays in scope while outbox or feature linkage exists. |
| Candidate contribution | `PK=CANDIDATE#<candidateId>`, `SK=CONTRIB#<contributorToken>` plus contributor GSI; vector/features/count | Clusterer writes; lifecycle finalizer and deletion bridge query/recompute | Deletion bridge deletes matching contributions and recomputes candidate; lifecycle deletes candidates after finalization | **Erasable rotating-pseudonymous contribution.** |
| Contributor tombstone | `PK=CONTRIB#<period>#<token>`, `SK=TOMBSTONE`; time/expiry only | Deletion bridge writes; clusterer checks before writing | Retained through the transient/retry window, then explicit expiration | **Necessary minimal anti-resurrection tombstone.** Do not export as content; retain long enough to defeat delayed queue/DLQ work. |
| Candidate summary / creation control | `CANDIDATE#.../SUMMARY` and `BUCKET#<period>#<taxonomy>/CREATION_CONTROL`; centroids/counts/features, no direct account | Clusterer writes; lifecycle reads/deletes; deletion recomputes | Individual contribution deletion recomputes candidate. Finalization deletes transient candidate state | **Derived transient data** that must be recomputed after erasure. Not a user-facing export record. |
| Period HMAC key registry / KMS key | Pipeline `PK=PERIOD#<periodId>`, `SK=HMAC_KEY`; KMS key ARN/status. KMS holds non-exportable HMAC key | Lifecycle creates/retires; publisher/deletion bridge uses `GenerateMac` | Keys retire after period plus seven-day recovery and are scheduled for deletion | **Necessary pseudonym-control state.** Not per-user export data, but backup/restore must not re-enable retired correlation or accept stale work. |
| Cluster SQS message and DLQ | Exact five-field envelope: schema/record versions, environment, event type and event ID | Publisher sends; clusterer consumes | No account ID/content, but event-linked until related records are gone. Queue/DLQ expiry is asynchronous; delayed delivery is defeated by tombstones/consent checks | **Transient event-linked work.** Approve retention/redrive and prove deletion wins over live, delayed and restored messages. Never log/copy bodies. |
| Campaign aggregate | Intelligence `PK=CAMPAIGN#<candidateId>`, `SK=AGGREGATE`; thresholded counts/bands/dimensions/summary key, no token/vector/content | Lifecycle creates; review updates; trends API reads | Individual erasure recomputes transient candidate before finalization; published aggregate has no source-level user link in this repository | **Non-user-linkable aggregate under current contract.** Do not rewrite/delete it per user without an approved statistical/privacy policy. Current aggregate retention is up to 400 days. |
| Review audit | Intelligence `PK=CAMPAIGN#<candidateId>`, `SK=AUDIT#<auditId>`; reviewer transition metadata, not contributor identity | Review Lambda writes | No user erasure/export relation unless reviewer workforce identity is added by infrastructure | Operational audit outside consumer account export. Workforce-data policy remains separate. |

Infrastructure reports outbox/pipeline PITR disabled and intelligence PITR enabled.
This does not remove the need to reconcile live streams, SQS/DLQ, export copies,
and any restored intelligence snapshot against deletion/retention rules.

### Web-risk cache

| Store / item family | Key and user data | Producers and readers | Erasure/export coverage | Classification and blocker |
|---|---|---|---|---|
| Full URL/domain result | `PK=WEBRISK#FULL_URL#<sha256(normalizedUrl)>` or `WEBRISK#DOMAIN#<sha256(domain)>`, `SK=RESULT`; threat list/confidence/count/times and expiry | `web_risk_communication/cache.py` writes/reads | New writes no longer persist `uri`; cached legacy `uri` is no longer returned. No subject index exists, so historical URL-bearing rows cannot be targeted per user | URL/query values are potentially **erasable sensitive content**, not anonymous merely because the key is hashed. Keep legacy cleanup/migration blocked until the table boundary, retention and physical sweep are approved. |

`WEB_RISK_TABLE_NAME` currently falls back to `TABLE_NAME` and then
`USERS_TABLE_NAME`. Activation must require a dedicated cache table and
least-privilege IAM. Otherwise a cache Lambda can read/write a users table and the
retention/backup boundary is ambiguous. Google Web Risk is also an external
processor; approve its request-data retention before enabling the flow.

### Logs, streams, object storage, secrets and backups

| Surface | Source behavior | Account-data treatment and blocker |
|---|---|---|
| CloudWatch Logs | Lambda handlers generally log bounded stages, result/error codes and counts. The static campaign contract forbids identity/content in logs. Runtime/service exception output can still include provider metadata. | Logs are not queried by the account deletion code. Set explicit retention on every log group, prohibit request/event bodies and identifiers, test representative failures, and document whether account export excludes operational security logs. The unmanaged post-confirmation retention is a known gap. |
| DynamoDB Streams | Users/ledger/outbox records may remain in stream retention and event-source retry/DLQ paths | Treat stream records as temporary copies of the source item. Configure bounded retention/failure destinations and prove deletion/withdrawal fences win on delayed delivery. Do not claim source-row deletion removes prior stream records immediately. |
| S3 | Lambda runtime source has no user-data S3 access. S3 is used by CI only for immutable release ZIPs/manifests under commit-derived prefixes. | Artifact buckets are not an account export/deletion store. Keep runtime roles without S3 access. Any future export object storage is a new data surface requiring separate encryption, expiry, access, deletion and audit approval. |
| Secrets Manager | OpenAI/Web Risk API secrets and the History cursor secret; no user payload intended | Not per-user export/erasure data. Rotate and scope IAM; never log values. Cursor-secret rotation must account for outstanding short-lived cursors. |
| DynamoDB PITR/backups | Foundation and History/intelligence tables may retain historical user-associated records outside the live table | Backup expiry is not live-table deletion. Define maximum restore windows, isolated restore, fence import, deterministic cleanup/reconciliation, queue/DLQ handling and approval before serving restored data. No restore may resurrect a deleted profile, binding, entitlement, URL, analysis response, History item or contribution. |

## Erasable data versus retained minima

Subject to policy approval, the implementation should use these boundaries.

**Erase or irreversibly minimize:** Cognito identity/attributes; profile contact and
age data; device pointer/records; recovery rate/receipt data; active entitlement
service state except approved financial evidence; discoverable purchase-token rows;
usage counters; analysis responses and authorizations; rate/consumption state;
History content and recognition progress; current campaign consent state and all
active/recovery-period contributions; account-linked outbox content; legacy cached
URLs.

**Potentially necessary minimal records:** the fixed account deletion fence;
component completion receipts; bounded purchase evidence required by accounting or
fraud rules; bounded recovery/consent audit; History request/mutation tombstones;
campaign contributor tombstones; operational lifecycle checkpoints. Each retained
record needs an approved purpose, exact allowlisted fields, retention duration,
access role and backup treatment. “Audit” is not permission to retain the original
payload, contact attributes, device detail, raw URL, model response or free text.

**Not per-user erasure targets under the current source contract:** thresholded
campaign aggregates, environment lifecycle checkpoints, release artifacts, and
service secrets. This classification must be revisited if any join key, contributor
token, raw content, reviewer identity or subject mapping is added.

## Missing deletion workers and least-privilege IAM contracts

The overall account-deletion required-component list must not be approved until
these component responsibilities have stable names, exact receipts and workers:

`ANALYSIS_ABUSE` is source-implemented but policy-blocked. Safe response and
authorization minimization, REQUEST/RATE/SCAN_RATE pagination, late-writer fences,
History-first 120-day tombstone compatibility, and an exact receipt contract are
covered. The worker stops before CONSUMPTION and cannot receipt until the owner
approves (1) the exact ordinary analysis-request dedupe duration and handling of
legacy rows longer than it, and (2) deletion or an exact minimal-retention contract
for local scan-consumption evidence. App-store billing alone does not answer the
second question.

1. `ENTITLEMENTS`: drain `USER#<sub>` entitlement/usage rows and discover every
   `TOKEN#.../IDEMPOTENCY` record through an approved existing-table account access
   path. The preferred new-write design is a transactional
   `USER#<sub>/PURCHASE_TOKEN#<hash>` locator alongside the token-keyed anti-replay
   record; it adds no table or GSI and preserves cross-account replay protection.
   Minimize approved purchase evidence and write a receipt. IAM must not allow a
   table scan. Legacy discovery/backfill, locator retention and financial evidence
   retention require approval first.
2. `CAMPAIGN_OUTBOX`: discover direct-account outbox records through an approved
   existing-table access path, delete content, and ensure publisher retries cannot
   recreate pipeline records; write a receipt distinct from pseudonymous campaign
   cleanup if independent completion is needed. IAM must not allow an unbounded
   scan.
3. `USER_PROFILE`: only after all producers are fenced and their component work is
   complete, erase profile identifiers and non-required users-partition state;
   retain only approved consent/deletion minima. IAM: exact `USER#<sub>` partition,
   conditional updates/deletes and the fixed ledger receipt.
4. `IDENTITY`: after every required receipt and export/final-status prerequisite,
   call Cognito `AdminDeleteUser` with an explicit username mapping; treat
   `UserNotFoundException` as idempotent only when the deletion command matches;
   write the final receipt without reopening the account. IAM: only
   `cognito-idp:AdminDeleteUser` and any separately approved read needed for exact
   mapping. Global sign-out remains an earlier independent component.

Each worker must accept only the exact fixed `account.deletion.requested` ledger
record, use bounded durable continuations, be retry-idempotent, reject mismatched
operation IDs/timestamps, report partial stream failures with the source sequence
number, and have a scheduled reconciliation path that outlives DynamoDB Streams.
The overall finalizer must never infer success from elapsed TTL or an empty
event-source batch.

## Protected full-account export contract still required

The existing `GET /v1/users/history/export` is not full-account export. The owner
approved **export before deletion**, but the product export API is not implemented.
Its contract must define:

- verified Cognito access token, exact `sub`, active device binding, no client
  subject targeting, recent signed `auth_time`, and private/no-store responses;
- an immutable `exportOperationId` and snapshot/cutoff time so retries cannot mix
  records created before and after the export began;
- bounded pages and response bytes, with server-side random cursor handles bound by
  HMAC to subject, operation, item-family, position, cutoff and expiry;
- exact family order and allowlisted public fields for profile, device/recovery,
  entitlements/purchase evidence, usage, analysis metadata, History/progress,
  participation/consent, and deletion status; operational secrets, internal hashes,
  model prompts, tombstone internals and other users' aggregates must be excluded;
- idempotent retry of the same page, explicit cursor expiry/error behavior,
  invalidation at the deletion fence, and no cross-environment cursor acceptance;
- accepting deletion atomically changes every unfinished export for the subject to
  `CANCELLED_BY_ACCOUNT_DELETION`, invalidates all of its cursors/download
  capabilities, and prevents retry from recreating export state. Deletion does not
  wait for an unfinished export. A completed export must be downloaded before the
  deletion request or it becomes unavailable;
- a durable `NOT_REQUESTED` / `IN_PROGRESS` / `READY` / `EXPIRED` / `DELETED`
  status model that does not disclose whether another account exists.

After `AdminDeleteUser`, the caller cannot authenticate to read status or download
another page. The approved behavior is no ongoing consumer login, export download
or deletion-status access after identity removal. Before removal, the existing GET
status route is best-effort only. Adding S3 export objects, cursor tables or status
token stores remains a new persistent surface and is not authorized by this
inventory.

## Final identity deletion and completion ordering

The safe proposed ordering is:

1. Atomically fence the profile and create the fixed ledger command.
2. Revoke sessions and reject every writer through both profile and ledger checks.
3. Atomically cancel every unfinished export and invalidate its capabilities; only
   exports completed and downloaded before the deletion request survive at the
   user's endpoint.
4. Complete and validate receipts for devices, recovery control, History/analysis,
   campaign contributions/outbox, entitlements/purchases/usage, and profile
   minimization.
5. Reconcile streams, queue/DLQ work and every backup/restore gate.
6. Perform final Cognito deletion idempotently.
7. Mark the fixed command complete and retain only the approved deletion tombstone
   and component minima for their exact approved durations.

This is the accepted export/deletion dependency order, but overall activation is
not approved. The source currently implements steps 1, session revocation,
device-binding cleanup, recovery-control cleanup/minimization, History cleanup and
campaign contribution cleanup only. It deliberately cannot mark overall completion.

## Evidence required before changing inventory status to approved

- Unit/contract tests for every new worker, exact receipt, continuation and replay.
- Environment-authenticated export pagination/retry/isolation tests without real
  paid model calls.
- Deletion tests across current and legacy item shapes, including token-keyed
  purchase rows and outbox event-keyed rows.
- Deadline evidence based on explicit deletion and reconciliation, not TTL age.
- PITR restore into isolation followed by fence-first cleanup and proof that no
  deleted data is served.
- Stream retry, SQS delay and DLQ redrive tests proving fences/tombstones win.
- CloudWatch retention and content-minimization checks, including post-confirmation
  failure paths.
- Cognito final deletion/retry evidence and the approved post-deletion status/export
  behavior.
- Remaining product/security decisions for purchase-token anti-replay minimization,
  legacy locator/backfill, existing entitlement/usage transactions and any local
  evidence that must survive deletion. No financial retention duration may be
  invented merely because separate local billing retention is unnecessary.

Until all evidence exists, keep `ACCOUNT_DATA_INVENTORY_STATUS=pending` and
`ACCOUNT_DELETION_COMPLETION_STATUS=incomplete`.

## Independently safe source changes made with this inventory

- Web-risk cache writes no longer persist the normalized URL/domain in `uri`, and
  legacy cached values are suppressed on read. Threat scoring uses only the bounded
  threat list, so this removes data without changing the decision contract.
- `post_confirmation` now condition-checks absence of
  `ACCOUNT#<sub>/ACCOUNT_DELETION` in the same transaction that creates a profile.
  A delayed/replayed Cognito trigger therefore cannot recreate a deleted profile.
- `age_attestation` now condition-checks the same fixed fence in the same transaction
  as an update and only permits matching `sub` profiles in `ACTIVE` or
  `PENDING_AGE_GATE`. It can no longer reactivate `DELETION_REQUESTED` state.
- Age-attestation exception logs no longer include `sub` or request IDs.
- `account_data_api` now performs bounded, resumable `DEVICE_RECOVERY` cleanup:
  rate rows are deleted, recovery receipts and 90-day audits are reduced to exact
  minimal allowlists without extending their original expiry, unexpected item
  families fail closed, and a request-bound 120-day component receipt is written.
- Self-recovery now strongly reads the active profile and fixed deletion fence
  before returning an idempotent replay, then repeats those authority checks in the
  write transaction. Pre-issued tokens cannot replay binding details after the
  deletion fence exists.
- `account_data_api` now performs bounded, resumable `ANALYSIS_ABUSE` cleanup over
  the deterministic request, request-rate and scan-rate partitions and can resume
  at scan-consumption. It deletes rate state and strips request responses and
  authorizations while preserving the existing bounded expiry, including valid
  History 120-day tombstones. It stops before scan-consumption and cannot receipt
  while either retention gate is pending. Corresponding conversation-analysis
  writers share the fixed deletion fence transactionally.
- Account deletion component receipts now carry `retainUntilEpoch` at the approved
  120-day boundary. This is retention metadata, not DynamoDB TTL; ledger TTL remains
  disabled and retirement requires backup/replay evidence. Legacy receipts missing
  this field are not overwritten or accepted without the documented pre-activation
  inventory/migration gate.

These changes require coordinated least-privilege ledger configuration/IAM for the
identity and analysis writers plus account-data cleanup before their updated ZIPs
are deployed. They do not authorize deployment or account-deletion activation.
