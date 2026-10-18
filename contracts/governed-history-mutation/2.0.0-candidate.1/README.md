# Governed delete/clear technical contract — policy blocked

This finishes the T1 technical design and gives one exact P1 owner proposal. It
adds **no production adapter, transaction executor, handler or retained data**.
Independent privacy/concurrency review found no existing approval for the new
governed marker purposes and lifetimes. Dependent mutation implementation stops
at that boundary. The preceding opaque preparation adapter is not extended.

`contract.json` fixes transition/commit/replay rules. `schemas.json` is a strict
technical request draft, not an active endpoint contract. `policy-decision.json`
pins the retained owner approval and separates technical choices from the new
owner choice. Existing V1 and governed read schemas are byte-identical.

## Technical choices closed without new product authority

- Use existing bootstrap/delete paths with explicit schema2 and governed family.
  Legacy schema1 remains exact; a gh1-looking legacy ID never selects governed
  deletion. No new route, query, header, progress/reset or export operation.
- The bootstrap `inspect` mode only reads current state/status. Missing state is
  unavailable, not generation0. It cannot create or repair state silently.
- A `prepare` mode issues a source-free, 600-second MAC-bound intent lease and a
  **server-generated** operation UUID. It binds action/target, original state,
  owner/device/inventory and restore epoch. This is a technical anti-replay
  contract, not another implemented preparation layer. No record/content/quota
  write occurs at issuance. Client UUID reuse is not execution authority.
- Mutations require the original lease plus expected random incarnation and
  monotonic per-account generation. No client clock orders admission or clear.
  Missing, unknown, expired or overflowing state fails closed; no nonce reuse or
  generation wrap. Explicit StartFresh is a separate proposed customer action.
- Resolve exactly one canonical state by acknowledged strong reads under EVERY
  retained HMAC key; duplicates/malformed state fail closed. Keep state at its
  original controlKeyId through ordinary key rotation; new check keys do not
  create a second generation. Explicit creation conditions absence under all
  retained keys and exact inventory in one transaction. This candidate refuses
  more than16 key versions or transaction/byte/expression limits; it never splits
  the safety fence or truncates coverage. Key retirement remains separately
  qualified through control/receipt/purge/account-deletion obligations.
- Admission captures state in the accounting-reservation transaction. Settlement
  uses the **same control condition** as its decision to persist BOTH
  `governedHistory` and `resultSummary`. An old-generation settlement may settle
  accounting once but persists neither assessment copy. A lost CAS rereads and
  suppresses; it never calls an evaluator/provider again or charges twice.
- Delete-one atomically removes the five assessment/index fields, retains only a
  fixed ERASED disposition in the original receipt, and writes an intent-bound
  operation receipt. It does not delete accounting, operations obligations or
  original expiry. Clear atomically advances generation and writes the operation
  receipt plus bounded purge job; enumeration is not the linearization point.
- Every public result path, including core reconciliation and consumer/worker
  replay, must filter disposition, current incarnation/generation and original
  expiry. List/detail alone are insufficient. Use an acknowledged atomic
  control/receipt snapshot or equivalent reviewed final fence; stale indexes and
  cursors only discover candidates. Immediate exclusion and <=24h physical
  removal are distinct. Responses already linearized/in flight cannot be
  recalled; coordinated Android drops responses tagged before known invalidation
  and inspects current state before showing caches on reconnect.
- An exact operation retry returns its immutable original logical-commit result
  while the seven-day receipt exists. Changed intent conflicts. An expired lease
  can only look up an already committed exact receipt; it cannot execute when
  the receipt is absent, including after its expiry. The original expected state
  independently prevents re-clearing new results. New intentional work needs a
  new explicit prepare and new server-issued UUID. There is no lifetime UUID
  registry promise after its bounded evidence expires.
- Unknown commit means acknowledged exact same-owner/UUID/intent receipt readback
  or UNRESOLVED. No new-ID retry, success by absence, automatic SDK retry or purge
  completion by inference. One atomic transaction binds current profile/age,
  deletion absence, active device, complete HMAC inventory, exact control/target
  and conditional operation receipt. No paid grant is needed for erasure.
- Logical response distinguishes `invalidationStatus=COMMITTED` from
  `purgeStatusAtCommit=PENDING|COMPLETE`; it fixes original time/state/deadline.
  Bootstrap inspection may separately report current purge status. Returning an
  original PENDING-at-commit response on retry is not a claim cleanup is still
  pending now. Original signing/intent keys must remain qualified through a still-live seven-day
  receipt, not only the600-second lease. Missing/retired key is unresolved/refused,
  never permission to re-sign an old operation or infer success. Current consumer
  erased replay uses generic unavailable with no
  outcome/accounting change; no invented public erased error is deployed.
- Activation waits for coverage of every writer/read/replay/cache and deletion
  inventory. Rows without admitted capture are unqualified, never inferred as
  generation0. Drain their original windows or explicitly reviewed StartFresh;
  do not perform automatic historical backfill. Recognition remains separate.

Background purge consumes only a trusted immutable committed job; it needs no
new raw-subject locator and a device move/session expiry cannot abandon erasure.
It conditions every removal on the exact current control and canonical target,
protecting even a newer incarnation created after an older StartFresh job. A
control race rereads; it never orders random nonces lexically. It must never
upsert missing receipts/jobs/operation evidence after global account deletion or
TTL retirement. Global account deletion covers these HMAC-owned rows through
the existing complete authority inventory.

