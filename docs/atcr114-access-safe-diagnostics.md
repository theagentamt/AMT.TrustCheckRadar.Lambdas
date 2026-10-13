# ATCR-114: qualify the real Dev access 503 without widening admission

Store-installed Android version 10 recovered startup and owner app sign-in. One
explicit registration succeeded. The real access snapshot remained unavailable:
aggregate GET `/v1/access` logs show two 503s during 02:44–02:46 UTC and one during
02:46–02:48 UTC on October 13, 2026. Aggregate logs have no account attribution;
the selected tester's active binding is a separate authoritative read.

The initial base-allowlist diagnosis is withdrawn. Deployed reader version 15 uses
`v1_entitlements.snapshot_gate` for GET access. Its extra GET-only list already
contains the selected tester and leaves trial admission false. Its original
base/trial list remains one preserved subject. Neither list may change here.
Configuration, Cognito settings, projected active/adult profile, deletion absence
and control-inventory metadata qualified. IAM simulation allowed the relevant
reads; it is not proof of an actual runtime call. No HMAC secret values were read.
The handler suppresses internal exceptions, so the actual 503 cause is unknown.

## Exact source and package provenance

The live package is from reviewed Lambda source
`8ecc79678fd1f87c575c43f7f4ffb856869db48d`, ZIP SHA256
`90b006ddee49e19b57d2fb5f3d2a9c89137b4593e837ba284cc56958ee7ff531`.
Its immutable existing object is
`releases/8ecc79678fd1f87c575c43f7f4ffb856869db48d/v1_entitlements.zip`,
version `y6ygRzfBxVX8Mv1S4RQieotbELdRQ9ab`, in the existing Dev artifacts bucket.
The actual deployed code hash matches this ZIP. GitHub API source reads confirmed
that its handler, snapshot gate and snapshot service exactly match that commit.
The checked-in ZIP fixture contains public source code, not runtime configuration,
credentials, account data or an invocation payload.

This source branch starts from verified `origin/release-V01`
`3423979f7c08ad7c392ed6e4acc28b64da4b902e`. That release source predates the deployed
reader. A generic release ZIP would regress its GET-only gate and other existing
reader behavior. No broad merge or source reset is performed. Instead the guarded,
offline packaging recipe uses the exact deployed ZIP plus this reviewed classifier
and handler hook. This two-part provenance is explicit; the candidate must never
be described as a generic build of the release branch.

The recipe preserves all 29 unrelated original entries and their ZIP metadata,
including the unused root `app.py`. Only the configured
`v1_entitlements/app.py` receives the guarded diagnostic hook; one classifier is
added. Removing that hook yields the original handler AST. GET admission, trial,
operator, status/body/retry behavior and shared dependencies remain exact.

Candidate SHA256:
`b56547ef6ad6c16da94ba2bcae866a0f3301c11474a0858ad15420ef5d14acd5`.
It is 64,738 bytes; Lambda CodeSha256 is
`tWVH72rWwW2pS6K8roZqDzMBwRR0oIWK0VQg710UrNU=`.

## Diagnostic boundary

Only GET failures emit a fixed schema: event name, bounded source stage,
allowlisted authority/provider code and fixed cause category. Exception text,
arguments, locals, stack text, identities, request IDs, tokens, device proof,
response bodies and request/database payloads are excluded. Suppressed exception
contexts can be classified without printing them. Traversal is bounded. Import,
classification and logging failures are swallowed without changing responses or
causing retries. POST trial and operator requests produce no new diagnostic log.
Existing log retention and execution permissions remain unchanged.

## Executable preparation and reviewed rollout scope

Build offline from the retained exact deployed ZIP (an existing immutable-object
GET can supply it; no edit or new permission is needed):

```sh
python scripts/prepare_access_snapshot_diagnostics.py \
  --baseline /tmp/sec195-snapshot-artifacts/v1_entitlements.zip \
  --output /tmp/access-safe-diagnostics-reviewed.zip
```

Then run the read-only plan with the existing SDK session and existing private
selection file. Both output paths must be unused; neither prints the selection:

```sh
python scripts/plan_access_snapshot_diagnostics.py \
  --candidate /tmp/access-safe-diagnostics-reviewed.zip \
  --selected-subject-file /tmp/billing-current-account-resolved.json \
  --private-plan /tmp/access-diagnostic-plan-private.json \
  --public-evidence /tmp/access-diagnostic-plan-public.json
```

The plan rejects a changed version, `$LATEST` code/configuration, weighted alias,
wrong package, expanded trial selection or open payment/background gates. It
qualifies the exact runtime limits, 12 other live runtimes, four protected routes,
existing execution-policy and log-group fingerprints. Private revision IDs remain
local. The qualified log retention is 14 days; no retention change is planned.

