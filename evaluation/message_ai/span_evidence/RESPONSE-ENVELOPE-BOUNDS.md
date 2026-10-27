# Bounded Responses envelope compatibility

The Responses API return type includes `text.format.schema`. That metadata is
separate from generated model output; `max_output_tokens` bounds generated tokens,
including reasoning, rather than the complete serialized HTTP response.

Sources, checked 2026-10-27:

- [Responses API return contract](https://developers.openai.com/api/reference/resources/responses/methods/create/)
- [HTTP Content-Length semantics](https://www.rfc-editor.org/rfc/rfc9110.html#section-8.6)

An offline constructed response using the unchanged current schema, a tiny valid
assessment and ordinary two-space JSON formatting is 32,392 bytes. Its compact
form is below 16 KiB. The documented envelope shape can therefore fail the former
16 KiB transport/parser/usage cap independently of output-token exhaustion or
semantic evidence quality. This is a compatibility reproduction, not captured
provider wire or proof of the cause of a particular discarded live response.

The runtime transport, both proposal parsers and numeric usage reader now share a
64 KiB envelope cap: a 32 KiB schema/envelope allowance, the retained 16 KiB model
text allowance and 16 KiB serialization headroom. This is a finite supported
envelope budget, not a claim that every possible provider response fits. Larger
responses still fail closed. Model output text is independently limited to
16 KiB of UTF-8 before assessment JSON parsing; normalized assessment and encrypted
reasoning-item limits retain their existing bounds. Total response wire, including
headers and chunk framing, is capped at 80 KiB. Each body read is at most 4 KiB and
the original shared network deadline still applies to headers and body reads.

Content-Length accepts ASCII decimal digits with HTTP optional surrounding space
or tab. Zero remains invalid for this JSON application. Signs, underscores,
comma lists and malformed numerals reject. Leading zeros are normalized only for
the size comparison; a decimal string is compared to the finite cap before integer
conversion. This avoids Python integer conversion limits on enormous numerals.
Duplicate/competing length and transfer headers, unsupported transfer coding,
compressed content, incomplete bodies and actual-body overruns still reject.
No decompression or automatic retry is introduced. With identity content encoding,
Content-Length describes body bytes; chunk framing and headers count separately
toward the wire cap.

The optional internal observer adds only `CONTENT_LENGTH_ZERO` and
`CONTENT_LENGTH_OVER_LIMIT`. Invalid syntax uses `CONTENT_LENGTH_VALUE`. The old
`CONTENT_LENGTH_BOUND` label remains allowlisted for historical interpretation.
No exact header length, raw header/body, prompt or secret is logged or retained.
The existing private recorder reads the source allowlist and preserves these
finite stages in its final receipt without modifying old recorders or fences.

The documented assessment/schema, prompt, request serialization, grounding,
uniqueness, span ordering/overlap rules, public projection, authentication,
cost policy, qualification gates and production 512-token/eight-second settings
are unchanged. Current unqualified profile digests are refreshed because source
files changed. Historical identities and execution authorities remain immutable.
The older controlled-evaluation transport is a separate frozen harness and retains
its prior bound; this change does not grant that harness execution authority.

The retained live `CONTENT_LENGTH_BOUND` result cannot distinguish a nonpositive
declared length from one above 16 KiB because its numeric value was not retained.
The preceding unknown-stage call also remains unknown. Offline reproduction does
not retroactively relabel either result, qualify the model, or waive the frozen
semantic rubric. Transport compatibility attempt 1 is separate from the exhausted
original span-defect Astra history. Any new live packet needs independent review,
fresh source/scope bindings, an unused fence and explicit call/key-use approval.
