# SECUR4ALL-207 Lambda Evidence

Status: **Lambda component work advanced; story completion blocked**

The complete Lambda-source account-data inventory and the remaining deletion,
export, identity-finalization, retention, IAM, queue and backup gaps are recorded
in `docs/account-data-inventory.md`. Inventory status remains `pending`; the
document does not approve any retention or activation decision.

Owner decisions dated 2026-09-14 now approve the Dev device-recovery split of
90-day minimal audit, seven-day replay receipt, 24-hour rate state and seven-day
PITR when provisioned, plus 120-day minimal account-deletion receipts. The
`DEVICE_RECOVERY` cleanup component implements bounded deletion/minimization and
late-writer fencing in source. Overall deletion remains gated by every other
inventory component and verified backup/replay coverage.

Billing is app-store authoritative, but that does not establish whether local scan
consumption has an independent quota/dedup/security retention purpose. The
`ANALYSIS_ABUSE` component processes deterministic REQUEST, RATE and SCAN_RATE
partitions in strongly consistent pages of 100, persists operation-bound progress,
then stops before CONSUMPTION while its policy is pending. It removes request
content and authorization/event metadata without extending or silently shortening
the existing expiry. History-first 120-day `COMPLETED_ERASED` tombstones are
accepted and minimized, and History cleanup after analysis cleanup writes the same
exact content-free shape with expiry anchored to the deletion request so retries
cannot extend retention. Every analysis write that can create or restore those
records now shares the active-profile/deletion-ledger fence in the same DynamoDB
transaction. No analysis-abuse component receipt can be issued while any ordinary
request-dedupe, legacy-request-retention, or scan-consumption policy gate is
pending.

Retention evidence and recommendation:

- The pre-change conversation-analysis source default for
  `REQUEST_ID_TTL_SECONDS` is 900 seconds. Current Dev infrastructure inputs do
  not set that variable. The older 86400-second deployment example was not an
  owner approval and has been removed as a default claim.
- History lifecycle uses its separate `HISTORY_DEDUP_RETENTION_DAYS` contract
  (Dev/default 120 days) and can legitimately create a longer-lived
  `COMPLETED_ERASED` analysis replay tombstone. This is not the 120-day account
  component-receipt retention contract even though the number is the same.
- No live analysis rows were read, so the presence or provenance of legacy
  ordinary request rows with longer expiry is unknown.
- Recommendation: keep the ordinary writer default at 900 seconds and do not
  expand it until product/security approves an exact need. Content-minimize any
  valid longer-lived History tombstone without shortening its approved expiry.
  For any other longer-lived legacy row, fail closed pending provenance rather
  than shorten, delete, or bless it automatically.
- Required decisions: approve the ordinary request-dedupe duration and legacy
  longer-expiry treatment; separately approve whether local scan-consumption is
  deleted or minimized, with exact retained fields, purpose, duration, access,
  and backup/restore treatment.

Previously implemented campaign lifecycle and withdrawal evidence remains:

- Current-period `HMAC_256` key creation with the required project,
  environment, purpose, and period tags, plus the DynamoDB period-key registry
  consumed by the publisher and deletion bridge.
- Key disablement after the 14-day period plus seven-day recovery window and
  seven-day scheduled deletion.
- Threshold finalization from a fresh contribution query. Cohorts below 10 are
  suppressed and qualifying output excludes contributor tokens and centroids.
- Consent-withdrawal/account-deletion token derivation is restricted to active
  and recovery periods.
- Tombstone-before-delete, targeted `ContributorPeriodIndex` deletion,
  feature/contribution/event-dedupe deletion, and centroid/count recomputation
  from surviving contributions.
- Cluster-side tombstone checks prevent queued work from resurrecting a deleted
  contribution.
- Exact pending campaign-withdrawal validation, successful-deletion-only
  transition to `withdrawn`, a privacy-safe 400-day completion receipt, atomic
  ledger completion, and completed-stream loop suppression.
- Sparse environment-bound expiration keys and an hourly index-query-only
  explicit expiration operation; DynamoDB TTL remains defense in depth.
- Content-free lifecycle/deletion logs and the privacy-safe operational runbook
  in `docs/campaign-lifecycle-runbook.md`.

Implemented Lambda evidence:

- Campaign consent withdrawal uses an idempotent UUIDv4 command, atomically
  transitions the current consent epoch to withdrawal-pending, adjusts quota,
  and writes the deletion-ledger command. The deletion bridge removes active
  contributions/features/dedupe siblings, recomputes affected candidates, and
  completes the matching withdrawal state and receipt.
- `account_data_api` contains disabled `POST`/`GET` account-deletion request and
  status handlers. Identity is only the strict Cognito access-token `sub`; POST
  additionally requires signed reauthentication no older than 300 seconds.
- The producer atomically changes the authoritative profile to
  `DELETION_REQUESTED` and writes the fixed account-deletion fence. Every device,
  History, and analysis writer checks that fence, including transaction-time
  checks for device registration/recovery.
- History and campaign deletion workers accept only the exact environment-bound
  command and emit component receipts bound to its operation ID and request
  timestamp.
- Session revocation occurs only after the durable fence. DynamoDB stream
  failures use sequence numbers for partial-batch retry, while configuration and
  setup failures propagate for whole-batch retry.
- Bounded scheduled reconciliation extends session-revocation retry beyond
  DynamoDB Streams retention and publishes full-pass/truncation metrics.
- Device bindings are deleted in strongly consistent pages of 100. Progress is
  resumable and the completion receipt is written only after the final page.
  Transactional fence checks prevent a device writer from recreating bindings.
- All component/status responses are private/no-store and logs/metrics use only
  bounded operation/environment dimensions.
- Strict legacy-receipt compatibility is documented: receipts without
  `retainUntilEpoch` remain pending and are never overwritten. Paired activation
  requires a read-only inventory proving they are absent or a separately approved
  audited migration.

Automated evidence:

- `tests/account_data_api`
- `tests/campaign_deletion_bridge`
- `tests/history_account_deletion_bridge`
- `tests/history_lifecycle`
- `tests/device_registration` and `tests/device_recovery`
- `tests/shared_history` and analysis/history race coverage

Current local result: **296 passed, 129 subtests passed**. Deterministic package
builds and checksum verification also pass.

The story must not be marked complete or activated yet. Remaining prerequisites
are the approved complete account-data inventory, deletion/final receipts for
all additional inventory components, final Cognito identity deletion, an
overall finalizer, deletion-fence retention policy, infrastructure wiring and
observability acceptance, authenticated Dev validation, and measured erasure
SLA evidence. `ACCOUNT_DELETION_ENABLED=false`, policy/inventory `pending`,
completion `incomplete`, and `COGNITO_USERNAME_IS_SUB=false` are deliberate
independent gates.

Full-account export is also not implemented. Paginated JSON is the preferred
direction, but implementation remains blocked until the inventory and protected
delivery contract are complete.
