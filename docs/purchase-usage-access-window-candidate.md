# Verified purchase access windows — inactive source candidate

This source increment separates the immutable funded period (`startEpoch` / `endEpoch`) from the latest provider-verified allowance access end (`accessUntilEpoch`). It does not activate billing, provision lifecycle infrastructure, call Google, or qualify physical retention enforcement.

The approved behavior preserves the existing 200-check funded-period limit and all used/reserved counters through grace or deferral. A verified access extension does not create a new funded period or replenish checks. The minimized global purchase ledger's logical deadline is the **latest verified access end plus seven days**, including a verified shortening; it is not the maximum expiry ever observed. An event timestamp is never substituted for provider proof.

## Stored shapes and transaction interface

- Exact global schema 1 remains readable with effective access equal to funded end. Ordinary counter changes preserve its schema. Exact schema 2 adds `accessUntilEpoch`; `expiresAt` and the expiry-index key must agree with that access end plus seven days. Neither schema contains an account identifier or original content.
- Account PERIOD may carry `accessUntilEpoch`, otherwise its funded end is effective. New admitted paid CHECK records copy the access end alongside the original purchase pointer/period identity. Older pending receipts may retain an earlier access end after a verified extension; recovery uses the current strongly verified PERIOD/global pair, not the stale receipt deadline.
- `effective_access_end(period)` and `usage_deadline(period)` validate/derive those values. `funding_actions(..., access_until_epoch=None)` defaults to funded end, returns native DynamoDB actions and preserved counter seeds, and refuses an extended missing ledger. A trusted fresh writer may upgrade an existing schema 1 row within its complete transaction.
- `access_window_actions(table, pointer, observed_global, access_until_epoch, *, now)` requires the exact existing global record. It returns a conditional Put, ConditionCheck, or Delete without executing it. The caller must compose it with exact owned local PERIOD, ACCESS/source, ownership, inventory and deletion fences. This helper is not a provider-verification or authorization boundary by itself.
- A shortened access end with unresolved reservations fails `PURCHASE_USAGE_RECONCILIATION_REQUIRED`. A future extension after the old ledger deadline also fails; an expired/missing accounting record is never reconstructed to recover usage.
- If the verified new deadline is already due and the existing ledger has zero reservations, the helper emits an exact conditional Delete. That action requires additional, separately qualified Delete IAM: the existing Put-only handoff does not support this path. The trusted caller must atomically update local period/source state so it cannot continue admitting checks.

Admission and the unchanged schema-1 mobile snapshot use the effective allowance end. The snapshot's `periodEndsAtEpoch` is the earlier of local effective access and the active grant end. Settlement still charges only completed checks and only the original admitted funded period; partial/failed checks and expired reservation repair do not charge.

## Explicit limitations and activation evidence still required

Provider/source integration is owned by the lifecycle writer increment. The snapshot regression here isolates projection from that writer and does not attest real grace/deferral provider acceptance. No background acknowledgement, notification authentication deployment, encrypted token store, scheduler, or live transaction is added here.

Expiry and erasure reuse the shared strong-read/exact-CAS implementation with the new deadline. DynamoDB TTL is asynchronous; changing `expiresAt` does not prove a physical deletion deadline was met. Explicit scheduled cleanup, IAM, restore/replay behavior and deadline enforcement require operational qualification. The previously disclosed 35-day backup window is unchanged; no new backup approval or additional retention duration is implied.

An old account awaiting final PERIOD erasure can have an older, longer local deadline after ownership transfer and a new owner's verified shortening. If the global record then disappears before the old local deadline, this source refuses to infer why it is missing and does not emit successful account-cleanup evidence. That overlap requires an explicit reconciliation design before activation; no indefinite expiry tombstone is introduced to hide it. Due shortening with pending reservations is likewise unresolved and fails closed.

The tests use synthetic SDK/Moto records. They cover schema compatibility, fresh exact-CAS upgrade, counter preservation, completed-only/duplicate settlement during extended access, old pending-receipt recovery, shortening and due deletion, unknown/missing records, unchanged snapshot wire shape, expired allowance exhaustion, concurrent mutation rollback, stale expiry protection, and the old-account overlap blocker. They are not live Google, AWS, mobile, billing-cycle or physical-retention acceptance.

## Local validation

- 195 SDK/Moto cases passed across access-window, purchase usage, transactions, deletion, expiry and snapshot integrity before the final receipt-bound correction.
- After that correction, all 28 access-window SDK/Moto cases passed, including new admission/settlement and recovery after an access end is shortened below funded end but remains in the future. Copied receipt access is bounded by funded start and current effective access; absent legacy evidence still means funded end.
- The ordinary suite passed 1,808 cases plus 232 subtests, with 27 isolated suites skipped, before that final one-line correction. Compilation and whitespace checks passed afterward. The integration owner must run the combined writer/provider suites after composing this source.
