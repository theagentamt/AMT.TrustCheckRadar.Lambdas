# SEC237 internal attempt consumer wiring

URL, message and recovery submit consumers now connect the existing receipt-local
start/finish adapter to their real internal evaluator invocation and result
validation. This is source implementation under the existing optional producer
flag, which remains off. It neither activates reporting nor establishes vendor
call, cost, report delivery or full SEC237 acceptance. SEC237 stays In Progress.

## Observed boundary and data

Only a newly admitted submit can start one fixed-ordinal internal attempt. Message
and recovery first obtain their existing business budget reservation; a refused
or throwing reservation creates no attempt. Prepare, pending replay and reconcile
never create an invocation ticket. A start acknowledgement is not permission to
execute: the existing admission remains the authority, and the consumer separately
recomputes its original evaluator deadline after optional work.

An opaque request-local ticket binds the exact authority instance, authenticated
owner, owned receipt proof, execution token, server-derived family/client/version,
random persisted attempt reference and a private request-scope object. A locked
identity claim permits one invocation-entry stamp, one measurement and one finish
dispatch. A copied,
modified, stale or cross-request ticket cannot finish another operation. The owner, proof, execution token, authority instance, request scope and private
timing-ticket identity stay in memory; they are absent from provider requests,
response DTOs, the persisted projection and logs. The opaque attempt reference
and approved finite event context remain in the original governed projection.

Completion measures only an entered internal invoke plus strict result validation
using the existing injected monotonic clock. A valid unavailable, inconclusive or
partial evaluator response can be internally successful while the logical check
remains uncharged or incomplete. An entered invocation exception or invalid
result has generic internal failure. The boundary does not prove a vendor HTTP
request, successful analysis, current paid access or a billable provider unit.
`providerFamily`, `providerRequestUnits` and `estimatedCostMicrousd` remain null.
No new client telemetry, field, locator, retention policy or table is introduced.

The existing authenticated capture path revalidates current owner, binding,
receipt/profile/deletion/key inventory and projection CAS before recording finish.
It enriches the already governed projection without changing settlement or
allowance accounting. Known-invalid finish source falls back once to the old
lifecycle-only capture. A response-unknown write is not retried. Unknown start
acknowledgement supplies no ticket; independently authorized business processing
can continue once, leaving a persisted start without a claimed finish. Reconcile
never repeats the evaluator or reconstructs a private timing ticket.

## Timing, failure and default-off behavior

The optional start uses observed spare time above the original provider and
settlement reservation, a one-second pre-dispatch cap, the existing single SDK
attempt and bounded connect/read configuration. These checks cannot impose a
hard synchronous SDK completion limit. After optional work the consumer reduces,
and never increases, the original provider budget. If there is too little time
for an otherwise required evaluator, the existing budget-limited settlement is
used. A stored start then remains awaiting finish; no unentered invocation is
reported as failed. Rules-only message mode retains its existing business budget reservation bypass.
When optional reporting is enabled, it also refuses entry after the observed
deadline is exhausted. The default-off legacy path is unchanged.

Enabled optional work consumes real time and storage capacity. It can therefore
reduce evaluator time or prevent entry near a deadline; enabled business latency
or availability parity is not claimed. Actual cost/latency/capacity qualification
is an activation prerequisite. With the producer disabled, the new wrappers do
not call the optional helpers, add clock/remaining-budget observations or alter
the legacy evaluator budget. Existing default-off response/accounting tests and
explicit clock-count assertions cover that invariant.

## Validation and remaining work

The companion JSON and independent review pin source, local cases and archive
closure. Local integration uses actual three-consumer admission, dispatch,
settlement, projection and replay with synthetic accounts and Moto storage. It
covers start-before-dispatch, success/failure, unavailable versus internal success,
budget refusal, late start, unknown start/finish acknowledgement, copied tickets,
request scope and one-time consumption. The first regression run found a misplaced
message completion call inside response rendering; it was removed, preserving
measurement solely at the evaluator boundary. Independent review also caught
post-start request preparation inside the duration and an enabled rules-only
late-budget bypass. Entry is now stamped immediately before invocation, and the
enabled rules-only path refuses late dispatch; dedicated regressions retain both
counterexamples.

The separate consumer dependency manifest supersedes no historical manifest.
All relevant authority carriers need the compatible projection reader/eraser and
caller modules before any later producer activation; v1-only workers can refuse
v2 records. Source ZIPs built without third-party dependencies and socket-blocked
host imports establish source closure only, not native ARM64 deployment readiness.
No build workflow, requirements, public contract or runtime configuration changes.

Still unfinished are uncertain dispatch/crash and missing-finish recovery, further
attempts, actual provider HTTP/units/rate evidence, assembled discovery and daily
aggregation/correction wiring, and report/alert delivery. Unknown coverage and cost
remain unknown. Coordinated artifacts/rollback, actual IAM/storage/retention/cost
and selected Dev end-to-end evidence remain open under existing follow-ups.

SEC198 policy is held pending the owner's answer to the parent's availability
choice. This reporting increment selects no public access, stale-cache, upgrade,
OS or enforcement policy. There were no AWS/auth/endpoint/provider/device or
financial calls, deployment, activation, new outbound delivery or main promotion.
Frozen Android readiness was not refreshed.
