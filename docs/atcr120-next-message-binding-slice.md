# ATCR-120: next bounded governed-message binding slice

The backend portion of this bounded slice is now implemented on the governed-message
feature branch: separately versioned prepare/submit/reconcile handlers, independent
privacy validation, shared authority settlement and a private deterministic evaluator.
See [implementation and qualification handoff](message-consumer-handoff.md). This is
not deployment or activation evidence. Android binding and guarded live integration
remain distinct acceptance steps; ATCR-120 must not close on component fixtures alone.

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

The legacy handler remains separate and is not used by the new message path. The
new consumer binds explicit sanitized-message intent using a separate account-bound
HMAC domain and stores only a minimized closed summary. URL intent/proof compatibility
is retained. The package below records the implementation requirements and remaining
client/live qualification work, not permission to activate the endpoints.

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
4. Keep sanitized content transient. Bind validated message intent with an
   **account-bound, message-kind domain-separated HMAC**, using the existing
   authority keyring/purpose-separation approach, never a bare text digest. Bind
   contract version, logical check identity, language and complete reviewed intent
   so another account/kind/projection cannot reuse the proof. Keep existing URL
   proof encoding and digest semantics unchanged. Store only the allowlisted
   minimized result, provenance/limitations and
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
   separately authorized. Preserve separate hard provider-attempt/cost budgets,
   explicit retry/backoff and circuit breakers; inconclusive/no-charge results
   cannot bypass operational limits or trigger silent resubmission. Exact values
   remain engineering configuration and release qualification, not new approved
   numeric policy in this document.

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

The owner approved the exact SECUR4ALL-229 rubric on 2026-09-20 America/Chicago;
see [approval provenance](sec229-message-policy-approval.md). Initial implementation
qualifies tested exact whole-message EN/ES templates only. Unmatched text and uncertain
roles are inconclusive without allowance deduction. No model score, second model vote,
or schema-valid claim alone proves semantics. No AI model is integrated in this slice.
The historical proposal bytes remain unchanged so the approval fingerprint is auditable.

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
[historical initial message rubric](sec229-message-rubric-proposal.md) and its
[separate explicit approval record](sec229-message-policy-approval.md). Recovery approval
alone was not the authority for these message rules.
