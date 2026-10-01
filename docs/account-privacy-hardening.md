# Account deletion transport and reconciliation readiness

SECUR4ALL-200 source increment; runtime remains disabled. The existing pending-only
transport is pinned in `contracts/account-deletion/1.0.0-candidate.1` for Android.
There is no overall COMPLETE response, identity finalizer or full-account export in
this increment. Existing policy/inventory/completion gates remain mandatory.

The HTTP parser now rejects ambiguous duplicate keys, Boolean/float schema versions,
encoded/non-string/oversized bodies, non-JSON values and hidden raw query targeting.
Component receipt versions must be exact integral non-Boolean values, with completion
at or after the original request. Provider/SDK exception text is not copied into
HTTP/stream/scheduled logs; scheduled failures still raise fixed errors for retries.

Scheduled reconciliation now processes at most ten commands and starts no further
scan/command below ten seconds of remaining Lambda time. It saves an existing
lifecycle checkpoint before each command with `passHadFailures=true`. A timeout then
continues after that account rather than indefinitely starving every later account.
Successful work clears the provisional flag only if no earlier failure affected that
pass. Poison commands increment fixed failure counts and the cyclic scan revisits
them on the next pass. No command ID/content/exception is emitted in those metrics.

Only a fully traversed pass without command failures updates the clean-pass timestamp.
Ending a failed pass resets the traversal for a fresh attempt, without erasing the old
clean-pass age. A clean traversal is not proof every deletion component completed:
partial work and policy-blocked components retain their separate counters/receipts.
Existing bounded components still perform up to 100 row operations per step; a
single slow component can exhaust runtime. This increment prevents starvation but
live deadline/throughput/failure-alarm qualification remains required before enablement.

New fixed metrics in `AMT/TrustCheckRadar/AccountData`, Environment dimension:
`AccountDeletionReconciliationCommandFailures` and
`AccountDeletionReconciliationPassFailures`. Alert on either greater than zero;
retain Lambda failure and existing clean-full-pass age alarms. Checkpoint writes are
on the existing lifecycle row and introduce no additional account-data store.
