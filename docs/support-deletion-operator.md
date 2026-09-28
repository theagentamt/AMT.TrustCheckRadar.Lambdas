# Support deletion operator candidate

This is the operational continuation of the [trusted human verifier](support-deletion-human-verifier.md), not the synthetic Dev qualification runner. It sends an existing, still-valid signed capability to the protected AWS IAM endpoint and separately observes the original operation with the restricted verifier role. It has no signing, mailbox, password, direct Lambda invocation, receipt-writing or direct deletion API.

The owner selected an initial acknowledgment/start-verification target of **within five business days** for the privacy inbox. This is not an account-deletion completion SLA. A request received earlier remains eligible after fresh ownership verification. The server command retains its original deletion deadline; support must not reset that clock or announce completion based on a 202 response.

## Preconditions and roles

Use the reviewed configuration and human policy for the exact environment, account, pool, table identities, signer/operator role IDs, generation, inventory and readiness. A hash binds reviewed inputs; it does not prove deployment readiness. The independently reviewed IAM boundary must deny direct Lambda invocation and signer submission. Actual SDK/IAM/HTTP qualification is separate evidence.

The same owner uses separate restricted AWS roles. This reduces accidental misuse; it is not two-person approval. The operator role needs only `sts:GetCallerIdentity` and the exact protected POST `execute-api:Invoke`. Status uses the verifier role's existing `sts:GetCallerIdentity`, exact tables `DescribeTable`, scoped `GetItem` for the owned profile/ledger/inventory, and exact pool `AdminGetUser`. It requires no `Query`, `Scan`, `Verify`, `Sign`, additional route or device-table permission. Select the appropriate AWS profile/session before each stage; the CLI validates the immutable role ID and assumed-role ARN.

The case directory is owned by the operator, mode 0700, and named by its canonical case UUID. Inputs and generated files are owner-only 0600. Keep the capability, challenge, intent and acknowledgment within the approved support-case custody; no stdout/body/token copies or separate permanent archive. The approved minimal verification record and correspondence expire 30 days after resolution. The actual case resolution/trash/backup cleanup mechanism remains an operational prerequisite. A failed write or ambiguous network response does not authorize removing an intent file or reissuing a new operation.

## Submit once

After the human verifier has prepared and signed an actual confirmation under the separately documented process, locally check its shape and current validity:

```sh
python scripts/support_deletion_operator.py --mode check \
  --configuration /private-case/configuration.json \
  --capability /private-case/OPERATION.capability.json
```

This offline check does **not** verify the KMS signature or prove ownership. The runtime verifies the signature and all current admission conditions. No client actor header or fabricated user JWT is sent.

Using the restricted operator AWS session, submit with the same configuration/capability and actual case directory:

```sh
python scripts/support_deletion_operator.py --mode submit \
  --configuration /private-case/configuration.json \
  --capability /private-case/OPERATION.capability.json \
  --case-reference CASE_UUID --case-directory /private-case/CASE_UUID
```

The original operation is read from the signed record. Before the sole network attempt, the CLI creates and fsyncs `OPERATION.admission-intent.json` exclusively, then fsyncs the directory. It refuses an existing intent. It uses one signed HTTPS POST, no redirect and no SDK/HTTP retry. Only the exact 202 acknowledgment produces a separate `OPERATION.admission-accepted.json`. Neither response nor this file means erasure is complete.

## Observe the original operation

Switch to the restricted verifier AWS session. Status requires the private intent, not a still-live capability, current profile or surviving Cognito user:

```sh
python scripts/support_deletion_operator.py --mode status \
  --configuration /private-case/configuration.json \
  --policy /private-case/policy.json \
  --intent /private-case/CASE_UUID/OPERATION.admission-intent.json \
  --poll-seconds 1800
```

Default status reads once. An explicit poll budget is limited to 1800 seconds with at least 60 seconds between polls. Reports contain counts/status/booleans and observed time, without subject, case, operation, provider body or signature. The private intent retains the exact original references. Output is:

- `ADMISSION_UNCONFIRMED`: no matching command observed. Do not infer that the POST never committed and do not repeat it automatically.
- `INCOMPLETE`: the exact original command exists but terminal proof or one or more of the twelve component receipts is missing/invalid. `deadlineOverdue` flags the original deadline; it does not extend it.
- `COMPLETE`: the actual production command/receipt validators accept all twelve receipts and the terminal command, with current retention clocks; the original command/inventory remain unchanged, profile is absent, and Cognito returns `UserNotFoundException`.
- `UNCONFIRMED` with exit 2: an invalid binding, provider failure, unexpected response or observation failure prevented a conclusion. Earlier acknowledged admission remains acknowledged.

Completion is receipt-backed component evidence with profile/Cognito absence. This restricted status tool does not independently scan device rows, unrelated accounts, backups or external providers. Synthetic qualification can perform additional separately authorized read-only baseline checks; that does not expand this tool's privileges.

## Failure and escalation

For timeout, 429, 5xx, malformed acknowledgment or failed output persistence, retain the existing intent and any acknowledgment. Reconcile through status under the original operation. Never replace the operation, remove the attempt marker, reset credentials, manufacture receipts or directly delete identity to force progress. If no command is visible, an authorized incident review must resolve the uncertain attempt before any separately approved resubmission; the CLI provides no replay override.

If signature proof expires before submission and no attempt exists, return to the verifier's documented fresh-confirmation/signing process. Do not edit proof clocks, reuse a consumed challenge as new confirmation or fabricate an email reply. If a POST intent exists, use status even after proof expiry; do not sign a replacement operation to evade uncertainty.

For missing components, report the affected receipt count and original operation privately to the responsible worker owner; inspect the existing stream/recovery schedules and sanitized alarms. The account-data abuse cleanup may need multiple 5-minute reconciliation ticks, so a short observation timeout is not a worker failure. A missed original deadline, recurring Lambda errors, denied IAM call or changed inventory is an incident. Retain incomplete status until the original operation has valid terminal proof. A provider outage must not be described as completed erasure.

The owner-approved workflow uses a deletion-specific challenge and an explicit reply in the shared inbox. The CLI does not send email or automate that human review. Mail delivery/reply handling, actual case retention cleanup and later release tests must be described as unperformed until separately qualified; synthetic assertions never count as a real mailbox confirmation.

## Local validation

`python -m pytest -q tests/support_account_deletion/test_operator.py` and the equivalent `python -O` invocation each pass 25 focused cases. They compose actual local RSA signing, admission service and SDK/Moto DynamoDB records, with injected transport, Cognito results and resource identity metadata. They cover original-operation submission, committed-response loss, output/file durability failure, expired or foreign input, restricted-role mismatch, exact acknowledgment typing and terminal/missing/foreign receipts. They do not prove live AWS IAM, gateway, mailbox handling or case-retention operations. No Lambda source or package bytes change in this operator-only increment.
