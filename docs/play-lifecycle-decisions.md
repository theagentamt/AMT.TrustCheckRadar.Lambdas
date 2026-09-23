# Google Play lifecycle decisions and implementation sequence

This follows the approved modern handoff and purchase usage ledger. It does not activate background access or change approved retention. Google catalog is now verified: canonical package/product, active `pro-monthly`, P1M, US new subscribers and USD4.99. License-test proof, orders, acknowledgment and notifications remain unqualified.

## Approved transport retention boundary

Use authenticated Google Pub/Sub push notifications for prompt changes, plus bounded reconciliation and acknowledgment recovery for already-owned subscriptions. Notification types and event timestamps only request a refresh; current Google API proof determines the result. Verify Google's signature, exact audience, issuer, verified service-account email and subject, then exact Pub/Sub subscription resource and app package. Topic publishing must be restricted to the expected Google Play publisher. Duplicate/out-of-order delivery is normal.

Current token hashes cannot call Google's API. The owner approved choice 1 below on 2026-09-22; choice 2 remains an explanation of the rejected reliability tradeoff.

1. **Approved: encrypted recoverable purchase token while linked to a live account**, with purpose-limited head/ownership reference, next-attempt time, fixed bounded retry counters and acknowledgment status. Store no messages, URLs, payment data or full provider responses. Delete the token on account deletion; do not keep it in the post-deletion usage ledger. The approved upper bound is latest verified entitlement expiry plus seven days, refreshed only by new authoritative store proof. Known hold/expiry beyond that window loses scheduled recovery and relies on a later RTDN/foreground token; this reliability limitation remains explicit; no longer retention is authorized. Use a dedicated encrypted token table with no point-in-time recovery, on-demand backup or AWS Backup inclusion. The owner's 35-day backup exception applies only to minimized usage records, not raw token ciphertext. Account-deletion and explicit expiry workers must cover this separate store before activation. The scheduler must not revive an account, assign unknown ownership or transfer a subscription.
2. **RTDN-only transient handling**, retaining no recoverable token in our own store. This supports already-owned event-driven refresh but cannot guarantee missed-notification recovery, acknowledgment completion after delivery exhaustion or interrupted initial handoff. Pub/Sub retry/DLQ copies still contain the token; configure and disclose their finite retention before provisioning. It is not full SECUR4ALL-125 acceptance by itself.

Approval covers only the described encrypted token and deadline, not unbounded queue/DLQ copies. Queue/DLQ retention, backups, erase-on-account-deletion and export inventory must cover any newly retained token. Prefer not to duplicate raw tokens into an additional AWS queue if Google already owns bounded retries.

## Grace and deferred access

Google requires retaining subscription benefits during grace, including silent grace while the subscription can still be ACTIVE. The deployed foreground boundary requires current access expiry to equal immutable funded order end. This inactive source increment separates those clocks; the runtime keeps its existing closed configuration.

Owner-approved policy: keep the same funded-period remaining allowance while Google explicitly extends entitlement; do not issue another 200 or reset usage. Separate immutable funded dates from verified access expiry. To support extensions longer than seven days, the minimized usage record must remain until the latest verified entitlement expiry plus seven days, while preserving immutable funded dates and cumulative counters. This changes the earlier fixed funded-end retention boundary and is explicitly approved. The latest provider-verified end is the upper bound, not the maximum extension ever observed; a later authoritative shorter end must shorten retention. Revocation stops access immediately, but notification arrival time must never fabricate an end. If safe pending-accounting reconciliation cannot meet that deadline, the candidate remains inactive and reports the unresolved case. A genuinely new funded order can create a new 200-check period only after verified non-overlapping accounting proof. Exhausted allowance remains exhausted during grace.

Disabling configurable grace does not eliminate Google's mandatory silent grace, and a provider-side deferral can still extend access. The alternative is leaving unsupported transitions gated and keeping paid launch incomplete; it is not a completed entitlement policy.

## Safe source work that can proceed now