After source publication/review and explicit coordinated runtime approval, the
only intended mutations are the existing
`trustcheckradar-dev-v1-entitlements` code and its `live` alias. Recheck the exact
approved metadata and latest/alias revision IDs immediately before applying.
Publish exactly the pinned diagnostic package with `UpdateFunctionCode`,
`Publish=true` and the current `$LATEST` revision fence. Verify returned code hash,
allocated immutable version and successful state before moving `live` from 15 to
that exact returned version, with the alias revision fence and no traffic weights.
The allocated version number must be recorded from the provider; it is not
invented in advance. A durable journal must precede each mutation. On an uncertain
reply, use read-only reconciliation; never replay upload or alias mutation blindly.

No environment variable, identity selection, IAM policy, route, concurrency,
memory, network, schedule, retention or other runtime changes. No new standing
infrastructure. Payment verification, lifecycle and RTDN remain disabled.

After readback confirms those fences, coordinate one supported signed-in Android
access refresh within the existing test scope. Extract only fixed classifications
and status counts in its narrow time window. This may identify a fix; logging is
not a 503 remedy and a mock is not live acceptance. Checkout stays held until real
access is healthy. Do not invoke the protected endpoint with extracted credentials.

Rollback, if authorized, is the same unweighted alias from the recorded diagnostic
version back to existing immutable version 15 with an alias revision fence. Both
previous allowlists and all environment values remain untouched. No account,
fixture or binding cleanup, automatic rollback or version deletion is performed.

## Validation and status

The classifier and exact candidate received independent source and package
reviews. The host regression checks include actual deployed-handler failure
response equivalence. Portable tests cover privacy, suppressed SDK causes,
cycles, non-GET silence, deterministic packaging, tamper rejection, unchanged
entries/metadata and read-only scope/conflict rejection.

Focused local authority checks: 82 passed plus 12 subtests; the complete isolated
authority/message job passed 850 tests. Script-suite checks:
161 passed, one existing optional skip, 21 subtests. Compile and shellcheck pass.
All applicable checked-in default CI suites pass in separate processes: 2,237
passed, 72 existing integration/optional skips. Those skips are not acceptance
passes. The repository's single-process `make check` hits existing global AWS/module stub
collisions; the unchanged baseline reproduces those SDK collection errors. The
checked-in CI instead runs suites in isolated processes. Applicable isolated-suite
results and final source/publication metadata are recorded with the PR; no main
CI or deployment workflow was dispatched.

ATCR-114 remains open for actual backend resolution and healthy snapshot evidence.
Android ATCR-177's independently completed startup fix is separate. ATCR-175/112
remain open; no preparation, checkout, purchase or Restore acceptance is claimed.
The original source preparation was not a runtime apply or an entitlement grant.

## Actual Dev rollout and single supported observation

Under the existing owner authorization for reviewed minimal-cost Dev fixes and
the parent's released provenance hold, the exact package was published once and
the existing unweighted `live` alias moved from immutable version 15 to the
provider-returned version 16. Readback at `2026-10-13T04:09:01.759532Z` verified
the candidate hash, ready immutable code and unchanged target configuration,
capacity, admission, IAM and 14-day log retention. Twelve other runtime and four
route fingerprints were preserved. Verification, lifecycle and RTDN stayed
disabled; authority and preparation retained their prior settings. Version 15
remains qualified for an authorized rollback; none was executed. There were no
mutation retries, new resources, direct endpoint calls or secret-value reads.

