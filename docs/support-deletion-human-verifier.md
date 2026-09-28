# SEC333: supervised shared-inbox verifier candidate

This source-only increment implements the approved human-verification model. It
does not send email, read the mailbox, deploy a route/key/role, submit deletion or
change Proton settings. The existing admission candidate remains disabled.

The owner confirmed Proton Mail Plus and a shared privacy inbox; independent
confirmation goes to the verified email already on the account. The owner performs
both verification and admission through separate restricted AWS roles. This limits
accidental use of the wrong authority; it is **not two-person approval** and does
not defend against a deliberately dishonest authorized signer. A human reviews
the actual explicit reply in the existing inbox. The signer is that trusted human
authority, not an automated email authentication engine. Incoming sender fields,
an operator JSON document, or a matching challenge alone do not independently
establish verification.

The owner approved keeping the minimal verification record with the support case
for 30 days after resolution alongside correspondence: case/operation reference,
verifier, confirmation time and outcome, without copied message bodies or identity
documents. There is no independent longer audit or new server table. Actual
case-resolution tracking, selective deletion, Trash/backups and local-copy cleanup
still need operational qualification. Shared-mailbox global retention settings must
not be changed or presented as resolution-based case deletion.

## Explicit reviewed inputs

`scripts/support_deletion_verifier.py` reuses the production admission contract and
profile/inventory validators. It requires owner-only canonical JSON files for the
admission configuration and human-verification policy; no enabled policy defaults
are supplied. The configuration pins the canonical policy SHA256. That hash is an
integrity binding to an externally reviewed policy, not self-generated approval.
Default `--mode check` validates only these files without creating SDK clients and
reports `policyApprovalInferred:false`.

The policy has exactly the fields in `POLICY_FIELDS`: version/method, exact signer
role ARN and immutable role ID, opaque custody reference, same-support-case audit
location, 30-day resolution-based retention, same-owner/separate-role arrangement,
and explicit challenge, reply-age and capability validity bounds. Challenge and
reply bounds cannot exceed 24 hours; capability lifetime cannot exceed the
admission configuration's maximum of 300 seconds. These are engineering ceilings,
not implicit selected production values. An older original request may be verified
now; a five-minute capability window is not a support-response SLA. Actual role
ARNs/trust/custody, operational validity values and approved runtime/readiness pins
must be selected and reviewed before any real use.

Recommended initial engineering selection is 86,400 seconds for challenge validity
and maximum reply age, with 300 seconds for the admission capability. These are
explicit proposals for the reviewed policy file, not selected defaults or evidence
of active settings. Scheduling the human review and operator handoff must fit the
chosen capability window; expiry is not permission to silently refresh a proof.

Every file is bounded to 8 KiB, canonical ASCII JSON without duplicate keys,
owner-only mode0600, a regular non-symlink with one link. Case outputs require an
owner-only mode0700 directory named by the case UUID. No body, email, subject,
challenge, signature or provider exception is printed. Do not enable shell tracing,
SDK debug logging or capture sensitive files in support tickets/source control.

## Prepare a challenge (read-only AWS, private file output)

The `prepare` mode checks live STS assumed signer role and immutable role ID,
exact users/ledger TableIds/ARNs, the twelve-component inventory, absence of an
existing deletion command, current ACTIVE or PENDING_AGE_GATE profile, and exact
Cognito username/sub/profile attributes with `email_verified=true`. It writes
`<case UUID>/challenge.json` exclusively; an existing file is never replaced.

This private file contains a cryptographically random challenge, one original
operation UUID, current account/profile/email binding, configuration/policy hashes
and fixed timestamps. It belongs to the governed correspondence case. It is not
itself a signed ownership assertion. The verifier manually sends the challenge
from the approved shared inbox **only to the independently read account email**,
not a requester-controlled Reply-To address. The message requests an explicit
deletion confirmation including that challenge. This tool neither sends it nor
claims that the file proves delivery.

The designated verifier reviews the actual reply, distinguishes explicit human
confirmation from quoted instructions, forwards, automatic replies and delivery
receipts, and prepares the closed `ATTESTATION_FIELDS` input. It binds the exact
challenge hash/case, returned challenge, reply time and actual verification time,
with two required booleans documenting actual-inbox review and explicit deletion
confirmation. Those booleans represent the trusted verifier's attestation; they
are not evidence that the program performed mailbox verification. No public link,
GET request or scanner action is consent. Re-reading an old confirmation must not
invent a new verification event to bypass the approved reply/challenge validity.

### Manual challenge templates (not sent by this tool)

Send only to the verified email read during preparation. Do not disclose whether
an account exists to a different requester address. Replace placeholders from the
private prepared case; never ask for a password or a login/one-time authentication
code. These templates ask for a separate deletion-specific challenge. The verifier
must inspect the new reply text outside any quoted message, not match text copied
automatically from this outgoing template.

**English**

