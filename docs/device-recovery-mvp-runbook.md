# Device Recovery MVP Runbook

## Purpose

This runbook defines the MVP support process for recovering users affected by stale or incorrect device bindings under the one-active-device-per-account policy.

## Scope

This process is for support/admin use only. It is intended for cases where the normal automatic device-registration flow does not resolve the problem.

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

Example:

```bash
python3 scripts/device_binding_recovery.py \
  --action RESET_ACTIVE_BINDING \
  --account-id user-123 \
  --operator-id support-1 \
  --table-name <device-bindings-table> \
  --region us-east-1
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

Example:

```bash
python3 scripts/device_binding_recovery.py \
  --action RECOVER_BINDING \
  --account-id user-123 \
  --binding-fingerprint fp-1 \
  --operator-id support-1 \
  --table-name <device-bindings-table> \
  --region us-east-1
```

## Recommended Process

1. Verify the user through the normal support verification process.
2. Confirm the affected `accountId`.
3. Prefer `RESET_ACTIVE_BINDING` unless support is certain which existing binding should be restored.
4. Use `--dry-run` first when possible.
5. Run the real recovery command.
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

## MVP Recommendation

- use the CLI/runbook first
- defer deployment of a privileged recovery endpoint until the team is ready for infra exposure and admin authorization controls
