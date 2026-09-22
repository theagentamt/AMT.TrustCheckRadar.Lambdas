# Purchase ownership actions for the modern paid writer

This source-only SECUR4ALL-195 helper prepares ownership actions for a caller's
single DynamoDB transaction. It does not enable a purchase route, verify Google
evidence, write a grant, execute a transaction or change existing claim/export/
deletion behavior. Live coverage, provider and paid-writer qualification remain
separate prerequisites.

## Internal interface

1. Discover the complete bounded lineage without mutations. If predecessor
   identities were unknown, this discovery is not the final verification.
2. Call `store.observe_claim(account_id, hashes, product_id=product_id)` before
   the fresh provider verification. It requires reviewed ownership inventory,
   absent owner/locator pairs or exact same-account pairs. Foreign ownership,
   unknown rows and incomplete pairs fail closed without repair.
3. Freshly verify the entire same lineage and the funded-period/store evidence.
   The caller also captures modern ACCESS before verification and must guard
   that exact observation. A new/reordered predecessor invalidates this attempt.
4. Call `store.prepare_claim_actions(observation, verified_hashes,
   product_id=product_id)`. It refuses changed inventory, owner or locator rows,
   and returns native Python DynamoDB actions: one ownership inventory condition
   and two conditional Puts per alias. It does not call `_transact`.
5. Include those actions in the **same** modern authority transaction with the
   caller's account, device, deletion, authority inventory and pre-verification
   ACCESS guards, immutable PERIOD handling and audit/idempotency evidence.
   Shared account/deletion checks must appear only once. No legacy ENTITLEMENT
   Put or usage reset is supplied by this helper.

`ClaimObservation` is frozen and contains immutable serialized call-local
snapshots. Its `inventory`, `owners` and `locators` properties return fresh copies;
mutating them cannot alter the evidence. Its representation omits account,
product, table, token hashes and rows. Do not persist or log the observation or
returned actions. All token inputs here are hashes; raw provider tokens remain
outside this helper.

The builder rechecks before returning and guards observed rows again in each
transaction action. A late competing claim or inventory change aborts the
transaction. Account deletion is caught by the caller's atomic deletion fence,
not by an omitted or advisory helper read. An ownership conflict requires new
observation and fresh provider verification; do not retry a stale proof against
newer state. An uncertain commit requires the modern writer's exact-operation
reconciliation before further provider work. The helper itself cannot establish
provider freshness or grant semantics.

Existing owner rows increment their revision; new pairs begin at revision one.
The account-lifetime ownership schema, deletion locators and retention remain
unchanged. No deadline extension, new persisted family or legacy coverage
approval is introduced. This does not decide how spent allowance survives a
cross-account restore after approved account erasure.

## Validation

`AMT_AUTHORITY_INTEGRATION=1 /tmp/amt-account-privacy-venv/bin/python -m pytest -q
tests/shared_purchase_ownership/test_claim_actions.py
tests/shared_purchase_ownership/test_lifecycle.py`: **46 passed** against isolated
SDK/Moto. Cases include copy isolation, native transaction composition, unchanged
usage, lineage and account conflicts, malformed/incomplete pairs, observed-row
changes during provider work, and owner/locator/inventory/deletion races after
preparation. These synthetic transactions are not live provider or rollout
acceptance.
