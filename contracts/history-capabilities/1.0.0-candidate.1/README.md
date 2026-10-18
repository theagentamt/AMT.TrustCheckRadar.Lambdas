# S10 existing History capability boundary — candidate.1

This is an **offline compatibility document and fixture package**, not an HTTP
request/response, new route, negotiated permission, feature switch or deployment.
The two existing wire contracts and current Android decoder remain unchanged.
An operation plan accepted by its schema only matches a documented contract
binding; `executionAuthorized` is always false. Authentication, current account,
device, deletion, inventory and runtime gates remain independent requirements.

## Existing families

- `legacy_history_v1`: existing `contracts/history/v1` methods and envelopes,
  `requestId`, owner `USER#sub` content/control namespace, retained 90-day History,
  independent progress and mutations. Its existing mutation receipt/dedup/PITR
  numbers are facts about that contract, not new governed-purpose approval.
- `governed_history_candidate1`: only existing GET list/detail under
  `/v1/users/analysis-history`, immutable typed authority-receipt projection,
  original seven-day settlement deadline, account/device/key-inventory fences.
  No current mutation, progress, export or bootstrap endpoint.

Android reviewed source a618408 consumes the governed result through a typed
`governedResult` while carrying its `gh1_...` reference in an AnalysisRequestId
wrapper. This does not convert it to a legacy assessment or grant ownership.
The local bootstrap shim and generation0 placeholders do not create server
state or a reset cutoff. Legacy progress/mutate/export are legacy-flag gated;
the current build fixes that legacy flag false. Test factories choose one family;
production constructor flags are independent, so do not infer they are mutually
exclusive for every possible future artifact. Exact source joins are recorded.

The existing legacy requestId grammar can accept a `gh1_...` string. A delete
against that legacy key can return an idempotent COMPLETE no-op receipt without
removing any governed record. That is not an identifier-family rejection and not
governed deletion. Do not add a prefix rejection to an old contract implicitly;
use a reviewed explicit semantic capability/identity boundary.

## Settled invariants and unresolved bridge

Keep approved badge thresholds1/5/20, no points, same-ID retry deduplication,
independent History clear and recognition reset, and server admission ordering.
History expiry/clear must not reset badges. No progress value is inferred from
the count of seven-day History items, absent recognition is not zero, risk is
not an independently confirmed scam, and accounting/consent is not award policy.

No bridge adapter is implemented in this package. Current governed outcomes do
not satisfy legacy score/confidence/free-text assessment fields. No fabrication,
90-day copy, backfill, new linkable store or retention renewal is allowed. Hiding
History must not delete accounting/dedup receipts, return erased assessments
through replay, extend markers, or substitute History-only for full account
product deletion. Destructive operations require a reviewed atomic generation,
replay-erasure, all-copy cleanup/restore and owner/lifecycle contract first.

The exact remaining decisions are in `contract-set.json`:

- G1: governed qualifying completion/source/new-ID deliberate resubmission rules.
  Candidate recommendation: one typed complete logical check across permitted
  message/URL/QR sources, independent of risk/payment; exclude partial,
  inconclusive, blocked, failed and pending; no content fingerprints. Same-ID
  retries never count twice. This recommendation is **not approval**.
- G2: reuse current checks_1/5/20 IDs and EN/ES catalog/current progress shape
  without adding award timestamps or per-result fields. Existing legacy code is
  a candidate, not approval of governed catalog/timestamp policy.
- T1: engineering contract for existing-route/version compatibility, identity,
  admission/completion generation fences, erased replay and Android invalidation.
  No guessed endpoint/header/response field is added here.
- P1: exact minimum new marker/progress/receipt fields, lifetime, deletion and
  restore/PITR purpose joins. Preserve seven-day governed and separate legacy
  limits; do not inherit 120/90-day retention by analogy.

G1/G2 do not reopen the approved milestones, no-points, clear/reset independence
or same-ID dedup. Contract refinement and synthetic tests are permitted now;
delete/clear design and synthetic tests do not wait for G1/G2; their adapter
requires reviewed T1/P1 identity, generation and erased-replay lifecycle. Award
wiring additionally needs G1/G2. No unrelated
native, read-only or independent backend source work is blocked by this package.

## Executable fixtures and limits

`operation-plan.schema.json` strictly validates only existing route bindings,
rejecting identity/proof/award claims, unsupported governed operations, new paths
and execution permission. These are offline plans, never bodies sent to an API.
Fixtures cover both supported source contracts and explicit unsupported cases.

Tests exercise the actual existing governed handler's unsupported-route refusal
with SDK constructors blocked, real local Moto owner/device/deletion fences,
read/settlement replay with original deadlines, legacy same-operation receipts
and target conflict, a gh1-shaped legacy no-op that leaves a separate governed
row unchanged, and rejection of a client account selector. Synthetic account
and SDK substitutes are declared in the tests; there is no real Cognito, AWS,
provider or mobile acceptance claim. Existing larger suites retain generation
races, clear/reset independence and transaction/lifecycle regressions.

Run locally with pinned requirements-dev.txt, no real credentials:

```sh
PYTHONPATH=src AMT_AUTHORITY_INTEGRATION=1 AWS_EC2_METADATA_DISABLED=true \
AWS_ACCESS_KEY_ID=synthetic AWS_SECRET_ACCESS_KEY=synthetic \
AWS_SESSION_TOKEN=synthetic AWS_DEFAULT_REGION=us-east-1 \
python -B -m pytest -q tests/history_capabilities
```

SEC185/186/224/225/226 remain open. This package is not completed S10 mutation or
recognition functionality, target qualification, policy ratification or a deployable
artifact. No runtime, IAM, credential, actor, endpoint, device, purchase, main or
security-setting action is authorized or performed by this source increment.
