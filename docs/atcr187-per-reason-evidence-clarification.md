# ATCR-187 per-reason evidence clarification candidate

This is one unvalidated prompt hypothesis arising from newly retained semantic
evidence. It is not a diagnosed fix for the original lost span-ordering failure,
and it does not reset either exhausted Astra attempt. Source publication and any
real-provider evaluation require independent review and their own approved scope.

The 2026-10-26 dev-019-en diagnostic completed with valid literal grounding, but
its AI_CONSEQUENTIAL_URGENCY evidence was only “today or your account will be
frozen”. Its own evidence omitted “authorize the transfer”, failing the unchanged
URGENCY_ACTION annotation. The payment reason contained that action; another
reason cannot supply missing evidence. Separately, the retained PR166
bank-gift-es PRETEXT quote omitted the conditional consequential-action
connection, failing PRETEXT_CONSEQUENTIAL_CONNECTION. Both requirements already
exist in the category instructions. The original ninth response's discarded
quote pair remains unrecoverable and cannot be inferred from these observations.

The candidate adds only this general instruction, without fixture-specific words:

> For each reason, its own quotes must together support every required element of
> its category. Evidence attached to another reason does not supply a missing
> element. The same source excerpt may support different reasons.

The input text, category definitions, schema, literal grounding, within-reason
ordering/overlap rejection, frozen rubric and qualification registry are
unchanged. Quote reuse across different reasons remains allowed. The prompt hash
changes, so evidence bound to the previous prompt cannot authorize this revision.
Production remains at its existing 512-token/eight-second limits; the separate
2,048-token/20-second diagnostic profile is not included here.

The six new offline regressions preserve both retained normalized observations
and their missing per-reason anchors, verify unchanged Unicode input projection
and production settings, preserve cross-reason quote reuse and within-reason
duplicate rejection, and reject qualification evidence bound to the old prompt.
Together with existing exact-evidence, reasoning-profile and code-purpose tests,
156 tests passed. One initial collection error from missing source-path setup was
corrected; both logs are retained in the owner's local review evidence. These
checks do not execute a model or establish compliance, semantic truth, reliability
or end-to-end acceptance. The strict adapter still checks structural grounding,
not general semantic completeness, so the historical gaps remain failures.

The smallest proposed live step is one separately approved dev-019-en call using
this prompt and the previously reviewed diagnostic 2,048-token/20-second profile,
with its 30-second supervisor, 300-second outer bound, unused one-call fence and
zero retries. Only the prompt would change from the last completed request. First
assemble and independently review an exact combined diagnostic source/request
binding; neither this candidate nor the consumed prior plan authorizes execution.
At the last verified rates/bounds, a fresh US$0.3401376 reservation would fit under
US$0.35, but fresh pricing, model limits, shared-budget capacity, allocation and
scoped key-use approval are still prerequisites. No reservation or credential
access was performed for this candidate.

Assess that one response against all unchanged dev-019-en criteria, including its
own action and time evidence. Stop after the first result or uncertain delivery.
A passing observation would not qualify the model or resolve the original
ordering failure; Spanish and broader qualification remain untested. Do not
automatically append more calls. Original NO-GO, ATCR-183 activation/release gates,
public-deployment prohibition and historical evidence remain unchanged.
