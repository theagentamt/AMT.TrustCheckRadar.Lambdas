# Store11 Dev verification: approved step 1

The owner approved selected Dev verification and one supported payment-details
inspection at 2026-10-14T00:46:29Z. This permits existing-test-proof automatic
reconciliation, entitlement persistence and server acknowledgment. No new
subscription transaction, including a zero-charge test transaction, or operator
Restore was approved. The earlier [connected preparation record](play-v11-connected-verification-handoff.md)
remains a historical record of closed verification and successful open/cancel.

## Verified activation

Live readback at 2026-10-14T01:02:04.370058Z confirms the existing handoff Lambda's
unweighted live alias is version 7, with unchanged code hash
`lRlJaVYOIIOGqpQml/HsF1VyyuCetakXoFdPGtUT9C0=`. The only configuration delta is
`PLAY_HANDOFF_ENABLED=false → true`. Preparation, authority, P1M catalog qualification
and test-only proof enforcement remain enabled. Lifecycle and RTDN remain closed.
The same sole selected Dev tester's metadata/eligibility and complete ownership
inventory were rechecked. Other 12 protected runtimes, four routes, security,
capacity, schedules, streams, additive cleanup coverage and trial admission remain
unchanged. Immutable closed version 6 remains available for rollback; rollback has
not been executed. No new infrastructure or recurring service was created.

Configuration and immutable publication each succeeded once. The pre-alias guard
stopped when latest revision metadata differed, while live version 6 stayed closed.
Read-only reconciliation qualified the exact desired version 7, desired latest
configuration and preserved state; the reason for revision rotation was not proved.
An independently reviewed alias-only continuation required the exact config/publish
journal, recorded version 7, original alias CAS, actor/inventory checks and matching
latest revision reads across those checks. The sole first alias call succeeded.
No configuration, publication or uncertain mutation was replayed. Twenty focused
synthetic guard tests and independent static review passed; those checks do not
establish real Google purchase verification, funded order or acknowledgment behavior.

## Bounded Android handoff and remaining acceptance

Android may preserve installed Store11, sessions and the protected emulator-5556,
then inspect exactly one actual payment sheet on emulator-5558. Record the item,
price/currency, explicit test/no-charge notice, payment instrument, renewal and
cancellation terms, then cancel and stop before Subscribe. Stop on an auth prompt,
identity/device/access failure, new terms, credential request or unexpected payment
method. No repeated checkout loop, account change or Restore is authorized.

Startup may now submit an existing same-owner purchased proof: the trusted Google
testPurchase marker and existing account/device/ownership/funding checks precede
authority and server acknowledgment. The shared gate also admits Restore intent;
this is disclosed configuration scope, not permission to execute Restore.
Test-only backend enforcement cannot prevent a Google charge if a real payment
method is confirmed. Zero-charge status is not yet independently verified.

The inspection is awaiting actual Android observation. Paid entitlement, actual
provider proof/order/acknowledgment, receipt replay, renewal and background processing
are unperformed acceptance, never passes. Keep [ATCR-112](https://andmorethings.youtrack.cloud/issue/ATCR-112),
[ATCR-114](https://andmorethings.youtrack.cloud/issue/ATCR-114) and
[ATCR-175](https://andmorethings.youtrack.cloud/issue/ATCR-175) open; physical-only cases
remain in [ATCR-148](https://andmorethings.youtrack.cloud/issue/ATCR-148).
This Dev activation is separate from release integration, main promotion and
UAT/Production readiness. Private identity, credentials, proofs, payloads and journals
remain outside the repository.

Privacy-safe evidence: [step 1 activation JSON](evidence/play-v11-verifier-step1-activation.json).
