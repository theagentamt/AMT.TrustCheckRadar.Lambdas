# SECUR4ALL-229: qualified phrases and disabled model proposer

## Scope

This increment expands the approved bounded rules, without introducing generic
AI verdicts or changing any mobile contract, approved EN/ES copy, allowance rule,
account/device fence, receipt or storage behavior. Coverage version
`qualified-phrases-2026-09-21-v1` enumerates 136 complete risky phrases through six
small closed English/Spanish phrase families. Together with four existing benign
fixed phrases, the supported surface is 140 normalized whole messages. This is
not general natural-language analysis, substring matching or a safety guarantee.
Whitespace/case normalization is the only normalization. Every slot is literal;
additional sentences, negation, quotations and unsupported variants are unresolved.
The new Spanish standalone authentication-code phrases explicitly refer to the
account, excluding an ambiguous building/shared access code.

A model proposer is implemented as disabled preparation for a separately reviewed
AI assessment policy. With the existing policy it **cannot promote any verdict**:
qualified phrases need no model call; out-of-coverage text remains inconclusive
even if the model asserts a rule with plausible supporting spans. Do not enable
this preparatory proposer to provide a paid general-analysis product. Any broader
policy, new AI provenance, explanations and completeness criteria require the
separate policy/contract increment. SECUR4ALL-229 remains open.

## Processing and failure boundaries

`MESSAGE_PROPOSER_ENABLED` is independently false/absent by default. A disabled
proposer loads neither provider configuration nor secrets and performs no model
request. Existing evaluator and approved-policy gates still apply.

When explicitly enabled for a future qualified development experiment, clear
`other` speaker input outside bounded coverage may make at most one provider
request after independent reviewed-link work. Hostile stops, unknown/mixed/self
roles and already-qualified messages skip the model. Both stages share the
18-second evaluator operation budget; depleted time causes a budget limitation.
Consumer aggregate attempts/failure circuit and per-account attempt controls
continue to apply to every evaluator dispatch. No provider retry or second model
vote is added. Technical failure, malformed output or an exceeded budget remains
unavailable with zero settlement, except independently supported link threats are
retained as partial high risk. All existing incomplete/no-deduction semantics hold.

The transport fixes `https://api.openai.com/v1/responses`, certificate/SNI hostname,
method and port. Fixed-host DNS has a deadline and rejects private or mixed public/
private results. TCP/TLS, every send/read, headers and body share the same monotonic
deadline. Secrets Manager uses bounded SDK connect/read timeouts, one attempt and
a post-call remaining-time check. SDK credential discovery/DNS is not claimed to
have a mathematically hard total-operation deadline; the Lambda timeout is the
outer hard limit. It caps total wire bytes at 32768 and JSON body bytes at 16384, rejects
redirects, compression, contradictory HTTP framing, truncation, incomplete/refused
outputs, tool calls, multiple outputs, duplicate keys, unknown rule IDs, invalid
span types/ranges/overlap and additional proposal fields. Network/secret errors
are reduced to fixed failure codes; no provider exception or response text is
logged or shown to users.

The strict model response is only `{ruleId, spans:[{start,end}]}`, with a closed
rule vocabulary and maximum three exact character ranges. Span existence or JSON
validity never proves semantic meaning. The model cannot provide mobile actions,
new URLs, summaries, scores, account claims or executable instructions.

## External data and retention

The server revalidates the reviewed intent immediately before constructing the
request. Only `sanitizedText`, selected language and reviewed speaker role enter
the model input. No account/check/device identifier, metadata, entity table,
original text, reviewed link values or conversation/history enters it. It has no
tools, sessions, prior-response references, streaming or background work and sets
`store:false` explicitly. No model response or supporting span is retained locally.

`store:false` disables Responses application-state storage; it is **not evidence
of Zero Data Retention** or proof that abuse-monitoring retention does not apply.
Confirm the organization's actual provider controls and the existing product
privacy disclosure before activation. No real provider call, paid API test,
credential read or vendor-retention validation occurred in this increment.

Official references checked 2026-09-21:
- [Structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
- [Provider data controls](https://developers.openai.com/api/docs/guides/your-data)

## Infrastructure interface for later activation

No secret permission is needed or should be granted while the proposer is false.
For a later separately qualified enablement, evaluator only would require:

- `MESSAGE_PROPOSER_ENABLED=true` and existing evaluator/policy gates.
- `MESSAGE_PROPOSER_MODEL`: explicit tested model name; no runtime default.
- `MESSAGE_PROPOSER_SECRET_ARN`: exact
  `arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/openai-XXXXXX`.
- `MESSAGE_PROPOSER_TIMEOUT_MS`: explicit integer from 250 through 8000, additionally
  capped by the evaluator's remaining operation time.
- `MESSAGE_PROPOSER_MAX_OUTPUT_TOKENS`: explicit integer from 128 through 512.
- `secretsmanager:GetSecretValue` only for the exact secret ARN and `AWSCURRENT`.
  The consumer receives no provider secret access; neither evaluator nor proposer
  receives ledger/storage writes. Existing URL private invocation remains intact.
- A secret string or exactly `{"apiKey":"..."}`; never paste a live value into
  source, logs, tracker, PR, output or Terraform configuration.

The legacy provider's connection pattern can be reused, but its model-generated
scores/free-text verdicts cannot. No default model, account model availability or
provider qualification is assumed. This parser intentionally rejects reasoning
items/other envelopes outside its single assistant-message profile; a candidate
model must pass real bounded qualification before activation. The added evaluator
dependency is `dnspython==2.8.0`; runtime stays Python 3.14 ARM64.

## Local qualification

Tests enumerate every finite phrase combination and paired boundaries: quoted,
negated, extra warning/context, hostile instruction, self/mixed/unknown speaker,
withheld links, plus separately authored EN/ES positive/negative examples. A fake
TLS/HTTP wire exercises the actual standard-library HTTP parser, request minimization,
redirect rejection, DNS/TLS host binding, framing/size limits, slow-header deadline,
strict proposal schema, no retry, no logs and inability to promote an AI claim.
Moto exercises existing real DynamoDB authority/complete-only settlement separately.

This evidence validates code/contract behavior, not population-level accuracy,
live OpenAI availability, production cost, full launch readiness or physical-device
acceptance. Build artifacts and local test counts are recorded with the PR.
