# SEC237 permanent operations facts and reduction

This bounded source increment implements normal application components. It does
not activate an operations store, schedule, report recipient, provider, or mobile
journey. No fixed tester, synthetic runtime exception, temporary permission
compiler, or separate test endpoint is part of the application path.

## Source behavior

`shared_operations.contract` validates the existing proposed event and rejection
schemas without extra fields, integral floats, booleans masquerading as counters,
unknown enums, arbitrary tags, or oversized facts. Rejected bodies are neither
logged nor quarantined. The source schemas are exact copies of the retained
SEC178 proposal; no mobile request or result envelope changes.

With `OPERATIONS_ENABLED` absent/false, existing authority behavior is unchanged.
True requires an explicit distinct operations table and store generation. The
destination cannot be any users, devices, deletion, or authority source table.
Every transaction checks its environment, generation, qualified state, fixed
retention and backup declaration. That metadata is a guard, not proof that the
real storage/backup configuration has been qualified. Infrastructure readiness
remains false until independent deployment readback and lifecycle evidence exist.

Authority admission atomically creates the winning receipt, a minimized `OPS#`
companion in the existing account partition, and one random-reference acceptance
fact in the separate operations table. Settlement atomically publishes the exact
original outcome/accounting fact; a lost acknowledgement reconciles the winning
receipt. History reopening, repeated admission and settlement do not create a new
logical check or charge. Existing receipts without the operations marker keep
their original path; no backfill invents references or historical facts for them.

The companion is the sole ownership lookup. It stores random fact/check/attempt
references, fixed enums and the original acceptance/deadline, never submitted
content. Operations child rows contain only closed projections and random refs;
they contain no subject, account partition, source proof, fingerprint, message,
URL, provider body, or explanation. The original authority mapping is not copied
into a second identity index. Linkable operational references are not anonymous.

The deadline is acceptance plus 604800 seconds. History's separate seven days from
settlement cannot extend it. Reads/writes recheck current authority/account,
deletion, generation, source execution ownership, companion revision/state and
deadline. Missing, changed, expired or restored source metadata fails closed.

## Dispatch ownership and real adapter hooks

The three consumers journal evaluator invocations as `internal_component`.
They never label a Lambda invocation as an OpenAI/Google request. Optional trusted
observer hooks claim planned execution before credential/DNS/TLS preparation,
after input/configuration/budget validation. Their conditional start must win
before preparation/HTTP; an exact existing
start is not a second dispatch permit. A lost start acknowledgement cannot yield
a permit or an automatic retry. Deadlines are checked again after the journal.

Finishes bind the exact claimed start, finish ID, original source and revision.
A response/finish failure cannot repeat the provider call. Missing finish remains
unresolved with unknown cost/units. Qualified response usage remains a bounded
numeric estimate, rounded up from nano-USD to micro-USD; it is not an invoice.
HTTP failure categories remain fixed. A valid HTTP response proves one request;
connection/dispatch uncertainty does not prove zero. Failures proven to precede
HTTP dispatch have zero provider units/cost, independently of AWS costs. Failed finish persistence
emits only a fixed counter event and leaves reconciliation unknown.

Provider observers are **not constructed by evaluator/Google runtime adapters in
this increment**. SEC243 must supply the reviewed server-only execution-context
wiring and least-privilege storage/condition-check permissions; IAM alone does
not supply that missing source integration. No client/model field may select an
account, proof, table, generation, observer, provider, or spend limit. Provider
telemetry coverage defaults to unavailable, independently of lifecycle coverage.

Embedded Google attempts use `url_check/message_url` with the original message's
random logical reference. They do not create an independently accepted URL check.
Each start/finish pair has immutable component/provider/ordinal/start identity.
The existing maximum ten attempt slots fails closed rather than truncating facts.

## Erasure and reconciliation

The companion deliberately has no TTL attribute: TTL must not drop the only
lookup while children remain. Explicit cleanup first atomically closes it to
`DRAINING`, preventing all stale OPEN writers. It drains bounded child pages,
strongly proves empty, then removes the companion and marks the existing receipt
erasure-complete in one fenced transaction. It never recreates a missing receipt.
No transaction multiplies twenty authority rows by all of their child facts.

Account deletion drains children before removing the corresponding receipt.
Source-receipt expiry proves its complete existing settled/policy/expiry/index
conditions before closing the companion. A missing lookup is not cleanup proof
for an operations-marked receipt: only a positively recorded erasure marker or a
legacy receipt allows source removal. Partial drains resume without reopening.

`Expiry.sweep_operations` is a separately callable bounded indexed sweep. The
existing deployed maintenance invocation does not call or activate it here.
Poison/stale index entries increment fixed failures and advance the checkpoint.
The ordinary source-expiry and deletion paths refuse to drop unqualified lookups
when no operations cleanup adapter is supplied. Generation changes therefore
need an explicit drain/compatibility plan; they cannot silently reset old work.

## Daily reducer and limits

The reducer is deterministic and bounded to 10000 input facts/snapshots. Exact
replays count once; conflicting identities/lifecycles are excluded and make
coverage incomplete. Out-of-order facts reconcile by their original identities,
not arrival order. Impossible times cannot certify a complete source.

Accepted cohorts remain on their original Chicago calendar day, including 23/25
hour DST days. Late terminal facts correct that cohort. Provider cost belongs to
the paired start day. Pending accounting/cost is unknown, not zero. Original
complimentary and noncomplete settlements deduct zero; the reducer never grants,
deducts, refunds or changes the ledger. Ratios expose numerator/denominator and
show N/A for zero received. Reports contain no check, attempt or customer refs.

The result explicitly separates lifecycle completeness, provider telemetry
coverage, and the closed reporting period. Missing provider coverage cannot
produce sourceComplete=true or make zero observed calls a completeness claim.
Local intake coverage remains unavailable; no mobile telemetry is invented.

This is not the complete SEC237/243 operational service. Remaining work includes
trusted provider-observer construction, rejection-ingress publication, bounded
source enumeration/backfill, durable aggregate revisions, service-level incident
classification and bounded scheduled delivery. Real seven-day erasure/restore and
backup enforcement, account export inventory integration, numeric resource/cost
limits and actual notification evidence remain activation gates. Do not flip
daily_reporting_ready based on library tests or a store metadata flag.

Prior model qualification remains unaccepted. This increment changes executable
source identity, so only mutable source-current evaluation pins are refreshed;
historical evaluation packets/approvals and immutable application contracts stay
unchanged. No provider request or secret-value read is authorized by these edits.

## Source validation

The frozen source passes all 57 isolated suites: 4,338 reported cases (4,059
regular cases plus 279 subtest entries), zero failures/errors/skips. The final
718 affected tests also pass independently; those overlap the aggregate and are
not additional cases. Compilation, shell checks, installed dependency consistency
and whitespace checks pass. Two independent exact-file reviews report source-only
PASS. Existing model qualification remains unaccepted.

The operations module was also checked in the existing official AWS Python
3.14.8 ARM64 image with the existing pinned Linux jsonschema 4.25.1 dependency:
24 event fixtures, one rejection snapshot and both DST boundaries pass. The
container had no network or credentials. This is module/dependency evidence, not
complete Lambda, provider, delivery or actual erasure acceptance. See
`evidence/sec237-operations-source-validation-20261024.json` for exact evidence.
