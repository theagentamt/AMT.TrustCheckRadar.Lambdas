# Journey presentation 0.1.0-candidate.1

Additive, engineering-only local presentation contract for ATCR-120. This is not
an API endpoint, a wire change to URL outcome 0.2.0-candidate.1, or replacement for
URL transport 1.0.0-candidate.1. Existing URL clients keep their canonical schemas,
validated outcome, checkId, operationProof, accounting and access unchanged.

`assessment.schema.json` separates processing, verdict, limitations, evidence and
closed native next actions. `reference_mapping.py` is a pure illustrative reducer
for already verified evidence; it does not authenticate evidence, grant access,
make provider calls, write receipts or determine deductions. Raw client/model
fields must never be passed as verified evidence. `fixtures.json` provides exact
inputs/results and `messages.json` provides engineering EN/ES copy requiring
product/language review. There are no free-form explanation, action, URL or contact
fields. Additional fields, versions, enums and incoherent combinations are rejected.

## Mapping and authority

- Instruction stop alone: blocked/unknown/HOSTILE_INPUT_STOP. A partial result with
  independent Google match retains high_risk, avoid_link, and the stop/failure
  limitation. Schema and reducer both apply the same precedence: verified threat,
  then hostile stop, speaker clarification, unsupported content, provider failure,
  then insufficient evidence. A lower-priority limitation cannot hide a higher
  one; recovery-content limitations apply only to the recovery journey. The account holder is not labeled abusive or a campaign participant.
- No-match can establish only the existing bounded full-link statement when the
  URL checks completed; it cannot clear a whole message or prove general safety.
- Suspicious exists in the URL contract but requires governed independent rules;
  this minimal handoff has no approved message rule registry and does not emit a
  suspicious message verdict. Unknown rule sources are rejected. Do not coerce it into low risk.
- `inconclusive` is additive to this presentation contract only. It is not an
  accepted URL wire enum. `CLARIFICATION_REQUIRED` represents a local speaker
  question; self/other/mixed/unknown choices remain on device, with no raw names or
  identifiers and no new message upload fields.
- Current authenticated access snapshot remains separate from an earlier result.
  Exhaustion/expiry block external services; authenticated built-in checks and
  recovery entry remain available. Missing access does not grant permission.
- Accounting comes only from the canonical authority transport. Pending/unknown
  remains unknown and retains original reconciliation context. Never deduce zero
  charge from presentation, an HTTP status, or a legacy error.
- Recovery presently supports only unavailable/RECOVERY_CONTENT_UNAVAILABLE. The
  native help entry must remain reachable without external subscription access;
  until SECUR4ALL-163/234 content approval, it must show this limitation without
  invented recovery steps, official destinations or live-person assistance.
- `use_built_in_help` selects the native help entry; it does not assert that an
  approved bundle exists. `review_input`, `verify_independently` and `avoid_link`
  are local closed actions, never executable URLs or model instructions.

## Legacy message compatibility correction

The existing schemaVersion 1.0 endpoint now returns HTTP 422 with the established
error envelope for detected instruction-style input:

```json
{"schemaVersion":"1.0","requestId":"legacy-request","error":{"code":"HOSTILE_INPUT_STOP","message":"Analysis stopped because instructions in the content could interfere with the check.","retryable":false}}
```

The guard runs before legacy request replay/locks, quota/history writes, provider
calls, result storage or charging. It makes no refund or zero-charge claim about
historical attempts. Android maps the code to its local EN/ES stop presentation;
unknown older clients receive an ordinary non-success error instead of fabricated
low risk. New legitimate input requires explicit review; no automatic retry.
This heuristic does not solve all injection or server sanitization cases.
Normal legacy model results still lack V1 governed output and all-service access;
V1 message transport stays gated until SECUR4ALL-228/229/230 readiness is proven.