## Policy audit: why no atomic mutation source is added

Retained SEC185 approval snapshot is pinned in `policy-decision.json`. Item7
requires active assessment/replay deletion within24h. Item8 permits only narrowly
justified anti-charge/resurrection metadata **but expressly leaves fields and
retention to be finalized**. Its remaining gate requires exact numeric metadata
lifetimes and backup/PITR/restore behavior, with no indefinite retention by
omission. These are owner decisions, not missing engineering permission.

Legacy USER History has 90-day assessments,120-day controls and seven-day mutation
receipts. The separate modern receipt/projection has its existing seven-day
deadline. Account-deletion120-day informational evidence and durable terminal
fence serve another purpose. None authorizes adopting new governed controls by
analogy. HMAC-derived ownership/target/intent metadata remains linkable.

The exact proposed record minima and clocks are in the JSON. No full lease,
submitted content, free text, input fingerprint, contact value or per-result
deletion timestamp is needed. Original receipt/disposition fields expire at the
original receipt deadline; no extension for erasure. New operation intent metadata
would last seven days from **first commit**, potentially after the result expires:
that new clock and purpose need approval. Retrying or reading never renews it.

The proposed live control hold is **691680 seconds (8days8minutes)** after a new
admission: seven-day assessment + enforced maximum480-second History-producing
settlement lease +24h physical purge. This480 cap is an engineering prerequisite;
the current generic Settings merely checks positivity and does not establish it.
A new mutation may hold control to max(existing hold, first commit+seven days).
Reads/retries/reconciliation never extend the hold. Control/purge retirement
requires complete inventory, canonical original deadlines and no unfinished job;
DynamoDB TTL or a clock crossing is not proof. Overdue cleanup is a breach and
remains unqualified, never permission to retain indefinitely or claim deletion.

### Exact minimal owner question — not yet asked or approved

> Approve the bounded governed-erasure policy in `policy-decision.json`: the
> listed minimized control/receipt/job fields, original-deadline-only result
> disposition, seven-day mutation receipts, control holds of at most8days8minutes
> from each new admission (or seven days from each new mutation),24h active-copy
> purge and a separate seven-day maximum backup/PITR window. If current control
> is absent or an exact supported control is past its hold, require an explicit
> **StartFresh** action that invalidates all old
> incarnations rather than guessing a recovery. On backup restore, keep traffic
> quarantined and purge **all pre-restore governed History assessments**, even
> undeleted ones, before reopening under a new externally authoritative restore
> incarnation. Restore accounting/charged usage independently without resetting
> it. Approve this bounded retention plus History-recovery tradeoff, or keep
> dependent mutations held?

This is one explicit proposal, not a collection of inferred approvals. Assessment
retention itself remains its current contract. The backup window is separate
from691680 seconds; backup expiration is not immediate physical deletion. A
backup may retain a historical copy within that separately disclosed window.
Restored resources must actually purge old copies before serving, not merely
hide them. New random state alone cannot establish that the restore epoch was
not rolled back: its authority must live outside the restored rollback set, all
old writers must be drained/fenced and source/alias/config evidence requalified.

StartFresh refuses valid live, malformed, duplicate or unqualified restore state;
ordinary live invalidation uses clear. Only ABSENT or exact supported logically
expired state may be explicitly replaced. Expired state cannot authorize any
ordinary mutation. Explicit StartFresh is safe without assuming absence proves
lawful retirement:
it deliberately suppresses ALL old/missing-incarnation results/proofs/cursors and
replay summaries, generates a fresh never-reused incarnation, and schedules their
purge under current owner/deletion/inventory fences. It is destructive customer
behavior and therefore included in the new proposal. Ordinary missing-control
reads remain unavailable. It must not reset badges, accounting or allowance.

If the owner instead requires recovery of undeleted backup History, keep this
implementation held and design a separately approved, independently nonrollback
erasure journal with its own exact purpose/lifetime. It is not silently added as
infrastructure or an extra indefinite marker. Current policy already requires
erasures be reapplied; it does not choose this availability/retention tradeoff.

## Later recognition bundle, still held

G1 recommendation: count one future typed-complete logical message/URL/QR check
independent of risk/payment; exclude noncomplete outcomes and count deliberate
new-ID submissions without content fingerprints. Tradeoff: consistent activity
recognition versus a narrower outcome/source rule. Same-ID counts once is settled.

G2 recommendation: reuse checks_1/5/20, current EN/ES catalog and progress fields,
without per-result awards/unlock timestamps. Tradeoff: minimal data/API versus
additional separately designed achievement metadata. Existing catalog presence
is not approval. Thresholds1/5/20, no points, clear/reset independence, no quota/
cash/campaign effects and no automatic backfill are not reopened.

## Bounded completion and later implementation

Independent privacy/concurrency review validates the policy gap and technical
fences. Strict schema and replay/restore boundary fixtures are local specification
tests only. Existing production source is byte-identical. No new non-dispatching
adapter or source atomic mutation is added while the exact policy choice is held.

After approval, implement one coherent atomic mutation/read/admission/settlement/
purge path against this contract, with physical erasure/restore/account deletion,
lost-ACK/concurrency and unaffected-owner tests; do not count this design as those
passes. Runtime/deployment and actual target acceptance remain separately gated.
SEC185/186/224/225/226 stay open; physical-only cases remain ATCR148. No real
delete, AWS/endpoint/auth/device/provider/runtime/financial/main action.
