# Backend/provider attestation — 30 September 2026

This is the Lambda owner's current source and evidence statement for ATCR-95 and
SECUR4ALL-92. The reviewed public `release-V01` baseline is
`0d90214ae83c69970a88b001c67951cb0d289fae`. This change updates documentation;
it does not enable a feature, change retention, call a provider, publish a policy,
deploy code or approve store disclosures.

Source capability, installed Lambda configuration, provider-console settings,
contractual terms and an enabled public product path are different facts. The AWS
observations below are metadata-only reads made in account `107827791950`, region
`us-east-1`, on 30 September. No records, log events, secrets, request bodies or
customer identifiers were read. Public provider evidence is the dated review in
infrastructure commit
[`1c57e985a37c6a46e8b9b100cc88d198df717b0a`](https://github.com/theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure/tree/1c57e985a37c6a46e8b9b100cc88d198df717b0a/website/policies).
It is not a claim that an external dashboard has remained unchanged.

## Current Dev activation readback

The live function inventory contained 32 `trustcheckradar-dev-*` Lambdas. The
modern message consumer/evaluator were absent. The relevant installed functions
listed below reported Python 3.14/ARM64; configuration values are readback, while
the detailed behavior is the reviewed source behavior at the commit above. This
audit did not prove every installed ZIP byte equals that commit.

| Boundary | 30 September Dev configuration | Supported statement |
| --- | --- | --- |
| Modern URL consumer | `STAGE=dev`, `CONSUMER_ENABLED=false`, `AUTHORITY_ENABLED=false` | No authenticated V1 mobile URL request can reach the private assessment through the modern consumer. The private assessment Lambda exists and an independently authorized IAM caller could invoke it. |
| URL assessment / Google Lookup | Private assessment installed; its source has no public/customer route or enable flag beyond Dev-only execution | Source resolves a bounded HTTP redirect chain and submits each observed canonical URL to Google Web Risk Lookup until a match or the bounded chain ends. This is capability evidence, not proof of an enabled user path. |
| URL lease recovery | `LEASE_SWEEP_ENABLED=false` | Background recovery is closed. |
| Legacy Web Risk route | Current source always returns HTTP 410 `LEGACY_ENDPOINT_RETIRED` and imports no provider client | Do not describe this artifact as the current analyzer. Installed-byte equality was not re-proved in this review. |
| Modern message analysis | Consumer/evaluator functions absent; committed `ai_qualifications.json` is empty | No modern OpenAI model is qualified or deployed for V1. The source transport and policy are preparation only. |
| Legacy message route | Installed `conversation-analysis`; current source is owned historical replay only | It cannot initiate new model processing or charge a new check. Installed-byte equality was not re-proved here. |
| Research participation | `CONSENT_INDEPENDENCE_ENABLED=false`; `CAMPAIGN_RECOVERY_WRITES_ENABLED=false`; approved notice/policy identifiers and 400-day audit/24-hour deletion policy are configured | Reads and withdrawal behavior remain defined, but new independent Join and recovery-write activation are closed. No entitlement gate is part of the participation service. |
| Google Play purchase/lifecycle | Handoff, authority and lifecycle gates false; catalog verification true; test purchases required; token cleanup/checkpoint gates false | Source is qualified preparation and closed-handler deployment. No Dev purchase can grant access through the modern handoff and no lifecycle notification can refresh it while these gates remain closed. |
| Account export | `ACCOUNT_EXPORT_ENABLED=false`, inventory `pending`, Play-token export false | Direct protected export is source-complete but unavailable. |
| In-app account deletion | `ACCOUNT_DELETION_ENABLED=true`, policy/inventory/completion approved, identity finalizer and campaign recovery writes true; HTTP qualification allowlist contains one subject | Deletion is live only for the one scoped Dev account. It is not general public availability. History and V1-authority deletion workers are enabled for the qualified flow. |
| Email/support deletion | `SUPPORT_ACCOUNT_DELETION_ENABLED=false` after the scoped qualification was restored | General always-on admission is unavailable. The protected route, signer/operator roles and per-case activation source are deployed and one synthetic-confirmation disposable Dev case completed all 12 receipts. An owner-supervised case can be verified, allowlisted, activated and submitted; no email is accepted automatically. |
| Age attestation | Installed route allows stored phone regions `AS,GU,MP,PR,US,VI` | It is a self-attestation plus account-eligibility check. It reads Cognito's stored phone and uses packaged libphonenumber metadata locally; it does not verify phone ownership or call a Google API. |

Configuration values can change independently of this source. Release acceptance
must re-read the exact aliases, hashes and flags used by the distributed app.

## Data sent to external services

### OpenAI

The modern candidate fixes the host and endpoint to
`api.openai.com/v1/responses`. It sends only independently validated reviewed
`sanitizedText`, language and the `other` speaker role. The request uses
`store:false`, `stream:false`, `background:false`, no tools and a strict bounded
schema. It does not send account, device, check, entitlement or entity-table
identifiers. Source does not retain or log the model response or supporting spans;
the authority receipt retains only an approved public result summary.

That source does **not** define an exact active model. Activation requires an
explicit dated `MESSAGE_PROPOSER_MODEL`, an exact qualification record and matching
evidence; the committed qualification registry is empty. The two modern runtime
functions are absent from the Dev inventory, so there is no live model/configuration
to attest. The 28 September signed-in dashboard review recorded Feedback,
evaluation/fine-tuning and API input/output sharing disabled for the selected
organization; project controls were Standard Retention, Global residency and API
call logging enabled per call. The owner attested that the AWS credential belonged
to that project, without retrieving or independently comparing the secret.

Public wording may conditionally say the reviewed project had training-sharing
controls disabled on 28 September and that the candidate request disables stored
Responses. It must not claim Zero Data Retention, no abuse-monitoring retention,
US-only processing, a currently deployed model, or immutable settings. Recheck the
exact release credential, project, model and controls before activation/publication.

### Google Web Risk and redirect destinations

The URL source fixes `webrisk.googleapis.com` and `GET /v1/uris:search`, sends the
actual URL in the `uri` parameter, and requests MALWARE, SOCIAL_ENGINEERING and
UNWANTED_SOFTWARE lists. A no-match means no match in those lists at that observed
time; it does not prove a URL is safe. The resolver separately makes bounded HTTP
requests to public redirect destinations, which exposes normal network/request
metadata and the requested URL to those destinations. QR decoding itself remains
a mobile boundary; a QR-derived URL follows the same backend path once submitted.

`release-V01` does not identify the Google Cloud project or applicable agreement
behind the secret, and Lambda configuration cannot prove that relationship without
revealing/validating external account evidence. The reviewed Google documentation
does not establish a Lookup-specific retention or erasure interval. Terms for
Evaluate, Submission or Brand Phishing Protection cannot be applied to Lookup by
assumption. Therefore public retention, deletion, processor-role or geography
claims about Lookup remain conditional on owner/provider evidence. VirusTotal is
not integrated in this Lambda source.

### Google Play

The modern verifier sends a purchase token/product/package proof to Google's
Android Publisher API, reads authoritative subscription/order state and, only
when qualified, acknowledges the purchase. Pub/Sub lifecycle input requests a
fresh provider verification and never grants access by itself. Local token and
usage deletion cannot erase Google's independent account, payment, order or
subscription records. Exact live license-test, RTDN and provider behavior remains
manual/provider acceptance; the current gates are closed.

### Support mailbox

The approved operational design uses the shared `privacy@andmorethings.com` Proton
Mail Plus inbox. Email receipt, delivery, reply review, Trash/backups and case
cleanup occur outside these Lambdas. The verifier source requires a human to send
a deletion-specific challenge to the independently read, already verified account
email and inspect an explicit reply. Its minimal case record is retained with the
correspondence for 30 days after case resolution; the signed capability itself is
call-local and contains no copied email body or identity document.

Source and Dev deployment are ready for the approved supervised per-case process:
the private IAM route, asymmetric signer, separate restricted roles and exact-case
activation source exist, and one authorized disposable account completed the real
12-component backend flow using explicitly synthetic confirmation. Configuration
was then restored to `SUPPORT_ACCOUNT_DELETION_ENABLED=false`; an operator must
independently review a real reply, add only that verified account to the reviewed
scope, activate the exact case, submit it and restore the closed configuration.

The public page can therefore invite requests to the inbox while describing the
fallback as reviewed and verified by support. It must not promise that receipt of
an email is automatic acceptance, that an always-on endpoint processes it, or that
deletion is immediate. Actual Proton delivery/reply review and resolution-based
correspondence/Trash cleanup have not been run end to end; they remain release E2E
evidence. The owner-approved five-business-day target is an initial response and
verification-start target, not a promise that deletion is complete.

## Research consent independence

The current participation service reads/writes only the user profile/participation
state, consent operations/audit and deletion commands. It imports no entitlement,
purchase or allowance authority. Join requires an explicit independent notice and
policy; withdrawal blocks future eligibility and preserves the original audit and
cleanup deadlines. Rejoining cannot cancel earlier cleanup. The campaign publisher
and downstream workers recheck participation, deletion and source evidence before
processing. Research participation cannot grant or reduce paid, trial,
complimentary or built-in access.

Current Dev still has `CONSENT_INDEPENDENCE_ENABLED=false`, so this is a source
property and closed configuration, not a claim that general enrollment is live.
Ordinary campaign research consent does not authorize demographic research or
commercial use; those require their own future optional choices.

## Local retention and deletion

The 30 September metadata audit found all 13 expected Dev DynamoDB tables. Current
PITR is 35 days for the users/foundation, deletion ledger, device bindings,
analysis-abuse, entitlements, Web Risk cache and campaign-intelligence stores;
seven days for device-recovery and History content/control; and disabled for the
campaign outbox, campaign pipeline and Play-token stores. TTL is enabled on every
table except the deletion ledger. The Play-token table uses `expiresAt`, has PITR
disabled and listed no native on-demand backup.

All 34 observed Dev Lambda/API log groups had 14-day retention. Current DynamoDB
`ListBackups` and AWS Backup protected-resource results showed no matching Dev
resources in this account/region. Those are point-in-time metadata results. They do
not exclude manual exports, another account/region, provider copies, a restored
quarantine resource or user-downloaded files. The owner separately reported no
known manual copies or restores; that remains an owner statement, not exhaustive
technical proof.

DynamoDB TTL and log/PITR expiry are asynchronous. Logical deadlines, explicit
worker deletion and reconciliation are the service controls; backup expiry is a
separate physical-copy boundary. In particular:

- modern check receipts keep minimized accounting/result summaries for seven days,
  never the submitted URL or message text;
- History content is bounded to 90 days, ordinary History dedupe to 120 days,
  mutation receipts to seven days and export capabilities to 15 minutes;
- participation audit/operation minima are retained 400 days, while research
  outbox content is bounded to 72 hours and transient work uses its stricter
  original/period deadline;
- recoverable Play tokens are encrypted and bounded to the latest verified access
  end plus seven days; minimized purchased-period usage can survive account
  deletion for that same reconciliation boundary without account ID or content;
- minimal deletion component receipts use the approved 120-day boundary, while
  the durable terminal fence cannot be removed merely because that date elapsed.

The scoped in-app flow creates a fixed fence, revokes sessions, traverses all 12
required components and finalizes profile/Cognito identity only after exact
receipts. It does not cancel a Google Play subscription, delete provider/store
records, revoke a file already downloaded by the user or promise immediate erasure
from PITR. Unknown or mismatched records keep completion unverified and require
reconciliation rather than a false success.

## Source evidence and remaining acceptance

Relevant source anchors:

- [message provider](../src/message_evaluator/proposer.py) and
  [qualification policy](../src/message_evaluator/ai_provider.py)
- [URL assessment](../src/url_assessment/assessment_service.py),
  [Google Lookup transport](../src/url_assessment/lookup_provider.py) and
  [URL consumer](../src/url_consumer/service.py)
- [research participation](../src/campaign_participation/service.py)
- [support admission](../src/support_account_deletion/contract.py) and
  [human verifier procedure](support-deletion-human-verifier.md); infrastructure's
  [scoped Dev qualification](https://github.com/theagentamt/AMT.TrustCheckRadar.Cloud.Infrastructure/blob/1420642446bfbfddad9b0f171cbae3e8eb04df91/docs/SECUR4ALL-333-DEV-EVIDENCE.md)
  records the synthetic-confirmation activation, completion and disabled-state
  restoration
- [account deletion configuration](../src/account_data_api/config.py)
- [Play verification](../src/shared_play_verification/proof.py) and
  [lifecycle contract](play-lifecycle-runtime-contract.md)

Non-manual Lambda source work is complete for this attestation. These separate
external/manual gates remain before SECUR4ALL-92/ATCR-95 public release claims:

1. Recheck the exact OpenAI credential/project, sharing/retention/region controls,
   selected dated model and qualification evidence if modern message analysis is
   deployed.
2. Establish the Google Web Risk Lookup project/agreement and any supportable
   request-retention, erasure and geography statements; qualify exact warning
   attribution/caveat behavior in the released client.
3. Complete real Google Play license-test, RTDN, renewal/cancel/grace/hold/refund,
   acknowledgment and store-disclosure acceptance with the release package.
4. Execute the real shared-mailbox delivery/reply review and resolution-based case,
   sent-mail and Trash cleanup in the release E2E handoff. Backend per-case
   admission/deletion already has scoped synthetic Dev evidence; keep general
   always-on admission closed and avoid automatic/immediate wording.
5. Approve and publish the exact EN/ES pages, verify public bytes/navigation and
   submit the platform data-safety/privacy forms for the exact released SDKs and
   enabled feature set.
6. Re-run retention/backup/log/gate metadata against the release environment and
   complete the existing UAT/physical-device handoffs; a Dev source test does not
   establish the distributed app's behavior.
