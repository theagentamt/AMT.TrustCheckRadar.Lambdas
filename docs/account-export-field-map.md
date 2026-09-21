# Full-account export field map — implementation proposal

Status: reviewed-source mapping for SECUR4ALL-236, not an active export endpoint.
This maps exact existing fields. The new export's user-facing scope and observed-data
semantics remain subject to the current owner review. Missing coverage blocks a
complete export; an empty or unreadable store is never automatically "no data."

## Account-owned source families

| Source / access path | Proposed public fields | Exclusions / validation |
|---|---|---|
| users `USER#subject/PROFILE`, produced by `post_confirmation/app.py` and `age_attestation/app.py` | `email`, `given_name`, `family_name`, `phone_number`, `over_18`, `status`, `ageVerified`, `ageVerifiedAt`, `agePolicyVersion`, `createdAt`, `updatedAt` | Exclude PK/SK/sub and deletion internal linkage. Exact owned profile required, known nullable text/booleans/time validation. No arbitrary attribute passthrough. |
| Cognito `AdminGetUser` using verified subject-to-username mapping | existing standard attributes `email`, `email_verified`, `given_name`, `family_name`, `phone_number`, `phone_number_verified`, `custom:over_18`; public enabled/status/creation/update times | Do not export all returned `UserAttributes` blindly. Exclude sub/username, security/session metadata, unknown custom attributes. Compare mapping and stop on conflict. This read is not currently implemented. |
| device-bindings `USER#subject/DEVICE#…`, built by `device_registration/service.py::_build_active_item/_build_inactive_item` | `platform`, `osVersion`, `status`, `firstSeenAt`, `lastSeenAt`, `deactivatedAt` | No `bindingFingerprint`, PK/SK/GSI keys, account ID. `ACTIVE_BINDING` is control-only and establishes current binding; no independent public record. Skip logically expired inactive rows. |
| recovery control `USER#subject/RECOVERY#…` | `operation`, `result`, `status`, `completedAtEpoch` | Existing `_public_self_receipt` also emits fingerprint; **do not reuse it unchanged**. Remove fingerprint, payload hash, operation ID/key and expiry-control fields. Skip expired. |
| recovery control `USER#subject/AUDIT#…` | `actorType`, `action`, `result`, `occurredAtEpoch` | Exact approved 90-day audit shape; omit fingerprint/keys/operation linkage. RATE records are security internals, not export content. |
| legacy entitlements `USER#subject/ENTITLEMENT*` | `entitlementTier`, `subscriptionStatus`, `platform`, `productId`, `billingPeriodStartUtc`, `billingPeriodEndUtc`, `lastVerifiedAtUtc`, `isAccessGranted`, `monthlyScanLimit`, `remainingMonthlyScans`, `remainingCredits` | Use stored observed values after validation, not `_normalize_entitlement` defaults that can invent current state for missing records. Omit account/order/token hashes. Compatibility and product rows must be labeled by their source purpose, not summed as duplicate grants. |
| legacy entitlements `USER#subject/USAGE#…` | `periodKey`, `usedCount` (plus other counts only after actual producer/shape is inventoried) | External writer/shape remains unresolved; no inferred values or invented complete coverage. |
| token idempotency rows found through a validated account locator, `purchase_handoff/idempotency.py::build_idempotency_record` | `platform`, `productId`, `verificationStatus`, `normalizedStatus`, `billingPeriodStartUtc`, `billingPeriodEndUtc`, `updatedAt` | Omit `accountId`, `purchaseTokenHash`, token PK. No locator exists yet, so current coverage is incomplete. Do not scan the entire table in the consumer export role. |
| V1 authority `ACCESS` across verified retained-key account partitions | `state`, `basis`, `validFromEpoch`, `validUntilEpoch`, `activationKind`, `activatedAtEpoch`, `policyVersion`; separate public projections of `sources.trial/paid/complimentary` validity/state | Never expose `sources` wholesale: store references, principal/session hashes and internal revisions are not public fields. Inactive sources and effective source need truthful labels. |
| V1 authority `PERIOD#…` | `startEpoch`, `endEpoch`, `limit`, `usedChecks`, `reservedChecks`, `policyVersion` where present in exact current row schema | Validate source and existing exact field names before implementing; do not infer free credits from legacy rows. No period SK/hash or grant revision. |
| V1 authority `TRIAL_HISTORY` | `activatedAtEpoch`, `policyVersion` | Account-lifetime existing state; export is not permission to reset it or add retention. |
| V1 authority `CHECK#…` (unexpired valid minimized receipt) | `state`, `chargedChecks`, `processingOutcome`, `resultSummary`, `assessmentEpoch`, original `retentionDeadlineEpoch` as public `expiresAt`; optional `messageTransportVersion` / `recoveryTransportVersion` | **Do not reuse `_receipt_public` wholesale**: `checkId` is an opaque admission proof. Omit proof/check IDs, client/receipt IDs, execution token, payload HMAC, attempt/prepare/dispatch/counter internals. `resultSummary` must pass its current URL/message/recovery public schema; usage-only recovery receipts have no recovered suggestion. |
| embedded `CHECK.feedback`, validated by `result_feedback/service.py::_existing` | approved `category`, `receivedAt` only | Feedback is a private product-quality record, not a campaign vote. Omit feedback ID/policy-control internals. Do not extend original CHECK expiry. |
| History content using verified account/current generation, `shared_history/contracts.py::public_history_item` | `requestId`, `sourceType`, `acceptedAtEpochMs`, `completedAtEpochMs`, validated `assessment` | Existing assessment allowlist: `schemaVersion`, `requestId`, `scamScore`, `riskLevel`, `confidence`, `summary`, `signals`, `recommendedActions`. Never original message/URL or arbitrary stored attributes. Skip logically expired content; missing/corrupt locator/generation is a coverage failure. |
| History recognition `PROGRESS#currentGeneration`, `history_read_api/service.py::get_progress` | `qualifyingChecks`, `awardedBadgeIds`, approved badge catalog fields | Validate catalog/count consistency. Exclude acceptance counters, cursor/erasure/completion jobs and internal tombstones. |
| users `CAMPAIGN_PARTICIPATION` | `state`, `noticeVersion`, `policyVersion`, `effectiveFrom`, `effectiveUntil`, `withdrawalRequestedAt`, `deletionDeadlineAt` where present | Export observed consent, not legacy incentive/bonus defaults from `_response`; the approved protection/consent separation still applies. |
| users exact `CAMPAIGN_CONSENT#…` consent-audit shapes | `eventType`, `occurredAt`, `noticeVersion`, `policyVersion`, `resultingState`, `effectiveMonthlyScanLimit` where present | Preserve approved retention; omit consent/operation IDs, subject keys. Operation replay rows are not additional consent events. |
| ledger current account deletion / withdrawal operations | `operation`, `status`, request/deadline/completion timestamps through a dedicated strict projection | A new export is denied once account deletion is fenced. Fixed fence, receipt keys and worker cursors are not exported. Withdrawal status can be included while the account remains active. |

