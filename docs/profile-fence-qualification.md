# Profile-writer deletion-fence qualification

This increment qualifies the existing post-confirmation and age-attestation
transaction fences and removes sensitive exception logging. It is source and local
package evidence only: neither package has been published or deployed by this
increment. There is no live signup, age change or account-deletion activation.

## Resulting behavior

- Post-confirmation atomically creates only an absent `USER#<sub>/PROFILE`, while
  `ACCOUNT#<sub>/ACCOUNT_DELETION` is absent in the deletion ledger. Any existing
  fixed fence, including COMPLETE or unknown state, blocks profile recreation.
- Duplicate confirmation preserves the existing profile but still fails the
  trigger. This is state-preserving retry behavior, **not successful replay**;
  this change does not invent a duplicate-success contract or read permissions.
- Age attestation atomically updates only an existing matching-sub PROFILE with
  ACTIVE or PENDING_AGE_GATE status, under the same absent-ledger-fence check.
  Repeated acknowledgments preserve identity/creation fields, while updating
  attestation timestamps; no exactly-once timestamp promise is made.
- Both trigger paths log fixed diagnostics and throw fixed exceptions with the
  original chain suppressed. Merely removing application traceback logging would
  leave Lambda's uncaught-exception logging able to expose an SDK message.
- The age API keeps its public response shape. Proven conditional cancellation
  remains 404. Missing resources, mixed/unknown cancellation reasons and storage
  failures now return its generic 500 instead of incorrectly implying a missing
  profile. Application error logs contain only a fixed diagnostic code.

The handlers retain their existing input/authentication behavior. The API's JWT
subject takes priority over its legacy body fallback; this increment does not
qualify unauthenticated exposure or alter the gateway authorizer requirement.

## Deployment contract

Publish exactly `post_confirmation.zip` and `age_attestation.zip` from one reviewed
source commit, each `app.lambda_handler`, Python3.14/ARM64. The build defaults now
select 3.14 for both. These archives contain one Python source member each, no
third-party or native dependencies, and use Lambda's runtime-provided boto3 and
botocore. Local execution used Python3.14 with actual SDK/Moto; it does not prove
AWS runtime loading or actual IAM permissions.

Both require `USERS_TABLE_NAME` and `DELETION_LEDGER_TABLE_NAME`. Post-confirmation
also retains its existing ARN-name fallbacks. Missing deletion configuration must
not be used as a compatibility mode: post-confirmation fails at import and age
fails the request without changing the profile.

Required permissions are unchanged:

| Handler | Users table | Deletion ledger |
| --- | --- | --- |
| Post-confirmation | transactional PutItem, `USER#*` | ConditionCheckItem, `ACCOUNT#*` |
| Age attestation | transactional UpdateItem, `USER#*` | ConditionCheckItem, `ACCOUNT#*` |

There are no standalone GetItem, ledger writes, DeleteItem, provider or Cognito
identity-deletion calls. Infrastructure must independently qualify IAM condition
keys: `dynamodb:EnclosingOperation=TransactWriteItems` is appropriate for the
mutations, but must not be assumed applicable to ConditionCheckItem without
actual authorization evidence. Preserve the exact Cognito trigger permission,
source account/pool ARN and trigger map. Coordinate code, environment and role
changes across the identity-workflows and API selectors before deploying either
active service path. Existing retention and deletion/export gates stay unchanged.

A valid synthetic trigger against the installed production-configured function
would write real account data. The disabled-handler `{}` smoke pattern used for
other candidates does not qualify these active writers. Any live write test
requires a separately reviewed isolated fixture/configuration or exact account
scope; local Moto tests perform no AWS calls.

## Validation

Commands, run in separate processes to avoid the ordinary suite's SDK doubles:

```sh
AMT_AUTHORITY_INTEGRATION=1 /tmp/amt-account-privacy-venv/bin/python -m pytest -q tests/profile_fence/test_dynamodb.py
/tmp/amt-account-privacy-venv/bin/python -m pytest -q tests/post_confirmation tests/age_attestation
bash scripts/build_lambda_zip.sh --function post_confirmation --output-dir /tmp/amt-profile-fence-packages
bash scripts/build_lambda_zip.sh --function age_attestation --output-dir /tmp/amt-profile-fence-packages
```

- 30 actual SDK/Moto cases passed: successful creation/attestation, duplicate
  preservation, requested/completed/unknown fences, fence-at-commit races, missing
  or mismatched profiles, repeated age decisions, denied/missing ledger, sensitive
  SDK diagnostics and unknown/mixed cancellation reasons.
- 6 existing SDK-double tests passed.
- Both full build commands succeeded; their one source member was compared
  byte-for-byte with its source. Extracted archives executed five synthetic Moto
  checks: creation, age promotion, both completed-fence refusals and preserved
  profile. Hashes and sizes are in
  [local-packages.json](evidence/profile-fence-qualification/local-packages.json).
- Python compile, shell syntax and `git diff --check` passed.

This is bounded component evidence, independent of mobile hardware. Physical
acceptance remains with ATCR-148; it is not claimed by these tests. Actual trigger
installation, IAM verification, live signup behavior and full account erasure
remain separate acceptance steps.
