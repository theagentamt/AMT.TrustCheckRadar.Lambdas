# Dev Handoff retained resubscription overlay deployment

The owner approved only the prepared existing Dev Handoff code deployment: response `Sentinel_29ab99976f2c8191bf152efc8a00f0a9` to proposal `Sentinel_8f6885a147f0819194e414a0d3fd1a84`. This did not authorize another purchase, emulator action, setting change or wider rollout. The source and artifact preparation are [PR131](https://github.com/theagentamt/AMT.TrustCheckRadar.Lambdas/pull/131) and [PR132](https://github.com/theagentamt/AMT.TrustCheckRadar.Lambdas/pull/132), with the [exact overlay runbook](dev-play-expired-resubscription-overlay.md).

## Actual deployed artifact

| Item | Verified value |
| --- | --- |
| Sole function | `trustcheckradar-dev-v1-play-handoff`, `us-east-1` |
| Live alias | Unweighted, `9` → `10` |
| Artifact SHA256 | `e91716c17d9dc66f3956452e5003d75e6c6e65ffc43c2f77d89c606c81cd8206` |
| Lambda CodeSha256 | `6RcWwX2dxm85VkUuUAPXXmxuZf/EPC932JxgbIHNggY=` |
| Artifact bytes | 6,279,910 |
| Root completed readback | `2026-10-17T21:21:34.287669Z` |
| Immutable ordinary rollback | Handoff version9, `TJ8Y1cPWTFlkh6Ndfh8Sh89xFQesh/f/Z1ZGbHk7tPE=` |
| Implementation head | `55f42bdd9bcb18e2c2966ff02aba481f4dfd92e3` |
| Preparation head / release | `cfc1241905b5a24dc6165c1d5025837f22161459` / `a51e17732ce101a54162e6a3c4db1b60597a76c5` |

The candidate changes exactly six existing Python entries and retains the other 603 entries, compressed payloads, metadata and dependencies. The merged writer preserves deployed complimentary audit retention and pointer/expiry behavior. No source-only generic ZIP was used.

## Execution and guards

The first existing AWS renewal session 2530 terminated with `InvalidGrantException`; no mutation had started. The owner requested a fresh single flow, 15552, then confirmed completion. That same CLI session completed successfully and the existing SDK account check passed. No duplicate concurrent flow, credentials entered by the agent, new permission or application-auth change occurred. No sign-in code or credential is retained here.

The reviewed fixed runner SHA256 is `21ae4edc93653cccaeeca625e632229fba7c7f231cec663a0834694fa0ab6d1b`. Independent static/offline review passed six probe groups, including mutation scope, artifact/revision/configuration/weighted-alias negatives, journal failure and uncertain-call replay refusal. No real runtime call was part of that runner review.

Fresh plan digest `db1760fecf13f691565a798341fd6a0a061b2e30cbfe8f5dd45e8c849e6c6ac4` matched the retained post-PR128 baseline and passed again at apply. It verified current live/latest9, expected versions 1–9, exact source/artifact/review pins, account/session, selected actor eligibility/deletion and ownership inventory, non-code configuration, alias/latest revisions, twelve other runtime fingerprints, routes, critical IAM policies/trust, logging/capacity, API authorizers/stages, schedules and cleanup stream mapping. SDK mutation retries were disabled.

The durable exclusive journal records exactly two attempted and confirmed mutations: Handoff code upload with `Publish=True`, producing version10; then its revision-checked unweighted `live` alias switch. The journal ended `completed`. No uncertain mutation was retried. Version9 was requalified before alias switch and in the final readback; rollback was not executed.

Access remains `live:18`; cleanup remains `live:17`. All twelve non-target runtime fingerprints and protected metadata remain unchanged. Handoff/preparation/authority/catalog remain enabled, test-only selected-actor restrictions retained, lifecycle/RTDN closed. No IAM, secret/key, account, retention, capacity, route, infrastructure, schedule, stream, Android/Play artifact or security-preference setting changed.

## Verification and limits

The root readback verifies published10/live/latest exact code and configuration, active/successful update state, immutable rollback9 and unchanged protected metadata. The normalized evidence deliberately replaces the raw runner's `NotExercised` field with an operator-only statement: this deployment invoked no application endpoint or provider verification. It does not assert that concurrent/background requests were absent.

Independent actual readback passed at `2026-10-17T21:22:59.061455Z`. The observer separately checked live/latest/published10, all protected metadata and rollback9, and downloaded the published AWS archive in memory: all 6,279,910 bytes match the approved ZIP. The presigned code URL was neither retained nor output. This is control-plane/code verification, not provider acceptance. Bounded snapshots are not a cross-service atomic lock.

The artifact had 207 independently passing self-contained local tests, 208 with one explicitly test-only existing deletion-fixture supplement, eight retention/rollback cases, ten builder guards and 244 script-suite JUnit entries including subtests. Those are local synthetic/Moto and package checks. Linux ARM64 native dependencies remain byte-identical, but local macOS tests used host dependencies. This task performed no Lambda invocation, migration request, proof replay, provider lookup, new purchase, acknowledgment/access test or rollback rehearsal.

The narrow behavior admits only fresh same-account-bound foreground single-root normal-funded purchases after nonoverlapping retained stored expiry; standard 200-check funding has no carryover and historical usage remains intact. Out-of-app association, replacement, missing binding, overlap, extended access and generic/background/RESTORE migration remain blocked. Stored expiry does not establish actual old Google termination. The historical fifth rejection predicate/cause remains unknown.

## Continuing work

Source/component [SECUR4ALL-343](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-343) remains Done. Live integration/release work stays open in [SECUR4ALL-195](https://andmorethings.youtrack.cloud/issue/SECUR4ALL-195), [ATCR-112](https://andmorethings.youtrack.cloud/issue/ATCR-112) and [ATCR-114](https://andmorethings.youtrack.cloud/issue/ATCR-114); physical-only cases remain [ATCR-148](https://andmorethings.youtrack.cloud/issue/ATCR-148). All five transaction budgets remain consumed. Any further transaction requires separately coordinated authority and prerequisites.

If a material regression is observed, the ordinary rollback target is only the Handoff alias back to immutable9 using fresh revision/metadata checks and the applicable incident authorization. Do not delete grants or usage, refund/revoke, change another function, weaken gates or retry an uncertain mutation. Rollback restores the previous new-root limitation and does not undo already committed state.

The parent will coordinate the queued V1 MVP backend/mobile story-to-code audit after receiving this deployment result. No audit conclusion or new audit-driven runtime change is asserted here.
