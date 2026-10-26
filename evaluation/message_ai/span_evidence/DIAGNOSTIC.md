# ATCR-182: one original-request observation

This recorder is a local diagnostic tool, outside the Lambda runtime. It grants
no permission to read a key, spend, deploy, activate, retry, or qualify a model.
Only separately approved execution may use it. Its fixed authority directory
permits one launch, including an interrupted launch. Do not remove that directory
to retry. An uncertain attempt consumes the full reservation.

## Decision

The historical ninth call established `SPAN_ORDER_OR_OVERLAP`, after literal,
unique quote grounding and individual bounds validation. The rejected assistant
text was discarded. No retained evidence distinguishes reversed disjoint spans
from duplicates, containment or partial overlap. It cannot be reconstructed.
There is no demonstrated normalization defect: the adapter derives Python
code-point positions from exact quotes in the unchanged reviewed input.

Sorting uniquely grounded, disjoint quotes could produce a canonical source-order
representation without fabricating or changing their text. However, the current
prompt and validator explicitly require ordered evidence, and regression tests
require rejection of unordered lists. Accepting those lists would change that
intentional input contract. It would not repair overlap or missing semantic
support. Such a change requires an explicit product/security contract proposal
and separate real qualification; it is not a diagnosed fix for this lost response.
Deduplication, overlap merging, substring repair, and ambiguous-match selection
would weaken existing boundaries and are excluded. A restricted quote schema
also cannot establish semantic support merely by making its JSON valid.

The compatible improvement remains the optional finite rejection-detail callback
from commit `5255f02`. It preserves strict rejection and adds no normal runtime
logging. The original prompt is restored; the Spanish prompt hypothesis is not
part of the candidate. The new recorder consumes that diagnostic interface.

## Exact next request

One `dev-019-en` request to the production Responses transport and parser, using
the original serialization with SHA-256
`9048e86d653ceded89669654867f6737a0a2fb8eac1513500db751bd97d1dd36`.
Model `gpt-5.4-mini-2026-03-17`, low reasoning, 512 total output tokens, no tools,
count calls or retry; 8-second provider budget, 18-second supervised attempt,
300-second outer bound including key retrieval. The fixed key version is metadata
only; action-time verification must confirm it before approval. A changed version
requires a revised, reviewed recorder and scope, never a fallback read.

Propose a US$0.3325344 reservation inside the shared US$50 ledger and US$0.35
maximum. The retained estimate is `(400000*750 + 512*4500)*1.10` nanodollars.
It uses the entire published input bound, assumes no caching discount and is not
an invoice. Fresh official price/model-limit and Dev identity/version checks are
required; if that bound is no longer valid, stop and revise the request.

The external packet pins the source commit/tree, source files, original request
bytes and shared ledger. The independently approved scope pins the packet and
recorder hashes, one key read, one generation, authority directory, spending
limits, review approval and fresh prerequisite checks. The current packet is a
proposal: it has no allocation or execution approval. A human-approved scope
must not be manufactured merely by changing template flags.

Offline preflight takes `--plan ABSOLUTE_PLAN --backend-root CLEAN_WORKTREE`.
It validates source and exact request identity only, without importing a secret
client or invoking a provider. Execution additionally requires `--approved-scope
ABSOLUTE_APPROVED_SCOPE`. Execution rejects stale or missing approvals, a missing
shared allocation, source drift, and any existing authority directory before key
access. The ledger is read-only to this tool. A reviewed allocation changes the
packet hash and therefore requires a fresh scope. Run using the already installed
reviewed Python environment with `-B`; do not install dependencies implicitly.

## Retention and interpretation

Before key access, retain a durable reservation, immutable plan and attempt
intent in private local files. The worker suppresses standard streams and core
dumps; the key exists only in worker memory and the provider Authorization header.
Python does not guarantee forensic memory erasure. The parent receives only
finite progress messages. Timeouts kill the worker and never cause a second call.

Retain bounded HTTP status/request ID, validated numeric usage or finite unknown
reason, elapsed time, parser stage, and the first violating adjacent pair's
allowlisted reason code and relation. Do not retain rejected content, quotes,
positions, lengths, hashes of evidence, reasoning, arbitrary exceptions or keys.
Only after strict acceptance retain the normalized assessment. There is no
general production-content logging and no raw response retention even on success.

`NEW_SPAN_VIOLATION` classifies a new failure only. `NON_REPRODUCTION_NOT_QUALIFICATION`
is one non-reproduction, not recovery or acceptance. Missing detail, other
rejection, transport uncertainty or supervision failure is inconclusive. Stop
after each outcome. All 203 original files and the original NO-GO remain intact.
The separate Spanish semantic-evidence failure remains unresolved. ATCR-183
activation and public deployment remain blocked pending positive qualification.

Isolated unit/functional fixtures test these controls; they are not live evidence.

## Finite envelope observations

The local recorder also observes allowlisted provider status and incomplete
reason, plus presence flags for incomplete details, provider error and a typed
refusal in the expected final assistant content structure. Unknown strings or
uninspectable structure produce null; false refusal presence is reported only
when the expected structure has an output_text item. No error/refusal text,
reasoning, arbitrary metadata or rejected evidence is retained. Incomplete
reasons are attributed only to an incomplete status. These observations occur
after the existing secret-echo and usage checks and cannot change strict parsing,
acceptance, budgets, request bytes or the no-retry fence.

For a future observation, incomplete/max_output_tokens directly records the
provider's token-limit reason; incomplete/content_filter records a separate
filter cause. The other documented finite reasons, max_messages and steered,
are retained as their own values. completed with a typed refusal is distinct
from an ordinary completed output_text. Unknown/null values remain inconclusive;
this diagnostic projection does not validate or repair the provider envelope.

The original one-call authority has been consumed. This change grants no new
call, key read, replacement fence or authority directory. A proposed 2048-token
diagnostic remains a separate design/approval question: the production 512-token
profile, settings and cost contracts are unchanged by this recorder-only patch.
