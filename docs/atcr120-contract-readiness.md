# ATCR-120: shared journey readiness and release integration

This is an engineering handoff, not activation, content approval or evidence of
all four live journeys. Source baseline: release-V01 a6f2372, including main
12964d0. ATCR-120 depends on SECUR4ALL-190/229/230; this increment does not close
those broader stories.

## Existing contracts and prerequisites

| Area | Existing source / contract | Readiness |
|---|---|---|
| URL and QR URL | URL outcome 0.2.0-candidate.1; consumer transport 1.0.0-candidate.1 | Keep versions and payloads unchanged; only URL payloads from QR use this route |
| Access/trial | contracts/v1-access/v1; v1_entitlements and shared authority writers | Already in release; b4c6516 branch has identical writer/handler/access-contract contents and is not a new prerequisite |
| Guarded Dev retention/deletion | 2e6b314, 8473330, 2f277a1 on codex/v1-authority-deletion | Separate normal release integration required to reproduce the approved engineering deployment and published proof-retention contract |
| Message | conversation_analysis schemaVersion 1.0 | Legacy quota/history path; not the shared V1 authority, privacy filter or governed model result contract |
| QR capture/non-URL QR | Native acquisition + local type handling | Capture does not authorize upload/open/execute. Non-URL values do not become a URL assessment |
| Post-scam help | SECUR4ALL-163/234 | Content approval/version/offline-session policy remain open; no approved playbook or contacts supplied by this increment |

The deletion branch is a coherent safety prerequisite for guarded live authority:
shared core inventory fences, explicit expiry, deletion worker, runtime gates,
packaging and regression tests must be reviewed together. Do not copy its
contract files while silently leaving deployed/runtime prerequisites behind.
The pure journey handoff itself requires none of these changes to run locally.

## Evidence boundaries

The earlier synthetic Dev qualification proved one trial check charged once,
zero reserved afterwards, explicit scheduled removal of three expired synthetic
records, and the V1-authority deletion component. It did not prove a Play purchase,
renewal, refund, complimentary operator activation, complete legacy account
deletion, V1 message orchestration or approved recovery content. Runtime gates and
schedules were subsequently disabled and synthetic records removed.

Verified paid access still needs real Play catalog/package/account ownership,
current funded monthly-period facts, ordered lifecycle reconciliation and store
sandbox evidence. Legacy FREE/PRO values, client claims and research balances
must not stand in for this authority. Built-in signed-in functionality does not
consume external entitlement; account authentication and deletion fences still
apply. Unknown access is not a subscription offer or proof of exhaustion.

## Android handoff invariants

- Four native entries are message, link, QR and post-scam help. Entry or local
  preview never sends data, launches a URL, invokes a provider or starts a trial.
- Processing, verdict, uncertainty, access and accounting are separate. Failure,
  hostile-input stop and missing content never imply low risk or zero charge.
- URL outcomes and their existing evidence/action/message enums stay canonical.
  An independent verified threat remains high risk when another stage fails;
  represent limited processing separately. A list no-match is not a guarantee.
- Submitted content, model output and client flags never select an arbitrary
  action, destination, phone number, URL or policy. Client actions are closed
  native identifiers mapped to reviewed resources. Never linkify generated text.
- Message legacy success is not a governed V1 assessment. Never relabel its score
  as shared URL evidence, and never map an unknown error into a successful result.
- An ambiguous speaker can be clarified locally with sender / me / unsure choices
  and a sanitized span reference; do not request names, phone numbers or raw IDs.
  Clarification cannot itself grant access or resolve an uncertain charge.
- Expired/exhausted external access keeps authenticated built-in checks and
  recovery entry available. Missing approved recovery content shows limited help;
  it is not an invented checklist, contact directory or human escalation service.
- Reconciliation retains original account-bound check identity and proof under
  the pinned URL transport contract; do not replace identity or resubmit content.
- EN/ES engineering fixtures are deterministic examples, not evidence of an
  approved safety rubric, reviewed translation or authorized recovery playbook.

## Required acceptance still outside this increment

Complete API privacy filtering and injection resistance (SECUR4ALL-228), governed
message evidence/model output and bilingual rubric (229), all-service accounting
and paid lifecycle gates (230), approved deterministic recovery content and offline
validity (163/234), and physical-device/TalkBack/live acceptance remain separately
tracked. No deployment or route addition is authorized by this handoff.

## This increment and validation

The additive local contract is
`contracts/journey-presentation/0.1.0-candidate.1`; it is not a public endpoint or
backend activation. Eleven deterministic fixtures cover bilingual stop, provider
failure, role clarification, non-URL QR, unavailable recovery content, independent
threat retention and bounded link no-match. Schema checks reject unsupported
actions/URLs/versions and contradictory copy or verdicts. A small legacy fix maps
detected instruction-style content to HTTP422 HOSTILE_INPUT_STOP before replay,
provider, storage or charge effects; legacy historical accounting remains unknown.

Local Python3.14 full repository suite: 718 passed, 4 skipped, 166 subtests
passed. Compile checks, shellcheck, diff whitespace validation and source-only
conversation_analysis Python3.14 ARM64 packaging passed. The real public handler
regression verifies the exact 422 envelope, no score/accounting, no downstream
side effects and no submitted-text/request-ID logging. No live provider/model,
paid lifecycle, translation approval, production deployment or device test is
claimed here. GitHub CI remains independently required; account billing failures
are not passing CI and no workflow/required-check setting is weakened.

Independent review added reverse limitation-precedence constraints and regressions:
hostile stop cannot be hidden by inconclusive/provider copy, clarification and
unsupported content outrank lower-priority failures, verified threats stay partial
high risk, and recovery-content limitations cannot leak into another journey.

## Subsequent owner approval: native recovery basics

On 2026-09-20 (CDT), the owner approved the exact limited native
`recovery-basics-1.0` draft. See
[the approval source and model boundary](atcr120-approved-recovery-boundary.md).
This supersedes the earlier pending-approval note only for that exact basic
bundle. Detailed playbooks and backend readiness remain open. No pinned contract
bytes or runtime behavior change; native bundle availability is separate from
the assessment contract's recovery-unavailable presentation.
