# Disposable Cognito all-component qualification

This fixture-only increment adds a separately selected `cognito_qualification.lambda_handler`
archive. It reuses all eleven actual receipt producers and the shared finalizer,
but replaces the injected identity adapter with exact-pool/subject SDK calls.
No production handler, public wire contract, runtime gate or retention changes.
The original 52-case `campaign_qualification.lambda_handler` remains injected.

## Bound resources and inputs

Use a new independent fixture run for **each** destructive case. Root provisions
the existing twelve dedicated tagged tables and HMAC key, plus a new disposable
user pool and one newly created synthetic user. No existing account is eligible.
The pool name is `amt-campaign-completion-qual-<12-lowerhex-run>-cognito` and its
exact tags match the four fixture tags: Purpose=campaign-completion-qualification,
QualificationRunId=<run>, Environment=dev, Project=trustcheckradar.

Configure `UsernameAttributes=['email']`, no Lambda triggers, and create the
synthetic user with `MessageAction=SUPPRESS` and `ForceAliasCreation=false`.
This differs from email aliases: email-only sign-in generates the username.
The provisioning journal and runtime must nevertheless verify the actual returned
Username exactly equals its unique `sub`; no inferred or fabricated mapping.
[AdminCreateUser reference](https://docs.aws.amazon.com/cognito-user-identity-pools/latest/APIReference/API_AdminCreateUser.html).

Keep the existing resource/source pins and add only:

- `QUALIFICATION_COGNITO_POOL_ID`: exact disposable pool ID in us-east-1.
- `QUALIFICATION_COGNITO_SUBJECT`: exact canonical UUIDv4 user subject.

Input is exactly schemaVersion1, operation `qualify-account-deletion-identity`,
runId matching the fixture, and case `identity_complete` or
`identity_delete_lost_ack`. No arbitrary identity/resource/event override.
The separate archive manifest must select the identity handler; the baseline
manifest cannot qualify it merely by adding environment values.

Runtime requires DescribeUserPool, AdminGetUser, AdminUserGlobalSignOut and
AdminDeleteUser on the **exact disposable pool ARN only**. No production pool,
ListUsers, user creation/update, messaging or pool mutation grant. Because IAM
scopes these admin actions to a pool, a checked wrapper independently requires
the exact designated pool and subject on every identity call. The infrastructure
journal owns any eventual pool/user cleanup; the runner cannot remove the pool.

Pool ARN/account/Region/name/tags/sign-in mode/no-triggers, enabled recognized
user state and exact unique-sub mapping are checked before any fixture reset or
seed. Every SDK call uses the existing six-second remaining-time cutoff and
fixed SDK timeouts/no automatic retry. Resource readback can become stale after
preflight; independently prevent concurrent privileged fixture reconfiguration.
Do not reuse an old run or recreate the identity to make a failed replay pass.

## Cases and evidence boundary

Both cases seed the existing nonempty device/recovery/abuse/outbox/ownership,
History multigeneration, paid-reservation and token fixtures. Real producer
transactions generate all eleven receipts; no component receipt is fabricated.
Missing proofs block finalization. The real finalizer checks actual identity
mapping, deletes the one disposable identity, writes the atomic fixed COMPLETE
fence/IDENTITY receipt, removes SEALED control and suppresses duplicate calls.
An independent final AdminGetUser must return UserNotFoundException. Component
retention clocks and other-account table sentinels remain unchanged.

`identity_delete_lost_ack` injects one failure **after** the successful SDK delete;
the first attempt must retain REQUESTED and no IDENTITY receipt. Retry observes
actual identity absence, completes the original command and never deletes twice.
This injection models response loss; it does not reproduce an AWS network fault.

Inventory/paid/token fixture values remain explicit synthetic assumptions.
The token cipher is not Google/KMS acceptance. The disposable identity pass does
not qualify production pool IAM, JWT admission, signup triggers, live historical
coverage, backups or restore admission. Those require separately reviewed real
Dev inventory and deployment evidence. No checkpoint-policy approval is implied.

## Build and local validation

From the clean exact reviewed source, run:

```
python scripts/build_campaign_qualification.py --source-sha <full-SHA> \
  --output-dir /tmp/cognito-deletion-qualification --real-cognito
```

This emits `cognito_account_deletion_qualification.zip` plus the source/member
manifest. The four production campaign archives are unchanged source and do not
contain either fixture identity adapter. Dependencies are application Python and
the AWS Python3.14 runtime SDK, without host-native wheels.

159 combined SDK/Moto cases passed at d290b8e: 28 identity/resource cases, 83
existing qualification cases and 48 shared-finalizer cases. A final focused run
passed all 29 identity/resource cases, adding refusal to reuse a deleted identity
before resetting completed evidence; fixture source stayed unchanged. Compile
and diff checks passed.
The focused validation covers actual receipt composition with modeled Cognito,
normal deletion, committed delete response loss/retry, wrong pool/subject/tags,
preflight refusal without writes, fixed handler/event separation, per-call guard,
error minimization and the original 52-case baseline. Local modeled Cognito is
not actual AWS identity deletion; cloud execution belongs to the reviewed root
fixture provisioner and must be reported separately.
