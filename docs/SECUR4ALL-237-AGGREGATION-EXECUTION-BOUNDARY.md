# SEC237 aggregation execution boundary

The next source audit found reducer, revision planner and conditional aggregate
store implementations already present. None should be duplicated. Existing
receipt ingestion is deletion/profile/key-inventory/exact-source CAS fenced;
internal attempt dispatch wiring is integrated through PR146. There is no daily
runtime handler, trusted discovery runner or reporting scheduler to invoke yet.

## Concrete integration blocker before implementation

`AggregateStore` keys by environment and Chicago local date. The proposed bounded
Dev source scans one selected actor. Those are different population boundaries.
Replacing one selected actor with another can produce larger valid counts and
pass every existing monotonic revision check. The store cannot distinguish those
sources from a legitimate correction because the approved aggregate deliberately
contains no owner/check identifiers. `producerCoverage=not_qualified` and a count-
regression guard do not establish a stable source cohort.

The retained offline Moto counterexample uses the actual unchanged reducer,
planner and store: a disjoint synthetic cohort with one completed check commits;
a later, different cohort with two completed checks commits as revision2 under
that same environment/date head. Monotonic counts, hash chain and fixed90-day
expiry all pass. This is not a defect in the existing environment-wide store
contract or an observed deployed incident. It disproves the proposed selected-
actor caller's unqualified use of that contract. Initial probe fixture mistakes
(requesting a nonexistent URL-complete pair, then using an event-ID prefix as a
logical reference) were corrected to the existing closed schemas, not relaxed.

No production implementation or aggregate schema/storage change was made.
Selected discovery/reduction can remain noncommitting, but must not be presented
as a completed daily execution path. The commit boundary needs a fixed, trusted
cohort and an exclusive matching store namespace whose selection is stable across
runs. Incompatible existing heads must be refused. Do not place an owner ID or
owner hash in90-day aggregates to solve this; that creates retained linkage.

## Smallest next design decision

Qualify a nonpersonal, finite namespace and trusted source-scope provenance for
this existing one-actor Dev test, isolated from the existing environment-wide
store. Explicitly establish how changes of selected actor or source configuration
cannot reuse its head. This is an integration correctness prerequisite, not a
request for new telemetry, new retention, recipient, rate/dollar budget, broader
actor access, deployment or activation. No durable scope provenance has been
qualified in this batch. Any solution requiring new retained linkage or a new
privacy/storage scope needs approval before implementation. A full-environment
source is an alternative future design, not permission to enumerate other users.

Once that boundary is qualified, the bounded runner should reuse, in order:

1. Existing verified retained-key inventory and server-created worker context;
   strong minimal `PK/SK/checkId` queries over existing `CHECK#` rows. No payloads,
   table scan, new index, subject locator or persisted cursor.
2. A global bound across keys/pages/rows, strict acknowledged pagination and
   forward progress. Discover all bounded keys before capture. Incomplete or
   uncertain discovery aborts aggregation, never commits a partial snapshot.
3. Existing `ReceiptIngestion.capture` for every owned proof. Nonexpired read,
   CAS or acknowledgement uncertainty aborts the run; missing finish remains
   unknown. Capture/retirement writes may already be partial before a later
   failure, so an aborted report is not a source-transaction rollback.
4. Final finite monotonic/as-of/receipt-expiry/inventory/profile fences, then
   existing `aggregate_day` and one `AggregateStore.commit`. Keep non-atomic scan
   and selected-source coverage unqualified. Existing correction/expiry and
   immutable-total guards remain intact.
5. Explicit caller reconciliation of conditional conflicts or response-unknown
   writes by a fresh source/read cycle. No catch/recompute write loop, evaluator
   redispatch or fabricated finish. Existing same-day defer is not a stored
   correction queue; next-daily scheduling and crash evidence remain acceptance.

The source audit and independent design review stop the unsafe commit proposal
before code. The existing offline operations suites qualify their current
contracts only. They do not prove an executed selected-source runner, source
completeness, deployed store isolation, provider costs or daily delivery.

See [the completion checklist](SECUR4ALL-237-COMPLETION-CHECKLIST.md) for all four
story criteria and the finite remaining implementation/Dev gates. SEC237 remains
In Progress. SEC198 policy remains independently held pending the owner answer.
No AWS/auth/endpoint/provider/device/financial action, resource/IAM/runtime change,
outbound delivery or main promotion occurred. Frozen Android readiness is intact.
