# SECUR4ALL-207 period lifecycle qualification

The integrated application source is `9147d545b719e54c1f967e35042bc502d79bc0f1`.
These reports establish the scoped results below, not production activation,
historical inventory approval, key destruction or story completion. They change
no existing customer account. Disposable fixtures contain synthetic data only.

## Artifact verification

[manifest.json](manifest.json) identifies eight Python 3.14 ARM64 archives.
[Local verification](local-verification.json) checks 283 Git-backed application
members plus the generated export wrapper (284 per-archive members), hashes,
sizes, compilation, dependency versions and ELF64/AArch64 native headers.
[Independent verification](independent-verification.json) separately verifies
the same archive bytes; its compilation/member counts also include dependency
files where present. The native export libraries were not executed on Linux.

[Local handler checks](local-handler-checks.json) import all eight extracted
archives with SDK calls blocked and observe zero SDK calls. Export uses host
cryptography only for this macOS import check. Disabled handlers, missing-auth
review, and missing-configuration conversation-analysis responses are distinct;
conversation-analysis's 500 is not evidence of an admission guard. These checks
are not enabled-runtime or actual-role IAM acceptance.

The intended deployment selects seven compatible packages: the four campaign
workers, account-data, export and campaign-review. Conversation-analysis is a
prepared eighth artifact, while the actual retired function stays on its existing
installed hash with the explicit DynamoDB write deny and closed work/research
configuration. The inventory must record that exact installed source/role as
`RETIRED_DISABLED`; it must not claim the new analysis package was deployed or
that its optional allow policy overrides the existing deny.

## Composed lifecycle against AWS

| Case | Report | Seconds | Result |
| --- | --- | ---: | --- |
| Normal drain and replay | [complete](lifecycle-complete.json) | 70.414 | Passed |
| Poison preservation and recovery | [poison](lifecycle-poison.json) | 73.561 | Passed |
| Committed deletion acknowledgment loss | [lost acknowledgment](lifecycle-lost-ack.json) | 70.499 | Passed |

Each case runs local Python 3.14 against five dedicated AWS DynamoDB tables and
one dedicated AWS KMS key. It is not a deployed Lambda or production-role test.
Each uses six producer events and 42 indexed targets, three early-deadline ticks,
and four or five drain ticks. Actual application code computes SEALED proof,
then completes account and withdrawal commands without a later MAC call; replay
preserves the completed state. An explicitly seeded expired anonymous aggregate
is deleted and a future aggregate is preserved.

Clocks, inventories and commands are synthetic. Queue/metric transport is
captured, TTL delivery timing is not qualified, and the retirement switch remains
false. Poison must block sealing until the fixture restores its exact known
synthetic source; that restoration is not a production repair facility. Completed
GSI discovery does not certify all-table physical erasure.

## Computed seal through actual retirement and later cleanup

The [additional composed result](composed-retirement.json) passed in 91.631 seconds
at fixture-only source `81ae81a152eef30a903e23eff4d12da75b780c08`. Production source
and the eight production archives remain unchanged at `9147d545`. The fixture
change passed 14 focused SDK/Moto tests normally and under optimized Python,
with an independent 14-test pass and exact-head review before AWS execution.

This run closes the missing composition edge: six actual producer events create
42 indexed targets; three early-deadline ticks and four drain ticks compute SEALED
from actual paired erasure. The unchanged production retirement helper then
performs exactly two KMS mutations (disable and seven-day scheduling) using fresh
wall time. It preserves the computed seal, observes SCHEDULED, rejects a MAC probe,
and replays retirement without another mutation or record change. Actual account
and withdrawal cleanup both complete through the resulting retirement proof with
zero later KMS calls, followed by unchanged completion/delayed replay. The separate
seeded aggregate expiry check still removes only the expired aggregate.

Execution is local Python 3.14 against a fresh five-table AWS fixture and a dedicated
key, not a deployed Lambda or production-role test. The earlier producer/drain
clocks, inventories and commands remain synthetic; transport is captured, TTL
scheduling and general research admission are not qualified. The key is scheduled,
not destroyed. No actual attached Dev period was retired by this fixture.
The [cleanup report](composed-retirement-cleanup.json) and separate
[independent readback](composed-retirement-independent-cleanup.json) confirm all
five fixture tables absent. The dedicated key remains PendingDeletion for
October 4, 2026 UTC (October 3 local); it has not been observed destroyed.

## Separate irreversible-key and withdrawal evidence

Retirement reports [guards](retirement-guards.json) and
[lost acknowledgment](retirement-lost-ack.json) use frozen core
`e388184e8936da970c05d395353e4d12e8d1a3b9`, local Python 3.14 and actual AWS
DynamoDB/KMS. They begin with explicit synthetic SEALED/control/inventory proof.
They verify direct closed-gate and missing-proof refusals, actual disable and
seven-day scheduling, rejected MACs, unchanged replay and ambiguous-response
recovery. They do not derive their starting seal from actual period draining,
and PendingDeletion is not destruction.

Withdrawal reports [normal](withdrawal-complete_replay.json),
[producer acknowledgment loss](withdrawal-lost_ack.json) and
[delayed replay](withdrawal-delayed_replay.json) use source
`9bf336d3a39861925d5a7578e3a6de65bf3c2784` in an actual disposable Python 3.14
ARM64 Lambda. The actual participation, publisher/cluster and cleanup code
produces the durable withdrawal command, terminal audit and immutable replay.
Queue delivery, stream envelope and candidate discovery are injected; native SQS
or DLQ redrive and production-role acceptance are not claimed.
[Withdrawal cleanup](withdrawal-cleanup.json) verifies fixture tables, function
and role absent, with the key PendingDeletion rather than destroyed.

Independent [lifecycle cleanup readback](lifecycle-independent-cleanup-readback.json)
confirms all 15 tables across the three runs absent. Per-case readbacks are
[complete](lifecycle-complete-cleanup.json), [poison](lifecycle-poison-cleanup.json)
and [lost acknowledgment](lifecycle-lost-ack-cleanup.json). All three keys remain
PendingDeletion, scheduled for October 4, 2026 UTC.

Independent [retirement cleanup readback](retirement-independent-cleanup-readback.json)
confirms all four tables across the two runs absent; per-case readbacks are
[guards](retirement-guards-cleanup.json) and
[lost acknowledgment](retirement-lost-ack-cleanup.json). Both keys remain
PendingDeletion, scheduled for October 4, 2026 UTC. No function or role was created
for these local SDK fixtures. These observations do not claim the keys destroyed.

## Remaining delivery boundary

Public repository publication is paused after automatic approval review rejected
the push of this public payload. There is no alternate push or destination.
Source integration, independently approved attached-resource bootstrap, effective
IAM, coordinated deployment, actual worker/monitoring acceptance and release
tracking remain separate steps. SECUR4ALL-207 remains open until retained Dev
acceptance and verified release integration are complete. Later release/UAT is
[SECUR4ALL-330](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-330); native
restore/reopening remains SECUR4ALL-245.
