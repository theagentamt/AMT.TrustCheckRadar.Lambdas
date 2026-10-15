# SECUR4ALL-230 backend admission and accounting handoff

## Dev scope and result

This review started from cached `origin/main` commit
`662fbf47e9e392468b6d24ff7dc69206fb09a864` on 2026-10-01. A network refresh
was attempted first, but GitHub SSH authentication was unavailable; the cached
reference was already dated 2026-10-01. The production Lambda source already
contains the smallest complete current-authority implementation for governed
message admission and accounting. This increment adds direct acceptance
regressions rather than creating a second ledger or changing the reviewed runtime.

The implementation satisfies the backend source criteria as follows:

- `shared_check_authority` binds one client check identity and exact sanitized
  intent to one operation proof. Admission atomically creates one receipt,
  increments one reservation and increments the in-flight count. A duplicate
  receives the existing receipt and never receives another execution token.
- Settlement atomically converts the original reservation into either one used
  check for `complete`, or zero used checks for every limited, failed, partial,
  blocked, inconclusive or unavailable outcome. Retried and lost settlement
  responses reconcile the existing receipt.
- Paid settlement updates the account period and minimized global purchase-usage
  ledger in the same transaction. Conditions require a positive reservation and
  exact prior counters, preventing double charges and negative counters. Renewal,
  revocation and complimentary transitions preserve the original admitted basis.
- Paid and trial access require an active, unexpired grant with remaining
  allowance. Complimentary access bypasses the subscription/allowance period only;
  account, deletion, active-device, request, privacy, attempt, in-flight, provider
  circuit and provider budget controls still apply.
- The aggregate provider budget is independent of customer allowance. An open
  circuit or exhausted budget performs no evaluator call and settles an honest
  `BUDGET_LIMIT` unavailable result with zero charge.
- The legacy `/analysis` adapter remains a retired replay-only boundary. New work
  continues to return `LEGACY_MIGRATION_REQUIRED`; this increment does not import,
  route to or revive its historical dispatcher.

The added message-consumer cases prove the previously implicit acceptance edges:

- exhausted paid access, expired paid access and an ended trial stop before the
  evaluator and before provider-budget reservation;
- complimentary access still requires the active device and obeys provider cost
  budget, returning a limited zero-charge result when that budget is closed; and
- two admissions racing for the 200th paid check result in exactly one evaluation,
  one charge, `usedChecks == 200` and `reservedChecks == 0` in both account and
  purchase ledgers. The losing proof cannot evade exhaustion on retry.

## Local validation

Run from the repository root with the repository's Python 3.14 development
dependencies installed:

```bash
AMT_AUTHORITY_INTEGRATION=1 AWS_EC2_METADATA_DISABLED=true \
PYTHONDONTWRITEBYTECODE=1 python -m pytest -q -p no:cacheprovider \
tests/message_consumer tests/shared_check_authority tests/legacy_retirement

AWS_EC2_METADATA_DISABLED=true PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -q -p no:cacheprovider tests/message_evaluator

AWS_EC2_METADATA_DISABLED=true PYTHONDONTWRITEBYTECODE=1 \
python -m pytest -q -p no:cacheprovider tests/conversation_analysis
```

The final isolated runs passed 488 authority/message/legacy-retirement tests,
395 evaluator tests, and 119 legacy conversation-analysis tests plus 16 subtests.
Keep the conversation-analysis command in its own Python process: that legacy test
suite installs collection-time SDK stubs which are intentionally incompatible with
Moto fixtures collected later in the same process.

This is local Moto/component evidence. It does not prove an enabled AWS route,
real store ownership, real provider behavior or an assembled mobile journey.

## First synthetic trial qualification

The current candidate.1 release is sufficient for a bounded deterministic,
rule-only qualification with one allowlisted synthetic trial subject. The private
message evaluator must be enabled because it executes the reviewed rule policy.
The external model/AI path can remain disabled: use transport
`1.0.0-message-candidate.1`, keep `MESSAGE_AI_ENABLED=false` and
`MESSAGE_PROPOSER_ENABLED=false`, and send `reviewedLinks: []`. That path neither
calls the AI provider nor the URL assessment provider.

The consumer's `MESSAGE_PROVIDER_CIRCUIT_OPEN` name refers to its aggregate
evaluator-dispatch circuit. It must be `false` for the private deterministic
evaluator invocation; leaving it `true` intentionally returns a settled
`BUDGET_LIMIT` result without evaluating rules. Positive reviewed window, attempt
and failure caps remain mandatory. This does not authorize any gate change, but it
is the exact configuration distinction needed by the infrastructure activation
work.

For this bounded run, enable only the existing Dev authority, V1 entitlement,
trial-retention, message-consumer and message-evaluator gates for the exact
allowlisted subject. Keep candidate.2/candidate.3 AI admission, AI qualification,
the optional proposer, reviewed-link input and unrelated general access disabled.
The consumer and evaluator must carry the approved candidate.1 policy version and
digest, and the consumer must invoke the exact Dev evaluator `live` alias already
enforced by source. The test subject must already have an active registered device,
an active age-verified profile and no deletion fence.

