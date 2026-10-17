# SEC237 immutable aggregate revision planning

This continues the source-only [operations kernel](SECUR4ALL-237-OPERATIONS-KERNEL.md).
`src/operations_reporting/revisions.py` validates identifier-free summaries and
plans immutable daily revisions and numeric corrections. It has no persistence,
scheduler, service client, message send or runtime handler. SEC237 and SEC243
remain open; their trusted source, storage, deletion and actual delivery work is
not replaced by this planner.

## Strict aggregate boundary

Before a summary can become a revision, the implementation rejects extra keys,
unknown dimensions, invalid numbers and fabricated producer/delivery coverage.
It verifies the accepted/pending/settled equation, each service denominator,
access-class accounting, complete-only deduction, cost/unit coverage, rates and
compatibility of the aggregate reason totals with the event-schema outcome
families. No free-form notes, customer identifiers, check/attempt references,
provider response IDs or recipient copies are permitted in a revision.

The original source kernel always marks producer coverage `not_qualified`,
local intake `not_collected` and delivery false. Those values are retained and
cannot be relabeled complete by this planner. Its strict schema is an internal
source contract, not an authenticated producer or new public endpoint. A valid
report can still come from an incomplete or fabricated caller-supplied snapshot;
the trusted authority/ingestion boundary remains mandatory before activation.

Both event and revision validation now serialize an input once and validate
the exact captured bytes subsequently used. A caller mutation between schema
validation and copying cannot substitute a different payload. Optional finite
reason maps have a canonical sparse representation; adding a zero key cannot
manufacture a correction.

## Immutable correction rules

`plan_revision(previous, summary, generated_at_epoch=...)` returns a finite
decision, the proposed or unchanged record, and `expectedPreviousSha256`:

- `CREATE_REVISION` plans revision1 or an increment with the previous record's
  hash and exact nonzero numeric deltas. The report stays in its original
  Chicago cohort. The first revision's deltas must equal the report's counts;
  they cannot be omitted, negative or arbitrarily rewritten after rehashing.
- `NO_CHANGE` returns the original record unchanged when aggregate content is
  the same, including when only the snapshot's as-of time advanced. Replay
  never renews generated time, expiry or revision.
- `DEFER_TO_NEXT_DAILY` returns the prior record unchanged for further changes
  in the same Chicago local calendar day. A later run can coalesce those changes
  into one next-daily revision. This planner does not store the pending
  correction or run the approved 08:00 schedule.

Fixed errors reject stale snapshots, cross-environment/cohort records,
inconsistent prior records, expired windows and regression of already counted
immutable facts. Pending, awaiting-finish, unknown coverage and reconciliation
gap counts may decrease as facts resolve. Accepted, settled, known-cost and
other immutable totals cannot silently decrease when a scan becomes partial or
linkable rows disappear. The future store must preserve prior aggregate totals
and apply authorized deltas before deletion rather than reconstructing erased
links or presenting a smaller count as a correction.

Each proposed record has a SHA-256 of its canonical JSON report and of the
canonical record payload excluding its own `recordSha256` field, plus the prior
record hash. These are **unkeyed content integrity and conditional
write values**, not signatures, proof of authenticity or proof that the earlier
chain exists. A caller with write authority can recompute them. Real IAM and a
trusted conditional store are still required. In particular, two concurrent
planners may propose different records for the same next revision; both return
the same prior-hash precondition. Only the future atomic store can accept one.
No durable concurrency or duplicate-free email claim follows from these tests.

## Retention and correction limits

The aggregate expiry is fixed at original cohort-start plus 90 days in seconds;
it is never based on replay, correction or delivery time. No linkable fields
are copied into that aggregate. The seven-day correction boundary is
conservatively measured from the original cohort start, matching the kernel's
refusal to recalculate after linkage may have expired. A later snapshot cannot
recreate a revision after that boundary. This does not physically delete data,
enforce backup retention, attest account deletion or discard an existing
unlinkable aggregate early. Those remain separate tested storage obligations.

The current planner deliberately requires a closed local reporting day. It
does not treat partial-day input as a completed daily report, invent a known
zero, calculate alert state or emit a notification. Cloud resource cost and
execution/storage limits require their existing review before activation.

## Local validation and independent findings

```sh
PYTHONPATH=src python -B -m pytest -q tests/operations_reporting
```

The revision cases cover unchanged replay, late settlement and attempt cost
enrichment, same-day coalescing, explicit conditional-write limitations, partial
scan/deletion regression, fixed retention, hostile nested fields, stale/cohort
mismatch, corrupt prior records and captured-byte mutation races. The existing
kernel suite remains in that command. Exact results and source hashes are in
the validation and independent-review companion records.

Independent source review found three real pre-publication validation gaps:
an impossible access-class complete count, terminal-reason totals incompatible
with outcomes, and an impossible rehashed first-revision delta. The fixes and
independently authored counterexamples are retained. These were source-only
findings, not incidents observed in any deployed runtime.

Next implementation remains trusted receipt/reference/attempt producers,
atomic deletion/expiry-fenced ingestion, durable immutable correction storage,
the accepted alert/delivery state machines and cost coverage. SEC243 owns
actual scheduling/storage/IAM/retention and report/recovery delivery evidence;
SEC180 retains later assembled release qualification. No service call, new
credential, permission, infrastructure, mobile telemetry, purchase, runtime
activation or Production promotion occurred in this slice.
