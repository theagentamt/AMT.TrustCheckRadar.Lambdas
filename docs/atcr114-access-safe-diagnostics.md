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
The repository's single-process `make check` hits existing global AWS/module stub
collisions; the unchanged baseline reproduces those SDK collection errors. The
checked-in CI instead runs suites in isolated processes. Applicable isolated-suite
results and final source/publication metadata are recorded with the PR; no main
CI or deployment workflow was dispatched.

ATCR-114 remains open for actual backend resolution and healthy snapshot evidence.
Android ATCR-177's independently completed startup fix is separate. ATCR-175/112
remain open; no preparation, checkout, purchase or Restore acceptance is claimed.
This source preparation is not a runtime apply or an entitlement grant.
