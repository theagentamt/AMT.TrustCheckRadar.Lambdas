# Campaign publication and cleanup recovery candidate

This source increment addresses the failure between publishing an aggregate and deleting its transient inputs. A retry resumes an atomic phase on the existing candidate SUMMARY; it does not rebuild an aggregate from partially deleted contributions, overwrite a reviewed aggregate, or extend the existing retention deadlines.

The upgraded lifecycle handler is disabled by default. `CAMPAIGN_LIFECYCLE_CANDIDATE_ENABLED=false` denies every operation, without falling back to the legacy mutation routines. Even when explicitly enabled, the handler accepts only `recover_candidate` and `expire_locator`. The old `manage_keys`, `finalize_periods`, and blanket `expire_transient` operations are rejected. No scheduler or inventory-approval writer is added here.

## Exact prerequisites and operations

Both operations require a reviewed `CAMPAIGN_LOCATOR_MANIFEST_SHA256` and positive `CAMPAIGN_LOCATOR_INVENTORY_REVISION`, matched against the authoritative `INVENTORY#<environment>/CAMPAIGN_LOCATORS` record. Every mutation condition-checks that full marker in the same transaction. The marker cannot be created by these workers. The shared locator migration, writer coordination and retention prerequisites apply; configuration alone is not evidence of coverage.

An internal invocation is an exact object containing `environment`, integer `schemaVersion: 1`, `operation`, and these operation-specific fields:

- `recover_candidate`: `candidateId`, a canonical UUID4.
- `expire_locator`: `locatorPK` and `locatorSK`, identifying one exact contributor locator.

There is no public HTTP route or account-data completion response. Results are internal progress only. No account identifiers, contributor tokens or request bodies are logged.

## Publication transaction and retries

An eligible candidate must be at least seven days past its fourteen-day period end. The worker reads its SUMMARY strongly, then atomically freezes it with a version increment, UUID operation, original start time and inventory revision. That transaction also requires no `DELETION_RECOMPUTE` checkpoint. Producers and deletion repairs condition on `lifecycleState` being absent, so they cannot change frozen source under a publication read.

The FROZEN phase traverses at most five strongly consistent pages of 100 contributions, verifies the exact locator for every target and excludes logically expired input. An unfinished traversal fails without publishing. Ten distinct retained contributors are still required; existing three-submission caps and thresholded language/tactic/channel dimensions apply. An expired SUMMARY is suppressed, not republished.

The aggregate Put and SUMMARY phase change to PUBLISHED are atomic. A suppressed candidate records SUPPRESSED atomically with aggregate absence. The aggregate deadline is fixed at the original lifecycle start plus 400 days. Lost acknowledgments retry the persisted phase. A preexisting aggregate without matching phase evidence fails closed for reviewed migration.

PUBLISHED retries validate a digest of immutable aggregate fields. The existing review service may change only state/version and its matching publication index without preventing cleanup. Cleanup never rewrites the aggregate. It erases at most ten owned contribution/locator pairs per invocation with inventory and SUMMARY guards. A subsequent empty base-table pass removes SUMMARY last, also requiring no deletion-recompute checkpoint. The result's `complete` means only this candidate's observed live contribution cleanup ended; it is not a whole-account, period, locator-inventory or backup-erasure receipt.

## Paired expiration

`expire_locator` validates the exact locator and unchanged logical deadline. An expired FEATURE locator deletes FEATURE, DEDUPE, CLUSTERED and locator atomically, including when FEATURE has already been removed by TTL. Existing siblings must carry matching ownership pointers and deadlines. A replaced sibling or target cancels the entire transaction.

A CONTRIBUTION locator can be expired by this primitive only when SUMMARY and DELETION_RECOMPUTE are absent, checked atomically. A live candidate must use its coordinated publication/deletion path. This prevents expiration from silently invalidating a publication read or summary repair. A missing locator remains an unresolved operation rather than invented evidence that its legacy target disappeared. The response always says `scopeComplete: false`.

## Remaining acceptance limits

This is a disabled recovery primitive, not a completed campaign/account lifecycle. Required work remains:

- Qualify complete legacy locator migration, all replay/restore and old-writer fences, and every retained linkable period. No live marker is provisioned here.
- Implement and qualify fair durable scheduling, retries, poison-work handling and deadline monitoring across the complete candidate and locator backlog. The handler does not claim that two recent periods or an eventually consistent expiration index covers all work.
- Establish the key-retirement barrier and ordering between pending account deletion/withdrawal, frozen aggregation, final aggregation and key destruction. Logical key age alone does not establish anonymity. The candidate cannot create, disable or schedule deletion of KMS keys.
- Resolve independent DynamoDB TTL timing: SUMMARY disappearance is not publication or cleanup proof; dangling locators must still be reconciled. Locators deliberately do not carry independently deleting TTL and keep the original target's logical deadline. Overdue paired cleanup remains operationally visible work, not a new permission to retain indefinitely.
- Qualify oversized candidates (more than the bounded five-page read), sparse/eventually consistent discovery, interruption at every transaction, and staging restore/replay and volume behavior. No truncation is reported as success.
- Add campaign/withdrawal completion only after these proofs are satisfied. Existing completion gates remain closed. Ordinary account finalization must not bypass this missing component evidence.

## Validation

Synthetic actual-SDK/Moto tests cover atomic publication, lost publication and deletion acknowledgments, exact marker/version races, missing locators, pending recompute, reviewer mutations, changed aggregate contents, expired source, legacy refusal, disabled/no-fallback handler, and paired expiration after FEATURE TTL with ownership replacement rejection. These local tests do not qualify live AWS IAM, scheduling, deployment or production erasure.