The public projections are implementation bounds, not permission to silently omit
required families. Freeze typed validators before exposing the export endpoint.

## Copy classes requiring manifest disclosure

- Analysis abuse REQUEST/RATE/SCAN_RATE/CONSUMPTION rows are transient dedupe,
  security and accounting controls. They must not be presented as another retained
  full analysis history or used to export original request/response bodies.
- Campaign outbox/pipeline contributions remain account-linkable during the approved
  key/recovery window. Their user-understandable purpose/category/time projection is
  not defined yet; exact ownership traversal through period keys and locators must
  be implemented before the manifest can claim full in-scope coverage. Raw vectors,
  contributor tokens and another person's data are excluded.
- Historical backups, transient stream/SQS copies and logs do not appear as ordinary
  downloadable records. The scope must identify these classes and their retention /
  restoration behavior. No immediate erasure claim is implied.
- Provider/store records are external, and non-user-linkable campaign aggregates are
  separate. The download must not imply it includes all records held by Google,
  Apple, or model providers.

## Proposed stateless capability rather than a new export table

Use an AEAD-encrypted cursor with dedicated environment key, fixed fifteen-minute
expiry, and authenticated account + current device/version + operation + cutoff +
manifest revision + family/last position. No account content or server archive is
stored. Every page rechecks access/fence and returns observation time; final local
assembly is labeled complete only after all required family traversals finish.

This is an observed-data export. Mutable sources mean re-reading a page can change
its contents; there is no immutable snapshot or byte-identical replay promise.
Cancel discards the local partial download/cursor; it cannot revoke an already
issued stateless capability before its expiry. Identity/deletion/device changes
still invalidate access. Documenting those limits replaces the older proposed
persistent export-status/cursor contract; it changes no already-approved deletion
ordering or receipt retention.