> Subject: Confirm your TrustCheckRadar account deletion request
>
> We received a request to delete your TrustCheckRadar account. If you want us to
> delete the account and its associated data according to our deletion policy,
> reply with the following sentence as new text above any quoted message:
>
> I confirm that I want to delete my TrustCheckRadar account. Deletion challenge: [CHALLENGE]
>
> This deletion challenge expires at [EXPIRY WITH TIME ZONE]. It is not a login
> code. Do not send passwords or login verification codes. If you did not request
> deletion, do not confirm. Deleting your account does not cancel a Google Play
> subscription; manage any subscription separately in Google Play.

**Español**

> Asunto: Confirma la solicitud de eliminación de tu cuenta de TrustCheckRadar
>
> Hemos recibido una solicitud para eliminar tu cuenta de TrustCheckRadar. Si
> quieres que eliminemos la cuenta y sus datos asociados conforme a nuestra
> política de eliminación, responde con la siguiente frase como texto nuevo,
> encima de cualquier mensaje citado:
>
> Confirmo que quiero eliminar mi cuenta de TrustCheckRadar. Código de confirmación de eliminación: [CÓDIGO]
>
> Este código de eliminación caduca el [FECHA Y HORA CON ZONA HORARIA]. No es un
> código de inicio de sesión. No envíes contraseñas ni códigos de verificación de
> acceso. Si no solicitaste la eliminación, no la confirmes. Eliminar tu cuenta
> no cancela una suscripción de Google Play; gestiona cualquier suscripción por
> separado en Google Play.

## Sign and hand off (one KMS attempt, no admission)

The `sign` mode revalidates the policy/challenge/attestation, live signer identity,
current resource/inventory/account binding and exact enabled customer-managed
single-region AWS-origin RSA3072 SIGN_VERIFY key. It refuses an existing deletion
command instead of minting new proof after uncertain admission. It constructs the
unchanged production capability with fixed `RSASSA_PSS_SHA_256`, expiry bounded by
both the original challenge and verification time, and the same original operation.

Before the sole KMS Sign call, it exclusively writes and fsyncs
`<operation UUID>.sign-intent.json` and its containing directory. This is the
minimal approved case record, with `SIGN_ATTEMPTED` outcome. Failed/ambiguous writes
leave the file in place; an existing intent refuses another signing attempt. A
successful signature is followed by fresh account/resource checks and clock checks.
Only then is `<operation UUID>.capability.json` exclusively written mode0600. Lost
KMS acknowledgment, changed binding, expiry or output-write failure produces an
unconfirmed outcome and preserves the intent. There is no automated retry, intent
deletion, proof refresh or new-operation recovery.

The intent is deliberately not rewritten to claim admission/completion. The
supervised case procedure must record the actual outcome after the separate
operator acts. If any step is ambiguous, retain the original case/operation and
reconcile read-only. Do not copy the case to a new directory or remove its intent
to bypass the no-retry boundary. The local filesystem is trusted case custody,
not a global single-use/revocation service.

The separate admission operator can submit the capability through the approved
IAM route. This CLI has no HTTP admission client. The first admission now also
requires live Cognito `email_verified=true`; valid original-command replay still
does not require a surviving profile/Cognito identity. Profile CAS and all twelve
receipt/finalizer semantics are unchanged. Capability and challenge working files
must be removed after their operational need ends; they must not become an extra
indefinite audit archive. Correspondence/minimal case record follows the approved
resolution-based lifecycle, including operational cleanup of private working copies.

## Permissions and remaining activation qualification

Signer tooling uses only `sts:GetCallerIdentity`, DynamoDB DescribeTable on the
two pinned tables and strong GetItem for exact user PROFILE, account command and
account-data inventory, Cognito AdminGetUser on the exact pool, and KMS DescribeKey
and Sign on the exact key. There are no DynamoDB writes, Scan, Query, mailbox,
password, identity-delete, Lambda Invoke, API submission or permission-management
calls. Source checks cannot replace IAM boundaries: admission operator has no Sign
or direct Lambda Invoke, signer has no admission/direct invocation, and actual
role trust/session custody and inherited permissions need independent review.

The runtime readiness hash remains an external snapshot pin, not a live proof
that all workers/scopes are enabled. Before activation, qualify actual IAM gateway
context, invocation denial, deployed resources/inventory/scopes, manually delivered
synthetic challenge/reply, signer/operator separation and normal twelve-receipt
completion for one designated disposable account. No real mailbox, IAM, KMS or
deletion acceptance is claimed by local tests.

Local SDK/RSA tests cover signer identity, closed policy/attestation, current
verified email/profile/inventory, old request with fresh confirmation, first
admission and unchanged original replay, single-attempt signing, lost acknowledgment,
expiry/binding change after KMS, private files and content-free offline mode. Moto
resource identity and Cognito/KMS transports are injected; RSA signatures and
existing admission DynamoDB transactions are real local implementations.

Validation for this increment: 73 support tests passed normally and with Python
`-O` (pytest's standard optimized-mode warning retained). These comprise 36
existing admission cases and 37 verifier/first-admission integration cases,
including separate file-fsync and directory-fsync failures. No actual mailbox,
AWS role, cloud key, email delivery or customer request was used.

The separate [support operator CLI](support-deletion-operator.md) submits an existing signed capability once under the operator role and observes the original operation under the verifier role. Its receipt-backed completion report does not replace mailbox review or support-case retention controls.
