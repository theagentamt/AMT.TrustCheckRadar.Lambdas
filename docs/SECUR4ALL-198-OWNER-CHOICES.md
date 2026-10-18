# SEC198 owner-ready choices — proposed, not selected policy

The story already requires trusted, environment-specific guidance, approved
English/Spanish, predictable cache failure, tested rollback and no auth or
entitlement bypass. It does not choose public access, cache duration or how
expired guidance restricts online checks. SEC237's seven-day operations retention
cannot be reused as the lifetime of version guidance: the purposes differ.
No choice here has been implemented, deployed or silently approved.

## Concise decision for the owner

**Proposed conservative package:** show non-personal version/status information
before sign-in through the existing trusted API; make each configuration valid
for at most one hour; keep built-in help available but make online checks
unavailable when no valid configuration can be obtained; require owner approval
before increasing a minimum build or publishing an emergency restriction.
Keep OS recommendations optional.

**Approve that package, or should online checks continue under the existing
server authorization when version/status guidance is unavailable?** The second
option preserves availability but may delay enforcing a newly published security
upgrade. Neither option grants analysis access or changes server entitlements.
This is a decision prompt for the parent to present, not a recorded approval.

## Exact questions and tradeoffs

| Question | Recommended conservative default — requires approval | Alternative and effect |
| --- | --- | --- |
| May version/status be read before sign-in? | Yes, only global non-personal guidance on the existing trusted API host, with reviewed caching/rate controls. Do not send account, device or installed-build identifiers merely to fetch a shared document. | Authenticated-only reduces anonymous request exposure but guidance can disappear during sign-in/auth outages. Public reads need abuse/cost controls; they expose published version/maintenance facts, not user data. No endpoint path or access policy has been selected. |
| How long is a cached snapshot usable, and what happens when it expires? | Propose maximum one-hour validity, foreground refresh when stale, no recurring background polling and at most one snapshot, discarded at expiry. With no valid snapshot, built-in help stays usable; external checks wait for fresh guidance. | Continuing existing server-authorized checks with status marked unknown improves availability but cannot promise timely minimum-version enforcement. The proposed strict fallback can block otherwise healthy analysis during a configuration outage; it is temporary unavailability, not a permanent upgrade judgment. The hour and blocking choice are unapproved. |
| Who may force an app upgrade or emergency restriction? | Owner-reviewed configuration changes, including any minimum-build increase, required-upgrade flag and emergency restriction; owner-approved rollback under a newer revision. OS advice never blocks. | Delegating emergency publication to an operational role is faster but creates a separate lockout/abuse authority requiring explicit designation. Actual minimum versions, delegation and EN/ES text remain to be approved. Manual Production deployment approval alone does not approve future minimum-version changes. |

The strict expiry recommendation deliberately avoids retaining an expired
security rule indefinitely or inventing a permanent deny state. It is stronger
than merely keeping a previously observed security block: **all** online checks
would wait for fresh guidance during a configuration outage. That availability
cost must be accepted explicitly; it cannot be inferred from “trusted config.”
Only a single non-personal snapshot is proposed, with no version-request history
or new analytics. Request/log retention and actual runtime cost remain governed
by existing approved infrastructure policy; this document authorizes no new logs,
retention or dollar budget. A one-hour foreground refresh bound reduces repeated
requests compared with per-check polling but is not a measured cost guarantee.

## What engineering can infer without a new product decision

The existing requirements already establish separate UAT/Production acceptance,
trusted delivery, accessible approved English/Spanish, optional OS advice,
explicit app-security guidance, rollback testing and independent server auth/
entitlements. Closed parsing, rejecting wrong-environment/replayed data, bounded
payloads and not executing remote text/links follow those security requirements.
Canonical Android/iOS build ordering must be reconciled with native release
identifiers in SEC190; semantic version strings cannot be guessed from Dev codes.
No owner needs to choose a new JWT issuer, client or API path in chat: engineering
must propose an exact existing-boundary design after the access choice, with
normal security review. A new signing/key system is not presumed necessary.

The chosen access/expiry/enforcement/publication rules, numeric lifetime, actual
minimum builds and approved guidance text are genuine contract/operational gates.
They are the only parts that should block their dependent implementation. They
do not block the separate approved SEC237 receipt-local attempt work. SEC198 stays
Draft/To Do; source parsing/validation can proceed only against a clearly marked
proposal until the contract is approved. Actual UAT and manual Production gates
remain separate from these decisions.

This packet proposes client guidance and availability behavior, not a claim that
the backend already identifies or enforces a genuine installed app build. An
untrusted version string or user-agent cannot establish that trust. SEC190's
native/transport design must state what required-upgrade enforcement actually
guarantees; neither the public status document nor a client UI is a new
authorization authority. No attestation system is invented here.