- Unwired authenticated RTDN parsing with strict bounded payloads, exact scope, no logging/persistence or grant from notification data.
- Current-head provenance for owned subscriptions. Account-lifetime head token hash must advance only through freshly verified compatible lineage and atomic pre-provider observation checks. An old owned predecessor/root match cannot revoke a newer active head.
- A typed trusted-worker mutation boundary using current owner/locator/inventory, pre-provider ACCESS comparison and exact account/deletion guards. Do not fabricate a user JWT or device binding for background work. Unknown-token events stay unresolved; the current system lacks an approved reverse mapping from Google's obfuscated account hash for interrupted initial handoff.
- Normal verified funded renewals and exact-current-head no-access transitions can reuse paired usage/accounting and immutable period rules. Revocation prevents new paid admission, leaves original-period pending settlement intact, does not refund used checks and does not activate a trial. The owner-approved grace/token changes remain gated until source and coordinated retention/deletion testing pass; extra durable operation retention is not implied by this approval.

## Source references

[RTDN reference](https://developer.android.com/google/play/billing/rtdn-reference), [authenticated push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions), [delivery semantics](https://docs.cloud.google.com/pubsub/docs/subscription-overview), [Google subscription lifecycle](https://developer.android.com/google/play/billing/lifecycle/subscriptions).

Google's purchase-token API availability after expiration does not itself authorize our retention. Pub/Sub acknowledgment is transport delivery completion; it is distinct from Google Play purchase acknowledgment and from a durable entitlement grant.

## Current inactive source boundary

`shared_play_lifecycle.notification` authenticates via an injected Google signature verifier and validates exact push scope; it is not a deployed authorizer. `tokens` prepares ciphertext-only conditional writes to a dedicated table and refuses logical-expired decryption; no KMS key, token table or backup policy is provisioned here. `reconciliation` refreshes already-owned current heads through the typed worker writer without a fabricated device session. Unknown initial ownership remains unresolved. No background handler, authenticated ingress, queue, schedule or acknowledgment worker is installed.

The approved active access extension keeps immutable funded dates and counters. Exact current-head inactive proof can disable paid admission while retaining original pending settlement when access deadlines do not shorten. A shorter verified end with no reservations updates both usage and token deadlines. **Shortening with outstanding reservations remains an explicit reconciliation failure and activation blocker**: this source does not claim immediate revocation and strict retention are both completed in that case. Missing global usage before locally proven expiry is never reconstructed or silently forgiven. A late old-account deletion after another owner's shortened global expiry can likewise require reconciliation; no new tombstone retention is invented.

Before activation, finish dedicated-store explicit expiry and account deletion, export/retention inventory and no-backup proof; configure exact KMS context/role boundaries; qualify RTDN identity/topic/subscription and retries, background acknowledgment, missed-event scheduling, interrupted initial handoff, and all coupled accounting consumers. Foreground `lifecycle_enabled` is an internal default-false constructor option, not a public request or an enabled runtime switch. The existing runtime does not supply token table/cipher or trusted lifecycle principals.

## Local validation and review

The affected authority/ownership/handoff/lifecycle SDK/Moto suite passed 434 cases before the final previous-period identity guard; the final lifecycle suite passed 16 cases including both mismatched-identity preservation cases. Consumer compatibility passed 113 SDK cases (message, recovery, feedback and export). The final ordinary suite passed 1,847 tests and 232 subtests, with 28 isolated integration modules skipped there. Compile and whitespace checks passed.

Nine archives passed local disabled-handler import smoke and exact shared-source byte comparison. Handoff includes full Python 3.14 Linux arm64 dependencies and the new internal lifecycle module; the other eight are source-only composition checks using host dependencies. The macOS handoff smoke substituted installed cryptography for the packaged Linux native library; this is not Linux dependency execution or a deployable coordinated artifact manifest. No archives from this increment were published or deployed. The existing immutable mobile candidate.1 files remain byte-identical. See [local validation evidence](play-lifecycle-local-validation.json).

Independent review closed the funded-versus-access overlap, malformed previous-period identity, and retained DynamoDB numeric-clock findings. Root independently reviewed the accounting helper authored by the reviewer; the reviewer separately reviewed the lifecycle writer, provider, notification and token-store boundaries.
