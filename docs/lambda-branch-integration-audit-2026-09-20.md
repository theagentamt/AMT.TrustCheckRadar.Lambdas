# Lambda branch integration audit — 2026-09-20

Baseline release2996af4; remote main12964d0 remains unchanged. All43 local/remote refs below were enumerated after fetch. Ancestry alone is insufficient for squash merges; unique branch ranges were compared against their historical PR squashes and current touched paths. This audit does not delete branches or treat uncommitted files as missing committed work.

One unrelated dirty entry in the original Lambda worktree was left untouched. All integration uses a fresh release-based worktree. Recovery is still disabled: proposed status-route semantics and expired-receipt reuse after physical removal remain unresolved acceptance gates, not source-integration blockers. Infrastructure candidate commits1e5ea90/7192614 are separate inactive gates, not activation authorization.

| Ref | Head | In main by ancestry | Disposition |
| --- | --- | --- | --- |
| `refs/heads/codex/atcr120-assessment-contract` | `3b26bd597e38` | no | Squashed in PR14 (31fc195); runtime/contract paths match; later readiness documentation retained. |
| `refs/heads/codex/atcr120-recovery-boundary` | `c6b3489f649a` | no | Squashed in PR15 (fa94c57); historical rubric hash preserved; newer implemented-message handoff retained. |
| `refs/heads/codex/audit-campaign-stories` | `829530033b16` | yes | Already covered by release ancestry. |
| `refs/heads/codex/itcr59-device-recovery-contract` | `a5ae99efdd80` | no | Missing recovery source integrated as fc48f5d, preserving current workflow gates and correcting 31-artifact inventory. |
| `refs/heads/codex/main-only-ci` | `d5dc37f92323` | no | Patch-equivalent to PR17 squash2996af4; no duplicate integration. |
| `refs/heads/codex/sprint-7-history-badges` | `c0396535d7eb` | yes | Already covered by release ancestry. |
| `refs/heads/codex/url-assessment-dev-evidence` | `f113bfaac0f4` | no | PR10 squash7cd2e6f; smoke evidence matches, newer publisher gates retained. |
| `refs/heads/codex/url-assessment-mobile-contract` | `2ab03ddef770` | no | Patch-equivalent PR11 squash65a7ca3; all85 touched paths match current release. |
| `refs/heads/codex/url-assessment-v1-0-contracts` | `a04f089f70cd` | no | PR8 squash1f4e7ce; superseded by PR11 stricter URL authority/projection contracts; retain current contracts. |
| `refs/heads/codex/url-consumer-recovery` | `44bdf31bdeb1` | yes | Already covered by release ancestry. |
| `refs/heads/codex/url-resolver-dev` | `4040fc166bf4` | no | Resolver changes squashed into PR7 43625eb; missing ancestor recoverya5ae99e now integrated separately. |
| `refs/heads/codex/url-resolver-review` | `fa5bb6aada60` | no | PR7 squash43625eb; resolver runtime matches; retain subsequent publisher/CI/manual-authority gates. |
| `refs/heads/codex/v1-authority-deletion` | `2f277a1eda6e` | no | Already covered by release ancestry. |
| `refs/heads/codex/v1-check-authority` | `798c0f48d6a6` | yes | Already covered by release ancestry. |
| `refs/heads/codex/v1-entitlement-writers` | `b4c6516da598` | no | Individual patches equivalent in PR13/main; later authority/deletion/message fixes retained. |
| `refs/heads/codex/v1-governed-message` | `5928e933a0aa` | no | Already covered by release ancestry. |
| `refs/heads/codex/web-risk-lookup-dev` | `3a7edd728d73` | no | PR9 squash33669f1; later verified smoke, mobile and authority gates supersede old evidence/runtime. |
| `refs/heads/main` | `e7b9e8421140` | yes | Already covered by release ancestry. |
| `refs/heads/release-V01` | `2996af4f2f7a` | no | Already covered by release ancestry. |
| `refs/remotes/legacy/HEAD` | `7e8f901cb514` | yes | Already covered by release ancestry. |
| `refs/remotes/legacy/main` | `7e8f901cb514` | yes | Already covered by release ancestry. |
| `refs/remotes/origin/HEAD` | `2996af4f2f7a` | no | Already covered by release ancestry. |
| `refs/remotes/origin/codex/atcr120-assessment-contract` | `3b26bd597e38` | no | Squashed in PR14 (31fc195); runtime/contract paths match; later readiness documentation retained. |
| `refs/remotes/origin/codex/atcr120-recovery-boundary` | `c6b3489f649a` | no | Squashed in PR15 (fa94c57); historical rubric hash preserved; newer implemented-message handoff retained. |
| `refs/remotes/origin/codex/audit-campaign-stories` | `829530033b16` | yes | Already covered by release ancestry. |
| `refs/remotes/origin/codex/main-only-ci` | `d5dc37f92323` | no | Patch-equivalent to PR17 squash2996af4; no duplicate integration. |
| `refs/remotes/origin/codex/sprint-7-history-badges` | `c0396535d7eb` | yes | Already covered by release ancestry. |
| `refs/remotes/origin/codex/url-assessment-dev-evidence` | `f113bfaac0f4` | no | PR10 squash7cd2e6f; smoke evidence matches, newer publisher gates retained. |
| `refs/remotes/origin/codex/url-assessment-mobile-contract` | `2ab03ddef770` | no | Patch-equivalent PR11 squash65a7ca3; all85 touched paths match current release. |
| `refs/remotes/origin/codex/url-assessment-v1-0-contracts` | `a04f089f70cd` | no | PR8 squash1f4e7ce; superseded by PR11 stricter URL authority/projection contracts; retain current contracts. |
| `refs/remotes/origin/codex/url-consumer-recovery` | `44bdf31bdeb1` | yes | Already covered by release ancestry. |
| `refs/remotes/origin/codex/url-resolver-dev` | `4040fc166bf4` | no | Resolver changes squashed into PR7 43625eb; missing ancestor recoverya5ae99e now integrated separately. |
| `refs/remotes/origin/codex/url-resolver-review` | `fa5bb6aada60` | no | PR7 squash43625eb; resolver runtime matches; retain subsequent publisher/CI/manual-authority gates. |
| `refs/remotes/origin/codex/v1-authority-deletion` | `2f277a1eda6e` | no | Already covered by release ancestry. |
| `refs/remotes/origin/codex/v1-check-authority` | `798c0f48d6a6` | yes | Already covered by release ancestry. |
| `refs/remotes/origin/codex/v1-entitlement-writers` | `b4c6516da598` | no | Individual patches equivalent in PR13/main; later authority/deletion/message fixes retained. |
| `refs/remotes/origin/codex/v1-governed-message` | `5928e933a0aa` | no | Already covered by release ancestry. |
| `refs/remotes/origin/codex/web-risk-lookup-dev` | `3a7edd728d73` | no | PR9 squash33669f1; later verified smoke, mobile and authority gates supersede old evidence/runtime. |
| `refs/remotes/origin/dependabot/github_actions/aws-actions/configure-aws-credentials-6.2.4` | `06032def7fe9` | no | Superseded by newer compatible AWS credentials6.3.0 PR6; do not downgrade to6.2.4. |
| `refs/remotes/origin/dependabot/github_actions/aws-actions/configure-aws-credentials-6.3.0` | `65f7c6527e4c` | no | Normally merged PR6 (445999f4a227e66d9f4006ae78fddd6cfafc862b): credentials6.3.0; local actionlint and unchanged gates verified. |
| `refs/remotes/origin/dependabot/pip/jsonschema-4.26.0` | `4a84e1311074` | no | Normally merged PR5 (30bd9fc72dccc4548e53cf6d7a9402bceec6f73b): jsonschema4.26.0; test both dev/runtime pin sets. |
| `refs/remotes/origin/main` | `12964d0e537d` | yes | Already covered by release ancestry. |
| `refs/remotes/origin/release-V01` | `2996af4f2f7a` | no | Already covered by release ancestry. |

