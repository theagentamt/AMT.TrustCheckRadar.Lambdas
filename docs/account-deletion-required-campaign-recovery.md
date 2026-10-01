# Account deletion requires durable campaign recovery

An enabled account-deletion runtime now requires
`CAMPAIGN_RECOVERY_WRITES_ENABLED=true` in its existing configuration validator.
Otherwise it could accept and fence an account without creating the campaign
recovery job/control required by campaign completion and identity finalization.
HTTP requests fail with the existing `SERVER_UNAVAILABLE` 503 response before
account work; stream and reconciliation configuration failures propagate for
retry. A disabled runtime retains `FEATURE_DISABLED` precedence.

This change does not enable a gate, qualify an inventory, change retention, or
change the public contract. Direct service construction remains compatible;
the deployed handler enforces the runtime requirement. The existing closed
f92285b50561397355a5fe2e466d297041336755 deployment can finish unchanged. Any
subsequent account-data runtime refresh requires its own reviewed artifact plan.

Validation:

- `python -m pytest -q tests/account_data_api/test_handler.py tests/account_data_api/test_service.py`:
  44 passed, 20 subtests passed. Five new cases exercise the real validator,
  including HTTP, stream, scheduled, enabled-valid and disabled behavior, with
  SDK/client/service creation forbidden on rejected paths.
- `AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q tests/account_data_api/test_admission_dynamodb.py`:
  8 real SDK/Moto cases passed, preserving admission transaction behavior.
- Default dependency build targeting Python 3.14/ARM64 for `account_data_api`
  succeeded. ZIP SHA256
  `cea7eeefab1c9e867db67293b6141b1694350fa1461dbd166316076005b12d6b`,
  47,679 bytes. Compared with the f922 archive, only `config.py` differs; no
  members were added or removed. Every archived Python member compiled locally.

Only the account-data artifact needs refreshing for this source change. No
package was uploaded, no runtime was updated, and no enabled AWS acceptance or
customer operation was performed by this validation.
