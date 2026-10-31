# Guarded URL transport

Same prepare / submit / URL-free reconcile fields as candidate.2, with explicit
transportVersion 1.0.0-candidate.3 and contractVersion 0.4.0-candidate.1.
New candidate.1/.2 submissions return CONTRACT_UNSUPPORTED before refresh,
reservation or provider work; their previously admitted receipts still reconcile
using their original versions, summaries and charge. Candidate.3 proofs bind the
version and exact disclosed request intent under a distinct HMAC purpose.
Changing URL, scope, withholding or version cannot reuse a prepared operation.

One logical Check and one existing allowance reservation/settlement. Complete
checks charge one; partial/blocked/unavailable checks charge zero. Lost responses
require same-operation reconciliation, never automatic submit/provider replay.

fixtures.json is synthetic offline contract evidence, not live acceptance.
The high-risk initial fixture records zero observed HTTP hops. Redirect evidence
uses redirect_destination without returning a raw final URL or redirect chain.
All existing access/accounting and authentication/device/entitlement gates remain.
