# Approved recovery selection 1.0.0

The owner approved this bounded selection increment on 2026-09-21: “go ahead and
continue on. what we need to complete”, responding to the exact linked proposal.
The source commit/hash and tracker approval records are in selection-policy.json.
Approval covers the seven exact bilingual UI pairs, selection/display/privacy
rules and existing account/content policy. It does not approve detailed new
playbooks, AI semantics, provider execution or completion of all recovery stories.

This new immutable artifact preserves 1.0.0-candidate.1 unchanged. The approved
basic-content snapshot and original 2026-09-20 content approval/review clock are
byte-identical. `uiDraft` and `proposedActionIds` retain candidate field names for
consumer compatibility; the top-level policy/fixture approval is now Approved.
Fixture labels remain engineering expectations, not independent bilingual review.

## Deterministic behavior

Choices stay in original category order. Action paragraphs display once each in
payment, credentials, identity, link, device presentation order. This is not a
clinical priority or guarantee of the best recovery sequence. Link selection adds
device guidance below it to preserve the approved conditional reference; it does
not assert a download or compromise. Empty/unsure/show-all selects all categories.
Intro and official-help sections always accompany an available plan.

Selections exist only in screen memory and clear on exit, app backgrounding or
account change/removal. No incident narrative, saved state, analytics, logs,
research, upload or shared URL contains them. No exposure is inferred from a
threat verdict or model output. Input schema rejects extra or unknown fields.

Existing authenticated-session policy applies, including offline availability;
there is no expired-session extension or offline-login exception. Subscription,
trial expiration and exhausted allowances do not restrict built-in help. No
provider, assessment receipt, deduction/refund or prior-check charge claim occurs.

Content remains available with its existing review-due notice after 2027-03-19
(or if the clock precedes approval). Known missing, withdrawn or incompatible
content is unavailable. Updates/withdrawals arrive through app releases only; an
offline installation cannot learn a new withdrawal until updated. A unavailable
selector does not withdraw otherwise-approved basics: preserve the existing
all-basics fallback under its independent content/session policy.

Official links are the same static HTTPS destinations. Show the domain and open
only after explicit choice; attach no incident/account/query information, make
no automatic requests or reports, and promise neither recovery nor live support.
Optional AI clarification remains unavailable. The seven approved UI pairs say
so explicitly; this artifact grants no model/service activation.

## Verification and scope

Run `python -m pytest -q tests/recovery_selection` from the repository root.
The reference and 66 EN/ES fixtures cover every subset plus unsure. All combination
orders deduplicate deterministically; availability validates exact approved
content, schema compatibility and session state. Content approval metadata comes
from the trusted app bundle, never user answers. SHA256SUMS pins every file.

No Lambda/API endpoint, AWS resource, persistence, provider call or runtime
package is introduced. Android owns rendering/lifecycle tests and the new pin;
iOS implementation remains separate. Full SEC163 granular urgent/follow-up
playbooks and SEC235 bounded AI clarification stay open; implementation tests do
not substitute for independent bilingual or physical-device accessibility review.