## Local validation and integration boundaries

Recovery/stateful simulator/contracts:34 passed+49 subtests. Runtime-pinned jsonschema4.25.1 normal suite780 passed+212 subtests,9 expected isolated skips; separate authority/message Moto273 passed. Dev-only jsonschema4.26.0 also passes780 tests+212 subtests and isolated273; runtime Lambda pins are unchanged. actionlint and shellcheck pass. Source-only full packaging produces28 Lambda ZIPs and3 contract ZIPs, matching the publisher count31. No workflow dispatch, AWS calls, runtime activation or main mutation occurs.

Official dependency release references: [AWS credentials6.3.0](https://github.com/aws-actions/configure-aws-credentials/releases/tag/v6.3.0) and [jsonschema4.26.0](https://github.com/python-jsonschema/jsonschema/releases/tag/v4.26.0). The older AWS6.2.4 branch is superseded, not missing product work.

Recovery contract source bytes remain identical to a5ae99e. contract-manifest.json SHA256: `504a3b4d2667c35357b33d495543efc8d5304bea4655564e8eada60707c48884`; contract-set.json: `e5d09ff748ce5f6e2f0ad6d97dd2ce38723709e3e620365218dc20ff9c509050`; contract ZIP: `383729f8fdd261b935f9b8a35cb3399d66725e6325b304167838488e53856f21`.
