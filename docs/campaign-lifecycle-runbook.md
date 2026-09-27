# Campaign lifecycle candidate runbook

The current candidate handler accepts only `close_period`, `recover_candidate`,
`expire_locator`, and `recover_expired_orphan`. Each event has schemaVersion 1 and
an exact environment. Candidate and period-admission gates, generation and locator
inventory pins must validate before work. The historical `manage_keys`,
`finalize_periods` and `expire_transient` service functions are **unreachable from
this handler**. Existing infrastructure schedule definitions for those historical
payloads must remain disabled; this source does not install a scheduler.

`close_period` takes `periodId`, conditionally transitions the exact current
admission generation from OPEN to CLOSING, and preserves the key and retention
clocks. New producer transactions require OPEN; bounded cleanup can use CLOSING.
Closure alone proves neither period erasure nor eligibility to retire a key.

`recover_candidate` takes a canonical UUIDv4 `candidateId`. It freezes eligible
candidate input, publishes or suppresses under exact source/aggregate guards, then
removes owned locator/target pairs in bounded steps. Published anonymous aggregates
keep their existing retention. Threshold and transaction-size limits fail closed;
a partial step is not whole-period completion.

`expire_locator` takes exact `locatorPK` and `locatorSK`. It deletes only the
validated expired owned target/locator pair (including guarded FEATURE siblings).
A CONTRIBUTION requires absent SUMMARY and repair checkpoint. Missing locator
identity is unresolved; expiration-index absence is never erasure proof.

`recover_expired_orphan` takes canonical UUIDv4 `candidateId` and integer `periodId`.
It handles the narrower missing-SUMMARY case with expired, strictly typed
contributions and an optional version-2 repair checkpoint. See
[candidate orphan recovery](campaign-orphan-recovery-candidate.md) for bounds,
compatibility, exact outcomes and qualification commands. There is currently no
inventory-backed orphan-candidate discovery or scheduled caller.

## Withdrawal and account deletion

The deletion bridge consumes exact owned durable commands and their qualified
inventory pins. It establishes account/contributor fences, traverses retained
periods, removes paired features/contributions, and reconstructs surviving
candidate metadata. It preserves publication and original retention clocks.
Its separately gated completion transaction consumes the exact recovery job and
writes the component receipt only with the reviewed completion proof. Account
finalization still requires every component; a campaign result is not account
erasure. Matching terminal replay does not manufacture fresh completion evidence.

A queued producer cannot bypass the account/contributor tombstone or the period
admission fence. Missing or retired keys remain unresolved until a separately
qualified period-erasure proof exists. Never reconstruct a retired token, create
an approval marker from empty indexes, or reset an original operation to retry it.

## Remaining Dev lifecycle work

SECUR4ALL-207 remains In Progress. Whole-period authoritative all-family drain,
SEALED proof, compatibility of later account deletion with retired periods, safe
KMS retirement, poison/discovery fairness and scheduled deadline enforcement are
not implemented by this orphan increment. Current contribution expiry is based
on creation time plus 21 days; late-period rows can outlive period end plus the
seven-day recovery window. That existing mismatch must be resolved under the
approved policy before retirement; this change shortens or extends no deadline.
TTL remains defense in depth, never the completion mechanism.

Use fixed error/result categories and numeric counters for diagnosis. Do not put
accounts, contributor tokens, candidate contents, vectors or raw queue bodies in
logs or tickets. On failed conditional work, preserve evidence and retry the same
reviewed identity after establishing the current state; do not force deletion or
infer success from a successful Lambda invocation.

Later assembled UAT acceptance is tracked in
[SECUR4ALL-330](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-330), dependent
on retained Dev work in SECUR4ALL-207. It does not waive these unfinished Dev
requirements or authorize activation.
