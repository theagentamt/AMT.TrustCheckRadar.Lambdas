# ATCR-182 evidence composition candidate

Source base: `09152b691701ef3294f20cd6fb206efcd6bd6778` (PR166).
Tracking: [ATCR-182](https://andmorethings.youtrack.cloud/issue/ATCR-182), within
[ATCR-180](https://andmorethings.youtrack.cloud/issue/ATCR-180); unresolved release
work stays under [ATCR-186](https://andmorethings.youtrack.cloud/issue/ATCR-186).

The retained real PR166 batch is NO-GO: nine calls, eight strict-valid assessments,
seven evidence passes, one PRETEXT evidence-composition failure, one hard
`SPAN_ORDER_OR_OVERLAP` rejection, and five unrun cases. Historical outcomes and
frozen criteria remain unchanged. The artifacts are retained in
`/Users/theaimachine/Documents/ChatGPT/AMT Backend Recovery/message-ai-low-reasoning-qualification-pr166-20261025`.

The ninth rejected body was intentionally not retained. Source inspection proves
that at least one reason's exact, unique source quotes derived spans that failed
`previous_end <= start`. It does not establish whether the pair was reversed,
duplicated, contained, or partially overlapping. Emoji, offset conventions,
truncation, and the ninth semantic result are not established causes. The eight
retained successful parses independently reground from their literal quotes.

The retained `bank-gift-es` PRETEXT reason quotes only the claimed account-closure
situation; it omits the consequential action and conditional connection. The whole
message and another reason's evidence cannot fill that reason's missing elements.
This establishes an evidence-composition failure, not a need to change the rubric.

The prompt candidate clarifies that each reason's own quotes must jointly ground
all required elements and their required connection. It prefers one complete
contiguous excerpt over redundant subquotes when available. This is a general
instruction, without case-specific words, category filters, sorting, merging,
reason removal, or a new acceptance rule. Its effect on real responses is unverified.

An optional private `on_rejection_detail` callback on `parse` and
`validate_assessment` now reports only `reasonCode` and `spanRelation` for the first
violating adjacent pair within a reason. Relations are `EXACT_DUPLICATE`,
`CURRENT_WITHIN_PREVIOUS`, `CURRENT_CONTAINS_PREVIOUS`, `REVERSED_DISJOINT`, and
`PARTIAL_OVERLAP`. The generic rejection stage and public error remain unchanged.
Details contain no quote text, positions, lengths, input hash, provider IDs, or
exception messages. Callback failure cannot turn rejection into acceptance. This
callback does not add runtime logging or rejected-output retention.

Current source/profile pins change prospectively. The historical published profile
identities, semantic fixtures, rubric, policy, schema, model, low reasoning profile,
512 total output tokens, eight-second provider deadline, accounting, and transport
remain unchanged. Both qualification registries remain empty. Existing tests are
preserved; only the current-source base assertion follows the new candidate base.

Official OpenAI documentation confirms that
[Structured Outputs can still make content mistakes](https://developers.openai.com/api/docs/guides/structured-outputs).
The [dated GPT-5.4 mini model](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
supports low reasoning and Structured Outputs. Those contracts do not establish
semantic evidence completeness or source-span ordering.

The next proposal repeats all fourteen frozen real cases with unchanged criteria,
order, deadlines, and stopping rules. It adds only finite rejection detail retention.
Maximum fourteen attempts, no retries/tools/count calls, one memory-only approved
key read, and a US$4.6554816 conservative reservation within a US$5 batch ceiling
and the one shared US$50 ledger. The full 400,000-token input context plus 512
output tokens is reserved at published uncached rates with a 10% cushion; this is
an upper reservation, not an invoice. The parent must reconcile/reserve a fresh
allocation and authorize the exact frozen packet before key access or dispatch.

Ownership: Daniel approves scope; parent little b coordinates integration, budget,
and any escalation. Rollback: revert this source candidate; no deployed state has
changed. Support: retained artifacts plus the held packet handoff; missing ninth
text cannot be recovered by reinterpretation. Observability: existing finite stage
plus the new optional relation/code callback for future qualification only.

Decision: NO-GO for activation/public release. ATCR-182 remains open until real
qualification, independent retained-output review, accepted Dev evidence, and
required integration are complete. ATCR-183 activation depends on positive
qualification. Dev and later UAT require separate approved scope. Production and
public deployment remain prohibited. No build or retained-artifact inspection is
end-to-end acceptance. One candidate is prepared in this workstream; no paid fix
attempt or new failed live outcome occurred. Historical attempt counters are not
reset or inferred from missing records.
