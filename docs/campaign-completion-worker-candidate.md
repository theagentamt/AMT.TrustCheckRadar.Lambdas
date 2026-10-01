# SEC207 campaign completion worker integration

The real `campaign_deletion_bridge` stream and scheduled recovery entrypoints now
share `orchestration.process`: at most one fair retained-period cleanup attempt,
then the independent full qualified completion proof. This replaces the earlier
standalone-only composition. Every live deletion/recovery/producer gate remains
closed; this source does not approve a marker, backfill, historical coverage or
retirement. No new retention or durable record shape is introduced.

## Runtime contract

New default-false flags and empty pins:

| Environment field | Default | Purpose |
| --- | --- | --- |
| CAMPAIGN_DELETION_STREAM_ENABLED | false | Explicit stream processing gate |
| CAMPAIGN_COMPLETION_ENABLED | false | Qualified completion integration |
| CAMPAIGN_COMPLETION_MANIFEST_SHA256 | empty | Exact separate completion manifest |
| CAMPAIGN_COMPLETION_INVENTORY_REVISION | 0 | Exact separate completion revision |

Existing `CAMPAIGN_RECOVERY_ENABLED=false` independently controls scheduled ticks.
Existing locator/recovery manifest and positive revision pins are also mandatory
when completion is enabled. The marker shape and strict approval-before-command
rules are unchanged. The completion range stays bounded to eight retained periods.
`APP_ENVIRONMENT`, pipeline/users/ledger tables, at-most21-day transient policy,
400-day participation audit policy and seven-day contributor recovery remain.
Both trigger paths validate runtime account/Region identity and remaining-time API;
each further cleanup SDK/KMS call and every completion proof call requires at least
six seconds remaining. SDK latency after dispatch is not a hard timeout guarantee.

A disabled stream invocation raises fixed `CAMPAIGN_DELETION_STREAM_DISABLED` before
storage work: it must not acknowledge accidentally delivered pending work. Disabled
static recovery ticks may return `enabled=false, complete=false`. No Lambda partial
batch response setting is assumed. Enabled stream processes at most ten records,
validates the exact configured ledger stream ARN, ignores clearly non-command shared
ledger sidecars/control/receipts/inventory, and rejects malformed declared commands.
A mixed batch with unresolved work raises the fixed reconciliation error; completed
records in that batch can safely replay their unchanged receipts/audits.

Only an actual qualified `Completion` result counts as component completion.
Exact full-account terminal suppression can be acknowledged without claiming
campaign completion. A failed/expired/unqualified proof remains pending; it cannot
be inferred from a successful bounded sweep or an empty eventual index. Original
receipt/audit expiry is preserved, and a newer consent epoch is not rewritten.

The adapter reads existing receipt/terminal state before dispatching cleanup.
Every cleanup transaction now also requires CAMPAIGN receipt absence; when
completion is enabled it additionally checks the exact observed OPEN control and
own recovery job. This closes the race where the account command remains REQUESTED
after another invocation atomically seals the component and writes its receipt.
Such a late sweep must fail without modifying pipeline state. The completion
primitive still guards its own full proof and atomically consumes/seals/receipts.

## Metrics and retry semantics

Existing `TrustCheckRadar/Campaign`, Environment-only metrics remain. Scheduled
`CommandsCompleted` and `CommandsTerminalAcknowledged` now distinguish qualified
results and suppression from `CommandsUnverified`. Counts are successful attempts
(including replay), not unique accounts or newly written receipts. Global scheduled
`complete` and `receiptEligible` remain false: a bounded eventual-index tick is not
whole-inventory completion. Existing pending-age/deadline/budget/poison metrics retain
their meanings. Stream logs contain only fixed names and aggregate counters.

## Exact IAM and isolated package selection

