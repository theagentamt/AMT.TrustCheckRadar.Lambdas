# Bounded reasoning comparison — unqualified candidate

This permanent adapter capability is default-off. It grants no qualification,
paid execution, secret access, activation or deployment. The model/prompt/product
rules and all public Message/History contracts remain unchanged. Prior failed
qualification reports and frozen scores remain evidence, not accepted profiles.

## Proposed comparison

- Dated model: `gpt-5.4-mini-2026-03-17`.
- Explicit `reasoning.effort=low`, the lowest nonzero level documented for this
  model. This is an engineering hypothesis, not an established accuracy fix.
- Total `max_output_tokens=512`, including internal reasoning and visible output.
  Keep the existing eight-second transport deadline, 18-second supervisor limit,
  300-second batch limit, single attempt, no retries, no tools and no count call.
- At most fourteen nonpersonal EN/ES generations: the same ten regression cases
  and four contrast cases used by PR165. No cases or warning/evidence criteria
  may be dropped to obtain a pass. Freeze a fresh reviewed execution packet before
  spending. Preserve the prospective redemption-PIN action annotation correction
  separately and identify any future annotation version explicitly; never rescore
  the original batch.
- All fourteen must pass strict envelope/usage/schema/grounding and independently
  adjudicated semantic/own-reason evidence criteria. Report original and contrast
  groups separately. An incomplete/refused/truncated response fails; no retry.
- The 512-token cap may leave insufficient space for reasoning and a result.
  Reject that outcome honestly. Increasing the cap/deadline or changing models
  would be a different comparison requiring new review and spending bounds.

## Configuration and protocol

`MESSAGE_AI_REASONING_EFFORT` is absent by default. Only `low` for the exact dated
model with 512 total output tokens is currently supported. Empty values, `none`,
other efforts, aliases, snapshots or output caps are rejected. The legacy proposer
and recovery settings remain unchanged. Omission preserves the prior request body.

An explicit profile requires an exact additional qualification-record field:

```json
{"inferenceProfile":{"reasoningEffort":"low","maxOutputTokens":512}}
```

The existing dated model, policy, prompt/schema/contract/evidence hashes and cost
policy bindings remain mandatory. Evidence for omitted effort cannot qualify low;
low evidence cannot qualify omission. The registry remains empty. No Terraform
configuration or runtime environment is activated by this source change.

Only the explicit low profile may parse an optional single leading `reasoning`
metadata item before the existing single completed assistant message. Its required
fields are bounded `id`, `type=reasoning`, empty `summary`; only optional completed
`status`, empty `content` and bounded opaque `encrypted_content` (or null) are
accepted. The existing 16 KiB whole-response limit remains. Metadata is discarded,
never interpreted as evidence, exposed, logged, persisted or sent in a later turn.
No summary or reasoning-text retrieval is requested. Unknown fields/items,
multiple/reordered items, plaintext summaries/content, tool output, refusal,
incomplete status, invalid usage, malformed output and bad grounding fail closed.
Usage (including numeric reasoning tokens) is checked before assessment parsing.

## Conservative cost bound and next approval

Official standard prices checked 2026-10-25: input US$0.75 per million tokens;
output US$4.50 per million. Reserve the entire documented 400,000-token context,
plus 512 total generated tokens, even though the actual inputs are short. With a
10% prudential uplift: `(400000*750 + 512*4500)*1.10 = 332534400` nanodollars per
attempt, or **US$4.6554816 for fourteen**. Do not apply cache discounts. Maintain a
US$5 batch ceiling within the shared cumulative US$50 ceiling; that is not US$50
per batch or standing application spending permission.

The retained shared ledger held US$23.6123392, leaving US$26.3876608 unallocated.
This proposal fits without releasing historical reservations; it does not itself
reserve funds or change that frozen ledger. Before execution, reconcile the
current ledger, freeze source/runner/plan/review identities, recheck official
pricing, metadata/authentication and exact recipient/key-use approval. Do not reuse
consumed batch approval. Existing key use is limited to memory-only OpenAI
`api.openai.com` authentication; no key logging/persistence. Exposure could permit
unauthorized spending. No paid call or key read is authorized by this document.

A local adapter comparison does not establish deployed backend, Android E2E,
production readiness, representative bilingual accuracy, or permission to activate.

Sources:
- https://developers.openai.com/api/docs/models/gpt-5.4-mini
- https://developers.openai.com/api/docs/guides/reasoning
- https://developers.openai.com/api/reference/resources/responses/methods/create

## Source validation

The local 57-suite aggregate passed 4,560 tests with zero failures, errors or
skips. The final evaluator suite passed 646 tests; independent final review ran
772 relevant evaluator/sanitizer/controlled-protocol tests successfully. Review
caught and resolved float/integer equality in the qualification field: JSON
`512.0` is now rejected rather than treated as the exact integer profile.
A concurrent initial reviewer run observed a stale mutable source pin during that
fix; pins were refreshed and the final independent run passed. It is not a
qualification or runtime failure and was not silently waived.

Python compilation, shellcheck, whitespace checks and all source-only package
builds passed. Both evaluator archive entry copies match final reviewed source.
These source-only builds do not establish dependency-complete Linux runtime or
provider behavior. Existing main-only CI and publication guards must still run;
`authority_manual` scope intentionally skips AWS credentials/uploads and does not
activate services. No model-quality improvement is claimed by these local tests.
