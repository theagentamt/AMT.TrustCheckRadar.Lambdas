# Dev purchase and access-admission diagnostics

This source-only change adds fixed failure enums for the unresolved Store13
verification/admission observations tracked in [ATCR-112](https://andmorethings.youtrack.cloud/issue/ATCR-112)
and [ATCR-114](https://andmorethings.youtrack.cloud/issue/ATCR-114). It does not
resolve the historical rejection, change admission or purchase policy, or pass the
unfinished interruption/ACK/paid-access acceptance. Both acceptance items remain
open. Physical cases remain in ATCR-148.

The later bounded read of 2026-10-17 02:41–02:45Z contains two verification409
rows, one preparation200 and seven access200. The initial one409 collection was
an earlier ingestion snapshot. The separate02:45–02:47 window contains three
access503 rows classified GET_ADMISSION / ENGINEERING_ACCESS_UNAVAILABLE /
VALIDATION. These are aggregates without account/request attribution. The exact
409 code remains unknown. None of the new enums retroactively identifies it.

## Source and privacy boundary

`v1_play_handoff.app` passes only the already-mapped public error code, status and
one constant execution stage to its best-effort hook. The event
`dev_play_verification_failure` has exactly:

- `event`, `schemaVersion=1`, `errorCode`, `httpStatus`, `stage`.
- Stages: ADMISSION, REQUEST_VALIDATION, RUNTIME_INITIALIZATION, VERIFICATION;
  unexpected inputs become UNKNOWN. Unknown or inconsistent statuses become0.
- Public error codes are the existing contract enums, not exception messages.

Recording requires STAGE=dev, existing PLAY_HANDOFF_ENABLED=true, and the exact
POST verification route/method. Closed handoff, other routes and non-Dev stages
produce no new verifier event. Successful verification produces no failure event.

The existing deployed Dev snapshot gate records
`dev_access_admission_failure` with exactly `event`, `schemaVersion=1`, `branch`.
The branches are ROUTE_SHAPE, ORIGINAL_GATE, BASE_GATE_CONFIGURATION,
EXTRA_GATE_CONFIGURATION, CLAIM_CONFIGURATION, VERIFIED_CLAIMS and
SUBJECT_NOT_ADMITTED; unexpected inputs become UNKNOWN. These mark the operation
that failed, not an account identity or proof of which request produced it.

There are no identities, request IDs, proofs, tokens, bodies, provider responses,
exception text or traceback fields in either new event. Unknown values cannot
become dimensions or text. Both hook and logger failures are suppressed. Logging
performs no SDK, storage, provider or retry operation. Existing response bodies,
statuses, retry flags, ownership/lineage policy, grant and acknowledgment logic are
preserved. The existing coarse access classifier is unchanged.

The generic `v1_entitlements.app` remains byte-for-byte unchanged. Its production
admission behavior is not replaced with the Dev snapshot gate. The new gate source
is the exact deployed Dev gate plus diagnostic-only assignments and hook.

## Offline artifacts and deployment scope

`scripts/prepare_dev_failure_diagnostics.py` is an offline ZIP builder, not a
publisher. It accepts only the two pinned existing deployed bundles below. It
checks changed-source SHA and AST equivalence after removing the reviewed hooks
and constant diagnostic assignments. All unrelated entries and metadata are
preserved; each overlay changes one original source entry and adds one module.
AST stripping alone does not certify arbitrary future hook implementations;
independent review and source hash pins remain required.

| Component | Existing Dev baseline | Changed entry | Added module |
| --- | --- | --- | --- |
| Verification | play-handoff live7, SHA25695194969560e208386aa942697f1ec175572cae09eb5a917a0574f1ad513f42d | v1_play_handoff/app.py | v1_play_handoff/diagnostics.py |
| Admission | v1-entitlements live17, SHA256b56547ef6ad6c16da94ba2bcae866a0f3301c11474a0858ad15420ef5d14acd5 | v1_entitlements/snapshot_gate.py | v1_entitlements/admission_diagnostics.py |

Verified offline commands, with operator-supplied private local baseline paths:

```sh
python -B scripts/prepare_dev_failure_diagnostics.py --component verification --baseline "$VERIFIER_BASELINE_ZIP" --output "$NEW_VERIFIER_ZIP"
python -B scripts/prepare_dev_failure_diagnostics.py --component admission --baseline "$ACCESS_BASELINE_ZIP" --output "$NEW_ACCESS_ZIP"
```

Outputs are exclusive, private local files; existing paths are refused. The
commands contain no AWS client, deployment or endpoint invocation. Candidate
digests, byte counts and validation results are in the adjacent JSON record.

**Not authorized or performed here:** code upload, version publication, alias
movement, environment/IAM/route/capacity/schedule/retention changes, deployment,
new transaction, proof replay or app/device action. A later deployment proposal
would cover only the two exact Dev code overlays, fresh baseline/configuration
readback, immutable versions and guarded alias readback. Preserve access17,
handoff7 and cleanup17 until separately approved; cleanup code, selected admission,
test-only enforcement, preparation, authority and disabled lifecycle/RTDN must be
unchanged. No UAT/Production or main promotion is included. No generic full-repo
package is proposed as a replacement for the deployed access overlay.

## Pending live qualification and cleanup

Before any later deployment, obtain its exact artifact-scoped approval and
verify the source/artifact hashes and unchanged protected configuration. This page
is not an executable deployment runner. After separately approved deployment,
use only existing authorized supported app observations; no transaction or proof
replay is authorized to generate diagnostics. Capture closed bounded windows and
only fixed event fields. Empty rows do not prove no calls/writes, and timing does
not attribute a request to an account.

Expected future observations: a real verification failure carries its existing
public code/status and fixed stage; a real admission failure carries its fixed
branch. Response/retry/grant behavior must remain unchanged. No diagnostic event
may expose excluded fields; privacy failure blocks further qualification. Preserve
original evidence and stop rather than retrying a financial action. Any later
purchase still requires its own allowance and exact-new cancellation plan.

The current attempt's exact-new cancellation, terminal Unarmed control and restored
connectivity are already independently recorded by Android. Its consumed budget
is not reset. No cleanup or runtime action was performed for this source change.
