# Device Recovery MVP Runbook

## Purpose

This runbook defines the disabled Lambda contract for recovering users affected by stale or incorrect device bindings under the one-active-device-per-account policy.

The legacy `scripts/device_binding_recovery.py` writer predates the authoritative
pointer transaction and must not be used for live mutation. It is retained only
for offline inspection/dry-run compatibility until it can be removed. Every live
binding writer must use the Lambda transaction described below.

## Scope

The operator route is support/admin-only. A separate consumer route is present
but remains disabled until recovery policy, audit retention, and the coordinated
five-artifact release are approved.

## When To Use This Runbook

- user changed devices and normal registration did not recover access
- active device binding appears stale or incorrect
- support needs to clear the current active binding
- support needs to reactivate a known previous device binding

## When Not To Use This Runbook

- the user is signing in on the currently active device and normal flow works
- support cannot verify the user's identity
- support does not know which account is affected

## Supported Recovery Actions

- `RESET_ACTIVE_BINDING`
- `RECOVER_BINDING`

## Action 1: `RESET_ACTIVE_BINDING`

Use when:

- support wants to remove the current active binding and let the user retry registration normally
- support is unsure which previous device should be restored

Effect:

- current active binding becomes `INACTIVE`
- `deactivatedAt` is set
- `expiresAt` is set for TTL cleanup
- no new active binding is created by the support action itself

Expected next step:

- user retries login/device registration on the intended device

Legacy inspection only:

```bash
python3 scripts/device_binding_recovery.py \
  --action RESET_ACTIVE_BINDING \
  --account-id user-123 \
  --operator-id support-1 \
  --table-name <device-bindings-table> \
  --region us-east-1 \
  --dry-run
```

## Action 2: `RECOVER_BINDING`

Use when:

- support knows the exact existing `bindingFingerprint` that should become active again

Effect:

- target existing binding becomes `ACTIVE`
- if another binding is currently active, it becomes `INACTIVE`
- system never leaves multiple active bindings

Expected next step:

- user retries on the restored device if needed

Legacy inspection only:

```bash
python3 scripts/device_binding_recovery.py \
  --action RECOVER_BINDING \
  --account-id user-123 \
  --binding-fingerprint fp-1 \
  --operator-id support-1 \
  --table-name <device-bindings-table> \
  --region us-east-1 \
  --dry-run
```

## Recommended Process

1. Verify the user through the normal support verification process.
2. Confirm the affected `accountId`.
3. Prefer `RESET_ACTIVE_BINDING` unless support is certain which existing binding should be restored.
4. Invoke the AWS_IAM-protected `POST /device-recovery` route from an exact
   allowlisted principal. Do not run the legacy CLI without `--dry-run`.
5. Confirm the Lambda transaction completed.
6. Confirm the returned result.
7. Ask the user to retry on the intended device.
8. Record the action in the support ticket.

## Dry-Run Example

```bash
python3 scripts/device_binding_recovery.py \
  --action RESET_ACTIVE_BINDING \
  --account-id user-123 \
  --operator-id support-1 \
  --table-name <device-bindings-table> \
  --region us-east-1 \
  --dry-run
```

## Possible Results

- `NO_ACTIVE_BINDING`
  - no active binding existed to clear
- `CLEARED`
  - active binding was successfully deactivated
- `RECOVERED`
  - requested prior binding was restored as active
- `ALREADY_ACTIVE`
  - requested binding was already the active one

## Operational Rules

- never manually create a new binding during support recovery
- never leave more than one active binding
- do not store raw device identifiers outside the approved binding model
- always record:
  - `accountId`
  - `operatorId`
  - action used
  - `bindingFingerprint` if applicable
  - result
  - timestamp
  - support ticket reference

## Lambda authorization and consistency contract

- `POST /device-recovery` requires API Gateway `AWS_IAM` and an exact ARN in
  `DEVICE_RECOVERY_ALLOWED_PRINCIPAL_ARNS_JSON`. JWT, legacy claims, and
  `principalId` are rejected.
- `POST /v1/users/device-recovery` targets only the Cognito access-token `sub`,
  requires signed `auth_time` no older than 300 seconds, and accepts an exact
  UUIDv4 idempotency request.
- Both routes and normal device registration serialize changes through
  `PK=USER#<sub>, SK=ACTIVE_BINDING` with `stateVersion` conditions.
- `bindingFingerprint=NONE` is the authoritative reset sentinel. GSI1 is a
  legacy discovery path only and cannot establish uniqueness.
- Profile-active and fixed deletion-fence checks run inside the same DynamoDB
  transaction as pointer and device changes.
- Consumer recovery remains disabled while
  `DEVICE_SELF_RECOVERY_ENABLED=false`, policy status is `pending`, or audit
  retention is not exactly the approved Dev value of 90 days. Retry receipts stay
  seven days and rate state stays 24 hours; these durations are distinct.
- Activation requires one immutable release of `device_recovery`,
  `device_registration`, `history_read_api`, `history_mutation_api`, and
  `conversation_analysis`, because all readers must enforce the pointer.
