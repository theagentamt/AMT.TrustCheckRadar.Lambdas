# Approved AI message candidate.2 implementation

Owner approval covers the exact policy document at infrastructure commit
`8cc87c59f8d74dbc14a3dd810bea33cef43ca1dd`, SHA256
`d6e9fff12225540bef9ba7833cce457cca4b9c791af3b49dd8a4f1601d204349`.
The historical document's unapproved heading is preserved as history; the owner's
subsequent explicit approval and separate infrastructure approval record supersede
that status. Tracker approval records: SECUR4ALL-229 7-1244, SECUR4ALL-190 7-1245,
SECUR4ALL-230 7-1246, SECUR4ALL-242 7-1247, ATCR-120 7-1248 and ITCR-92 7-1249.

## Immutable mobile contract

Candidate.2 source pin is `80bcb53dd96058c55a48cbaedbfd3eeb48f84b41` at
`contracts/message-consumer/1.0.0-candidate.2`. It includes 50 full transport fixtures,
five deliberately invalid outcome fixtures, exact approved EN/ES copy, a standalone
reference validator, schemas and checksums. Candidate.1 files remain byte-identical.

Routes stay `/v1/message-checks/prepare`, `/v1/message-checks` and
`/v1/message-checks/reconcile`. Input field shapes are unchanged; clients explicitly
select transport `1.0.0-message-candidate.2`. Outcomes use schemaVersion 2 and policy
`message-ai-2026-09-21-v1`, with separate `assessmentBasis`, `aiAssessmentStatus` and
`aiReasonCodes`. No model text, supporting spans, confidence, executable action or
new destination is returned. Android's separate fixed localized reason-label
mapping must describe possible AI findings, never verified fraud.

AI-only warning is capped at suspicious. Qualified complete AI warning/no-warning
can charge one authoritative check; unsupported context, abstention, partial,
provider/schema failure and hostile stops charge zero. A qualified independent
finding skips the model. Unknown/mixed/self AI speakers and obvious quotation/mixed
source abstain; further context checks are part of model qualification. HTTP/spelling,
names or demographics alone are not sufficient AI warning categories.

Text warnings survive failed/withheld link work as partial. Text attribution failures
cannot keep an AI-only warning: hostile, unknown-speaker, unsupported and unresolved
context cases abstain. Contradiction with a known benign fixed phrase also abstains.
Independent high-risk rules and Google threat matches retain their evidence and
precedence across AI disagreement/failure. The current reviewed-link threat path
still stays partial; complete joint text/link semantics require separate qualification.
Android initially sends no reviewed links. No AI no-warning can clear a withheld link.

Only an authoritatively settled zero-charge envelope can use the new inconclusive
copy promising no deduction. Pending/unknown accounting retains neutral existing
copy and proof-only reconciliation.

## Version binding and seven-day recovery

The authority hashes candidate.2 transport and policy into a separate
`message-payload-v2` purpose. Preparation and check rows retain only
`messageTransportVersion` metadata alongside the existing minimized proof/receipt.
Candidate.1 HMAC bytes/purpose are unchanged; legacy rows without version mean
candidate.1. A proof submitted or reconciled under a different version fails with
`CHECK_ID_CONFLICT`, including unadmitted preparations. No silent upgrade or fresh
identity bypass occurs. Candidate.2 complete settlement requires a matching validated
candidate.2 summary; failed lease recovery needs no message package dependency.

Reconcile accepts both retained versions even when candidate.2 admission is disabled.
Unknown/expired receipts are not evidence of a refund. Android must persist the
minimal explicit version with account/check/proof/expiry and treat legacy local
unversioned receipts as candidate.1, preserving independent evidence on refresh.

## Disabled runtime interface

Existing consumer/evaluator/authority Dev gates and legacy policy settings remain.
New gates are independently explicit:

- Both functions: `MESSAGE_AI_ENABLED=false`,
  `MESSAGE_AI_POLICY_VERSION=message-ai-2026-09-21-v1`, and
  `MESSAGE_AI_POLICY_APPROVAL_SHA256` equal to the approved digest above.
- Evaluator: `MESSAGE_AI_QUALIFIED=false`. No `MESSAGE_AI_QUALIFICATION_ID` is set.
- The committed `ai_qualifications.json` is empty; no model qualifies today.
- A future reviewed enablement requires exact model/prompt/schema/policy/contract/
  evidence binding and the actual evidence artifact. The model must be a dated
  immutable snapshot, and returned provider model metadata must match exactly.
- Provider connection configuration reuses the explicit
  `MESSAGE_PROPOSER_MODEL`, `MESSAGE_PROPOSER_SECRET_ARN`,
  `MESSAGE_PROPOSER_TIMEOUT_MS` (250–8000), and
  `MESSAGE_PROPOSER_MAX_OUTPUT_TOKENS` (128–512). None has a runtime default.
  The new AI path has its own gate; `MESSAGE_PROPOSER_ENABLED` does not enable it.
- No provider secret IAM access is needed while disabled. Future evaluator-only
  GetSecretValue must target the exact reviewed Dev OpenAI secret/AWSCURRENT;
  consumer still cannot read that secret or bypass the authority.

The model receives only independently revalidated sanitized text, language and
speaker role. It has no tools/history/session or account/check/device data, no raw
reviewed link values and no free-text UI authority. Existing fixed-host TLS/DNS,
body/wire/time caps, no redirects/retries, store:false and refusal/error handling
are reused. Store:false is not proof of zero provider retention. Qualification and
actual provider settings remain unperformed; see `sec229-ai-qualification-plan.md`.

Python remains 3.14 ARM64. Message consumer packages both contracts and the pure
policy modules; evaluator packages the AI profile and candidate.2 manifest. URL
consumer/recovery archives remain independent of shared message modules, with real
archived settlement/recovery regressions. No AWS deployment, activation, paid API
call, broader quality claim or story closure follows from source integration.
