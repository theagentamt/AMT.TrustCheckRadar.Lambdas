# Provider output consistency

The permanent Message AI adapter requests a closed `result` object with three
disjoint schema variants: `warning` / `clear` / 1–5 reasons; `no_warning` /
`clear` / no reasons; and `abstain` / non-clear context / no reasons. This removes
the former independent-enum combinations that Structured Outputs could accept
but the application parser would reject. The prompt states the same constraint
without directing a model to invent context or force a warning.

The provider-private wrapper uses nested `anyOf` inside a root object, following
the [supported Structured Outputs subset](https://developers.openai.com/api/docs/guides/structured-outputs).
It does not use root `anyOf`, `if`/`then`/`else`, `allOf` or a new endpoint.

The parser requires exactly the wrapper and existing assessment fields, then
retains every independent context, reason, uniqueness and Unicode-span check.
It returns the original flat assessment to policy. Policy revalidates callback
results using the shared normalized validator; the wrapper never enters candidate.3
outcomes, receipts, reconciliation, original accounting or History records.

Two content-free rejection stages distinguish abstention with clear context
from a non-abstaining assessment with non-clear context. Only finite codes are
logged; no enum values, input, rejected text, credentials or raw error bodies
are added to logs. The historical `CONTEXT_CONSISTENCY` observation remains
unchanged evidence and does not identify the lost exact pair.

Both prompt and provider-schema hashes change. Existing historical profile
identities and paid-run evidence are preserved and cannot qualify this format.
Model qualification and experiment registries remain empty; activation gates
remain default-off. No model request, secret read, runtime configuration, IAM,
customer allowance or deployed service changes are part of this source batch.
The mutable sanitizer evaluation-source metadata and its checksum are refreshed
for these exact unqualified bytes; immutable historical identity records and
public message/History wire schemas are preserved.

Validation covers all 30 assessment/context/reason-presence combinations,
reason/span bounds, Unicode code points, missing/extra/legacy provider roots,
finite diagnostics, historical hash rejection, policy projection, existing
usage-before-parser checks, transport failures and original receipt/History
regressions. These are offline source checks, not evidence of provider quality
or acceptance of the new schema by a live API.

The exact new source, prompt, schema and request bodies need a fresh reviewed
qualification packet. Unrun cases in prior stopped batches provide no renewed
execution authority. Any new paid qualification requires a new approval;
successful qualification still does not itself authorize deployment or activation.

## Exact evidence grounding revision

The private provider reasons now require one to three exact `quotes` instead of
numeric spans. The adapter accepts only unique literal matches in the unchanged
reviewed text and derives Python Unicode code-point offsets itself. It rejects
missing or repeated matches (including overlapping repeats), empty or oversized
quotes, out-of-order or overlapping evidence, and placeholder-only evidence,
including fragments inside a placeholder. It does not trim, normalize, reorder,
round, clamp or repair evidence. Ordinary literal substrings remain supported;
exact matching does not prove that a reason is semantically correct.

The normalized callback boundary snapshots JSON data before validation and returns
a detached closed projection. Its strict reason, span, context and content checks
are shared with provider grounding. Public outcomes, receipt/accounting and History
schemas are unchanged; neither excerpts nor offsets are added to public output or
diagnostic logs. Usage is validated and reported before assessment parsing.

Granular finite diagnostics distinguish evidence type/bounds, missing/ambiguous
matches, span object/type/bounds/order and content failures without reporting their
values. The earlier paid batch's `SPAN_SHAPE` evidence remains unchanged: its lost
exact offsets cannot establish a UTF-16, emoji or other particular cause. This
revision removes model-generated offset arithmetic without making that claim.
Prompt/schema/profile hashes change again; all prior qualification evidence is
stale for these bytes. Registries remain empty and runtime activation remains off.
Historical normalized fixtures are retained, while isolated provider fixtures use
the new private format. No new provider request is part of this correction.

## Code-purpose clarification

The permanent prompt distinguishes codes by their stated function. Gift-card
redemption numbers/PINs carry prepaid value and alone do not establish the
account-authentication category. A separate request for account passwords or
authentication codes still supports `AI_CREDENTIAL_REQUEST`, including alongside
payment pressure. Ambiguous purpose is not invented; a decisive missing purpose
requires abstention. The closed taxonomy, parser and public contracts are unchanged.

Paired EN/ES engineering contrasts cover gift-card-only, authentication-only,
mixed requests, preventive advice and ambiguous code purpose. They verify actual
request construction, strict decoding and reference-output policy projection;
they do not establish that a model will follow the clarified prompt. Structurally
valid but semantically incorrect reasons are not silently removed by a new filter.

The prior PR163 paid batch remains nine of ten against its unchanged frozen
semantic criteria: the Spanish gift-card warning included an unlisted credential
reason. Its results and evidence are preserved. This new prompt changes request
and profile hashes and requires a fresh reviewed qualification packet and new
paid/secret-use approval. No qualification acceptance or runtime activation follows
from the offline contrasts.
