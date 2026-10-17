# Approved PR126 Dev diagnostic deployment

The exact reviewed diagnostic overlays from [PR126](https://github.com/theagentamt/AMT.TrustCheckRadar.Lambdas/pull/126)
are deployed to the two existing Dev functions. Source commit
`94f0f448661d69798f7621cb777262e029a8814e` is integrated by release-V01 merge
`f66f06e18f075f90d32caeae7e92143a45be06a8`.

Owner response `Sentinel_4929ccce42288191adbc21f3e2ba5752` approved proposal
`Sentinel_ec752b96f8bc819182dc86ba9a3001a3`: those exact two code overlays only,
with existing execution/logging charges, no new infrastructure or purchase. This
supersedes the source task's deployment-pending status only for these artifacts;
it is not permission for another transaction or proof replay.

## Actual deployment and rollback targets

| Existing Dev function | Previous live / retained rollback | New live | Exact artifact SHA256 |
| --- | --- | --- | --- |
| trustcheckradar-dev-v1-play-handoff | 7 | 8 | ae22d0ddbc2c52bf791cf2728c2be2642a8f7e6968ca81b9d794e3b9cb268c06 |
| trustcheckradar-dev-v1-entitlements | 17 | 18 | ba9d1efbe25cdf2e6854e43da1a3c1df0d74462a2275f3d58355038a5255865a |

Final live readback completed at **2026-10-17 03:21:11.229077 UTC**. A separate
read-only post-metadata capture at **03:21:32.941815 UTC** passed all protected
comparisons. Both aliases are unweighted, and `$LATEST` and each live alias match
the approved candidate code hash. Old immutable versions7 and17 remain available
with their original hashes/configuration. No rollback was executed.

The bounded runner used the established Lambda `update_function_code` with
`Publish=true`, followed by guarded `update_alias` calls. Each of the four calls
has a durable intent written before dispatch and a confirmed journal entry after
the response. Revision IDs protected both code and alias changes. The journal is
completed with exactly two code publications and two alias updates; no mutation
was replayed. No function-configuration update API was used.

Pre-write independent review found and corrected two runner-only defects:
malformed-error sanitizer type handling and journal overwrites by a losing
exclusive creator/concurrent readback. All seven local synthetic guard probes
passed after correction. The actual plan was bound to final runner SHA256
`5acf50dbc6acb1e9c292a26078a93f354e26d44f2a192dbd60de1b75f7b11e7a`.
These fixes occurred before any AWS mutation. The exact review is retained in
`evidence/pr126-deployment-runner-review.json`.

## Preserved scope

Full before/after comparisons passed for target configuration and admission
lists, function role/trust and inline/managed IAM policies, live resource policies,
four application routes/integrations, API authorizers/stages, log settings,
reserved capacity, cleanup mappings/schedules and lifecycle settings. The other11
runtime fingerprints are unchanged. Cleanup stays on live17. Authority,
preparation, test-only enforcement and the selected admission are unchanged;
lifecycle/RTDN remain closed. No new function, infrastructure, permission,
capacity, retention, UAT/Production configuration or main promotion was created.

Private before/after configuration metadata and revision guards stay in owner-only
local records. Public evidence contains only fixed names, versions, hashes and
booleans; it excludes identities, credentials, proofs, tokens and payloads. The
adjacent JSON pins the private evidence files for independent review.

## Remaining acceptance and future operation

This verifies deployment, not real diagnostic event behavior or purchase recovery.
No application endpoint, proof replay, purchase, Restore, emulator, account or
security-setting action was performed by Infra. New diagnostic failures were not
deliberately generated. Historical409cause remains unknown; existing aggregate
records cannot be retroactively attributed. ATCR112/114 stay Open and
SECUR4ALL195 stays In Progress; physical cases remain in ATCR148.

Future approved observations must use the new code/alias baselines8 and18, while
preserving cleanup17 and all protected settings. Previous readiness receipts
pinned to verifier7/access17 are historical and must not be retimestamped or
reused as current readiness. No fresh transaction is authorized by this record.
Rollback, if later required and coordinated, is limited to the retained old
immutable versions under a fresh protected readback; this record does not execute
or automatically authorize a rollback or uncertain deployment retry.
