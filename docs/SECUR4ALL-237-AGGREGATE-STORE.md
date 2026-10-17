# SEC237 conditional aggregate revision storage

This continues the [revision planner](SECUR4ALL-237-AGGREGATE-REVISIONS.md) with
`src/operations_reporting/store.py`, an injected-client DynamoDB adapter for
identifier-free aggregates. It creates no resources and has no handler, trusted
producer, ingestion endpoint, scheduler, notification or delivery capability.
SEC237 and SEC243 stay open. No real AWS/auth/session/API or runtime action is
part of this source increment.

## Durable transition and replay

The environment is fixed at construction. A caller summary passes the existing
strict schema and accounting/denominator validation before any storage read.
Customer/check/attempt references, recipients, content, arbitrary tags and free
text cannot enter this boundary. Producer coverage remains `not_qualified` and
delivery remains false; schema validity and an unkeyed content hash do not make
caller facts authentic or complete. Trusted authority producers and their
separate expiry/deletion-fenced ingestion remain required.

Each finite `(environment, Chicago cohort date)` partition has one head and
immutable numbered revision rows. Reads use a strongly consistent head followed
by its immutable revision. A read may observe the head immediately before a
concurrent commit; it is not an atomic multi-row latest-snapshot query. The
returned revision must match the head's hash, revision, cohort and fixed expiry.
Missing, expired, corrupt and temporarily unavailable states stay distinct.
None means an observed zero-volume report.

A new revision and its head update share one `TransactWriteItems` call. The
revision row must not exist, and the head must still have the exact old hash,
revision and expiry (or be absent for revision1). Two distinct proposals against
one head cannot both append. A failed condition leaves neither a partial
revision nor an advanced head. Old revisions are never overwritten. No-change
replay writes nothing and renews nothing. Same-day coalescing remains an explicit
`DEFER_TO_NEXT_DAILY` result; this adapter does not yet persist the pending
correction or run the next day.

The request token derives only from the table, finite cohort key and revision
content hash. AWS documents atomic transactions and a ten-minute idempotency
window for identical calls with one token in the
[TransactWriteItems API](https://docs.aws.amazon.com/amazondynamodb/latest/APIReference/API_TransactWriteItems.html).
That short provider window is not the durable deduplication mechanism. The
conditional rows preserve immutability beyond it. Identical calls may receive
more than one acknowledgement for one durable write; the competing-writer test
uses distinct valid candidates sharing one prior head. This local emulator
evidence does not qualify real AWS concurrency behavior.

## Failure and retention boundaries

The injected SDK client must have `total_max_attempts=1`. There is one write call
and no catch/recompute/retry loop. Only an exclusively conditional transaction
failure becomes `CONDITIONAL_CONFLICT`. Mixed, malformed, throttling or transport
write failure becomes `WRITE_OUTCOME_UNKNOWN`; raw error messages and cancellation
bodies are not surfaced or logged. A timeout after the service committed is
therefore unknown until a deliberate supported read reconciles the stored head.
No report or email is marked delivered from a transaction acknowledgement.

Every head and revision has expiry fixed at original cohort start plus90days,
matching the approved unlinkable aggregate retention. Exact expiry hides rows
through the adapter even if DynamoDB has not physically removed them. A delayed
read rechecks the trusted clock before returning a present record. Replay and
correction do not extend expiry. Writes are refused at the conservative seven-
day cohort correction boundary, including a slow read that crosses it before
dispatch; a backward clock also fails closed. These are trusted-client logical
fences, not a server-side clock guarantee during a delayed in-flight request.
Physical TTL/cleanup latency, restore/backup exclusion, deployed IAM/table
isolation, clock configuration and execution/storage cost limits still require
review and actual qualification before activation. No such storage is created
or approved here.

Unlike the future linkable operations receipt/attempt store, these rows contain
only unlinkable aggregates and finite revision metadata. This is not evidence
that account deletion or seven-day linkable expiry has been implemented. It
must not be reused to store customer references or to inherit governed History's
separate settlement-based retention policy.

## Validation and remaining work

Actual local Moto transaction cases cover initial append, immutable next-daily
correction, concurrent distinct writers, orphan-row all-or-nothing conflict,
response lost after commit, no-blind-retry, malformed/hostile storage responses,
fixed environment, no sensitive extensions, delayed reads, clock regression,
logical expiry and unchanged replay. Companion JSON and independent-review
records retain exact source/test hashes and results. The broader reducer and
revision suites remain passing. The operations library stays outside the Lambda
package registry; no distribution ZIP is produced for it.

Independent pre-publication review reproduced one finite-error gap: an injected
stored JSON string with an unpaired Unicode surrogate raised a Python encoding
exception before validation. The bounded encoding step now maps it to
`INVALID_STORED_RECORD`; the independent regression passes. This is a source
finding, not an observed corrupt deployed table or runtime incident.

Still required: trusted authority receipt/reference and pre-dispatch attempt
producers, atomic linkable ingestion with deletion/restore fences, bounded
pending correction storage, actual cost coverage, alert and delivery state
machines, scheduler/resources/IAM/physical retention/cost review, and real Dev
report/alarm/recovery/delivery-failure evidence. The approved recipient remains
support@andmorethings.com, but no sender or send operation exists here. Later
assembled release qualification remains SEC180; physical-only follow-ups stay
ATCR148. Local storage tests and merged source do not replace those criteria.