After the reviewed infrastructure change exposes the installed routes, the
following connected commands exercise the exact contract. Set the API base, then
enter the token and device fingerprint through silent prompts. The commands keep
both values out of shell history and process arguments:

```bash
export SEC230_API_BASE='https://REVIEWED-DEV-API.example'
umask 077
SEC230_TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/sec230.XXXXXX")"
trap 'rm -rf "$SEC230_TMP_DIR"' EXIT
read -rsp 'Dev Cognito access token: ' SEC230_ACCESS_TOKEN
printf '\n'
read -rsp 'Active device fingerprint: ' SEC230_DEVICE_FINGERPRINT
printf '\n'
printf 'Authorization: Bearer %s\nx-device-binding-fingerprint: %s\n' \
  "$SEC230_ACCESS_TOKEN" "$SEC230_DEVICE_FINGERPRINT" \
  > "${SEC230_TMP_DIR}/sec230-headers.txt"
unset SEC230_ACCESS_TOKEN SEC230_DEVICE_FINGERPRINT
```

Read the current authority snapshot and explicitly activate the one-time trial if
the snapshot reports `trial.activationAvailable: true`:

```bash
curl --fail-with-body --silent --show-error \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  "${SEC230_API_BASE}/v1/access" > "${SEC230_TMP_DIR}/sec230-access-before.json"

curl --fail-with-body --silent --show-error \
  -X POST \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  -H 'Content-Type: application/json' \
  --data-binary '{"schemaVersion":1,"activate":true}' \
  "${SEC230_API_BASE}/v1/access/trial" > "${SEC230_TMP_DIR}/sec230-trial.json"

curl --fail-with-body --silent --show-error \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  "${SEC230_API_BASE}/v1/access" > "${SEC230_TMP_DIR}/sec230-access-activated.json"

jq -e '.access.basis == "trial" and .allowance.limit == 10 and
  .allowance.completedUsed == 0 and .allowance.reserved == 0 and .allowance.remaining == 10 and
  (.trial.expiresAtEpoch - .trial.activatedAtEpoch) == 604800 and
  .allowance.periodEndsAtEpoch == .trial.expiresAtEpoch' \
  "${SEC230_TMP_DIR}/sec230-access-activated.json" >/dev/null
```

The activated snapshot must report `basis: trial`, limit 10, used 0, reserved 0,
remaining 10 and an expiry exactly seven days after activation. Use a fresh
`checkId` for the subject, then create the exact deterministic candidate.1 intent:

```bash
export SEC230_CHECK_ID='sec230-rule-only-001'

jq -n --arg check "${SEC230_CHECK_ID}" '{
  transportVersion:"1.0.0-message-candidate.1",
  checkId:$check,
  entryPoint:"message",
  language:"en",
  target:{
    scope:"sanitized_message",
    sourceType:"pasted_text",
    sanitizedText:"Send me your account password and the login code.",
    speakerRole:"other",
    entities:[],
    withheldLinks:false,
    reviewedLinks:[]
  }
}' > "${SEC230_TMP_DIR}/sec230-message.json"

curl --fail-with-body --silent --show-error \
  -X POST \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  -H 'Content-Type: application/json' \
  --data-binary @"${SEC230_TMP_DIR}/sec230-message.json" \
  "${SEC230_API_BASE}/v1/message-checks/prepare" \
  > "${SEC230_TMP_DIR}/sec230-prepare.json"

jq --slurpfile prepared "${SEC230_TMP_DIR}/sec230-prepare.json" \
  '. + {operationProof:$prepared[0].operationProof}' \
  "${SEC230_TMP_DIR}/sec230-message.json" \
  > "${SEC230_TMP_DIR}/sec230-submit.json"

curl --fail-with-body --silent --show-error \
  -X POST \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  -H 'Content-Type: application/json' \
  --data-binary @"${SEC230_TMP_DIR}/sec230-submit.json" \
  "${SEC230_API_BASE}/v1/message-checks" \
  > "${SEC230_TMP_DIR}/sec230-submit-response.json"

jq -e '.state == "settled" and .outcome.processingOutcome == "complete" and .outcome.verdict == "high_risk" and
  (.outcome.ruleIds | index("REQUEST_SECRET_DISCLOSURE")) != null and
  (.outcome.limitationCodes | length) == 0 and (.outcome.evidence | length) == 0 and
  .accounting.chargedChecks == 1' \
  "${SEC230_TMP_DIR}/sec230-submit-response.json" >/dev/null
```

The response must be settled and charged once, with `processingOutcome: complete`,
`verdict: high_risk`, rule `REQUEST_SECRET_DISCLOSURE`, no limitations and no
provider evidence. Repeat the identical submit to prove replay, then reconcile
without sending message content:

