# Dev create-only campaign period initializer

`scripts/initialize_campaign_period.py` is an operator CLI, not a Lambda handler
or scheduler. It accepts an independently provisioned HMAC key and a reviewed
admission generation/locator manifest. It cannot create, disable, retire or delete
keys; update/delete registry rows; write an inventory; or enable runtime gates.
There is no automatic adoption of legacy rows or replacement of an existing key.

## Plan and apply

The default operation creates a local plan only:

```
python scripts/initialize_campaign_period.py --plan-out /tmp/period-plan.json \
  --period-id <current-period> --key-arn <exact-provisioned-ARN> \
  --generation <reviewed-UUIDv4> --locator-manifest-sha256 <reviewed-SHA256> \
  --locator-inventory-revision <reviewed-revision>
```

Only a separately reviewed operation should apply that exact plan:

```
python scripts/initialize_campaign_period.py --apply-plan /tmp/period-plan.json
```

The CLI is fixed to Dev account107827791950, regionus-east-1 and table
`trustcheckradar-dev-campaign-pipeline`. It verifies STS account and client region.
The selected ARN must identify an Enabled, CUSTOMER-managed HMAC_256 key with
GENERATE_VERIFY_MAC usage. Its complete tag set must be Project=trustcheckradar,
Environment=dev, Purpose=campaign-contributor-token and PeriodId=<current period>.
No aliases, pagination, unexpected tags or foreign ARN are accepted.

The current period is floor(server epoch / 14 days). Planning and applying reject
clock regression, future timestamps and rollover. The whole validated locator
inventory must match the supplied pins, require prior-period erasure evidence,
cover the selected period, and have approval before the immutable planned row's
change timestamp. This checks an existing approval; it does not establish or
create one. The JSON parser rejects duplicate members and non-finite values;
Boolean/integral ambiguity is rejected for planned rows and exact comparisons.

The plan binds fixed identity, all request parameters, the full observed inventory
and the exact thirteen-field HMAC_KEY row. It proposes OPEN/revision1, preserves
the existing `(period+1)*14days+7days` retireAfterEpoch formula and chooses no data
retention changes. Apply rereads every prerequisite, then transactionally compares
all observed inventory fields and conditionally inserts only an absent period
row. An exact preexisting row is checked transactionally and never rewritten.
A different/legacy/CLOSING/unknown row refuses without repair. A strong reread of
row/inventory and fresh key/tag/clock checks are required before reporting success.

An uncertain transaction returns a fixed unavailable error. Retry the **same plan**;
do not generate a new timestamp or create another key. A committed exact row plus
current matching inventory can satisfy the retry through a check-only transaction.
If period rollover or metadata change occurs after commit, the CLI refuses a
readiness claim and performs no destructive compensation. The operator must
reconcile the retained row/key explicitly. Time checks cannot cancel an in-flight
SDK request; success is an observation after the bounded call sequence, not a
promise against later privileged changes or restoration.

## Infrastructure and rollout dependencies

The operator role needs GetItem on the exact pipeline table's inventory/period
keys; ConditionCheckItem for the same namespaces; transaction-only PutItem for
PERIOD#*. It needs DescribeKey/ListResourceTags on the exact independently
provisioned key and STS identity read. No GenerateMac, CreateKey, DisableKey,
ScheduleKeyDeletion, UpdateItem, DeleteItem or inventory-write grant is required.
Use fixed SDK timeouts and one total attempt as implemented; no automatic mutation
retry. No new Lambda role or runtime permission is implied by this CLI.

Root owns provisioning and tracking the key, including ambiguous creation/orphan
reconciliation. Never automatically delete an uncertain key that may have become
registered. Automatic future-period provisioning remains a separate explicit
readiness requirement; the existing age-only `manage_keys` is still unreachable
and must not be enabled. Missing current-period data fails closed.

Creating current1480 does not upgrade legacy1479 or recover lost1478 key material.
Existing-row admission adoption needs its own reviewed exact preservation/CAS
plan. Prior-period/restore/minimum-period coverage needs real evidence. A fresh
key cannot recover prior contributor tokens. All live gates remain closed by this
source change; no marker, key, registry or customer data was mutated in validation.

## Local validation

Thirty SDK/Moto tests passed, including default dry run, exact apply/replay,
commit acknowledgment loss, inventory and competing-key transaction races,
legacy/unknown/CLOSING preservation, malformed plans/identities/tags, rollover
before and after commit, and actual CLI plan/apply dispatch with synthetic clients.
KMS/STS metadata are injected; no cloud key or registry was used. The tests create
only in-memory synthetic inventories and do not approve historical coverage.

```
AMT_AUTHORITY_INTEGRATION=1 python -m pytest -q tests/scripts/test_initialize_campaign_period.py
```
