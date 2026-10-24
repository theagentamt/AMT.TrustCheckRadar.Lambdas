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
results using the same private wrapper; the wrapper never enters candidate.3
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
qualification packet. The stopped prior batch and its nine unrun cases provide
no execution authority. Any new paid qualification requires a new approval;
successful qualification still does not itself authorize deployment or activation.