```bash
curl --fail-with-body --silent --show-error \
  -X POST \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  -H 'Content-Type: application/json' \
  --data-binary @"${SEC230_TMP_DIR}/sec230-submit.json" \
  "${SEC230_API_BASE}/v1/message-checks" \
  > "${SEC230_TMP_DIR}/sec230-submit-replay.json"

jq -n --arg check "${SEC230_CHECK_ID}" \
  --slurpfile prepared "${SEC230_TMP_DIR}/sec230-prepare.json" '{
  transportVersion:"1.0.0-message-candidate.1",
  checkId:$check,
  operationProof:$prepared[0].operationProof
}' > "${SEC230_TMP_DIR}/sec230-reconcile.json"

curl --fail-with-body --silent --show-error \
  -X POST \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  -H 'Content-Type: application/json' \
  --data-binary @"${SEC230_TMP_DIR}/sec230-reconcile.json" \
  "${SEC230_API_BASE}/v1/message-checks/reconcile" \
  > "${SEC230_TMP_DIR}/sec230-reconcile-response.json"

curl --fail-with-body --silent --show-error \
  -H @"${SEC230_TMP_DIR}/sec230-headers.txt" \
  "${SEC230_API_BASE}/v1/access" > "${SEC230_TMP_DIR}/sec230-access-after.json"

jq -e --slurpfile replay "${SEC230_TMP_DIR}/sec230-submit-replay.json" \
  --slurpfile reconcile "${SEC230_TMP_DIR}/sec230-reconcile-response.json" \
  '.accounting == $replay[0].accounting and .accounting == $reconcile[0].accounting' \
  "${SEC230_TMP_DIR}/sec230-submit-response.json" >/dev/null
jq -e '.allowance.completedUsed == 1 and .allowance.reserved == 0 and .allowance.remaining == 9' \
  "${SEC230_TMP_DIR}/sec230-access-after.json" >/dev/null
echo 'SECUR4ALL-230 rule-only accounting assertions passed.'
```

The original submit, replay and reconcile accounting objects must match exactly.
The final authority snapshot must report used 1, reserved 0 and remaining 9. The
independent Lambda invocation metric must show one consumer-to-evaluator invocation
for the logical check and no URL-assessment or AI-provider invocation.

## Pending Dev activation and release qualification

The installed Dev message consumer/evaluator and current authority remain gated
off. No gate, IAM policy, retention value, alias or deployment was changed here.
Before the backend can be called release-qualified, reviewed infrastructure must
select an exact published Lambda artifact and contract, preserve the legacy route
retirement, and explicitly configure the existing authority, engineering-subject,
consumer, evaluator, AI qualification and provider-budget gates. Current store
ownership and allowance records must be provisioned through the verified modern
authority path; tests must not manufacture a legacy FREE/PRO grant.

The release-test item remains pending and needs a tracker link because tracker
access was unavailable during this Lambda increment. A future operator should use
dedicated synthetic Dev accounts representing paid with one remaining check,
exhausted paid, expired paid, ended trial and complimentary access. Record exact
release commit, Lambda version/alias, contract version, environment identity and
privacy-safe timestamps before running these cases:

1. Submit one complete supported message with the paid one-check account. Verify
   one evaluator invocation, one settled charged receipt and zero remaining checks.
2. Repeat submit and reconcile with the same proof, including a deliberately lost
   client response. Verify the original receipt and no second evaluator call or
   deduction.
3. Race two different prepared proofs for the final check. Verify one evaluation
   and charge, no negative counter, and denial of the losing proof.
4. Attempt prepare/submit with exhausted, expired and ended-trial accounts. Verify
   no evaluator invocation and no provider-budget attempt.
5. Exercise complimentary access with an invalid device and with the reviewed
   provider circuit/budget closed. Verify the device denial and then the honest
   `BUDGET_LIMIT` zero-charge result; no evaluator call may occur in either case.
6. Exercise a bounded provider timeout/lost evaluator response. Verify a settled
   limited zero-charge result or unknown accounting requiring proof-only
   reconciliation, according to where uncertainty occurred. Never resend content.
7. Verify the retired `/analysis` route cannot start work or grant legacy access,
   then repeat after the reviewed rollback procedure. Rollback must leave the
   legacy migration fence and current-authority accounting intact.

For each case, compare API accounting with the minimized authority receipt and
account/global counters. Capture invocation metrics without message content,
tokens, account identifiers or secrets. Clean up only the dedicated synthetic
fixtures through the approved account-deletion path. Any counter disagreement,
second provider call, legacy grant, negative count or completed result produced
under a closed provider budget blocks release and returns to the owning component
as a linked defect.

This release-test item closes only after the enabled Dev cases pass and their exact
evidence is linked. Deployment or gate enablement alone is not behavioral proof.
