# ATCR-182 diagnostic-only candidate

Source base: `09152b691701ef3294f20cd6fb206efcd6bd6778` (PR166).
Tracking: [ATCR-182](https://andmorethings.youtrack.cloud/issue/ATCR-182), within
[ATCR-180](https://andmorethings.youtrack.cloud/issue/ATCR-180). Parent owns any
specific unresolved follow-up under [ATCR-186](https://andmorethings.youtrack.cloud/issue/ATCR-186).

The bounded Astra extra-high review found no provable application contract or
normalization defect. The ninth response's precise violation is unrecoverable from
the retained evidence. A future instrumented real response is required to observe
its own failure class; it cannot reconstruct the discarded historical response.

The retained PR166 batch stays NO-GO: nine real calls, eight strict-valid
assessments, seven evidence passes, one PRETEXT evidence-composition failure, one
SPAN_ORDER_OR_OVERLAP rejection, and five unrun cases. All 203 retained evidence
files in the two original evidence directories remain unchanged.

The original request explicitly requires literal, unique quotes in source order,
without overlap within a reason. The adapter derives positions with `text.find`
in the same unchanged sanitized text, rejects missing/repeated matches, and passes
those spans directly to validation. Valid integer bounds precede the ordering
predicate. For these bounds, `previous_end > start` is exactly the rejection
condition for violating `previous_end <= start`. The initial zero end cannot
reject the first nonnegative start; each reason resets its own predecessor.
Cross-reason reuse of a passage remains allowed. No model-supplied offset or
UTF-16/code-point conversion participates in this path.

Therefore the finite rejection stage records an actual structural violation under
the selected contract, rather than proving a validator defect. The body, reason,
quote pair and exact relation are missing. No assertion about emoji, the ninth
semantic classification, duplication versus reversal, or the model's internal
cause is justified. A 206-token completed response reached the span validator;
there is no retained evidence of the 512-token cap or timeout causing this failure.

The retained Spanish PRETEXT quote covers the account-closure claim but omits its
conditional consequential-action connection. The original prompt already requires
that connection. Other reasons' quotes cannot fill the missing evidence under the
frozen scoring policy. This is a separate observed model evidence failure. The
parser checks structure/grounding, not general semantic truth; no approved lexical
or deterministic correction follows from this example.

Sol's four-line prompt addition has been removed from the current candidate.
Original prompt and schema hashes, request bodies, model, low reasoning setting,
512-token cap, deadlines, rubric and fixtures are preserved. The earlier candidate
commits and parked fourteen-call packet remain historical, unapproved artifacts;
they must not be presented as a diagnosed fix or executed as the next step.

The retained source change is an optional private `on_rejection_detail` callback
on `parse` and `validate_assessment`. It reports only canonical `reasonCode` and
`spanRelation` for the first violating adjacent pair within one reason:
EXACT_DUPLICATE, CURRENT_WITHIN_PREVIOUS, CURRENT_CONTAINS_PREVIOUS,
REVERSED_DISJOINT, or PARTIAL_OVERLAP. The generic stage and public error remain
unchanged. No rejected text, offsets, lengths, hashes, arbitrary exception messages,
or reasoning content enter this callback. Observer exceptions cannot permit
acceptance. Default runtime logging is unchanged; the diagnostic runner must
explicitly subscribe. This closes an evidence gap, not the model-quality failure.

Astra validation: 61 focused exact-evidence and identity checks passed in 0.10s,
as lightweight supplementary unit checks with no heavy slot. All eight retained
real assessments were regrounded identically without constructing new responses.
The original source pins match the base commit, the historical evidence manifest
matches all 203 files, and prompt/schema restoration and unaffected functions were
checked directly. Sol's earlier 834-check pass and its earlier collection/checksum
failures remain historical evidence; no broad suite or new build was run for this
revision. Neither result establishes real-provider or end-to-end acceptance.

Smallest next request: exactly one separately approved real generation of the
original dev-019-en request, using this diagnostic-only adapter and an observer
that durably retains the finite rejection details. No retry or automatic second
call. At the retained price basis, propose a US$0.3325344 reservation under a US$0.35
cap inside the one shared US$50 ledger; recheck prices and allocate separately
before execution. A valid output means non-reproduction on that call, not a pass
for the old response or the full qualification. A rejected output with finite
relation/code classifies only that new failure. Missing diagnostics, transport
uncertainty or a different failure yields an explicit inconclusive/blocked result.
The Spanish failure does not need another paid reproduction to establish its gap.

One Astra extra-high diagnostic/design attempt is complete. There were no provider
calls, credential reads, allocations, remote writes, deployments, or activations.
No heavy or emulator slot was used. Do not repeat speculative Sol/Astra patches;
more reasoning cannot recover discarded content. Parent alone controls any final
Astra ultra escalation, which should address a concrete remaining design question.

ATCR-182 remains open and ATCR-183 activation remains blocked. Full accepted real
qualification, independent semantic review and the separate Dev/UAT gates remain
required. Production/public deployment is prohibited. No acceptance criterion has
been changed or waived by the one-call diagnostic request.
