# Recovery selection candidate.1 — draft mechanics

This local contract supports ATCR-121 and SECUR4ALL-234. It creates no Lambda,
API route, incident record, assessment receipt, subscription check or provider call.
The bundled `recovery-basics-approved.json` is an exact copy of the existing
approved EN/ES content snapshot; approval provenance refers to the historical
SEC163 comments and exact reviewed document hash. Its approval does **not** approve
the new question, unsure option, combination mapping or ordering policy.

`selection-policy.json` remains **Draft** with no approvalRecord. The reference
selector returns `limited_help / selection_unapproved` and no action IDs for this
new feature. This state concerns the selector only: hide the draft questionnaire
and keep the existing approved all-basics screen under its independent content
availability policy. Do not withdraw already-approved help because the new
selector is unapproved. Known withdrawn, missing or incompatible actual basic
content remains unavailable under the existing policy.

## Reviewable proposed behavior

Use one local multi-select question and existing approved category titles. The
closed five exposure IDs map one-to-one to whole approved paragraphs. Empty or
unsure means all five categories. A clicked-link selection also includes device
steps, preserving the approved paragraph's "below" reference. Combine by set
union, display each paragraph once in this proposed presentation order: payment,
credentials, identity, link, device. Question choices keep the original category
order. The payment paragraph already says to act immediately; this adds no new
advice or guarantee. Show the
intro and official-help paragraph always present. Selection order cannot change
the plan. Priorities are display ranks, not medical, legal or financial urgency
ratings. Whole paragraphs are `immediate_basics`; this is not newly authored
urgent/follow-up recovery guidance or proof that the full SEC163 story is complete.
The 66 EN/ES fixtures cover every subset plus unsure. Their labels are engineering
expectations pending selection-policy approval, not human-reviewed recovery advice.

Answers contain only contract version, language, unique closed exposures and an
unsure boolean. No free text, identifiers, passwords, codes, financial numbers or
incident history is accepted. Keep selections in volatile screen state, clear on
exit, backgrounding or account change/removal, and never include them in analytics, logs, research,
requests or deep links. The reference does not persist them. Do not infer an
exposure from a scam verdict, a URL scan or an AI output.

## Version, availability and external links

Require a currently authenticated session using existing app rules; this adds no
expired-session or offline-login exception. A valid signed-in user's subscription,
trial or allowance state does not restrict built-in guidance. There is no
accounting result or deduction/refund promise for any earlier analysis operation.

Retain the approved 2026-09-20 date and 180-day review rule: after 2027-03-19,
retain content with its existing review-due notice. A local clock earlier than
approval also shows review due. Approved content is not guaranteed current.
Distribution/withdrawal is by app release only. Known incompatible or withdrawn
content is hidden; offline installations cannot learn a newer withdrawal until
updated. There is no fetch endpoint, background refresh, signature delivery or
instant revocation claim in this candidate.

The exact static HTTPS links are unchanged. Display the destination before an
explicit user open action; attach no account/incident/query information, do not
fetch/check destinations automatically, and do not claim AMT reports for users.
The selector exposes only fixed link IDs; the app maps them to the bundled table.
No new contact, guarantee, emergency instruction or live-agent commitment is added.

## Optional AI clarification is not implemented

`clarification-capability.json` records an unavailable capability; it is not a
callable endpoint or successful classification contract. SECUR4ALL-235 remains
open for bounded model classification, approved action grounding, injection and
invented-contact rejection, per-check entitlement/accounting and failure recovery.
No model or paid service is invoked here. Cancellation/failure of any future
clarification must leave deterministic approved guidance accessible and use that
service's authoritative receipts rather than changing this local plan's state.
A disabled capability alone does not fulfill SEC235's acceptance criteria.

## Reference and acceptance

`reference_selector.py` is executable contract evidence, outside runtime packages.
Its policy/content arguments are trusted bundled metadata, never user answers.
Tests use an in-memory synthetic approval only to exercise mechanics; the shipped
artifact remains Draft. Runtime schema validation is closed, output contains only
allowed IDs, and modified content/order fails closed. Review this source alongside
fixtures; neither test success nor a JSON approval string constitutes owner approval.

```
python -m pytest -q tests/recovery_selection
```

The focused tests forbid sockets, exercise all combinations/permutations, raw-input
rejection, content/approval tampering, account removal, review due and fixed links.
No cloud deployment, live provider, physical-device or independent bilingual review
is implied. Android owns its renderer and session/privacy integration tests.

Before enabling selection, record exact owner-approved policy/copy/source, update
the gate and new immutable artifact pin, then verify Android fixture parity. Full
SEC163 playbook/urgent-follow-up approval and SEC235 optional AI remain separate;
SEC234 cannot be declared complete for unapproved exposure selection.
