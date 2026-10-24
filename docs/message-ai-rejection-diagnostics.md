# Content-free Message AI rejection diagnostics

The normal application adapter now emits a finite parser stage on assessment
rejection. It retains no model body, reviewed message, spans, subject/device,
credential, arbitrary exception text or arbitrary response metadata. Usage
reporting remains numeric and independent. Public `PROVIDER_RESPONSE_INVALID`,
strict schema/semantic predicates, prompt, request body and accounting/recovery
behavior are unchanged. Logging failure cannot turn rejection into success or
change its public error.

The event is `message_ai_rejection` with one allowlisted `stage`. Categories cover
response bounds, envelope JSON/status/model, output/message/content shapes,
assessment JSON/shape, context/reason consistency and reason/span validation.
Unknown reporter input maps to `PARSER_INTERNAL`. Direct parser callers may use
the trusted optional callback without automatic logging; normal `assess` connects
it to the closed event. No event field is copied from provider content.

The historical rejection remains unexplained: its rejected body was not retained.
These diagnostics cannot reconstruct it, and no new provider call was made. The
completed qualification packet, reservations and failure evidence stay immutable.
A future paid run needs a newly reviewed packet/source identity and approval;
this source change does not authorize it or populate a qualification registry.

## Validation

- All 56 component reports reconcile to 4,243 testcases with zero failures,
  errors or skips. The first aggregate found the expected current-source identity
  mismatch; only mutable source-current pins and their checksum were refreshed,
  and that 83-test suite was rerun. The original failed evidence is retained.
- Independent evaluator suite: 456 passing tests; 52 old/new parser boundary
  parity cases; all 12 original validation predicates remain AST-identical.
- Compilation, shell checks and installed dependency consistency pass.
- External networking was blocked and AWS configuration synthetic for tests.
  These are source/regression results, not provider or deployment acceptance.

The historical published evaluation identities and immutable wire/schema fixtures
are untouched. The separate current-source record is refreshed for the new
implementation and remains explicitly unqualified, with no standing paid-call
authorization. Main CI and existing Dev artifact publication remain required
source-integration evidence. Runtime AI activation, IAM/secret changes and paid
qualification remain separate gates.
