# Governed message transport candidate 1

This is a separately versioned message transport; existing URL contracts and proofs
remain compatible. The fixtures are synthetic outputs of the actual envelope builder
and qualified evaluator, not evidence of an activated or deployed service.

Authenticated HTTP API routes are POST `/v1/message-checks/prepare`, POST
`/v1/message-checks`, and POST `/v1/message-checks/reconcile`. All use the existing
verified account and active-device binding. Request/response schemas are closed.
The transportVersion is `1.0.0-message-candidate.1`.

Prepare and submit bind the exact reviewed intent, account and client checkId through
a message-domain-separated HMAC. Submit adds the original operationProof. Reconcile
contains only version, checkId and proof, never content. A completed check deducts
one; partial, blocked, unavailable, unsupported and inconclusive deduct zero only
when an authoritative settled receipt confirms it. A failed request, lost response,
auth failure or expired proof alone does not prove zero charge. Pending/unknown
retains the original identity and reconciliation path. `rejected` with
`OPERATION_EXPIRED`, `not_started`, zero and no receipt is the narrow authoritative
closure of an expired preparation that was never admitted.

No-proof prepare uncertainty permits an explicit retry of the exact original
checkId/intent while still in memory. Never automatically submit or invent a
proofless reconciliation route. After process loss, a user may explicitly start a
new check; that does not establish the old check's accounting. Store returned
account/checkId/proof/creation time in a separate encrypted no-backup namespace for
seven days; no message, OCR, provider response or raw URL is persisted by this API.

The maximum complete serialized request is 32768 UTF-8 bytes, including metadata
and proof. sanitizedText is 1–8000 Unicode codepoints, NFC, nonempty and trimmed;
never truncate to fit. sourceType is acquisition provenance only. speakerRole is an
explicit whole-message self/other/mixed/unknown choice, never a named person.

Entity tokens use the exact uppercase names in the schemas and consecutive 1-based
numbers per type, with at most 100 declarations. Each declared token must occur,
each reserved token must be declared, and duplicate declarations are rejected.
Repeated occurrences can reuse a declaration. Reject user-supplied reserved-token
collisions before generating tokens. Show the exact transmitted tokenized preview.
The initial Android flow sends no reviewedLinks and marks withheldLinks whenever
links occurred. It sends no original link origins, domains or email values.
Server validation independently rejects bounded residual identifier forms; this is
not a claim that all personal information can be detected or made anonymous.

At most one future explicitly reviewed link is supported. It must also pass the
existing private URL disclosure validation: full_url may withhold only fragment;
origin_only withholds path and query. Sensitive values and invalid projections are
rejected before provider calls. A match may concern an observed redirect chain, so
`observed_http_chain` never claims that the submitted URL itself matched. Joint
message/link coverage remains limited and match results are partial, preserving
independent evidence without charging. A no-match never clears the message.

Outcome message/action keys resolve only through messages.json. These exact EN/ES
rows were approved 2026-09-20; arbitrary provider prose, links and actions are not
accepted. Only tested exact whole-message rules qualify for complete coverage.
Unmatched or ambiguous text is an actual inconclusive result. Provider failure and
hard attempt/cost-budget denial remain distinct limitations; no automatic retry or
allowance deduction occurs. Account allowance and provider attempt budgets are
independent, including complimentary accounts. Runtime defaults are disabled.

response-fixtures.json supplies HTTP statuses and full envelopes, including
PRIVACY_REVIEW_REQUIRED (422), SERVICE_NOT_ENABLED (503), lost prepare/submit,
authoritative closure, independent threat preservation and chargeable/zero-charge
outcomes. reference_validation.py carries additional request/summary invariants;
JSON Schema is not a substitute for server privacy/rule validation. SHA256SUMS pins
all files except itself. No live paid lifecycle or deployment readiness is claimed.
