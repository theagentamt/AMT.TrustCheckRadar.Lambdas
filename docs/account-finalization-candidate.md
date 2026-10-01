# Account deletion finalizer candidate

This source-only module is disabled by default. It performs no live calls unless
an integrating handler explicitly enables it with reviewed inventory pins. No
deployment, Cognito deletion, inventory approval or activation was performed.

## Authoritative evidence

The metadata-only ledger marker has key `INVENTORY#<environment>` /
`ACCOUNT_DATA_INVENTORY`, with exact fields `recordType=ACCOUNT_DATA_INVENTORY`,
`schemaVersion=1`, positive `revision`, `environment`,
`coverage=VERIFIED_COMPLETE`, `manifestSha256` (64 lowercase hexadecimal),
`requiredComponents`, `usernameIsSubVerified=true`, `approvedAtEpoch`, PK/SK.
This is deliberately outside the writable `LIFECYCLE#<environment>` checkpoint
partition. The worker needs GetItem and transaction-only ConditionCheckItem, not
PutItem or UpdateItem, on the inventory partition. There is no automatic writer.

The finalizer pins the expected manifest and revision and requires the command's
original `occurredAtEpoch` **strictly greater** than `approvedAtEpoch`. A material
manifest/revision change must advance the approval epoch. Pending older requests
must be drained or explicitly requalified through controlled migration. Never
rewrite their operation ID, original time or deadline to pass this check.

The admission handler can share `validate_inventory(...)` and
`inventory_condition(...)`. It must apply the same strict approval-time ordering
and include the exact inventory condition in the request transaction. The helper
returns a native-value DynamoDB operation; serialize it when using a low-level
client. Creating a marker requires independently verified inventory, writer-fence,
cleanup, identity-mapping and restore acceptance; passing a test is not that proof.

All ten upstream component receipts are mandatory: SESSION_REVOCATION,
DEVICE_BINDINGS, DEVICE_RECOVERY, ANALYSIS_ABUSE, HISTORY, CAMPAIGN,
CAMPAIGN_OUTBOX, ENTITLEMENTS, V1_AUTHORITY and USER_PROFILE. Each must have its
exact existing shape, match the account/environment/original operation/time,
have a completion time between request and now, and remain inside its approved
120-day informational receipt period. No campaign component is skipped.

## Identity and completion

`Finalizer.finalize(original_requested_command)` verifies the durable command and
proofs with strongly consistent reads. A transaction condition-checks the complete
command, inventory and receipt field values before any identity deletion.

AdminGetUser must return the exact account as Username and exactly one matching
`sub` attribute. UserNotFound is accepted as absence only after the verified
inventory's username-is-sub assertion and all upstream proofs. AccessDenied,
network failures, duplicate/mismatched identity attributes and other errors fail
closed. No contact/profile attributes are retained or logged.

AdminDeleteUser targets only that verified username in the configured pool. An
uncertain response leaves the command pending; a later invocation reconciles the
same mapped identity's absence. This does not promise ongoing authenticated
consumer polling after account removal.

After deletion, another transaction rechecks every inventory/receipt proof and
the original command, writes the existing exact IDENTITY component receipt, and
replaces the fixed command with `status=COMPLETE`,
`eventType=account.deletion.completed`, `completedAtEpoch` and `retainUntilEpoch`.
Changing the event type keeps request-only workers from treating completion as
another deletion request. A lost transaction acknowledgment is reconciled by a
strongly consistent read of that exact completed fence; it cannot fabricate a
completion. The integrating handler still needs a versioned public contract.

The completed fence is a durable suppression control. `retainUntilEpoch` records
the already approved 120-day informational policy and is **not** a TTL or physical
expiry promise. This module neither removes receipts nor retires the fence.
Retiring suppression remains blocked until verified backup/replay coverage and a
separately qualified cleanup path permit it. An intact completed fence remains
safe internal idempotency evidence after informational receipts expire.

## Integration and acceptance still required

The orchestrating Lambda owns packaging, runtime/config flags, exact pool/manifest
pins, receipt/admission integration, completed-command filtering and public
contract behavior. Infrastructure owns scoped AdminGetUser/AdminDeleteUser and
ledger transaction permissions. Missing inventory prevents execution. Synthetic
Moto/Cognito-stub tests do not qualify real identity deletion, backup erasure,
restore suppression, physical device behavior, or a complete account lifecycle.

## Account-data integration

The account-data API requires the metadata inventory at admission, with the exact
manifest/revision and a request time strictly after approval; its transaction checks
that row while fencing the owned profile. Workers repeat the check before cleanup.
Owned PENDING_AGE_GATE profiles can request deletion; export still requires completed
onboarding under the owner's explicit decision.

`ENTITLEMENTS_TABLE_NAME`, `ACCOUNT_DATA_INVENTORY_MANIFEST_SHA256` and
`ACCOUNT_DATA_INVENTORY_REVISION` are required configuration. The separate
`ACCOUNT_IDENTITY_FINALIZER_ENABLED` defaults false and must also be enabled before
account deletion can be activated. No config value creates the authoritative marker.

Purchase cleanup performs bounded guarded transactions, then requires a subsequent
verified empty owned-partition pass. The ENTITLEMENTS receipt is written atomically
under the fixed command, purchase inventory and account inventory guards. Material
inventory changes require advancing the authoritative approval epoch and draining
or explicitly requalifying prior work; old request identity/time is never rewritten.

Identity finalization runs only in the durable stream/reconciliation path, after
profile/component cleanup. The synchronous HTTP request does not delete Cognito.
A terminal fixed fence denies ordinary authenticated status/retry. Duplicate old
stream events recognize the exact terminal fence and skip cleanup; the V1 authority
worker uses the same strict terminal-fence validator. Its ZIP now requires the
shared_account_finalization module. Campaign worker terminal-event handling is
coordinated in a separate bounded campaign increment.

This source remains disabled. Verified legacy inventories, all writer/restore
fences, complete campaign coverage, deployed component compatibility and staging
end-to-end acceptance are prerequisites. A terminal fence is authoritative only
when written by the reviewed atomic finalizer; it is never provisioned to bypass
missing component evidence.

## Local validation of the integration

Python 3.14 full ordinary suite: 1,757 passed, 233 subtests, 16 separately run
integration skips. Isolated SDK/Moto: 43 finalizer cases, six receipt/lifecycle cases,
four admission cases, and 52 V1 deletion/worker regressions passed. These cover
inventory/proof races, exact ownership, same-second approval rejection, terminal
replay, disabled identity calls and provider acknowledgment uncertainty.

The HTTP acceptance regression confirms concurrent identity completion does not
turn a committed accepted request into an error by re-polling after cleanup.
Empty inventory pins load safely while disabled. compileall, shellcheck and diff
check passed. Python 3.14 source-only account-data/V1 deletion packages build;
packaged imports and the account-data disabled/no-AWS response were verified.
No live AWS operation or native dependency/deployment qualification is claimed.
