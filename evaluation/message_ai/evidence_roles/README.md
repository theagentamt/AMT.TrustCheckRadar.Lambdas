# ATCR-187 private evidence-role candidate

The two retained English diagnostics were structurally accepted but failed the
frozen `URGENCY_ACTION` evidence group: the urgency reason quoted `today or your
account will be frozen` without its own `authorize the transfer` action anchor.
The second call used PR 170's per-reason clarification; that one live clarification
trial failed. The separate historical Spanish PRETEXT finding omitted the
connection between the claimed situation and purchasing gift cards. Neither
observation identifies the discarded original ninth response's span-order cause.

This candidate adds a required private `support` map to each reason's existing
`quotes` array. The closed role keys follow the unchanged category definitions:

| Category | Required roles |
| --- | --- |
| AI_CREDENTIAL_REQUEST | disclosureRequest, authenticationPurpose |
| AI_PAYMENT_PRESSURE | paymentRequest, pressure |
| AI_PRETEXT | claimOrSituation, consequentialConnection |
| AI_VERIFICATION_BYPASS | secrecyOrBypass, consequentialAction |
| AI_CONSEQUENTIAL_URGENCY | requestedAction, urgency |

Each value is a genuine integer index into that same reason's one to three
quotes. One quote can support both roles by reusing its index. The OR alternatives
remain OR alternatives; a claimed identity and situation, or secrecy and bypass,
are not both required. There are no generated explanations, free-form role names,
lexical overrides, cross-reason references or automatic evidence repair.

`message_ai_role_evidence_v1` is an incompatible **private** wire revision.
Quote-only legacy wire fails closed. The schema enforces category-specific role
keys and indices 0–2; the independent parser also checks the actual quote count
and rejects booleans/floats. Existing unique literal grounding, source order,
within-reason overlap, Unicode code-point positions, placeholder and response
bounds remain unchanged. Support is discarded before the existing normalized
`{code, spans}` boundary and public candidate.3 reason-code projection. Android
and the public response schema require no changes.

A role declaration is an untrusted model claim about meaning. It cannot prove
that a quote contains the requested action or coercive connection. Regression
counterexamples explicitly label the retained incomplete quote with both roles;
they remain structurally valid and still fail the frozen per-reason containment
check. Offline tests establish codec boundaries, not model compliance. The
frozen rubric and historical results are not changed or rescored.

The prompt gains format instructions for references; category definitions and
reviewed user text are preserved. Production remains 512 output tokens and an
at-most-eight-second network deadline, with no retry. Diagnostic 2,048-token /
20-second variants and all consumed execution fences remain separate. Role maps
and the larger schema add token overhead within unchanged bounds; truncation or
semantic omissions remain possible and must be measured before qualification.

Both original Astra attempts remain exhausted. This is one separately authorized
structural candidate after one failed live semantic-clarification trial, not a
new root-cause diagnosis or qualification. Both qualification/experiment registries
remain empty; original NO-GO, authentication, deployment and public-release gates
remain intact. Prospective current source identities change; historical identities,
requests, recorder source and execution authority do not. Historical recorder
unit tests explicitly bind their outgoing format to the immutable PR166 request;
synthetic response fixtures exercise the current strict parser.

Next: independent review and backend-main source integration. Dev runtime
artifact deployment and any new finite real-service experiment need their own
exact source/request/recorder binding, fresh unused fence, shared-budget
reservation and scoped key-use/execution authorization. A diagnostic success is
non-reproduction, not qualification; ATCR-183 activation and real Android
integration acceptance remain blocked by positive qualification and their actual
Dev dependencies. This source change performs none of those actions.

Official schema reference, checked 2026-10-26:
https://developers.openai.com/api/docs/guides/structured-outputs
Nested `anyOf`, required closed objects and bounded integer properties are used;
provider acceptance of this exact candidate has not been measured.
