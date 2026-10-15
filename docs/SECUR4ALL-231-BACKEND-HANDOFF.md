# SECUR4ALL-231 backend completion and release handoff

This record maps the Lambda portion of SECUR4ALL-231. It does not activate the
trial, deploy a runtime, call a provider, or declare assembled release readiness.

## Retained Dev behavior

- Trial activation is explicit and server-authoritative: seven days or ten
  completed checks, whichever occurs first.
- `TRIAL_HISTORY` is account-bound and has no TTL. Reinstall, active-device
  replacement, concurrent activation, research enrollment/withdrawal/re-enrollment,
  and an expired retry cannot create a second clock or allowance.
- Activation and every external admission repeat the active account, adult,
  deletion, device, entitlement, allowance, replay/race, rate and in-flight
  checks. Partial, failed, blocked, invalid, unsupported and unavailable outcomes
  release the reservation without charging.
- Exhausted or expired trial authority returns an authenticated snapshot with
  `externalChecksAllowed=false`; it never silently activates another trial.
  Built-in checks, recovery playbooks and privacy/account operations remain
  authenticated client/application capabilities and do not depend on this
  external-provider entitlement.
- Account deletion inventories and deletes the trial history, source, period,
  receipts, counters and unknown future rows. No device-local reinstall or
  research state acts as eligibility authority.

The store identifiers and broader paid lifecycle still open in SECUR4ALL-76 are
not trial defaults and do not block this backend trial scope. SECUR4ALL-241 is
Done; no legacy FREE/bonus row can become current trial authority.

## Automated evidence

Run the actual SDK/Moto authority suites with Python 3.14:

```bash
AMT_AUTHORITY_INTEGRATION=1 python3.14 -m pytest -q \
  tests/shared_check_authority/test_entitlements.py \
  tests/shared_check_authority/test_entitlement_http.py \
  tests/shared_check_authority/test_snapshot_integrity.py \
  tests/shared_check_authority/test_transactions.py \
  tests/shared_check_authority/test_deletion.py
```

The focused additions prove one durable activation during a deterministic
transaction interleaving that models two first activations. This is SDK/Moto
transaction evidence, not real threads or live-AWS concurrency. They also prove
unchanged eligibility and counters across fixture-only research row changes
(join, withdrawal pending, withdrawn and re-enrollment with a new epoch), and an
authenticated fail-closed snapshot after the seventh-day deadline. Existing
cases cover device replacement, deletion/activation races, corrupted retained
history, ten-check exhaustion, complete-only charging, ambiguous responses,
account isolation and full authority-partition deletion.

The research transition proof deliberately mutates the independently owned
research row as a fixture; it is not an entitlement-to-research handler
integration. The separately isolated campaign participation handler suites cover
their own join/withdraw/re-enroll behavior. This separation is the product
contract: neither service calls the other to calculate authority.

Historical live Dev evidence on 2026-09-20 used one disposable synthetic account:
explicit 7-day/10-check activation passed; one complete result charged one; a
partial result charged zero; retry/reconciliation did not duplicate the charge;
expiry cleanup and authority deletion completed. General admission and trial
gates were restored inactive and the synthetic identity was removed.

## Later release qualification

[SECUR4ALL-334](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-334)
already owns the assembled current-authority matrix in Backend V1-5. Reuse it for
fresh, exhausted and ended-trial cases; do not create another release umbrella.
Record exact Lambda/infrastructure artifacts, configuration and emulator/client
versions. Use disposable synthetic accounts and CI/CD activation/rollback only.

Release evidence must show: one explicit clock; ten complete-check cap; zero
charge for partial/failed/inconclusive work; stable receipt and counters after
retry; external denial after exhaustion/expiry; research independence; account
and device isolation; deletion of eligibility; and restored inactive gates with
no unrelated data changed. Provider calls, UAT deployment and physical-device
testing remain pending unless separately authorized.
