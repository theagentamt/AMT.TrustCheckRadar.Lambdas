# SEC237 completion checklist

This checklist is the story boundary, not a growing sequence of independent Done
claims. SEC237 remains In Progress until its required Dev acceptance is performed.
Source integration, deployment, Dev behavior and later release/physical testing
remain distinct. SEC243 delivery evidence and SEC180 release do not replace
unfinished SEC237 implementation or acceptance.

| Required outcome | Existing implementation | Remaining acceptance/work |
| --- | --- | --- |
| One accepted logical check; attempts separate | Authority admission/settlement, receipt projection v1/v2, reducer deduplication, internal consumer start/entry/finish | Actual provider boundaries/units/rates; durable crash/dispatch ambiguity remains unknown, never retry automatically; further attempt facts only with genuine new invocation evidence |
| Correct outcome, rejection and local-intake denominators | Finite allowlisted settlement reason hooks with explicit unknown-cause coverage, reducer closed event/rejection schemas, internal-success vs logical-result separation | Trusted rejection-counter ingestion; local intake remains not collected without newly approved telemetry (do not invent a zero or add collection) |
| Chicago day, pending, late completion/corrections | DST-aware reducer, 7-day correction fence, immutable daily planner, conditional aggregate store | Qualify stable cohort/exclusive-store provenance, then assemble bounded source discovery and execution; same-day changes currently defer and are reread on next daily run; scheduler and repeated real Dev runs remain unperformed |
| Privacy-safe output and cost/complimentary totals | Strict content-free event/summary/revision validation, accounting classes, null unknown provider units/cost, existing selective erasure/account deletion | Source-to-aggregate preservation across deletion/expiry without restored linkage; actual IAM/source selection/backup/restore/physical deletion/cost qualification; audit the assembled source-to-revision path and verify no live content/identifiers enter reports |
| Durable reporting recovery | Conditional immutable revisions and finite write-unknown/conflict states | Bounded caller reconciliation with no blind write or provider retry; actual competing/crashed workers and retention evidence before activation |
| Approved report and alert policy | SEC178 version1.0.0-policy.1: Chicago08:00/prior day; seven-day linkage,14-day diagnostics,90-day unlinkable revisions; approved recipient/caps/thresholds | Execution/scheduler/resources and notification state machines; actual destination verification and SEC243 Dev delivery, failures, caps and recovery. No outbound delivery is enabled in this source batch |

The next bounded source increment first resolves the stable cohort and exclusive
store provenance blocker in [the execution boundary](SECUR4ALL-237-AGGREGATION-EXECUTION-BOUNDARY.md). Only then may it connect existing discovery inputs,
ReceiptIngestion, reducer and AggregateStore. It must refuse incomplete bounded
runs and retain unqualified producer coverage. It must not call missing internal
finish a failure, infer vendor billing from an internal Lambda invocation, or
restore deleted links to improve report coverage.

There is no new owner policy decision needed for these already approved internal
report facts. Activation/deployment and actual delivery remain separately gated.
Any need for new linkable storage, retention, collection, recipient, rate/dollar
budget or broader actor scope must be surfaced before implementation. SEC198 is
independent and remains held pending the parent's owner policy answer.

Scoped source follow-up: [versioned aggregate isolation](SECUR4ALL-237-SCOPED-AGGREGATES.md) separates candidate2 namespaces, binds revisions to exact scope
and refuses selected-Dev storage before I/O. This does not qualify a selected
account-to-generation join as unlinkable or authorize its90-day retention.
Selected execution remains noncommitting until that precise privacy/provenance
boundary is resolved; no subset may be relabeled an environment total.
