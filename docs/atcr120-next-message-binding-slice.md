# ATCR-120: next bounded governed-message binding slice

This is a handoff for the next implementation, not an endpoint declaration,
activation approval or completion record. The documentation-only recovery PR
changes no runtime, pinned contract bytes or AWS configuration. ATCR-120 remains
open where actual result routing is absent; a component fixture is not that proof.

## Owning stories and concrete gap

Primary implementation: [SECUR4ALL-229](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-229)
(governed message verdicts/actions). Focused dependencies:
[SECUR4ALL-190](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-190)
(versioned message transport),
[SECUR4ALL-228](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-228)
(independent API privacy filter), and
[SECUR4ALL-230](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-230)
(one authority reservation and settlement). Android binds the real response under
[ATCR-120](https://andmorethings.youtrack.cloud/issue/ATCR-120).

The merged legacy handler now stops detected instruction-style content with an
honest error. It still uses legacy quota/history and free model result fields.
There is no new governed V1 message consumer endpoint. Current shared authority
`core._payload` validates URL-only intent and `summary.validate_summary` accepts
URL-only minimized private summaries. Passing message text through a URL field or
relabelling a legacy response would violate both contracts.

## Smallest coherent implementation package

1. Publish a separately versioned message prepare/submit/reconcile transport;
   proposed route family `/v1/message-checks` is a design proposal, not deployed.
   Give every request an immutable logical check identity and account-bound proof.
   Preserve current URL request schemas, proof digests, receipt semantics and
   replay compatibility. New message-kind intent/summary discrimination must be
   explicit, not inferred from caller-supplied fields or legacy receipt shape.
2. Before provider admission, independently validate bounded sanitized text,
   deterministic entity tokens and residual identifiers. Reconcile current limits
   explicitly: legacy Lambda permits 8,000 characters; Android local intake may
   accept more before review. Publish one UTF-8/body/text/token contract and test
   both clients against it. Never trust `localSanitizationApplied`, upload images,
   persist raw replaced values, or send a raw fallback when filtering fails.
3. Reuse existing account/device/deletion fences and real trial/verified paid/
   complimentary authority. Perform one reservation for a logical message and its
   explicitly reviewed link targets. Add a bounded private message evaluator with
   strict typed findings; close output/action vocabularies and reject model-chosen
   URLs, contacts, tools, policy or executable actions. Do not call the legacy
   message pipeline after this reservation because it would apply another ledger.
4. Keep sanitized content transient. Hash the validated message intent into its
   proof; store only the allowlisted minimized result, provenance/limitations and
   receipt needed for seven-day reconciliation. Reuse approved deletion/expiry
   behavior coherently; do not lose the existing deletion/inventory fences by
   cherry-picking only contract files from the guarded engineering branch.
5. Return actual processing/verdict/limitation states and separate authoritative
   access/accounting. Preserve independent verified link threats when the model
   fails. Settle at most one deduction for a policy-complete result; partial/failed
   attempts settle zero only when the authoritative ledger confirms that state.
   Lost/early-error/replayed attempts remain unknown/pending where appropriate;
   never infer zero from a transport error or create a new check on retry.
6. Bind Android's authenticated transport to those real envelopes and its trusted
   state renderer, carrying the same checkId/proof through reconciliation. A
   disabled feature flag means the service is not enabled; it is not evidence that
   a provider failed or a real analysis returned inconclusive. Keep runtime/client
   gates closed until the integration evidence below passes and activation is
   separately authorized.

## Content/privacy prerequisites and decisions

Already approved: account-required local access; trial seven days/ten completed
checks; individual allowance 200 completed checks per verified monthly period;
complete-only charging; no charge for partial/failed checks; seven-day minimized
receipts; trial eligibility until account deletion; research independent of
service access; reviewed URL disclosure; no raw screenshots or secret fallback.
Do not reopen these decisions merely to implement the new transport.

No new privacy product decision is identified for the bounded slice. The exact
server token grammar/residual-value handling and reviewed-link projection are
engineering contracts still to reconcile with the approved local sanitizer, not
authorization to send extra identifiers or retain message content. A proposal to
expand collection, retention or research use would require separate approval and
is outside this slice.

A current versioned, approved message-evidence rubric was not found in the audited
release. Before producing stronger model-supported verdicts or marking a message
assessment policy-complete, locate any existing approval or prepare concrete
EN/ES examples and rules for review under SECUR4ALL-229. Define which verified
findings support each verdict, what counts as sufficient/complete evidence, and
which fixed explanations/actions are acceptable. Do not invent confidence/score
thresholds or treat schema-valid model claims as facts. The initial safe outcome
set can cover actual hostile/privacy stops, provider failures, insufficient
evidence and independently verified link threats without claiming a model alone
establishes safety. Engineering fixtures and newly approved recovery text are not
approval of this message rubric.

## Acceptance evidence for the bounded integration

- Handler-to-private-evaluator-to-ledger-to-response tests using controlled provider
  doubles, then Android decoding of those exact responses: true inconclusive,
  provider failure, hostile stop, residual-identifier rejection, independent threat
  plus model failure, malformed/adversarial model output and unsupported version.
- Assert zero provider calls for rejected/unentitled/deleting/wrong-device input;
  no raw text, replaced values, URLs, proof, credentials or exception payload in
  storage/logs. A user-supplied instruction is data, not evidence of customer abuse.
- Verify one logical reservation across bounded reviewed links and message work;
  complete-only once, partial zero, pending/uncertain reconciliation, lost response,
  duplicate request, concurrent last allowance, account switch and deletion races.
- Regression-test every existing URL fixture, proof/digest and recovery path.
  A message proof must never authorize URL work, or vice versa; changing payload,
  language, reviewed-link projection or check identity must not reuse a proof.
- With separate authorization, qualify the guarded Dev endpoint using an actual
  allowlisted account and real trial authority, bounded provider calls, returned
  minimized receipts and cleanup. Record emulator, controlled failure and live
  provider evidence separately. Do not fabricate a paid grant for this test.
- Paid Play renewal/refund/catalog lifecycle remains a separate gate for paid
  access; missing verified paid facts continue to fail closed. It need not block
  all real-trial transport tests or force completion of the entire billing epic.

Detailed recovery playbooks remain separate. This slice supplies the actual
message result binding that component-only ATCR-120 rendering cannot establish.

For a concrete reviewable policy decision, see the
[unapproved initial message rubric](sec229-message-rubric-proposal.md). Its proposed
verdict/completeness/copy rules are not implemented or implied by recovery approval.