Only `campaign_deletion_bridge.zip` needs selection for this runtime increment.
`shared_campaign_recovery.worker` is imported only by this bridge; its scheduling
metrics change does not require deploying account-data/participation/finalizer
packages. No other worker artifact should change as a side effect.

The future selected bridge needs existing pipeline strong Get/Query, exact
inventory/PERIOD/CONTRIB/candidate transaction checks and guarded cleanup mutations;
ledger Get/Query on ACCOUNT partitions and Get/check on exact inventory partition;
ledger transaction-only Put/Delete/Update on ACCOUNT keys; users Get and check-only
USER keys plus transaction-only Put for participation and completion audits.
ConditionCheck grants use ReturnValues NONE, not unsupported enclosing-operation
conditions. There is no runtime marker write and no Scan requirement. KMS calls
remain GenerateMac only on the tag-scoped same-account/Region campaign HMAC keys;
no DescribeKey call or permission is added by this source.

The infrastructure-owned nullable deletion-only artifact override must require the
paused privacy candidate and prepared recovery dependency, take precedence only for
the deletion worker, and preserve every other worker's existing immutable pin. All
new flags stay false with empty/zero pins in the closed deployment. Source/handler
is `app.lambda_handler`, runtime Python3.14 ARM64. Build exactly this archive:

```sh
bash scripts/build_lambda_zip.sh --function campaign_deletion_bridge --python-version 3.14 --arch arm64 --output-dir /tmp/<reviewed-build-directory>
```

A future authorized publication uses the usual immutable
`releases/<full-reviewed-source-sha>/campaign_deletion_bridge.zip` with exact S3
VersionId and base64 SHA256, then selects only that object. This increment performs
no upload, deployment or activation. Local package evidence is supplied separately
for the exact frozen source. The qualification builder preserves the committed
production app, whose completion integration is now gated rather than unwired.

## Next concrete Lambda engineering work

1. **Period admission/closure fence.** Current publication freezes one candidate
   SUMMARY; it does not install a whole-period fence observed by every publisher,
   cluster, repair and replay path. Add a strict period-closing transition and
   transactional writer guards before attempting whole-period absence/retirement.
   Keep this metadata tied to existing period purpose and approved retention;
   describe its exact shape before implementation.
2. **Orphan publication recovery.** Strong locator pairs survive target TTL loss,
   but candidate publication recovery currently requires a surviving SUMMARY and
   bounded contribution inventory. Qualify recovery of missing/expired candidate
   state and pending repairs without publishing reconstructed guesses or refreshing
   deadlines. Existing qualified metadata reconstruction is a prerequisite, not
   proof all historical rows match it.
3. **Retirement proof and recovery.** Legacy time-only key management remains
   unreachable. A new path must require the reviewed period-closing fence, strong
   coverage of every relevant current/legacy family and restore invalidation, then
   record/verify actual key-state transitions with retries. Missing/retired keys are
   still unverified in account completion; current zero counts and key age never
   authorize skipping a retained period. Root's prior1478 metadata/backup audits
   remain separate observations until that evidence is actually qualified.

These are concrete remaining engineering/inventory dependencies. No mobile hardware
or unrelated Play checkpoint-policy decision blocks the integration above. SEC207
is not Done merely because the completion code is now reachable behind a gate.

## Local qualification

The actual handler test file contains26 SDK/Moto cases. The combined affected run
passed202 SDK cases (handler, primitive, retained traversal, progress, durable
recovery and finalizer); the expanded exact cloud-runner implementation separately
passed56 SDK cases, including seven new actual-handler cases. Six ordinary bridge
tests plus nine subtests passed. Synthetic markers remain explicitly assumed
qualification, not real approval. The final combined rerun passed258 SDK/Moto cases. Both production/runner
package hashes are recorded in the exact-source handoff. The qualification builder
now preserves campaign_deletion_bridge.zip alongside its separate runner ZIP and
records both handlers, hashes and sizes; it uploads neither. No cloud invocation is
claimed by local evidence.
