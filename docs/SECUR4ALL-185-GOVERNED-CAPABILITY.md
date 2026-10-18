# S10 governed History compatibility checkpoint

This is the bounded source contract/fixture milestone under SEC185/186/224/225/226,
not completed mutation/recognition functionality. The additive package is
`contracts/history-capabilities/1.0.0-candidate.1`. It describes existing contract
bindings offline and does not change an HTTP request/response, create a route,
add a header, enable a feature or authorize execution.

## What is reconciled

Existing legacy History1.0.0 has numeric schema1 mutations, UUIDv4 operation IDs,
owner-scoped idempotent receipts, independent History/recognition generations,
90-day assessment retention and catalog checks_1/5/20. The exact legacy routes,
body/response fields, error schemas and retention constants are preserved.

Existing governed History candidate1 has two GET routes, a typed content-free
projection tied to immutable accounting, seven-day settlement retention and
HMAC-inventory/account/device fences. It has no mutations, progress, export or
server bootstrap. Android a618408 carries governed references in the generic
AnalysisRequestId wrapper, with a typed governedResult and local generation0
shim. These do not supply server generation, legacy assessment or ownership.
Current legacy progress/mutations remain disabled by the legacy build flag.

The package records exact backend contract hashes and retained Android source
joins. Governed schema bytes also match the vendored Android files. Legacy parser
behavior is reconciled as source; no claim every legacy schema is vendored or a
current app/device/session was inspected. The strict existing decoders reject
new capability/progress fields, so this offline document is not appended to wire
responses. No new negotiation endpoint is proposed.

## Independent privacy/ownership/idempotency fixtures

Tests validate the source-contract matrix and reject unsupported governed
operations, new paths/versions, execution permission and client-supplied owner,
proof, generation, accounting or points in offline operation plans. This schema
checks compatibility only; an accepted plan is not authorization.

Actual unchanged handler and local Moto cases demonstrate:

- Unsupported mutation/progress routes return404 from the governed handler
  without constructing an SDK client/resource.
- A gh1-shaped ID is syntactically valid in the legacy API. Its legacy delete
  can produce an idempotent COMPLETE no-op receipt while a separate governed
  record remains unchanged. This is not governed deletion and not a prefix
  rejection. Replaying the same operation returns the original receipt; a
  changed target conflicts without another write.
- A client account selector is rejected; changing the authenticated synthetic
  owner cannot obtain the prior owner's mutation result. Real local governed
  authorization still denies foreign owner, moved device and deletion fence.
- Governed reads and settlement replay preserve original receipt/accounting and
  expiry, do not create progress, and stop serving the result at original expiry.
  History item count cannot stand in for lifetime badge progress.

These are local synthetic/Moto/source checks. JWT/service fixture substitutions
are explicit; no Cognito, live IAM/provider, deployment or mobile acceptance is
claimed. Larger preserved suites retain concurrent generation/replay/erasure
coverage; they are not tests of a newly implemented bridge.

## Exact decisions and implementation split

Approved behavior stays unchanged: thresholds1/5/20, no points, same-ID retry
counts once, clear does not reset badges, reset does not clear History, and only
server post-reset admission ordering can count. Those are not new questions.

The candidate records two explicit governed owner decisions:

1. **G1:** approve or revise counting one future typed complete logical check
   across permitted message/URL/QR sources, regardless of risk/payment basis;
   exclude partial/inconclusive/blocked/failed/pending; count a deliberate new-ID
   submission without retaining a content fingerprint. Same-ID dedup is already
   settled. No risk score is proof of an independently confirmed scam.
2. **G2:** approve or revise reusing existing checks_1/5/20 IDs, current EN/ES text
   and current progress fields without new per-result awards/unlock timestamps.
   Existing legacy catalog presence is not governed policy ratification.

T1 is an engineering contract dependency: explicit existing-route/versioned
capability selection, owner/result identity, server generation capture and atomic
completion/reset/clear, erased replay, Android invalidation and all-copy erasure.
P1 requires a concrete minimal record/lifecycle design and privacy-purpose join
before adopting fields or lifetimes. Existing legacy120/90/7-day facts do not
approve new governed linkages by analogy. No new retained data is introduced now.

No fully determined production adapter change is added here. Reusing old delete
success would falsely acknowledge a governed deletion; removing the authority
receipt would damage accounting/dedup; copying a governed outcome would fabricate
legacy score/confidence/summary fields and extend retention. Prefix rejection
would change the old requestId contract. These are not safe substitutes for the
missing compatible mutation/erased-replay contract. Only the dependent destructive
and award wiring is held; contract refinement/tests and unrelated source work
can proceed without runtime, policy or mobile activation.

## Dev and later target handoff

The companion validation and two reviews pin source, fixtures, local results and
initial findings. Existing source, wire packages, CI triggers, publisher, dependencies,
configuration and IAM stay unchanged. The existing main-only authority test job
adds this suite so gated Moto cases are exercised rather than skipped. No runtime ZIP is built or qualified by
this contract-only milestone. Existing SEC344/345 inspection ZIPs remain source
only and are not deployment-qualified.

Local entry point, with existing pinned requirements-dev.txt and synthetic
credentials, is in the package README. No live runner is provided. Delete/clear contract design and synthetic fixtures can proceed independently
of G1/G2; their adapter needs reviewed T1/P1. Recognition award/read wiring also
needs G1/G2. Neither blocks current read-only work. Each implementation then needs local atomic
account/device/deletion/generation/replay/privacy tests. Target deployment requires
its own immutable complete artifact/config/rollback and separately authorized
scope. Two disposable accounts/devices, complete/partial/error/replay/concurrency,
reset with delayed completion, clear without badge reset, all-copy deletion,
expiry, backup restore and unaffected-account cleanup remain acceptance cases,
not passes from these fixtures. Physical-only testing remains ATCR148.

SEC185/186/224/225/226 remain open. The package is a candidate contract checkpoint;
no story is marked Done or criterion waived. No AWS/endpoint/actor/auth/credential,
security-setting, runtime/gate/IAM, emulator, purchase, deployment or main action.
