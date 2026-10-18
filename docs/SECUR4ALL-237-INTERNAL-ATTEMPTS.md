# SEC237 receipt-local internal attempt source component

This adds start/finish recording methods to the existing `ReceiptIngestion`
transactional adapter. It does not wire a consumer to call them, enable a
producer, dispatch an evaluator/provider, or establish cost coverage. SEC237
remains In Progress. The approved SEC178 retention policy supplies the existing
seven-day linkable deadline; no new retention or storage policy is needed for
facts kept inside that already governed receipt projection.

## Smallest source increment and exact data

The closed internal projection format v2 contains the existing accepted/terminal
check events plus at most one internal evaluator start and matching finish.
Legacy v1 snapshots keep their exact closed validation; a v1 extension is not
silently accepted. There are at most four events, one attempt reference, fixed
ordinal 1 and a component derived from the actual receipt family:
`url_assessment`, `message_evaluator` or `recovery_evaluator`. No caller chooses
an owner, component, provider, price, extra dimension or ordinal.

Internal attempts are not vendor calls. `providerFamily`, `providerRequestUnits`
and `estimatedCostMicrousd` are always null, not zero. A validated partial or
unavailable evaluator result can still represent an internally successful
invocation. An internal failed finish records only `SERVICE_UNAVAILABLE`;
provider root causes and billable units cannot be inferred. No price catalogue
or dollar budget is created. The existing reducer keeps these attempts outside
provider-cost denominators and still says coverage is not qualified.

A random start reference/event ID is minted only for a missing attempt on the
actual admitted receipt. Finish identity is stable from the opaque random
attempt reference, with collisions against lifecycle IDs refused. No content,
account, device, check proof, execution token or provider text is copied into the
projection or a new locator. Coarse duration buckets are the existing contract's
internal envelope bins; the caller must eventually measure them from its
monotonic invocation boundary. They are not measured vendor latency or cost.

## Authority, replay and retention

Start requires the existing server-created worker context, valid owned receipt
proof, exact current 32-hex execution token, ADMITTED state and an unexpired
settlement lease. Finish requires that same receipt/token and persisted start;
it can enrich an already SETTLED check without rewriting settlement, charge or
its original event. Finish without start, wrong owner/token/reference,
conflicting outcome/duration bucket or a second attempt is rejected. Start replay
returns the existing reference; identical coarse finish replay returns its exact
original event, never a new time or deadline. A recorded acknowledgement is **not
a dispatch permit** and must never trigger automatic evaluator redispatch.

The adapter reuses existing exact-source/projection CAS, verified key inventory,
non-DELETING access, active/age-verified profile and deletion-absence guards.
Absent prior projection is compared for absence. Competing first start, concurrent
settlement/capture, changed execution owner, deletion/erasure or inventory drift
cannot overwrite newer facts. The adapter adds no application-level retry of a
response-unknown write; a deliberate later revalidation may find the committed reference but does not
prove any component was actually invoked. No provider/network call occurs here.

Source time must remain within the receipt's original deadline. Captured attempts
cannot be later than the current observed time; start cannot follow a known
settlement. A delayed acknowledgement past the admitted lease is not returned as
success. These are application dispatch/readback guards, not a server wall-clock
condition or a hard SDK completion guarantee. A transaction begun before the
boundary may have committed even when its response is rejected/unknown; the
stored start then remains awaiting finish. Existing optional `can_continue`
guards are preserved, not replaced with a new execution SLA.

Projection validation captures one deep copy before inspecting fields and returns
only that validated snapshot. It cannot append unvalidated identity/content at a
late copy. It rejects future/rollback times, malformed tokens and derived-ID
collisions with finite errors. Unknown result or missing finish stays unknown;
it does not become zero cost, provider failure, or a second logical check.

All attempt facts reside in `operationsProjection`, which the existing selective
eraser and account-partition deletion already own. No extra field, table, index,
subject locator, backup, retention grace or deletion obligation is added. Expiry
removes the whole projection without changing full receipt/History TTL or
accounting. Expired orphan v2 projections are recognized for erasure; a later
finish never remints removed metadata. Actual local tests exercise deletion and
paid-shortening/closure as well as normal lifecycle expiry.

## Validation and coordinated artifacts

The companion evidence JSON pins exact source, tests, independent findings/fixes,
full gate and archive closure. Four retained independent failures were fixed:
carried attempt timestamps after a clock rollback; derived finish ID colliding
with a lifecycle ID; mutable input changed at a late copy; non-ASCII stored
execution token escaping the finite error boundary. Existing response/accounting
and legacy tests remain intact. An initial recovery test omitted its required
result summary and was corrected to the existing contract, not by relaxing it.

`SECUR4ALL-237-ATTEMPT-DEPENDENCIES.json` is a new inspection manifest. The old
manifest remains immutable historical evidence. The checker now requires the
attempt module in every relevant shared-authority carrier and rejects a missing
module even when a ZIP digest was recomputed. Fifteen dependency-skipped source
archives join thirteen authority carriers plus two existing evaluator contract
dependencies. Host imports with sockets blocked qualify Python module closure
only. These are not dependency-complete ARM64 candidates or deployed versions.

Later deployment must coordinate all relevant validators/cleanup/paid/deletion
carriers. Old v1-only workers may refuse a v2 projection, especially an orphan;
rollback must use a compatible reader/eraser or qualify physical retirement of
those records first. Neither an old manifest nor a source PASS authorizes a
rollback or producer activation. Native imports, IAM/invocation/table/index
permissions, mixed workers/backup retention, cost and actual Dev behavior remain
separate gates.

## Still unfinished source and acceptance

Consumer start/finish wiring, its actual time reservations and source-derived
monotonic duration, uncertain dispatch/crash/finish reconciliation and further
attempts are unimplemented. The new adapter records a bounded internal envelope;
it cannot prove real pre-dispatch coverage for currently deployed services.
Provider HTTP boundaries, actual units and a reviewed rate catalogue remain
needed for provider-cost capture; unknown is mandatory until then. No new policy
question blocks storing these approved internal fields under the existing
seven-day boundary, but no permanent access/security/retention rule is inferred.

Assembled discovery/aggregation/corrections and report/alert delivery source,
SEC243 real Dev validation, SEC180 release and ATCR148 physical scope remain open.
There were no AWS reads, endpoint/auth/provider/device/financial actions,
permissions/resources/runtime changes or main promotion in this source batch.
Frozen Android readiness was not refreshed. The separate SEC198 owner-ready
choices are proposed only; they do not block this approved reporting component
or select an API access/cache/enforcement policy.
