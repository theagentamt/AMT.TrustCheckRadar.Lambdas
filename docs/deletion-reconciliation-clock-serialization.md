# SECUR4ALL-332: deletion reconciliation clock serialization

The Dev Play token-deletion alarm reported a Lambda error on September 27, 2026.
Infrastructure's narrow log diagnosis found `TypeError: Object of type Decimal is
not JSON serializable` in the Play handler's final content-free JSON log. The
installed Play and V1 deletion packages use source
`39cce61623794a123e61d25f5248b0c081eccf4a`; their deletion handlers and shared
reconciler were unchanged through release `2ce864e58f2b3dab88b28cbf0bc5ccce1879de34`.
Root's read-only metrics found errors in both functions, not only Play. Those
observations do not mean every underlying erasure operation failed.

The shared scheduled reconciler reads `scanStartedAtEpoch` and
`lastFullPassAtEpoch` with the DynamoDB resource SDK, which returns numbers as
`Decimal`. A partial pass computes `fullPassAgeSeconds` from those numbers and
commits its cursor CAS. Play then fails in the `finally` JSON logger, replacing a
successful return with an invocation error; V1 catches its own logger failure and
raises its fixed unavailable error. A complete pass sets the age to integer zero,
explaining why complete-pass logs can succeed while partial-pass logs fail.

The correction converts only the two clocks to Python integers **after** the
existing integral and future-time checks. Fractional, Boolean and future stored
clocks remain invalid. Persisted values, cursor identity/revision, scan bounds,
account allowlists, receipt fields, deletion logic and retention are unchanged.
There is no generic JSON `default=str`, exception swallowing, alarm suppression,
provider retry or fabricated completion. The scheduled response and its log now
both carry numeric integer age. Genuine store/transaction errors still fail.

## Local validation

Fourteen new SDK/Moto cases exercise both actual scheduled handlers with real
DynamoDB-deserialized checkpoint numbers: partial then completed pass, previous
full-pass present/absent, committed component receipt and cursor, preserved
unrelated rows, no-time budget, malformed clocks with unchanged checkpoint, and a
real storage-failure path whose public diagnostic remains fixed. This is local
SDK/handler evidence, not a new deployed Lambda invocation.

```sh
AMT_AUTHORITY_INTEGRATION=1 /tmp/amt-live-deletion-venv/bin/python -m pytest \
  tests/shared_check_authority/test_deletion_clock_json.py \
  tests/shared_check_authority/test_deletion_worker.py \
  tests/shared_check_authority/test_deletion.py \
  tests/shared_play_lifecycle/test_runtime_workers.py -q
```

Result: **84 passed**. The independent reviewer separately ran all 14 new cases.
No customer data, AWS invocation, configuration or artifact was changed by these
tests. Subsequent installation and recurring runtime/alarm recovery require
infrastructure-owner evidence; local success alone does not close the incident.

## Artifact boundary

Both `play_token_deletion` and `v1_authority_deletion` require the shared correction.
Current release packaging also contains later shared-finalizer/recovery changes,
so a fresh broad package build must not silently upgrade them during this incident.
The paired archive handoff must enumerate exact installed baseline hashes and all
changed members before deployment. No sibling Play ingress/lifecycle or V1
consumer/entitlement package needs a behavioral upgrade to correct this clock.
The reviewed infrastructure selection must preserve their installed versions and
all current gates, policies, aliases and account scopes.
