# SEC237 scoped aggregate isolation

This fixes the source boundary demonstrated in PR147 without declaring a selected
actor's data to be environment-wide traffic. It reuses the reducer, revision
planner and conditional store; no discovery runner, endpoint, resource or delivery
path is added. SEC237 remains In Progress.

## Versioned scope and physical isolation

Legacy summary/revision candidate1 remains exactly closed and uses its existing
`OPS_AGGREGATE#environment#date` key. A mandatory scoped candidate2 adds only closed source provenance
(which does not itself prove nonpersonal status): fixed receipt-capture contract, population, random UUIDv4
source-definition generation and explicit coverage. The generation belongs to
reviewed trusted source configuration, never a request, owner ID/hash, cursor,
run/retry token or automatic error recovery. This format is not authentication or
proof that the configured population was completely discovered.

`environment_customer` says environment completeness remains unqualified.
`selected_dev` says `selected_subset_not_environment_total`, and is rejected
outside Dev. Existing producer/local-intake/provider-price coverage remains
unknown/uncollected as before. A caller cannot add arbitrary tags, identity,
claimed complete coverage or silently extend candidate1 with this metadata.

A scoped store captures its descriptor at construction. It selects only
`OPS_AGGREGATE_V2#environment#population#generation#date`. Summary scope must match
exactly before any read/write; readback must match the stored key scope even if a
record was validly rehashed. No legacy head is read, adopted or upgraded. Revision
planning rejects any version/scope/generation change in a prior chain, even with
increasing counts. Existing atomic append/head CAS, deterministic request token,
response-unknown handling, same-day defer and deliberate fresh reconciliation
remain intact. All correction and90-day expiry fences are unchanged.

Different qualified environment source generations cannot replace each other's
head. This is namespace isolation, not proof that an actor swap used a new
generation. Trusted configuration must keep the population definition stable
across restarts and require a newly reviewed generation when it changes. No code
mints a current-run namespace, makes arbitrary caller snapshots authentic or
claims complete environment totals from a selected subset.

## Selected-account persistence remains refused

A random identifier tied in configuration/audit to one account can still be a
pseudonym. Removing owner IDs and hashes from the aggregate does not establish
unlinkability if a selected-account-to-generation join remains available beyond
its approved seven-day linkage window. A frozen Python object also cannot prove
historical selection stability across restarts. Therefore `AggregateStore` always
refuses **both read and commit** for `selected_dev` with `SCOPE_UNQUALIFIED` before
I/O. There is no boolean override. The selected-report schema can be validated as
a noncommitting preview only; it is not a new approved retained data category.

The original same-generation actor-swap counterexample remains a valid warning:
asserted provenance alone cannot prevent a privileged misconfigured source from
lying. It is not claimed fixed by UUID randomness. The prospective selected
runner cannot commit through either legacy or scoped environment stores without
violating its required source scope; no such runner was implemented or enabled.

The owner's conditional instruction requires stopping any part that changes
product report meaning or needs new retention. To persist selected-account
aggregates, the exact unresolved decision is whether they remain linkable (and
must be retired under the existing at-most-seven-day bound), or whether a design
can prove that no actor-to-generation link survives while retaining only truly
unlinkable90-day aggregates. A longer selected-actor join is not approved here.
The conservative recommendation is to keep selected previews noncommitting and
preserve the approved environment-wide, unlinkable report meaning. No new report
policy is silently selected and no approval is inferred from these source tests.

## Validation and next execution gate

Tests exercise two disjoint scoped heads plus legacy isolation; scope/legacy
mismatch before storage; forced selected refusal; captured descriptor aliases;
closed versioning and wrong coverage; rehashed cross-scope rows; competing CAS;
unknown-write reconciliation without retry; fixed retention and correction.
Independent source/adversarial review and the companion JSON pin exact results.
An initial broad run hit two historical assertions requiring these versioned
files to remain byte/AST identical to an older release. The reporting-only
branches now execute that exact pinned original package and compare legacy
report, first revision, replay and error outputs across six valid/hostile cases.
All other auth, billing, transaction and publication preservation fences remain
unchanged. The independent legacy probe also pins its original Git commit rather
than moving HEAD, so it remains executable after publication.

The operations library remains outside the Lambda packaging registry, so no
unused distribution artifact or runtime handler is created for this source fix.

Bounded aggregation execution remains gated on a genuine trusted source scope
that is both stable and nonpersonal. A selected scan must not be relabeled as an
environment source to pass this gate. Once qualified, reuse existing minimal
receipt discovery, atomic ingestion, reducer and one store commit; keep incomplete
pages, response-unknown capture/commit and missing attempt finish explicit. No
blind evaluator retry, reconstructed finish, restored deleted linkage or invented
vendor units/cost is permitted. The completion checklist remains the finite
story boundary; selected execution, true provider facts, rejection capture,
delete/expiry rollup preservation, scheduling and actual Dev acceptance are open.

No AWS/auth/app endpoint/provider/device/financial call, table/index/IAM/runtime
change, telemetry/retention expansion, outbound delivery or main promotion occurs.
SEC198 policy remains separately held. Frozen Android readiness is unchanged.
