# Store11 connected preparation and next foreground verification

## Completed Dev observation

Reviewed Android implementation `d0a8c36a8262b1144456797daf2fb41bc717febb` and
handoff `f4b07ea25a7f5ec6a0cdb252e916b77bb10f5ea9` were integrated through
[PR79](https://github.com/theagentamt/AMT.Android.TrustCheckRadar/pull/79) into
release-V01 merge `62a9d1b53dc9cbf7cadb18ac5b28687248e572fe`.
The signed uploaded AAB is SHA256
`1624e5fa8e9c30555625793936faba8990ee209035feae70ab1101593f34ee0e`.
Version11 was published only to the existing internal track after fresh edit
inventories qualified it unused, and other tracks/tester metadata were preserved.

The isolated emulator-5558 received its actual Store update without clearing data
or changing accounts. All four installed APK signatures, canonical package/version,
nondebuggable status, Play installer and referenced Dev authentication resource
passed. The installed base DEX matches the authenticated Google-generated APK.
The documented public-code comparison preserves original method bodies, with added
licensing classes and two qualified metadata-only differences. Whole universal APK
and installed split hashes are different artifacts. Emulator-5556 was preserved;
no physical hardware was discovered or tested.

Actual startup restored the supported signed-in session. The visible identity
matched the selected Dev tester. The authoritative snapshot qualified the current
device as active, with basis `none`, reason `EXTERNAL_ACCESS_UNAVAILABLE`, and all
allowance fields null. Null does not mean zero, a trial or a paid grant. The separate
legacy device panel's “Connection not checked here” did not require registration.

On October13 UTC, one Get Pro at 22:09:04.341016 reached a real Google subscription
sheet with the canonical Pro Monthly product and $4.99/month. The operator did not
tap Subscribe. Back at 22:09:29.590276 cancelled the sheet; the SDK callback code was
1, and the app displayed “Purchase cancelled / No access change.” Connection,
product query and launch codes were all 0. The previous app-not-configured-for-billing
error did not occur. Test-card/test-subscription markers were not observed.

Closed server aggregates corroborate two access HTTP200 responses during startup,
one during Pricing, and exactly one protected prepare HTTP200 in the checkout
window. Registration/verification counts in these windows were empty, and no
qualified failure classifier appeared. Aggregates cannot identify an individual
request/account; absence of events alone cannot prove absence of calls or writes.
This is real Dev preparation/open-cancel acceptance, not purchase/acknowledgment,
paid entitlement, Restore, renewal or RTDN acceptance.

Post-observation readback at 22:11:07.289615Z confirms handoff6, access17 and cleanup17,
with preparation/authority enabled, verification/lifecycle disabled, RTDN closed,
prior cleanup coverage retained, and protected configuration/routes/security/
capacity/schedules/streams unchanged. The prior approved tester replacement changed
only GET-only access selection, preparation selection and an additive cleanup union;
trial base admission stayed unchanged.

## Read-only next-stage qualification, not permission to execute

Fresh qualification at 22:17:45.693443Z finds the same deployed handoff6 code hash
`lRlJaVYOIIOGqpQml/HsF1VyyuCetakXoFdPGtUT9C0=` and sole selected Dev tester.
Actual Google catalog GET confirms ACTIVE product `trustcheck_radar_pro_monthly`,
base plan `pro-monthly`, P1M, US availability and $4.99. Ownership control inventory
is VERIFIED_COMPLETE. The existing credential reference has a current version;
existing SDK authentication/catalog access succeeds. These checks do not establish
runtime proof/order/acknowledgment permission or behavior without a genuine purchase.

The exact proposed configuration delta is only
`PLAY_HANDOFF_ENABLED=false -> true` on the existing handoff Lambda, followed by
immutable configuration-version publication and the existing live alias update.
`PLAY_CATALOG_P1M_VERIFIED`, `PLAY_REQUIRE_TEST_PURCHASES`, authority and preparation
are already true. No code, role, route, resource, capacity or background schedule
change is required by the existing foreground implementation. Activation is NOT
approved or applied by this document.

The same flag admits PURCHASE and RESTORE intents and automatic current-owner proof
reconciliation; it is not a PURCHASE-exclusive configuration gate. Operator Restore
remains excluded from the proposed test. The handler requires authenticated adult/
active-account/device/ownership checks, trusted Google testPurchase proof, matching
catalog and funded order before durable paid authority. It acknowledges on the
server only after the durable receipt and fresh account/device/ownership checks.
Lifecycle/RTDN can remain disabled for this foreground sequence. The client must
refresh authoritative access and must not acknowledge or infer a local grant.

## Smallest proposed owner-approved sequence

1. Keep the existing app session and Google Play account; no account or tester-list
   changes are proposed. The owner's approved Google tester statement is accepted.
   Internal-track installation and billing license testing remain different scopes.
   The existing API discovery exposes track-testers operations but no license-testing
   membership lookup; independent zero-charge status was not established.
2. Obtain new approval for the one-flag Dev foreground verifier activation, its shared
   reconciliation/Restore admission boundary, and one conditional zero-charge test
   transaction on emulator-5558. Preserve lifecycle/RTDN/trial/other accounts/gates.
3. Under that new approval only, reconcile the exact current source/config/sole tester,
   active device, cleanup, inventory and catalog, review the bounded plan and read back
   the activation. No pending activation plan may silently broaden IAM or permissions.
4. Open checkout once under the newly approved scope and inspect the actual purchasing
   Google account plus an explicit test-only/no-charge notice and test payment instrument
   (for example Test card, always approves). Do not use a real card. If those facts are
   absent or ambiguous, CANCEL and report; do not purchase or ask for another blind
   same-account retry. Prior approved-tester evidence need not be re-entered.
5. Only when zero-charge conditions are positively observed and the new transaction
   approval covers them, permit one test confirmation. Qualify actual SDK purchase,
   protected verify, trusted provider test marker/order, durable ownership/authority,
   server acknowledgment, fresh access/period/nullable counters and receipt replay
   without replenishment. Keep tokens/proofs/identity/private payloads out of evidence.
6. Re-read held protected gates and restore the verifier to its agreed closed state
   when the bounded test ends. Closing a Lambda gate does not cancel Google's test
   subscription or erase entitlement/ownership records. Any provider-side test-subscription
   cancellation or data cleanup needs explicit scope; do not reset counters or existing
   user data as a testing shortcut.

`PLAY_REQUIRE_TEST_PURCHASES=true` rejects non-test proof before grant or acknowledgment;
it cannot prevent a real Google charge if the operator confirms a real payment method.
Real charges, refunds, Restore, lifecycle/RTDN activation and new credentials/grants
remain excluded. Test-period timing, interruption, acknowledgment failure and background
renewal/revocation are not claimed by the completed open-cancel observation.

This reuses existing Lambda/API/DynamoDB/Secrets Manager components and per-request
usage; no always-running service or new polling schedule is proposed. Google charge
avoidance requires the actual test instrument, not an infrastructure cost estimate.

## Open acceptance and durable references

Maintain existing [ATCR-112](https://andmorethings.youtrack.cloud/issue/ATCR-112) and
[ATCR-114](https://andmorethings.youtrack.cloud/issue/ATCR-114) for transaction/authority
and assembled-system acceptance. [ATCR-175](https://andmorethings.youtrack.cloud/issue/ATCR-175)
owns the old provider configuration blocker; its resolution must cite the successful
real sheet rather than claim payment completion. Physical-only cases remain explicitly
in [ATCR-148](https://andmorethings.youtrack.cloud/issue/ATCR-148). Android's ATCR-169
component completion and later cross-component/payment qualification are distinct.
This evidence does not promote main or establish UAT/Production release readiness.

The privacy-safe machine evidence is
[evidence/play-v11-connected-verification.json](evidence/play-v11-connected-verification.json).
Private actor references, edit journals, credentials and raw responses are not committed.
