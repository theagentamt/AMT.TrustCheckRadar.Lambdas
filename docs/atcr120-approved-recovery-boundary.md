# ATCR-120: approved native recovery basics and assessment boundary

The owner approved the exact minimal English/Spanish recovery draft on
**2026-09-20 (CDT)** after reviewing the proposed copy, destinations and behavior.
This approval covers native bundle **`recovery-basics-1.0`** for the limited
ATCR-120 post-scam entry. It does not approve an AI-generated recovery service,
all detailed playbooks, broader backend activation or completion of those stories.

## Exact approved source

- Tracker source: [SECUR4ALL-163](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-163),
  comment **`7-1189`** (the reviewable draft).
- Orchestration review artifact: `/tmp/amt-atcr120-recovery-approval.md`.
- SHA-256 of that exact UTF-8 artifact:
  `ce40bd3e6b0529cef759da34a4a1d511fd3358b87b69edf5b02ca9e75d287f22`.
- Its historical “DRAFT” heading records its state when submitted for review;
  the subsequent owner approval is recorded here. Do not silently replace the
  reviewed bytes or infer approval of later edits from this record.

Android owns the bundled resource implementation, approval date and tests. This
Lambda document records the boundary; it does not deploy or activate the app.
Independent bilingual quality and physical-device accessibility review remain
separate release evidence, not implied by owner approval or unit tests.

## Separate local content and assessment models

`RecoveryBasicsBundle` / `RecoveryAvailability` is the native content model.
It can be available to a currently authenticated account without a subscription
or remaining external-service checks. Showing bundled text performs no provider
call, reserves no allowance, creates no assessment receipt and makes no claim
about any earlier pending check's charge status.

The separately pinned journey-presentation `0.1.0-candidate.1` contract is an
assessment/limitation presentation model. Its recovery variant describes an
**unavailable** recovery-content state. It is not a catalogue of all native bundle
availability states. That contract's unavailable-only variant therefore does not
prevent the owner-approved native bundle from being shown.

Do not change the assessment to `complete`, issue a low-risk/safe verdict, create
an accounting result, or add a backend recovery endpoint to represent reading the
local bundle. Existing URL outcome, transport, access and journey-presentation
contract bytes and Android source-hash pins remain unchanged by this clarification.

## Approved behavior and acceptance evidence

- Require the existing authenticated-session policy; do not extend expired
  sessions or create an offline sign-in exception. Session/account removal clears
  access. Subscription expiration and allowance exhaustion do not hide built-in
  help from a still-authenticated account.
- Bundle the exact approved EN/ES basic categories directly. Collect no incident
  answers, history or research contributions. Use no model-generated instructions,
  destinations, contacts or guarantee of recovery or live AMT assistance.
- Offer only the exact static HTTPS destinations in the approved draft, show the
  destination before opening, and open only after explicit user choice. Attach no
  incident/account identifiers or query parameters. Opening official help is not
  a paid reputation/AI check and does not consume an allowance.
- Record the approval/review date and version. The review deadline is 180 days
  after approval (**2027-03-19**). After that date retain the basic text with the
  approved review-due notice, not a guarantee of current validity. Known withdrawn
  or incompatible content is unavailable. Offline installations cannot discover
  a new withdrawal until an update; no instant-revocation promise is made.
- Test actual native entry, account clearing, expired/exhausted subscription
  presentation, no automatic upload/open/execute, explicit static-link confirmation,
  EN/ES rendering and withdrawn/incompatible/review-due states. Label host tests,
  emulator tests, physical-device review and live services as distinct evidence.

These local behaviors address ATCR-120 routing and honest presentation. They do
not require a new Lambda, storage, provider credential, entitlement grant, API
schema or AWS change. Broader exposure combinations, deterministic playbook
selection, urgent/follow-up ordering, remote version/revocation delivery and AI
clarification remain ATCR-121 / SECUR4ALL-163 / SECUR4ALL-234 and related work.

## Message and external-service readiness remains unchanged

The message `HOSTILE_INPUT_STOP` guard is merged source, not proof of a deployed
V1 message pipeline. The candidate inconclusive/provider-limited fixtures are
local presentation evidence, not newly available message consumer endpoints.
Android must keep external message transport gated until API privacy filtering,
governed model evidence/actions, shared authority/accounting and paid lifecycle
readiness are demonstrated under SECUR4ALL-190/228/229/230. A local disabled-service
notice must not invent a provider result, deduction or refund.

ATCR-120 is assessed against its four native presentation acceptance criteria;
this increment neither closes the broader backend epics nor claims live billing,
provider or detailed-recovery acceptance.
