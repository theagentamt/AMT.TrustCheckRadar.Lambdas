# SECUR4ALL-226 Lambda Evidence

Status: **safe disabled implementation complete; story activation pending**

Implemented Lambda scope:

- Idempotent delete-one, clear-History, and reset-progress mutation receipts.
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
- Bounded resumable erasure stages explicitly remove content-bearing cached and
  `RESULT_READY` analysis responses. Tests do not simulate DynamoDB TTL cleanup.
- Lifecycle metrics include overdue erasure jobs for the 24-hour SLA alarm.

Automated evidence is in `tests/history_mutation_api`,
`tests/history_lifecycle`, `tests/history_read_api`, and the analysis generation
race tests.

Restore remains fail-closed: before restored data can be served, both expiration
checkpoint families must be rewound to the earliest restored expiry and sweeps
must complete. Application writes outside the configured reconciliation window
are forbidden.

The story must remain open until account deletion/consent lifecycle integration,
backup erasure replay/PITR policy, final mutation schemas, deployed alarms, and
authenticated Dev 24-hour deletion evidence are complete.
