# SEC237 terminal consumer facts and remaining source scope

This source increment supplies existing finite terminal facts when the optional
operations producer is enabled in the URL, message and recovery consumers. It
changes no endpoint, authentication, entitlement, accounting, provider retry,
resource, permission, reporting schema or deployment setting. The producer stays
default off. SEC237 remains In Progress.

## Why this increment precedes new reporting or service-status infrastructure

The retained SEC237/SEC178 requirements include more than deployment: durable
pre-dispatch attempt facts, logical-check/attempt reconciliation, known versus
unknown provider costs, daily aggregation execution and actual report delivery.
The existing source has receipt-local lifecycle capture, a bounded discovery
adapter, selective retirement, reducer and revision/store kernels. Those kernels
and inspection archives do not establish a running publisher, aggregation job,
notification sender or complete provider-attempt ledger. Reporting is therefore
not blocked only by deployment. This increment closes one concrete source gap
without adding another ingestion or control layer.

Existing actual consumers settled partial/failed/unavailable work without passing
an operations reason; those records could remain accepted-only reporting facts
even when business settlement was known. The new hook supplies `COVERAGE_PARTIAL`
for validated partial settlement and `SETTLED_FAILED` for failed settlement.
These describe observed settlement, not a provider root cause. An unavailable
result supplies `PROVIDER_UNAVAILABLE` or `PROVIDER_RESPONSE_INVALID` only when
one such closed result code is present and that consumer entered its existing
provider-call boundary. This is a local classification fence, not a durable
attempt record, external vendor attribution or measured provider cost.

A local budget exception is especially important: the existing public fallback
uses `PROVIDER_UNAVAILABLE` even before dispatch. Reporting now preserves unknown
cause on that path while leaving that public response and zero charge unchanged.
A normal `BUDGET_LIMIT`, mixed limitations, unknown codes, missing helper or
classifier failure also remain unknown. No precedence, elapsed-time cause,
provider authorization failure or timeout subtype is invented. URL freshness
failure maps only to generic failed settlement. Message freshness fallback
recomputes options from its final validated presentation.

## Containment and existing authority

All three consumer boundaries validate the helper's outcome-specific code, and
unavailable codes must equal the exact singleton result code. A wrong but otherwise
allowlisted helper result cannot make `Authority.settle` reject a valid business
settlement. Missing imports, exceptions and invalid values omit optional metadata.
Default off skips the import/helper and passes precisely the pre-existing
`result_summary` keyword. No new service read or transaction is introduced here.

Only the existing authority settlement transaction can commit the reason with
actual accounting. Existing replay returns the committed row; it cannot replace
that reason, charge again or redispatch the provider. The seven-day operations
deadline, deletion/expiry fences, lease/paid closure and response validation are
unchanged. No content, owner identifier, operation proof, provider exception text
or model output is copied into operations facts or logs.

## Validation and artifact limits

The companion JSON pins exact source, both local/independent test files, local
commands/results, independent source review and three source-inspection archives.
Actual local Moto tests cover all three consumers, partial/failure settlement,
replay/accounting, duplicate-safe daily aggregation and summary validation,
provider/budget/mixed causes, optional-helper failures, malformed recognized
reasons, and default-off legacy keyword parity. Independent review found the
budget-exception and wrong-listed-helper cases before integration; both are
fixed and retained as regressions. The initial local aggregation fixture used a
pre-day-end `asOf` for a final daily summary; it was corrected to the existing
post-day-end contract, with no validation weakening.

The three current archives use the unchanged build script, Python3.14/ARM64 and
`--skip-dependencies`; they are source-inspection artifacts, not deployable or
native-runtime-qualified candidates. Their changed source members are checked
byte-for-byte. The earlier 15-package dependency manifest remains an immutable
historical record. Any later coordinated build must incorporate this delta in
all relevant shared-authority carriers, not silently deploy the earlier manifest
with an enabled producer. No deployment version, IAM permission, route or rollback
configuration is selected by this record.

## Remaining SEC237 work and real gates

| Remaining work | Classification |
| --- | --- |
| Durable pre-dispatch provider/internal attempt facts, uncertain dispatch/finish and retry reconciliation | Source implementation and component acceptance still needed; no inferred attempts from these codes |
| Known/unknown provider units and cost join, paid/complimentary totals across all real producers | Source completeness and approved existing cost-bound qualification; no new dollar budget |
| Discovery/cursor invocation into aggregation/revision execution, late correction/deletion/expiry and crash recovery across the assembled path | Existing components require assembled source execution and acceptance |
| Approved report/alert delivery, retry/reminder/cap priority enforcement and verified recipient | Delivery/SEC243 source and actual acceptance; no message sent by this increment |
| Dependency-complete ARM64 artifacts, coordinated aliases/rollback, invocation/IAM/table/index permissions and mixed-version/backup retention | Separate deployment qualification and activation gates |
| Real selected-actor Dev evidence, timely sweep/correction/delivery and later release/physical acceptance | Unperformed runtime/release evidence; SEC243, SEC180 and ATCR148 remain distinct |

SEC178 policy 1.0.0-policy.1 remains the approved baseline: America/Chicago
08:00 prior-day report, seven-day linkable reconciliation, fourteen-day fixed
logs, ninety-day unlinkable revisions, existing failure/stuck/budget alert
thresholds and delivery caps. This batch neither changes those decisions nor
waives SEC237 acceptance. There were no AWS calls, application endpoints,
credential flows, runtime/permission changes, devices or financial actions in
this source batch. The separately captured Android Phase A readiness files are
frozen and are not refreshed by this work.

## SEC198 disposition

SEC198 remains Draft/To Do. Its retained requirement allows either safely public
or authenticated trusted configuration and requires approved cache/failure,
EN/ES and rollback behavior before Ready for Development. A targeted search of
current Lambda contracts/source, retained infrastructure source and Android
source found no minimum-version/emergency-configuration implementation; generic
Home service-state rendering is not that contract. See the separate exact
proposal and decision gates in `SECUR4ALL-198-CONTRACT-PROPOSAL.md`. No runtime
endpoint, authorization mode, minimum build, TTL or security policy is invented.
