# Isolated orphan recovery qualification

The four new lifecycle-handler cases passed on actual AWS Lambda Python 3.14
ARM64 with exact source `68198f448f378508783925526a59cc3454137c6a` and fixture
archive SHA256 `15b13d8a53d2a989dfb72ce4784d8fca991d63415237052a77f7503c7728346d`
(224,114 bytes). Results were observed September 26 local / September 27 UTC.
This evidence changes no production/customer account or live admission gate.

| Case | Result | Duration |
| --- | --- | --- |
| [Actual handler bounded drain](runtime-drain.json) | Passed | 2.688 s |
| [Legacy checkpoint refusal](runtime-legacy_refusal.json) | Passed | 0.824 s |
| [Live contribution refusal](runtime-live_refusal.json) | Passed | 1.172 s |
| [Committed delete acknowledgment loss and retry](runtime-lost_ack.json) | Passed | 1.712 s |

The [independent package verification](package-verification.json) checks all 99
fixture Python members against frozen Git. The
[local package manifest](local-package-manifest.json) records the four production
ZIP identities and fixture source hashes. Every production archive also passed
independent member/hash/size checks. These production ZIPs were not deployed by
this increment.

The three run-specific tables, fixture key and disposable Lambda/role used the
existing independently reviewed containment harness. The actual lifecycle handler
and DynamoDB transactions ran with synthetic inventory, period and clock inputs.
The fixture role has isolated-table permissions; this is not qualification of the
full deployed lifecycle-role union, live inventory, scheduled discovery, native
backup restore, whole-period completion or key retirement. The fixture explicitly
requires `scopeComplete:false` and `retirementEligible:false` even after the
candidate partition becomes empty. No Cognito/customer account is used.

The [independent cleanup readback](cleanup.json) confirms all three tables,
fixture Lambda and role absent. The fixture key is PendingDeletion with scheduled
deletion on October 4 UTC (October 3 local), **not destroyed**. No identity pool
was needed for these four cases. Runtime results and resource cleanup are
separate evidence.

Local results remain 342 bridge tests plus nine subtests and 63 lifecycle tests;
six directly affected fixture cases were rerun after a routing-only cleanup.
No broad CI, UAT or general campaign activation is implied. SECUR4ALL-207 remains
In Progress; remaining Dev work and SECUR4ALL-330 are linked in the
[candidate contract](../../campaign-orphan-recovery-candidate.md).
