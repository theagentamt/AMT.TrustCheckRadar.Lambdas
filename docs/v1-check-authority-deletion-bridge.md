# V1 authority deletion component — unwired candidate

This increment adds `shared_check_authority.deletion.AuthorityDeletion` and isolated DynamoDB transaction tests. It does not add a handler, change account-deletion completion requirements, create inventory metadata, rotate keys, publish a package, or activate a service. The three manually deployed inactive ZIPs remain pinned to source `44bdf31bdeb150b13c2cc3732421acbdc5b7bf1d`; this helper is separate follow-up source.

## Boundary and existing deletion contract

The component is **V1_AUTHORITY**, separate from legacy **ENTITLEMENTS**. Deleting V1 rows cannot satisfy legacy entitlement deletion. The existing `account_data_api.service.AccountDeletionService.request` transaction places `ACCOUNT#<subject>/ACCOUNT_DELETION` and fences the user profile. The helper requires the exact version-1 Dev request to match a strongly read authoritative ledger row. It can operate after account access is disabled; it does not call `load_authority`, require an active account, fetch provider data, or store raw URLs.

All V1 client/worker mutations in `shared_check_authority/core.py` use `_account_conditions`, including the global deletion-row absence check inside their transactions. Entitlement mutations in `entitlements.py::_commit` use the same conditions. `recovery.py::expire`, which intentionally knows no raw account identity, conditions its transaction on an existing ACCESS row whose state is not DELETING. Those requirements are essential to the empty-read/completion proof. Every future writer and recovery path must preserve an equivalent fence; privileged unguarded writes are outside that proof.

## Deletion behavior

`delete_batch(command, page_size=20, max_pages=2)` validates explicit limits (at most 20 data rows per page and four pages), reads the verified key inventory, and derives all account partitions using the existing `account\0<subject>` HMAC namespace. It atomically fences all existing ACCESS rows as DELETING for the deletion operation, removing any ACCESS TTL. It never creates a missing ACCESS row.

A ledger progress row pins the operation and inventory revision/fingerprints. Every page transaction checks the unchanged global deletion command, inventory, and existing-or-absent ACCESS fence. It deletes every row family in each partition, including pending receipts, non-expiring INFLIGHT counters, trial eligibility, operator audit, and unknown future sort keys. ACCESS is deleted last. Empty partition checks use strongly consistent base-table queries, not the eventually consistent pending GSI. A final guarded transaction writes the distinct V1_AUTHORITY receipt and removes progress.

A batch that exhausts its page budget returns `complete: false`; a durable caller must schedule continuation and monitor the existing deletion SLA. Repeating a batch starts at the remaining rows rather than advancing a cursor past potentially failed deletes. A valid existing component receipt returns `alreadyComplete: true`. SDK retries must be disabled (`total_max_attempts=1`). An ambiguous transaction response returns a fixed error; retrying the same command safely resumes or reads the committed receipt. No provider replay or quota settlement occurs during deletion.

The global deletion fence and eventual parent-completion gate must remain in force while any component is pending. A poisoned inventory/progress row cannot be skipped to declare completion. This library adds no automatic retry loop or scheduler.

## Verified key inventory is an activation prerequisite

The candidate reads one authority-table control row:

```json
{
  "PK": "V1#CONTROL",
  "SK": "HMAC_KEY_INVENTORY",
  "recordType": "V1_HMAC_KEY_INVENTORY",
  "schemaVersion": 1,
  "revision": 1,
  "coverage": "VERIFIED_COMPLETE",
  "issuedKeys": {"k1": "<SHA-256 fingerprint of key material>"}
}
```

This is a **trusted, independently verified, append-only history of all issued HMAC namespaces**, not a list synthesized from whatever keys happen to remain in the current secret. The coverage label alone proves nothing about historical completeness. Before enabling writers or this component, review the initial issuance/migration history, establish this inventory, and enforce it in all authority key loaders and rotation tooling. Those integrations are not implemented here. No inventory row or secret value has been provisioned by this increment.

The supplied retained keyring must match the inventory exactly, including material fingerprints; missing keys, omitted historical IDs, changed material under an existing ID, an unverified inventory, or a revision change during an in-progress deletion fail closed. Missing old keys cannot be reconstructed and their partitions cannot honestly be claimed as deleted. Freeze rotation for an in-progress deletion or implement a reviewed migration/continuation procedure. The current keyring supports at most four retained IDs; reaching that limit requires a reviewed namespace migration, not silently dropping inventory or keys. Secret removal needs independent proof that every affected old namespace is empty across accounts and all dependent tokens/work are retired. This helper does not provide such a proof or retirement workflow.

## Retention and future integration gates

The constructor requires an explicit `receipt_retention_seconds=120*86400` solely to match the **existing** account-deletion component receipt validator. This does not approve new V1 result/trial retention or supersede the owner's pending retention decision. Receipt timestamps are integral, versions reject booleans, and unknown receipt fields fail validation. Progress has no TTL and remains until completion; its cleanup and operational escalation must be reviewed with the deletion ledger policy. The inventory contains no account identifiers. Component receipts/progress use the existing account-keyed deletion ledger and therefore remain linkable under that ledger's policy.

Before runtime wiring, jointly review and implement:

1. Inventory initialization and loader/key-rotation enforcement, including all historical namespaces and explicit lost-key handling.
2. An independent AWSCURRENT key loader usable after the account/global feature has been disabled; do not reuse the enabled-only authority factory as a cleanup gate.
3. A bounded deletion adapter with durable continuation/reconciliation, fixed identifier-free failure metrics, and the existing deletion SLA monitoring. TTL alone is not an exact retention or deletion guarantee.
4. Exact-table IAM: authority GetItem/Query, transaction-scoped UpdateItem/DeleteItem/ConditionCheckItem; ledger GetItem plus transaction-scoped PutItem/DeleteItem/ConditionCheckItem; exact HMAC secret GetSecretValue at AWSCURRENT. No provider invocation, user enumeration or arbitrary table access is required. Inventory initialization must be a separate privileged operation.
5. Add V1_AUTHORITY to the reviewed required-component and user-profile/identity completion gates, without replacing ENTITLEMENTS. Confirm existing legacy deletion components remain compatible before changing these gates.
6. Owner-approved V1 retention, active-device/account integration, and the other consumer activation gates remain outstanding. This helper alone does not make the consumer safe to enable.

## Focused validation

Run in the isolated Python 3.14 Moto environment:

```sh
AMT_AUTHORITY_INTEGRATION=1 /tmp/amt-check-authority-venv/bin/python -m pytest -q tests/shared_check_authority/test_deletion.py
```

The focused suite passes **33 tests** on Python 3.14. It covers bounded continuation, multiple retained keys, unknown/orphan row families, ACCESS-last deletion, TTL removal, exact command/receipt checks, dropped-key and rotation failures, ambiguous committed transactions, writer/recovery races between query and delete/completion, and stale recovery transactions after deletion. Synthetic credentials/identities only; no live AWS or secret reads.
