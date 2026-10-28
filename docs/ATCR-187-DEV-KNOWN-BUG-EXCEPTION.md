# Owner-accepted AI use in Dev

Daniel accepted the current AI implementation for MVP Dev use on 2026-10-28 at
02:24:06 UTC and stopped further investigation. ATCR-187 under ATCR-186 retains
the unresolved bug. Qualification remains NO-GO; this is not UAT, production or
public-release approval. The original span-defect Astra attempts remain exhausted.

The fail-fast qualification made two requests: bank-gift-es passed and dev-019-es
omitted required AI_CONSEQUENTIAL_URGENCY. Its optional PRETEXT evidence missed
the exact leading connection anchor. Twelve cases were unrun. The consumed fence,
all failed evidence and full batch reservation remain intact. The exception does
not authorize more qualification calls, fixture-specific rules or prompt changes.

## Explicit selection

The evaluator can select **one** approval path. Normal qualifications still use
`ai_qualifications.json`, `MESSAGE_AI_QUALIFIED=true` and the matching qualification
ID. That registry remains empty. The new path uses `ai_dev_exceptions.json`, with
status `accepted_known_bug_dev_only` and `qualificationPassed=false`. Its record
binds the owner-decision evidence, prompt, schema, contract, model, inference
profile, network timeout and cost policy. It is accepted only in `STAGE=dev`.

To prepare the reviewed Dev configuration, set these evaluator values explicitly:

```text
MESSAGE_AI_DEV_EXCEPTION_ENABLED=true
MESSAGE_AI_DEV_EXCEPTION_ID=atcr187-known-bug-dev-20261028
MESSAGE_AI_QUALIFIED=false
MESSAGE_AI_QUALIFICATION_ID=
```

Both approval paths selected together, an unknown ID, missing record, tampered
evidence or a mismatched model/policy/prompt/schema/contract/cost/profile fail
closed before provider transport. Merely adding these source files does not
enable a runtime. The exception is never selected by an incoming request.

Use the existing AI enablement/primary/policy switches and exact bound settings:
`gpt-5.4-mini-2026-03-17`, low reasoning, 512 output tokens, 8,000 ms network budget,
and cost policy `{model: gpt-5.4-mini-2026-03-17, contextTokens: 400000,
maxOutputTokens: 512, inputNanoUsdPerToken: 750, outputNanoUsdPerToken: 4500}`.
These are retained profile bounds, not fresh pricing verification. Revalidate
pricing and the shared budget before any separately scoped real execution.
Preserve server-owned provider windows, circuit controls, evaluator enablement
and policy approvals; never infer their live values from this document.

## Rollout and limits

This patch performs no deployment, activation, provider request, secret read,
IAM change, account/device change or tracker write. Exact Dev rollout must bind
the reviewed source and package digests to the current Dev function versions,
aliases and nonsecret configuration, with the current rollback target retained.
S3 artifact publication is separate from deployment and activation.

Keep the existing engineering-subject/JWT gate, object/account authorization,
entitlements, attempt/currency budgets, strict model validation, privacy and
fixed provider destination. The consumer still invokes only the exact Dev
evaluator alias. This exception cannot extend an expired tester entitlement.
Verify actual Dev endpoint/Android use separately with real services and the
approved existing tester/device; a source test is not live acceptance.

Contain a failed rollout with the existing AI/circuit switches or the bound
alias/configuration rollback. Disable the exception switch and clear its ID to
remove this approval path. Re-review this exception before any UAT, Production
or public exposure or any change to its pinned profile. There is no automatic
promotion. The public response contract, prompt, semantic rubric and strict
validation are unchanged.
