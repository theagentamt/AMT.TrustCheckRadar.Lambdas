# Purchase-linked usage accounting candidate

Source-only integration for the approved paid-period policy: 200 completed checks per funded period. A minimized purchase-linked row survives account erasure until the original funded period end plus seven days, preventing a fresh verified restore from resetting completed usage. The row contains hashed purchase/order keys, policy, funded bounds, counters and expiry metadata; it contains no account ID or submitted content. Purchase hashes remain linkable within this system and are not described as anonymous. Disclosed backup retention remains 35 days.

## Atomic accounting

`shared_check_authority.purchase_usage` uses `V1#PURCHASE_USAGE#<root digest>` / `PERIOD#<order digest>` in the existing authority table. The trusted paid writer derives the root digest from store plus verified subscription lineage and the period digest from the verified order. `funding_actions` returns native transaction actions and the existing counter seed. It never executes a transaction or resets an existing row. The writer must compose these actions with verified ownership, account/deletion and ACCESS guards, reject a missing global row when an existing local period expects it, and reject creation of a new account period while reservations remain. Same-account verification of an existing period can preserve its reservations.

Paid account periods and admitted receipts carry `purchaseUsageKey`. Admission reserves both local and global counters atomically; complete settlement charges both once; partial/failed/unavailable settlement and recovery release both without charging. Settlement uses the receipt's original period after renewal. Strong reads and exact observed-row conditions reject counter races, missing rows and malformed evidence. Paid periods without the new pointer fail closed; no legacy usage is inferred or rewritten.

Account deletion groups pending receipt releases by global key and couples each release to exact receipt deletion. A paid local period cannot be erased while any reservation lacks a corresponding release. Its exact row is guarded on deletion. Once its reservations are zero, the old account period may be deleted even if a newly verified owner has advanced global usage; immutable funded bounds and a monotonic completed-usage floor are checked. Completed global usage remains until its own approved deadline. This component does not itself qualify ownership transfer or whole-account completion.

## Deadline and permissions

The global `expiresAt` is fixed at funded end plus 604800 seconds and is never extended by replay, renewal of another period, restore or deletion. `GSI1PK=V1_EXPIRING` and the existing expiry index format make it eligible for the bounded explicit expiry sweep. Exact row deletion is guarded by the existing verified inventory; no account association is added to the global row. DynamoDB TTL is a fallback, not evidence of prompt physical erasure. Scheduler operation, overdue metrics, backup behavior and actual deadline enforcement require qualification before activation.

At or after that deadline, late zero-charge receipt recovery/account cleanup may proceed without the physically missing global row, but cannot charge or recreate it. An orphan local reservation without its receipt still blocks account-period deletion. Before the deadline, a missing global row always blocks rather than reconstructing accounting.

The existing authority-table policy must permit strong GetItem, transactional Put/Update/ConditionCheck on `V1#PURCHASE_USAGE#*` for the paid writer/consumer, transactional Update for recovery/deletion, and transactional Delete for the expiry worker. The global rows use the existing GSI1; account-partition erasure queries do not include them. Infrastructure must independently verify actual IAM and reviewed inventory coverage. No new resource, key, inventory approval, live write or runtime gate is activated by this source increment.

## Validation boundary

Focused SDK/Moto tests cover complete-only charging, atomic admission races, original-period settlement, strict corruption refusal, missing global/pointer, grouped deletion, orphan reservation refusal, concurrent new-owner usage during old-period erasure, exact deadline expiry, late zero-charge recovery and preserved restoration seeds. Existing authority fixtures explicitly provision synthetic global evidence; that fixture adaptation is not a migration implementation. The paid writer and Play ownership adapter are integrated separately by their owner, and the combined suite must pass before the candidate is packaged. Real Play purchases, restore, acknowledgment, account transfer and device qualification remain separate acceptance work.

Local validation for this isolated slice:

- `AMT_AUTHORITY_INTEGRATION=1 /tmp/amt-account-privacy-venv/bin/python -m pytest -q tests/shared_check_authority/test_purchase_usage.py tests/shared_check_authority/test_transactions.py tests/shared_check_authority/test_deletion.py tests/shared_check_authority/test_expiry.py`: 130 passed (20 new accounting cases and 110 existing cases).
- `/tmp/amt-account-privacy-venv/bin/python -m pytest -q`: 1769 passed, 232 subtests passed, 24 skipped; SDK suites are intentionally opt-in, not counted as ordinary passes.
- Shared-authority compilation and `git diff --check`: passed.

The full combined paid-writer authority suite is not claimed here: the separate writer integration supplies the new pointer/global funding evidence. No live AWS, provider or device acceptance was performed by these tests.
