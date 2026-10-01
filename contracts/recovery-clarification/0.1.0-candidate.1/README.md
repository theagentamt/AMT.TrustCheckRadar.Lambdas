# SEC235 offline clarification validator candidate

Status **Draft / disabled**. This is a structural engineering increment, not
approved AI semantics or an available recovery service. Selection 1.0.0 and its
seven interface pairs are approved separately. The detailed playbook and AI
review packet remains unapproved; its future granular action IDs do not replace
the approved basic-paragraph IDs used by this prototype.

## Boundaries implemented

- A distinct recovery_clarification scope, fixed selection/content versions,
  EN/ES language and closed sanitized description/entity input. Raw JSON is
  bounded at 32768 bytes, cleaned description at 2000 Unicode code points and
  8000 UTF-8 bytes, output at 4096 bytes. Reject duplicate JSON keys, malformed
  encoding, invalid types and unknown fields; return only fixed error codes.
  Client enforcement of the proposed 2000-code-point **raw** input cap is separate:
  a backend cannot establish original length after sanitization.
- Reuse only existing NFC/scalar/placeholder/residual-identifier privacy validators,
  including the IPv6 guard, through a private adapter. No message prompt, verdict,
  receipt or authority scope is reused. Neutral typed placeholders may repeat;
  originals, raw links/domains/contact/financial/government identifiers matching
  existing detectors and explicit password/code values are rejected. This is not
  comprehensive PII recognition or guaranteed anonymity; undetected identifiers
  and semantic privacy cases require separate review/evaluation.
- A draft minimal fake-model projection contains cleaned description, language,
  classification-policy version and closed allowed exposure/action IDs. It omits
  entity declarations, account/device, entitlement, receipts, manual selections,
  research, original text and history. No actual vendor request builder exists.
- Output accepts only fixed status/context/exposure/action IDs. No prose, contact,
  URL, phone, explanation, verdict or score field exists. Classified suggestions
  must be nonempty/clear and exactly match the deterministic approved action
  mapping/order, including the link-to-device dependency. Unknown, stale-version
  fields, invented contacts, extra data and mismatched action ordering fail closed.
- Ambiguity carries no suggestions and no generated follow-up questions. The
  `questionIds` field is constrained to an empty array; callers retain existing
  manual categories. This is no interview and does not initiate another call.
- `evaluate_disabled(baseline)` has no activation argument, callback or narrative
  input. `simulate(..., fake_model=...)` is explicitly an offline test seam. A
  bounded known-injection heuristic stops covered EN/ES instruction combinations;
  it is not a claim to detect all attacks. Both successful proposals and every
  uncertain/cancelled/failed/invalid branch return a deep copy of the unchanged
  local baseline. This baseline is client-side offline simulation state only: it
  is not part of the request schema, provider projection or a proposed wire
  response. Nothing is applied automatically and no incident is persisted.

`offline_proposal` is not complete/chargeable or proof the exposure inference is
correct. Output declares authorityAccounting=not_exercised. providerCalls=0
means this reference implements no provider transport; tests inject local fakes
and prohibit sockets/subprocess credential access. Do not supply a live callback
or treat this module as an authorized live-service adapter.

The eight EN/ES injected fixture labels are engineering proposals, not independent
human review or measured model accuracy. Schema checks cannot detect a plausible
but semantically wrong known exposure ID or guarantee the relevance of a model
classification. Adversarial, negation, hypothetical, quoted-request, attribution,
privacy and bilingual qualification remains required before activation.

## Deliberately missing service pieces

No Lambda route, API client, credential lookup, model, AWS resource, qualification,
provider retention setting or remote activation is added. No shared-authority
extension, entitlement integration, receipt, allowance deduction, reservation,
retry/reconciliation, cross-account/in-flight response fence, real cancellation
or latency behavior is simulated as implemented. A stale request version is
rejected here; asynchronous stale-response fencing belongs to the later runtime.

The full SEC235 service needs reviewed classification/copy/privacy/completion
semantics, a distinct governed scope and usage-only summary, input minimization,
provider budget/qualification, authoritative complete-only settlement and failure
reconciliation. Seven-day usage-only receipts must not store descriptions or
exposure/action IDs. Lost-response recovery may confirm usage without recovering
suggestions and must not trigger another paid request; those are draft runtime
requirements, not satisfied by this prototype. Reading local help remains outside
external-service billing throughout.

Run `python -m pytest -q tests/recovery_selection tests/recovery_clarification`
with repository src available (tests arrange it). Fixtures/default-disabled,
privacy, Unicode, injection, output grounding, cancellation, failure and baseline
preservation are covered. These contract files are outside existing Lambda source
packaging. SHA256SUMS binds the artifact; neither this manifest nor its successful
tests authorizes a provider call or closes SEC235/ATCR121.
