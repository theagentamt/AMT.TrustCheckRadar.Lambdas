# Freshly bound subscription root after stored expiry

This is the source/component handoff for [SECUR4ALL-343](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-343), based on release `034e566ac3f5a79b9034d9b50a5cbf145b11ac41`. It implements a narrow foreground purchase transition that the previous Handoff and authority writer both rejected. Deployment and live acceptance remain pending in [SECUR4ALL-195](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-195), [ATCR-112](https://andmorethings.youtrack.cloud/issue/ATCR-112) and [ATCR-114](https://andmorethings.youtrack.cloud/issue/ATCR-114). Physical cases remain in [ATCR-148](https://andmorethings.youtrack.cloud/issue/ATCR-148).

The fifth live attempt's aggregate `PURCHASE_TRANSITION_UNSUPPORTED` does not establish which original predicate rejected it. This implementation is independently justified by the reproduced source limitation; it does not identify that historical cause. All five transaction journals remain consumed. This page grants no new transaction, deployment, proof replay or activation authority.

## Admitted transition

The explicit proof-facing writer accepts only authenticated foreground `PURCHASE`, with a fresh active canonical Google Play proof, exact current-account binding, a single unlinked root, normal funded access end equal to funded period end, and no existing new-root ownership or locator. The captured old head's ownership and locator must belong to that same account and canonical product. Fresh ACCESS and device observations must remain unchanged.

Both old stored access expiry and the old immutable funded period end must be no later than the new funded period start; stored access must also have expired by current authority time. This proves a nonoverlapping transition in retained local authority, **not that the old Google subscription has actually terminated**. An inactive old source can qualify only with the same retained evidence. Shortened access cannot hide an overlapping funded period.

The existing allowance policy applies: 200 checks for the new verified funded period, no carryover. Old used/reserved counters, periods, ownership and locators are never rewritten. Existing new-root global usage retains its spending; outstanding reservations without a represented local period fail reconciliation. A retired/already-owned root cannot be revived through this path.

RESTORE, generic/background writers, replacement line items, out-of-app context, missing or different binding, extended/grace access, live overlap and missing/corrupt evidence remain blocked. Google documents new-token resubscription and separate out-of-app association context; this patch does not treat that context as verified account ownership. See [subscription lifecycle](https://developer.android.com/google/play/billing/lifecycle/subscriptions#resubscribe) and the [SubscriptionsV2 resource](https://developers.google.com/android-publisher/api-ref/rest/v3/purchases.subscriptionsv2).

## Atomicity and replay

`commit_verified_resubscription` builds new ownership claims itself and mints a private call-local capability bound to the account, exact captured ACCESS and exact decision object. Generic writers have no migration switch. The final transaction contains:

- Existing inventory, account/deletion, current-device and key/partition guards.
- Exact read-only conditions for old head ownership, locator, immutable period and global usage.
- New ownership and locator claims, normal purchase-global funding, new local period, exact old-field ACCESS compare-and-swap on its single Put, and immutable operation receipt.

There are no duplicate item actions. A different-root race has one ACCESS winner; a failed condition rolls back every new claim and grant. Existing exact request replay reconciles the receipt before migration absence checks; a lost commit response cannot fund twice. Conflicting request payloads remain rejected. Normal acknowledgment occurs only after durable authority/receipt and fresh ownership/access fences.

Old global usage may legitimately be absent only after its existing retention deadline and with zero reserved checks; the transaction then checks absence without recreating it. Before the deadline, or with reservations, reconciliation is required. Existing old global usage is checked against immutable period counters and boundaries and left unchanged. Later settlement continues against the old period.

## Fixed diagnostics

Original rejection sites attach only these finite predicates: `LINE_ITEM_REPLACEMENT`, `OUT_OF_APP_CONTEXT`, `PENDING_LINKED_LINEAGE`, `ACCESS_FUNDED_BOUNDARY`, `PAID_HEAD_OUTSIDE_LINEAGE`. The external HTTP/error mapping is unchanged. Unknown metadata becomes `UNKNOWN` without stringifying objects.

The separate `dev_play_transition_failure` event has exactly `event`, `schemaVersion: 1`, and `predicate`. It runs only in Dev with the existing Handoff gate enabled, exact HTTP API v2 POST verification route, verification-stage transition rejection. UAT, Production, wrong routes and closed gates are silent. It adds no provider calls and records no actor/request IDs, tokens, claims, proofs, provider fields, payloads or exception text. Logging failure cannot alter the response. Existing log collectors do not automatically recognize this new event; any future collection must explicitly validate its finite schema.

## Validation

The adjacent JSON pins source, tests and retained reports. All provider data is synthetic; DynamoDB integration uses Moto. The focused suite passed 200 cases locally and independently. It covers boundary equality, live/shortened overlap, wrong account/binding/ownership, absent or drifted evidence, deletion/device/inventory/ACCESS/usage races, concurrent roots and duplicate requests, lost commit replay, old reservation settlement, existing new spending, retention expiry, retired roots and generic/background/RESTORE rejection.

Independent review found that the initial prototype could accept extended access when new global usage already existed. The fix requires normal funded equality at the Handoff predicate, explicit proof API and private writer invariant. The original exploit is now denied; direct API and lifecycle-option regressions pass. The finding and resolution are retained in the review record.

Full isolated suite results: 2,775 default JUnit-reported tests, including subtests and 73 gated skips; 1,206 selected integration JUnit-reported tests, including subtests, with no skips. There were zero failures/errors across 63 child-process suites. Integration runs explicitly cover authority, ownership, lifecycle, Handoff, scripts, message consumer, result feedback and export. There is no standalone recovery-consumer test directory; its source packaging was checked, without claiming a dedicated integration-suite pass. These counts overlap the focused suite and must not be added as unique cases.

Python compilation, shellcheck and diff checks passed. Local source packaging built 41 dependency-skipped ZIPs and compiled 944 archived Python entries, with all six changed modules exact in their package layouts. Five disabled package imports passed with network connections forbidden and host jsonschema. These are source-layout checks, not dependency/ARM64 or deployment qualification. Dependencies, workflows, API/durable schemas, routes, IAM, retention and configuration were not changed.

## Later deployment and release procedure — not executed

1. Under separate deployment scope, qualify a fresh exact deployed baseline and construct the smallest reviewed overlay. The frozen deployed Handoff 9 writer contains complimentary retention overlays absent from generic source: `_commit` audit expiry handling, `_operation_sk`, and related initialization/read/complimentary receipt validation. Preserve those blocks and every unrelated payload/configuration change. **Do not replace the deployed writer wholesale with this source file or use the generic source ZIP as a deployment candidate.** No exact deployable overlay is prepared here.
2. Independently review exact source/artifact hashes, complete dependencies/runtime architecture, changed-file allowlist, aliases/configuration, rollback ZIP/configuration and all protected fingerprints. Preserve test-only actor/ownership/catalog/authority/preparation gates and closed lifecycle/RTDN gates. Existing rows remain schema-readable by the old writer, but exact rollback behavior/artifacts still need qualification.
3. After separately authorized deployment, read back exact code/configuration and guarded readiness without app endpoint replay. No automatic provider lookup, table scan or purchase is part of this handoff.
4. If a later separately approved real test occurs, verify installed app/account/device/baselines, actual explicit no-charge terms, bounded single-dispatch authority and exact-new cleanup. Observe strict acknowledgment and fresh authoritative access independently. Collect only fixed-enum bounded route/status evidence with ingestion and attribution limits. Never infer a cause from aggregate 409 or callback status.
5. Keep release/E2E work open until those observations occur. Out-of-app association requires a separately reviewed verified account-mapping design; do not broaden this patch to cover it. Physical-only acceptance remains deferred, not passed.

No deployment, runtime mutation, app endpoint, provider/proof replay, account/auth operation, emulator action, new purchase or main promotion occurred in this source task.
