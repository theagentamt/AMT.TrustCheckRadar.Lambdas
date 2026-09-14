# SECUR4ALL-226 Lambda Evidence

Status: **safe disabled implementation complete; story activation pending**

Implemented Lambda scope:

- Idempotent delete-one, clear-History, and reset-progress mutation receipts.
- Secure idempotent bootstrap for new and existing eligible accounts, with
  atomic foundation-profile and deletion-fence conditions.
- History-only account-data deletion is explicitly separate from full product
  account deletion.
- Delete-one atomically removes content, tombstones its durable locator, and
  redacts the short analysis replay record.
- Clear advances only `historyGeneration` and creates a durable erasure job.
- Reset advances only `recognitionGeneration`, records the server acceptance
  cutoff, and initializes zero progress for the new generation.
- Reads exclude old generations immediately.
- A scheduled lifecycle Lambda discovers work through `ExpirationIndex` and
  `PendingLifecycleIndex`, physically removes content, and content-free
  tombstones locators without a scan, stream, or queue.
- Every sweep revisits all current-hour shards, while durable monotonic
  per-table checkpoints drain closed-hour backlogs and rolling reconciliation
  checkpoints revisit recent closed hours. This prevents later-in-hour due
  records and delayed GSI visibility from being permanently skipped; a backlog
  bucket advances only after it is drained.
- Pending completion observations advance `lifecycleAt`, preventing stuck
  completions from starving later erasure jobs in the same shard.
- Active request locators are durable retention work records indexed at the
  content expiry but retained under the longer deduplication policy. Replay
  redaction failure leaves the marker retryable after the History GSI entry is
  gone, and the same marker still purges replay if native TTL removed History
  before lifecycle observed it.
- Write, mutation, and lifecycle validation require the locator's whole-day
  deduplication retention to cover the 90-day content deadline plus the 24-hour
  cleanup allowance. Longer outage/restore coverage remains a separate policy
  gate.
- Bounded resumable erasure stages explicitly remove content-bearing cached and
  `RESULT_READY` analysis responses. Regression tests explicitly simulate both
  replay update failure after content deletion and History-first native TTL.
- Lifecycle metrics include overdue erasure jobs for the 24-hour SLA alarm.
- `history_account_deletion_bridge` consumes only the exact authoritative fixed
  deletion-fence record, atomically fences History, and creates a resumable job
  spanning all captured generations. Lifecycle completion writes only the
  History component receipt and never completes the overall account deletion.
- The same bridge performs a bounded, strongly consistent reconciliation scan
  with a durable continuation checkpoint, recovering commands missed beyond
  DynamoDB Streams retention.
- Reads, mutations, analysis entry, acceptance, and completion check the same
  authoritative deletion fence, covering pre-issued tokens and delayed/missing
  stream delivery.

Automated evidence is in `tests/history_mutation_api`,
`tests/history_lifecycle`, `tests/history_read_api`,
`tests/history_account_deletion_bridge`, `tests/shared_history`, and the
analysis generation race tests. The local full suite result for this candidate
is 245 passed with 126 subtests passed.

Restore remains fail-closed: before restored data can be served, both expiration
checkpoint families must be rewound to the earliest restored expiry and sweeps
must complete. Application writes outside the configured reconciliation window
are forbidden.

The story must remain open until infrastructure wiring is accepted, the Dev
bridge/lifecycle path is exercised, deployed alarms are verified, and the
24-hour deletion evidence is recorded. No Lambda in this repository currently
writes the authoritative fixed account-deletion fence, so the owning
`SECUR4ALL-200` producer remains a genuine external prerequisite.
