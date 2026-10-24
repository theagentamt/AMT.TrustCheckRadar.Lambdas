# Permanent Message AI implementation candidate

This focused branch starts from Lambda main `8ecc79678fd1f87c575c43f7f4ffb856869db48d`.
It does not merge release-V01 history. The existing main authority/recovery and
sanitizer-history controls are preserved. This is application source work, not
activation, a qualified model, or evidence of a real provider connection.

## Application behavior

The server-selected `ai_primary_v1` policy sends every eligible, privacy-reviewed,
supported message to the normal qualified AI adapter regardless of deterministic
phrase matches. Privacy, speaker/context, hostile-input, access and device gates
remain enforced. Deterministic warnings and link findings remain independent and
are labelled by their actual provenance. They do not masquerade as AI findings.
AI refusal, timeout, malformed/unknown usage, failure or abstention cannot produce
a complete AI-primary outcome or deduct a customer check, even when a deterministic
warning survives. Complimentary success remains zero-charge; it does not prove
paid-allowance deduction. Existing candidate.3/schema-3 and History/accounting
contracts are unchanged, including original charges on aged historical outcomes.

Operation proofs bind the server execution profile. Public requests cannot select
it. Submission and reconciliation preserve the existing operation identity;
reconciliation never resends content and does not require the new provider-cost
configuration. The normal OpenAI transport remains one request with no automatic
retry, no tools, `store:false`, fixed endpoint, capped output and total deadline.
Only reviewed sanitized text, language and speaker role enter its request.
Recognized-format masking does not guarantee that all names, addresses or other
freeform personal data have been removed; the existing explicit review is required.

## Permanent spending and usage controls

There is no default model, price or spend allowance. Dispatch requires the existing
AI gates and an approved, hash-bound model qualification plus an exact
`MESSAGE_AI_COST_POLICY` shared by consumer and evaluator. Its closed fields are
`model` (dated identifier), `contextTokens`, `maxOutputTokens`,
`inputNanoUsdPerToken` and `outputNanoUsdPerToken`. Qualification must establish
that contextTokens is the full documented provider context maximum, including all
system/schema overhead; it is not a character estimate. The model, prices,
context/output bounds must match the qualification record's `costPolicy`.

The existing aggregate provider-budget record atomically reserves
`contextTokens * inputNanoUsdPerToken + maxOutputTokens * outputNanoUsdPerToken`
before evaluator dispatch, alongside its attempt/failure caps. The additional
`MESSAGE_PROVIDER_NANO_USD_PER_WINDOW` cap is mandatory for AI. Reservations are
never released based on a failed or uncertain response. Conditional writes reject
concurrent stale reservations. Window/cost-mode changes and corrupt accounting
metadata cannot erase live priced reservations. Older unpriced/unknown-window
records fail closed until their bounded window has expired (up to 24 hours when
the old length is unknown); this code does not reset or delete those records.

Normal adapter usage logs contain only allowlisted bounded numeric token counts,
an uncached-price upper estimate, and finite failure reasons. Unknown usage is not
zero billing. No response text, credential, account/check identifier or submitted
content is logged. A valid provider response without qualified usage is not a
successful AI-primary assessment. Usage estimates are not provider invoices.

The old optional candidate.1 paid-proposer switch fails closed rather than bypass
these qualified monetary controls. Candidate.1 deterministic/link behavior and the
reusable transport remain; qualifying that historical paid prompt is outside this
change. Normal AI is candidate.2/3, with AI-primary restricted to candidate.3.

## Removed from the application candidate

The fixed tester/message, `dev_ai_primary_e2e`, one-use DynamoDB test reservation,
temporary secret selector and one-hour IAM compiler are absent from deployable
source. Earlier worktrees/commits/evidence are preserved. Synthetic messages and
fake transports exist only in isolated tests, not as an application runtime path.
The model and experiment approval registries remain empty. Historical published
sanitizer evaluation identities remain byte-for-byte unchanged; only the separate
current-source candidate pins are refreshed and explicitly remain unqualified.

## Validation and integration boundaries

Five earlier aggregate failures were independently diagnosed against main. Main
already corrected the release inventory and historical/current sanitizer identity
separation. Three test-loading fixes scope temporary module/path stubs and restore
process globals. They do not change account, campaign or deletion business code.
Normal-path tests exercise real in-memory Moto transactions, usage parsing,
idempotent settlement, History projection, currency exhaustion, stale writes,
lost commit acknowledgments, recovery and paid/complimentary accounting, with all
external connections blocked. Those results are source validation, not live AI.

Merge target is main through a focused reviewed PR. The existing main-only CI and
publication workflows are unchanged. Main publication side effects must be
reviewed before merging: this cross-cutting change is conservatively classified
`all` by the existing release-scope selector, and ENABLE_DEV_PUBLISH can cause AWS
artifact publication after green main CI. No merge, cloud publication, deployment,
IAM or secret changes are authorized by a passing local test alone.

Before any future activation: choose/qualify the model and provider data handling,
verify current prices/context limits and an explicitly approved recurring budget,
configure normal scoped credentials/IAM with approval, deploy exact reviewed
packages/configuration with unweighted aliases, drain old invocations and qualify
budget-row migration/readiness, and verify rollback and observability. Preserve
maintenance v12 and the existing seven-day complimentary grant. Real provider,
backend integration, URL enablement and mobile acceptance remain separate gates.
