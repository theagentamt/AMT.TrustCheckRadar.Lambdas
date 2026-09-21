# Purchase ownership lifecycle candidate

This is source for SECUR4ALL-200/236, disabled unless
`PURCHASE_OWNERSHIP_CANDIDATE_ENABLED=true`. No deployment, activation, migration or
Google Play call was performed. This does not finish billing or account deletion.

The owner approved erasing an old account's local purchase bindings and restoring
to a new account only after fresh store verification, without two active owners.
The orchestrator clarified that exact binding cleanup under the durable old-account
deletion fence may precede remaining identity/backup erasure. That is never reported
as whole-account completion. An existing other-account binding always conflicts;
there is no in-place transfer, even if that account has requested deletion.

## Writer and exact stored records

The candidate freshly queries Google `purchases.subscriptionsv2.get` for the
configured package and follows every `linkedPurchaseToken`, bounded to eight total
tokens. Cycles, longer chains, an unreadable predecessor or an unexpected product
fail closed. The head must have an active/grace/canceled-with-unexpired-access
status and parseable start/expiry. Raw tokens stay in request-local memory and
provider calls; only SHA-256 hashes reach the ownership store. No rejected-token
ownership is created. Google's guidance explicitly requires handling predecessor
entitlements to prevent duplicate grants:
https://developer.android.com/google/play/billing/security

Each token uses the existing `TOKEN#sha256 / IDEMPOTENCY` key, with exact fields:
`recordType=PURCHASE_OWNERSHIP`, `schemaVersion=1`, monotonic `revision`, `accountId`,
`purchaseTokenHash`, `platform=google_play`, `productId`, `verifiedAtEpoch`, PK/SK.
The reverse locator is `USER#account / PURCHASE_TOKEN#sha256`, exact fields
`recordType=PURCHASE_OWNER_LOCATOR`, `schemaVersion=1`, `accountId`,
`purchaseTokenHash`, PK/SK. Both are account-lifetime records erased during approved
account deletion, with no new post-deletion retention or TTL.

One DynamoDB transaction checks the claimant's active age-verified profile and
absence of the fixed deletion command, then conditionally writes all token locks,
all reverse locators, and the entitlement. Existing same-owner locks use revision
compare-and-swap. Existing entitlement source fields are compared atomically so
slow re-verification cannot overwrite a concurrent usage debit. A race produces
an unconfirmed result, never a fabricated accepted replay. Candidate entitlement
updates omit raw order IDs. This does not wire the legacy paid row into the V1
shared check authority or change the approved 200-check plan.

## Coverage, cleanup and export hooks

`PURCHASE#CONTROL / OWNERSHIP_INVENTORY` is a prerequisite for candidate activation,
cleanup and export. It has exact fields `recordType=PURCHASE_OWNERSHIP_INVENTORY`,
`schemaVersion=1`, `revision>=1`, `coverage=VERIFIED_COMPLETE`, `environment`, PK/SK.
No code here sets that marker. Setting it requires a separate reviewed migration
that finds and validates all old token records, converts approved active bindings,
creates every account locator, and fences every old writer. Old-shaped token rows
fail closed; missing coverage can never be treated as an empty account.

`OwnershipStore.delete_owned_batch(command, limit=20)` verifies the exact durable
deletion command, reads the bounded account partition, and removes owned token plus
locator together under transactional command and inventory-revision guards.
Legacy ENTITLEMENT/ENTITLEMENT# and USAGE# rows are erased under the same fence;
unknown row families or missing/corrupt ownership targets block completion. A page
with rows always returns `complete=false`; a subsequent empty read is required.
The caller owns the ENTITLEMENTS component receipt and must write it in a guarded
transaction using the same command and inventory revision. This helper never
writes a completion receipt or claims identity/backup removal.

`OwnershipStore.owned_page(account, cursor=None, limit=20)` is an internal adapter,
not an unauthenticated API. The export handler must recheck authenticated account,
device and deletion fence for each page and protect the cursor in its capability.
It returns only `platform`, `productId`, `verifiedAtEpoch`, plus an internal cursor.
It validates every locator target; missing or wrong-owner targets fail coverage.
Legacy entitlement/usage records are separate export families, not silently omitted
by this token projection. All writes are conditional, content is absent from error
messages, and SDK exception details are not logged by the purchase handler.

## Infrastructure interface and remaining qualification

The candidate writer needs consistent GetItem on exact owned profile/fence,
token keys, account entitlement/locator keys and the exact inventory key.
Transaction-only PutItem is limited to `USER#*` and `TOKEN#*`; transaction-only
ConditionCheckItem covers users `USER#*`, ledger `ACCOUNT#*` and inventory. Cleanup
needs bounded Query on `USER#*`, GetItem token/ledger/inventory and transactional
DeleteItem on `USER#*`/`TOKEN#*`, with fixed command/inventory ConditionCheckItem.
Export needs only bounded account-locator Query and target GetItem. No scan,
provider access or mutation belongs in its consumer role.

Required before activation: migration/coverage evidence, every writer fence, the
ENTITLEMENTS hook/receipt, paid-authority integration, IAM review, real store
qualification (including linked-token replacement and unavailable old tokens),
revocation/expired-subscription reconciliation, and end-to-end account erasure and
restore tests. The implementation refuses an expired head but does not yet revoke
an existing paid authority: that broader billing lifecycle remains a blocker.
