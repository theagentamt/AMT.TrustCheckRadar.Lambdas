# Whole-period admission fence candidate

This increment makes all four upgraded campaign workers observe an exact
versioned admission fence on the existing `PERIOD#<periodId>/HMAC_KEY` record.
It provides an irreversible `OPEN` to `CLOSING` transition. It does not certify
period erasure, authorize key retirement, initialize an inventory or enable a
worker. Existing data deadlines, key retirement metadata and command/receipt
retention are unchanged.

## Record and runtime contract

The existing six fields (`PK`, `SK`, `periodId`, `keyArn`, `status`,
`retireAfterEpoch`) remain unchanged. `status` must remain `ENABLED` while
qualified cleanup needs the key. Seven exact additional fields are required:

| Field | Contract |
|---|---|
| admissionSchemaVersion | integer 1 |
| admissionGeneration | canonical UUIDv4 matching the independently deployed pin |
| admissionState | OPEN or CLOSING |
| admissionRevision | integer 1..9007199254740991; no overflowing transition |
| admissionManifestSha256 | existing pinned locator manifest SHA256 |
| admissionInventoryRevision | existing pinned locator inventory revision |
| admissionChangedAtEpoch | positive integral epoch, never future at observation |

No legacy or absent record is automatically considered OPEN. Unknown fields,
malformed clocks, stale generations, foreign account/region key ARNs and retired
keys fail closed. No initializer, reopening, generation-assignment or retirement
API is supplied. Operator provisioning requires actual inventory and rollout
review; fixture rows are synthetic assumptions, not that review.

All four artifacts require `CAMPAIGN_PERIOD_ADMISSION_ENABLED=false` by default,
`CAMPAIGN_PERIOD_ADMISSION_GENERATION=""` by default, and protected
`CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID` equal to the deployment account. Region
comes from `AWS_REGION` (or `AWS_DEFAULT_REGION`). Actual handlers compare their
invoked function ARN with those account/region values. Existing locator,
stream, recovery, completion and lifecycle gates/pins remain independently
required. Admission false cannot fall back to a legacy mutation handler.

The lifecycle handler accepts one additional exact event:

```json
{"schemaVersion":1,"environment":"dev","operation":"close_period","periodId":1479}
```

The period must have ended and fall inside the verified locator inventory range.
The transaction compares the entire observed registry and inventory, changes
only admission state/revision/change time, and returns fixed `state`, `changed`,
`periodComplete:false`, `retirementEligible:false`. Replaying CLOSING performs a
check-only transaction and does not refresh any timestamp. A lost transition
acknowledgment can be retried. This is an observation at the transaction point,
not durable absence or retirement evidence. Calls stop before the next SDK
operation if the remaining budget is below six seconds; SDK latency itself is
not cancelled by that check.

## Writer inventory

| Path | Required transactional period proof |
|---|---|
| Publisher FEATURE/DEDUPE/locator creation | exact OPEN registry |
| Publisher PENDING to PUBLISHED | same exact OPEN registry |
| Cluster new candidate/contribution, repeat, capped dedupe | exact OPEN registry |
| Retained cleanup anchor/cursor, locator deletion, repair checkpoint/page/final summary | exact observed ENABLED OPEN or CLOSING registry through selected-period wrapper |
| Legacy two-period helper (no production handler dispatch) | same mandatory period validation and transaction wrapper |
| Completion and positive replay | exact observed admission schema/generation plus existing full completion proof |
| Lifecycle freeze, publish, publication replay/paired cleanup | exact CLOSING registry in every transaction |
| Lifecycle orphan locator expiration | exact OPEN or CLOSING registry |

Publisher rereads OPEN before sending SQS, but sending and the DDB transition
are not atomic. A message can be queued just as closure wins. Cluster's OPEN
transaction guard prevents that late message from admitting a contribution.
Duplicates that make no changes are not new admission. Registry changes during
cleanup prevent the transaction; retry can read the new CLOSING record and
continue. Existing candidate-local publication fences still prevent repair and
publication from concurrently changing the same SUMMARY.

IAM additions are exact pipeline `PERIOD#*` GetItem/ConditionCheck as needed;
ConditionCheck uses `dynamodb:ReturnValues=NONE`, with no unsupported enclosing
operation restriction. Lifecycle close needs transaction-only UpdateItem on
`PERIOD#*`; the broad legacy mutable PERIOD grant must be removed. No initializer
Put, key mutation or new KMS action is needed. Existing inventory conditions
remain required. Root owns effective-role qualification and deployment.

## Qualification and deployment boundary

The extended isolated runner contains 46 fixed cases, including the prior 35
completion/stream/recovery cases with updated synthetic registry evidence. New
cases execute the actual shared close, publisher and cluster transaction code
against dedicated SDK tables. SQS sending and candidate-index discovery are
injected; candidate data is strongly reread. Consent/profile/ledger checks use
actual synthetic records and actual source guards. These cases do not claim real
SQS delivery, GSI propagation, historical inventory or provider acceptance.
The local runner uses synthetic KMS; the root-owned AWS runner uses its isolated
HMAC fixture key. No production identity or key is used.

`build_campaign_qualification.py --source-sha <clean exact HEAD> --output-dir <dir>`
full-builds and preserves all four production Python 3.14 ARM64 ZIPs, verifies
every Python member against the source commit, and builds a separate fixture ZIP
with those same modules. Production handlers never import the fixture entrypoint.
Root must repin all four `account_privacy_artifacts` plus the deletion-specific
completion override to the same reviewed source before a closed deployment.

The admission fence is effective only after all affected writers are upgraded,
old invocations drained, and inventory/generation pins qualified. A privileged
restore of OPEN and its matching metadata under an unchanged external generation
cannot be detected merely by reading those rows. Restore procedures must first
quarantine writers, invalidate the external generation and inventory approval,
and requalify restored content before any gate opens. No generation is inferred
from time or from a restored CLOSING record.

Remaining SEC207 work includes qualified whole-period all-family/orphan absence,
publication/deletion ordering across all retained historical data, a later sealed
boundary preventing cleanup writes before retirement, exact KMS retirement/retry
proof, restoration acceptance and actual inventory approval. Closing alone or an
empty GSI does not satisfy any of those. The legacy age-only `manage_keys` and
generic `expire_transient` operations remain unreachable.

## Local evidence

Using `/tmp/amt-account-privacy-venv/bin/python` (Python 3.14) with
`AMT_AUTHORITY_INTEGRATION=1`, isolated SDK/Moto runs passed:

- 179 coverage, retained cleanup, completion, actual handler and metadata/repair cases.
- 77 producer/locator/metadata/period cases, including all three cluster write branches.
- 23 lifecycle publication/expiration/closing handler cases.
- 67 qualification runner and containment cases, including all 46 fixed runner cases.

These are 346 distinct SDK cases. The final defensive pre-SDK identity check was
additionally verified by rerunning the 30 period cases. Ordinary isolated runs
passed publisher 23 (15 subtests), cluster 15 (13 subtests), deletion 6
(9 subtests) and lifecycle 9. Compileall and diff whitespace validation passed.
Lifecycle tests run in a separate process because existing packages use colliding
flat `service` module names; a combined invocation initially exposed that test
isolation issue, not an application runtime failure.

Frozen-source archive identity and byte-equality evidence is produced separately
by the clean-source builder. None of these local results substitutes for the
root-owned actual ARM64 Lambda run. No upload, deployment, marker write,
historical backfill or live activation occurred in this source task.
