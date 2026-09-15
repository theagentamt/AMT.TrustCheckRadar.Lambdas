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

The owner also confirmed that billing is app-store authoritative with no separate
local scan-consumption retention requirement. The `ANALYSIS_ABUSE` component now
drains the deterministic REQUEST, RATE, SCAN_RATE and CONSUMPTION partitions in
strongly consistent pages of 100. It deletes rate/consumption rows and expired
requests, reduces unexpired requests to the exact content-free dedupe allowlist
without extending their approved 24-hour expiry, persists operation-bound family
progress, and writes the exact component receipt only after all four families.
Every analysis write that can create or restore those records now shares the
active-profile/deletion-ledger fence in the same DynamoDB transaction.

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

Current local result: **293 passed, 129 subtests passed**. Deterministic package
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
