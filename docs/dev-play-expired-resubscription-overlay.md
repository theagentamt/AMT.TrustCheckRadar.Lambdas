# Exact Dev Handoff overlay for PR131

Preparation only: no Lambda upload, version publication, alias switch, configuration change, endpoint invocation, provider replay or purchase. This page replaces the artifact-preparation blocker in the [PR131 source handoff](dev-play-expired-resubscription.md); it does not authorize deployment. Source/component [SECUR4ALL-343](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-343) is complete. Deployment/live acceptance remain in [SECUR4ALL-195](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-195), [ATCR-112](https://andmorethings.youtrack.cloud/issue/ATCR-112) and [ATCR-114](https://andmorethings.youtrack.cloud/issue/ATCR-114). All five purchase journals remain consumed.

## Exact candidate and scope

| Item | Pin |
| --- | --- |
| Reviewed source head | `55f42bdd9bcb18e2c2966ff02aba481f4dfd92e3` |
| Source release merge | `775db2ef23bfa47249a8dd08f43096d23f831d25` |
| Baseline complete Handoff9 ZIP SHA256 | `4c9f18d5c3d64c596487a35d7e1f1287cf711507ac87f7ff6756466c793bb4f1` |
| Candidate retained file | `/tmp/amt-dev-play-resubscription-live9-overlay.zip` |
| Candidate ZIP SHA256 | `e91716c17d9dc66f3956452e5003d75e6c6e65ffc43c2f77d89c606c81cd8206` |
| Lambda CodeSha256 | `6RcWwX2dxm85VkUuUAPXXmxuZf/EPC932JxgbIHNggY=` |
| Candidate bytes | 6,279,910 |
| Future sole target | `trustcheckradar-dev-v1-play-handoff`, `us-east-1`, `live:9` to one new unweighted published version |

Fresh read-only proposal metadata at `2026-10-17T11:23:36.742235Z` confirmed Handoff9, entitlements18 and cleanup17; all 13 runtime and four-route/IAM/log/capacity/schedule/stream fingerprints match the retained post-PR128 baseline. Handoff is Python3.14/ARM64,256MiB,29s,reserved concurrency2. Versions1–9 were listed;10 is only the proposed next version, not reserved. Future execution must requalify versions, revisions, credentials and protected metadata. The12 non-target functions include access18 and cleanup17.

`scripts/prepare_dev_expired_resubscription.py` is offline and refuses optimized Python, wrong complete baseline, source drift or existing outputs. It produces the same bytes on independent rebuild. A verified preparation command is:

```sh
python -B scripts/prepare_dev_expired_resubscription.py \
  --baseline /tmp/amt-dev-prepare-auth-diagnostics-verified.zip \
  --output /tmp/amt-dev-play-resubscription-live9-overlay.zip \
  --manifest /tmp/amt-pr131-exact-overlay-manifest.json
```

The retained output already exists; this command must not overwrite it. Verify its hash or choose fresh preparation paths if rebuilding; do not interpret the builder as an uploader.

## Preservation boundary

Exactly these six existing entries change: `shared_check_authority/entitlements.py`, `shared_play_verification/proof.py`, `shared_purchase_ownership/service.py`, `v1_play_handoff/app.py`, `v1_play_handoff/diagnostics.py`, `v1_play_handoff/service.py`. Five are exact reviewed PR131 source bytes. The writer is a merge onto the deployed writer, not whole-file replacement.

The merged writer retains the original365-day complimentary audit policy, opt-in/default validation, source audit-operation-pointer validation, `_operation_sk`, `_expire_audit`, `_commit` audit-expiry validation/receipt fields, and all `set_complimentary` expiry behavior. New migration methods and replacements are AST-equal to PR131. Removing only the reviewed capability/method/CAS deltas reproduces the complete original writer AST, including retention. Existing unchanged methods are also checked independently.

All609 archive names/order remain; there are no added/removed entries. The other603 entries preserve uncompressed bytes, compressed payloads, and ZIP metadata. Necessary archive offsets move when changed entries change size. Dependencies and native libraries are unchanged. All476 archived Python files compile; no stale `.pyc` entries exist. The four inherited native libraries have LinuxELF64 AArch64 structure; this is static architecture qualification, not native Lambda execution.

## Combined artifact and rollback checks

The exact extracted ZIP passed208 focused tests with eight added retention/rollback cases. Runtime-loaded application source paths and all22 loaded archive Python-module hashes were checked against the candidate ZIP. The retained live9 writer and Handoff fixtures are hash-checked against the actual baseline bytes; the unchanged access18 snapshot fixture is hash-checked against its retained ZIP. Tests prove:

- Same-account migration, exact atomic ownership/ACCESS/usage conditions, competing roots, lost responses and request replay retain the PR131 behavior.
- Default complimentary gate still denies; finite and indefinite complimentary pointers/receipts remain valid across migration. Revocation retains audit expiry and exposes the existing paid period without regrant.
- Invalid audit expiry and corrupt audit pointers still fail closed.
- The old live9 writer reads migrated state; old Handoff exact replay and a new request preserve spent counts and the original funded period. Its generic writer still rejects another disconnected root.
- The unchanged access18 reader returns200, then199 after one completion, then denied access/null allowances at stored expiry, with trial activation still disabled in the fixture.

This is local synthetic SDK/Moto testing. Host-native test dependencies were preloaded because the unchanged Linux ARM64 `cryptography` library cannot execute on macOS. The initial host import failure is retained, not called an artifact defect or an ARM64 execution pass.

One existing account-deletion fixture requires `shared_account_finalization`, absent from the frozen Handoff9 ZIP. The same failure was reproduced against unchanged live9. It passes only with three pinned, explicitly test-only support files outside the ZIP; they were not added to the artifact. A separate run without that support passes207 tests with that one fixture deselected. This bounds the qualification rather than expanding the deployment dependency set.

The complete local scripts suite passes223 tests plus21 subtests, including10 new builder tests (six source-drift negatives, wrong baseline/set, deterministic preservation and optimized-Python refusal). Shellcheck, Python compilation and diff checks pass. The actual root handler also imports from the candidate and returns closed-gate503 with socket connections forbidden. None of these checks is live provider, new Lambda runtime, real acknowledgment, fresh paid-access or interruption acceptance.

## Approval-ready deployment boundary

After separate owner deployment approval, independently requalify this exact candidate/source/evidence and fresh live9/latest revisions, configuration, rollback and protected metadata. The narrow operation is one code upload, one version publication, and one revision-checked unweighted Handoff `live` switch. Preserve every non-code setting, existing selected actor/eligibility/ownership/catalog/test-only/authority/preparation controls and closed lifecycle/RTDN. No IAM, route, network, secret/key/grant, capacity, retention, schedule, stream, other function, Android or Play artifact change is needed or proposed. No broad package rollout.

No new DynamoDB action or key family is needed: the existing role's GetItem/ConditionCheckItem/PutItem model covers the proposed operation types. Policy inspection is not a new live permission test. Per eligible request there are bounded added point reads/conditions and normal ownership/period/usage/receipt writes; rejection can add one finite log event. There is no fixed-capacity increase, added provider read or numeric dollar-cost claim. The existing200-check policy can now be funded for the narrowly admitted case.

Use a durable mutation journal and alias/revision CAS. Stop on drift or uncertain mutation and reconcile read-only; no blind retry or automatic version increment. Ordinary rollback is only Handoff `live` back to immutable9 (`TJ8Y1cPWTFlkh6Ndfh8Sh89xFQesh/f/Z1ZGbHk7tPE=`), preserving unweighted routing. Rollback does not erase grants/spending or refund/revoke anything; it reinstates the old new-root rejection for future transitions. Closed version6 exists but is not part of ordinary rollback or this proposed setting scope.

Exact artifact review and preparation integration are separate from deployment approval. Later real migration/ACK/access and outage acceptance need separately authorized tests. No historical fifth-run predicate or provider-termination cause is established by this artifact; out-of-app association and the other source exclusions remain blocked.