Source `addf795ffc472a7db69d2d139f4653e4779d1c8b` was integrated into
`release-V01` by [PR 114](https://github.com/theagentamt/AMT.TrustCheckRadar.Lambdas/pull/114),
merge `616c2f084e180b3e08c7bef43f90136492f6a7bb`. The execution script received
independent static review and seven focused mutation/privacy/journal guard tests.

Android used one supported Pricing route-entry automatic GET on already signed-in
Store version 10, emulator 5558, from `04:11:29.889120Z` to
`04:12:26.354492Z`. It did not additionally tap Refresh balance. The actual UI
still reported unavailable plan and allowances; no authentication prompt occurred.
No registration, sign-in/OTP action, prepare, checkout, payment, Restore or install
was performed in this observation. Emulator 5556 remained untouched.

The closed aggregate window `04:11:20Z`–`04:13:00Z` contains one GET 503 and one
fixed classification: `GET_ADMISSION`, `ENGINEERING_ACCESS_UNAVAILABLE`, provider
`NONE`, category `VALIDATION`. This establishes an admission-stage failure before
authority storage. It does not identify the exact predicate or actual request
subject. Log aggregates have no account attribution. The separate configured
selected-tester GET admission probe cannot establish the claims or subject of the
actual request.

Read-only API contract qualification confirms GET uses JWT authorization,
AWS_PROXY payload 2.0, the configured issuer/client, Authorization header and
required scope. The successful registration route shares the issuer/client and
payload format but does not enforce that scope at the route. No configuration
was changed. The withdrawn base-list expansion remains **DO NOT APPLY**.

Sanitized rollout, observation and contract evidence is retained in
[the live evidence record](evidence/atcr114-access-safe-diagnostics-live.json).
ATCR-114 remains open: actual access still fails, the exact failed admission
predicate needs qualification, and checkout remains held. The one observation
is exhausted; this record does not authorize another app request or rollout.

## Supported current-user reconciliation

The parent subsequently authorized one bounded Settings navigation specifically
to reconcile the visible signed-in account, including its incidental automatic
GET. This was performed once at `04:23:58.947733Z`–`04:24:01.002355Z`; no manual
refresh or authentication action occurred. The supported Settings account display
identifier matched none of the selected tester's Cognito email, username or
subject references. Comparison used salted local equality and retained only
**mismatch** and metadata. No identity text, screenshot, token, code, session or
comparison digest was retained in the evidence. Android qualified the supported
source mapping: the display identifier comes from
`Amplify.Auth.getCurrentUser().username`; its stable account identifier comes from
`user.userId`. This visible comparison does not inspect actual JWT claims.

Fresh read-only qualification confirms the deployed handler
`v1_entitlements.app.lambda_handler`, unweighted live version 16 and exact reviewed
package. Snapshot gate, engineering gate and JWT parser are byte-identical to
version 15. The selected Cognito user is enabled and confirmed, and its metadata
subject and pool match the private selection and reader issuer. The unchanged
base list contains one preserved subject; the extra GET-only list contains the
selected tester, is a valid canonical UUID and satisfies its length bound.
Executing the exact gate locally with synthetic claims admits that selected
tester with trial permission false, preserves original base behavior and rejects
an unrelated subject. These local checks do not establish live acceptance.

The incidental GET's closed `04:23:50Z`–`04:24:30Z` window again contains one
503 and one `GET_ADMISSION` / `ENGINEERING_ACCESS_UNAVAILABLE` / `NONE` /
`VALIDATION` classification, with no account attribution. Synthetic wrong-subject
claims produce this classification; wrong client, token type, expiry and missing
scope instead produce `TYPED_AUTHORITY`. Together with the supported identity
mismatch, this strongly supports rejection of a different authenticated subject.
It is a source-based inference: the exact failing traceback line and JWT subject
were not logged. Independent review identified a parser edge case in which
malformed Unicode-digit or excessively long expiration strings also raise
`ValueError`; no raw actual claims were inspected to exclude it directly.

The confirmed reconciliation gap is the supported signed-in identifier differing
from the approved tester. Neither admission list changed. Parent/owner must
resolve that Dev app account selection through a supported user-entered workflow
before another coordinated access test. Do not expand the base/trial gate, grant
access, activate verification/RTDN, clear data or request authentication codes to
compensate. No logout, sign-in, OTP, account switch, registration or checkout was
performed here. ATCR-114 and billing acceptance remain open.

The privacy-minimized source, metadata, identity comparison and server evidence is
[recorded here](evidence/atcr114-access-visible-identity-reconciliation.json).

## Reported corrected sign-in: retained identity limit

After the owner reported corrected Dev app sign-in completion, Android performed
only its authorized identity-first Settings observation, at
`04:35:02.825965Z`–`04:35:05.011242Z`. It still reported **mismatch** against the
same selected references and stopped. No manual refresh, authentication, OTP,
account change, registration or checkout was performed by agents. The closed
`04:34:50Z`–`04:35:30Z` server window again contains one GET 503 and the same fixed
admission/validation classification, with no request/account attribution.

Fresh readback at `04:35:28Z` confirms exact diagnostic live 16, pinned handoff 5,
preparation/authority true, verification/lifecycle false and RTDN closed. Cleanup
16 retains the two-subject union, prior coverage and unchanged stream/schedule.
The selected tester has a consistent ACTIVE authoritative binding. That read
does not establish that emulator 5558 owns it; registration necessity must use
the supported app's current-device state rather than repeat registration blindly.

The private reference's three salted comparisons agree with selected email,
username and subject metadata. Static audit found no demonstrated prefix,
suffix or normalization bug in the comparator. It selects a unique separate text
node next to the exact label, consistent with the source's direct InfoRow values;
malformed or ambiguous extraction reports unknown. Two limits remain: selection
uses geometry rather than structural sibling proof, and the displayed identifier
is remembered UI state. It is **not a fresh SDK/JWT identity read**, so this
observation alone cannot prove what the latest owner sign-in changed.

Selected Cognito metadata independently confirms enabled/CONFIRMED state,
verified email and exactly one matching email account in the Dev pool. The pool
uses email usernames, no alias attributes and case-insensitive usernames.
`AdminGetUser` by the approved email resolves to the selected canonical username
and subject. This is read-only metadata, not authentication; it excludes a wrong
or ambiguous selected-email lookup as a current metadata hypothesis.

Do not ask for another blind same-email sign-in or expand admission based on the
remembered display. The actual current SDK/JWT identity and exact failing request
predicate remain unqualified. Android stays held pending supported session/state
qualification coordinated by the parent. No session copying, token/process/private
database extraction, credential/code request or financial activation is justified.
This record authorizes no further app request. Sanitized evidence is retained in
[the post-sign-in qualification record](evidence/atcr114-post-signin-readonly-qualification.json).
