# Read-only campaign completion coverage assessor

SECUR4ALL-207 now has an unwired diagnostic at
`campaign_deletion_bridge/coverage_assessment.py:assess_account_coverage`. It
makes no write, creates no marker/receipt, and does not change the deployed
handler, runtime flags, retained data or approved durations. Both campaign
completion helpers still fail closed. This is independently useful prerequisite
assessment, not completed campaign erasure or full story acceptance.

## Input and observations

The caller supplies the exact requested account-deletion command, environment,
AWS account/Region, pipeline/ledger table names, pinned existing CAMPAIGN_LOCATORS
inventory, clock and bounded clients. The command is validated and strongly read
back from its owned fixed ledger key. The strict inventory must match its existing
reviewed manifest/revision and predate the request; old commands are not rebound.
No new inventory schema or approval writer is introduced.

Every period from inventory.minimumPeriodId through the request-time period is
examined, including older periods beyond the previous current/previous shortcut.
A missing key or legacy RETIRED status is unverified, never erasure evidence.
Enabled keys must have exact known fields, matching period/deadline and a KMS key
ARN in the configured account/Region. GenerateMac must return that exact key,
HMAC_SHA_256 and32 bytes. These are read-only provider computations, not evidence
that physical key retirement occurred. No live KMS call was made in local tests.

The derived contributor partition needs an exact version2 non-TTL tombstone with
strict canonical operation/cursor/repair state. A fully paginated strongly
consistent **base-table** query examines every row, not an eventually consistent
index or a filtered locator-only subset. Unknown/legacy rows make it unverified;
remaining locators or non-idle/mismatched-operation repair make it pending even
when target TTL has passed. Empty observations require a matching idle repair
state. The exact command, inventory, observed keys and tombstones are reread
before returning; changes invalidate the range observation.

## Output and budgets

The fixed JSON result contains schemaVersion, scope READ_ONLY_LOCATOR_COVERAGE,
complete=false, receiptEligible=false, rangeExamined, period/page counts and
sorted fixed reason codes. No account ID, period ID, contributor token, target
key, raw exception, original content or arbitrary provider value is returned or
logged. `COMPLETION_PROOF_UNQUALIFIED` is always present.

`rangeExamined` means the configured range was visited without unverified reads;
it may include known pending work. Counts are attempted/observed diagnostics,
including when final revalidation fails. They are not an atomic snapshot, an
absence certificate or permission to write a receipt. Even repeated strong reads
cannot prevent a later writer or an ABA change. A future completion transaction
needs fully qualified writer/retirement fences and atomic stable-proof checks.

Defaults are8 periods,32 query pages and25 rows/page. Valid hard maxima are32
periods,64 pages and100 rows/page; oversized range fails before period/KMS work.
The optional remaining-time callback stops period/query work and every final
proof reread below3 seconds. Query/page/time exhaustion cannot report an examined
range. No persisted cursor or additional retention is added; large inventories
remain explicitly incomplete. A future operational caller must also configure
bounded SDK connect/read timeouts and retry attempts; count limits do not bound
an arbitrary caller's network timeouts. No such caller is wired by this change.

## Permissions and deployment

Only ledger GetItem for ACCOUNT#*, pipeline GetItem for INVENTORY#/PERIOD#/CONTRIB#
and strongly consistent base-table Query for CONTRIB#*, plus the existing
account/Region/tag-scoped KMS GenerateMac are needed. The independent IAM review
confirmed existing candidate grants cover these reads; no ConditionCheckItem,
write, new table, schedule or public API is required. This source is packaged
with the existing deletion bridge but is not invoked by it. No artifact upload
or runtime deployment belongs to this increment.

## Validation and next work

Synthetic actual-SDK/Moto tests cover older retained periods, missing/retired/
foreign/malformed keys, incomplete/changed inventory, strongly paginated reads,
range/page/time limits, malformed and pending repair state, unknown retained
rows, changed command/key/tombstone evidence, KMS errors and mismatched responses,
state preservation and sanitized outputs. The test client exposes only GetItem
and Query, and synthetic KMS exposes only GenerateMac, preventing accidental
mutation calls. Tests use no live AWS account or customer data.

Validation: the combined coverage/strong-locator/progress SDK run passed92 cases;
then the final time-budget guard was verified by the focused39-case coverage run
(including two new reread-budget regressions). Six ordinary deletion cases plus9
subtests passed. The full Python3.14 ARM64 deletion-bridge ZIP built; every local
function source member and shared locator core matched the archive byte-for-byte,
and every Python member compiled. Archive SHA256:
`d15dc723a5b2703233ccc582f07382bea706dbaa7b1a8f3b097e0b6150321587`.
No native dependencies or live AWS loading were qualified by these local checks.
Compileall and git diff whitespace checks passed.

Next source work must finish derived candidate metadata repair (beyond centroid/
counts), all-period/key/legacy coverage and stable retirement ordering, and fair
durable reconciliation before adding an actual CAMPAIGN receipt. Thresholded
aggregate retention still requires the approved non-linkability proof. Existing
policy controls14-day periods,7-day recovery,<=21-day transient retention and
<=24-hour withdrawal erasure; no new checkpoint/key extension is inferred.
Infrastructure203/245, account orchestration200, consent217 and privacy evidence215
remain the associated owners. Historical210/old story-map status is not new
completion evidence. Android local accepted-request cleanup proceeds separately.
