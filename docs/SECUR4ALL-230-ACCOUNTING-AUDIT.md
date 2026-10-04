# SECUR4ALL-230 Lambda accounting audit

This is a source and scoped Dev accounting audit. It does not activate a Lambda, route, provider, subscription, trial, or account; it performs no AWS mutation. It records the approved V1 policy against Lambda source at `6d9ae503b7d4e36a16a4cfc6bee2c939b988c723` and separates the evidence already demonstrated in Dev from later release qualification.

## Approved policy and authority boundary

The current authority contract implements these approved values:

- The individual subscription is USD 4.99 for 200 **completed** logical checks in each funded `P1M` service period.
- A trial starts only through explicit activation and ends at the earlier of seven days or ten completed checks.
- Only `complete` settles a charge. `partial`, `failed`, `inconclusive`, `blocked`, `invalid_input`, `unsupported`, and `unavailable` settle with zero charge.
- One consumer operation owns one authority reservation and one stable receipt. Evaluator work and approved internal work run inside that operation. Duplicate submit, lost response, and proof-only reconciliation reuse the original receipt and do not start a second logical check.
- A separate user-triggered URL or QR check is its own logical operation. A proof for one check kind cannot be reused for another kind.
- Complimentary access bypasses subscription and allowance checks only. Account, device, deletion, rate, in-flight, provider-circuit, and provider-budget controls still apply; completed usage remains unchanged.
- An exhausted provider budget yields an honest bounded result and zero charge. It cannot silently spend beyond the budget or turn an internal failure into a completed charge.
- A verified Google Play restore preserves purchase-linked used checks for the same funded period. The minimized global usage row contains no submitted message, URL, or deleted account identifier and expires after the verified access end plus seven days. The disclosed backup window is separate.
- VirusTotal remains disabled pending a commercial agreement. No VirusTotal qualification is claimed.

The canonical Google Play catalog evidence is the later verified entry in the infrastructure Google Play verification guide: package `com.andmorethings.trustcheckradar`, product `trustcheck_radar_pro_monthly`, base plan `pro-monthly`, `P1M`, US availability, USD 4.99. This audit did not call Google Play and does not claim a new purchase transaction.

## Acceptance mapping

### 1. At most one deduction for one logical check

Source and scoped message Dev evidence is complete:

- `src/shared_check_authority/core.py` creates one reservation and one receipt for a consumer operation, charges only its original period, and makes duplicate settlement idempotent.
- `src/message_consumer/service.py` keeps evaluator work inside the admitted operation and reuses the receipt for replay and reconciliation.
- Cross-kind proof tests reject reuse between message and URL operations.
- Scoped Dev rules-only, no-link message checks advanced completed usage exactly once per completed message.

Live multi-service fanout is still release qualification. The retained Dev run executed no URL assessment, and the later English/Spanish app runs used no-link rules-only messages. Therefore this document does **not** claim that a message or QR check exercised several approved internal services live under one deduction. That matrix remains in `SECUR4ALL-334`.

### 2. Denial and complimentary behavior

The reviewed shared-authority/current consumer source denies expired, exhausted, or ended-trial work before evaluator/provider execution. Scoped Dev exercised authenticated access and a temporary complimentary grant/revoke boundary. Complimentary completed checks were `not_charged`, with zero charged checks and unchanged completed usage. Complimentary execution retained account/device and operational controls.

Actual paid AI/provider execution is not qualified by that evidence. The current retained rules-only Dev scope keeps provider circuits closed and makes no paid provider call.

### 3. Replay, races, transitions, and restoration

The reviewed tests cover:

- lost prepare, admission, and settlement responses;
- duplicate submit and proof-only reconciliation;
- concurrent final paid checks with no negative allowance;
- charging the original period when settlement crosses renewal;
- paid to complimentary to paid transitions;
- verified restore retaining used checks and releasing obsolete pending reservations;
- grace and billing deferral retaining remaining checks without creating a new 200-check period;
- account, device, period, proof, and check-kind isolation.

The server-completed lost-response rule is preserved: a settled completed check is charged even if the response is lost, while failed, partial, blocked, and inconclusive requests do not charge.

### 4. Provider budgets

Source tests prove that closed/exhausted budget state returns bounded `BUDGET_LIMIT`/unavailable behavior with no evaluator call and no charge. Provider exception or malformed output also settles zero and cannot cause a second provider attempt through replay.

This is source evidence. Actual paid model/reputation calls, commercial budget economics, store sandbox purchase lifecycle, UAT, and physical-device qualification remain in the existing release handoff. No provider call was made for this audit.

## Privacy and retention

The minimized result/usage receipt has a fixed seven-day deadline. Reads of governed History do not consume allowance and do not renew the original evidence clock. Current allowance is read from authority rather than persisted into the History projection.

Purchase-linked restored usage is retained through the verified service/access end plus seven days. Account deletion removes the local binding; a later restore requires fresh store verification and exclusive purchase ownership. The minimized purchase usage record preserves counters without messages, URLs, proofs, or the deleted account identifier.

Research participation and withdrawal do not grant, replenish, or reset protection allowance. Re-enrollment does not change the original trial clock, period, counters, or usage history.

## Exact validation

All commands ran from an isolated checkout at exact source `6d9ae503b7d4e36a16a4cfc6bee2c939b988c723` using Python 3.14.5 unless stated otherwise:

- Focused authority, message, and Play suites: **290 passed**.
- Shared authority, message consumer, and Play handoff suites: **507 passed**.
- Message evaluator in its isolated SDK process: **403 passed**.
- Entitlement snapshot in its isolated process: **12 passed and 2 subtests passed**.
- Retired conversation handler: **4 passed and 3 subtests passed**.
- Isolated real-SDK/Moto retirement boundary: **32 passed**.
- `compileall` and `git diff --check`: passed.

One deliberately over-broad combined collection was invalid because evaluator and entitlement test modules install collection-time SDK stubs that are incompatible with Moto (`boto3.DEFAULT_SESSION` is absent). The documented isolated processes above were rerun and passed. The invalid mixed run is retained as test-environment evidence and is not represented as a source failure or a successful suite.

## Retired conversation-analysis compatibility boundary

`src/conversation_analysis/app.py` is an owned-replay-only handler. A missing legacy request returns `LEGACY_MIGRATION_REQUIRED`; ambiguous evidence returns `LEGACY_RECONCILIATION_REQUIRED`. It never starts provider work, allocates allowance, retries dispatch, writes History, or reconstructs missing accounting.

The least-privilege replay role needs DynamoDB `GetItem` only for:

- the authenticated user's profile and fixed deletion fence;
- the authoritative device pointer and exact active device row;
- the exact legacy request and matching consumption receipt;
- when History tables are configured, the exact History state, request locator, and content row.

It needs no DynamoDB `Query`, `Scan`, write, delete, or transaction action; no Secrets Manager or SSM access; no Lambda invoke; no provider access; and no outbox access. CloudWatch log delivery is the ordinary runtime permission outside this data-plane list. The API integration must invoke an exact qualified immutable version or alias on the exact retired route; unqualified `$LATEST` or wildcard route permission is not part of this compatibility boundary.

The retirement behavior entered in commit `cdf607854db89534c8dbd3d2e72b725e2ed28249`. On audited main, the key source files remain:

- `src/conversation_analysis/app.py`: SHA-256 `a8a5e730f958f3830caf70d088f6d3f81be4d083d7e1070089469f06948dabcc`
- `src/conversation_analysis/retired_replay.py`: SHA-256 `a0976e62b6ff916e8ca700be965b5bbf12703de40c57bd1ca7bb24bb64737812`
- `src/conversation_analysis/validation.py`: SHA-256 `a6c62b7643b46ebfcfda8185aa4d59c9a6d425ca32ad364e7936e5b703ac2b8d`
- `src/shared_history/security.py`: SHA-256 `71d562ee364c7a91bba0a10694316cfb1f70a552861554f19bed0a613492c5d8`
- `src/shared_history/contracts.py`: SHA-256 `da093661729195de48705f7e280c355aa1fcf0b55ff315eade7a7c6c80f06133`
- `src/shared_history/config.py`: SHA-256 `fce97a98c3cb5332e6904cf80165f2d229e85c58983d375fc19fdf9dfae295dd`

An immutable retirement version must use the CI-produced `conversation_analysis` package from audited main (or a later independently reviewed source with the same boundary), verify its package digest/provenance, keep handler `app.lambda_handler`, and route only to that qualified version. A pre-`cdf6078` artifact is unsafe because it may contain the former dispatcher. File hashes are review anchors; they do not replace the immutable package digest.

The initial known catalog candidate is the already published package from source `d98ffd65b42d54953ad83e980e58846b6fc02c5d`, with AWS `CodeSha256` `vMGNoWsUlbRK+JWlONEQ8tAjK+XvsOeyO4wYmKAn0O4=`. Commit `cdf6078` is its ancestor, and its `app.py`, `retired_replay.py`, `shared_history/security.py`, and `shared_history/contracts.py` hashes are byte-identical to the audited main values above. Commit `d98ffd6` changed publication tooling rather than retirement source. Admission still requires the exact reviewed package/version metadata; a caller-supplied label is not qualification.

The package still contains historical modules for build compatibility, but `app.py` imports only validation, identity/history security, and `LegacyReplay`. Least-privilege IAM is required even when unused historical files exist in the ZIP.

## Completion boundary

No Lambda/API source defect was found in the reviewed shared-authority and scoped message accounting paths. This does not universally qualify every legacy API or deployment path.

`SECUR4ALL-242` remains a mandatory promotion boundary: infrastructure must pin the reviewed retirement artifact to a qualified immutable version or alias, grant only the replay `GetItem` permissions above, and restrict API Gateway invocation to the exact retired route. Quarantining `$LATEST`, wildcard route permission, obsolete provider access, and legacy writes is required before promotion; it is not optional release polish. Root infrastructure work owns that correction, and this Lambda documentation makes no deployment claim.

This documentation increment can close the Lambda source-accounting audit after independent review, main integration, and CI readback. It does not close `SECUR4ALL-230`'s live multi-service fanout evidence or make the story Done by itself. `SECUR4ALL-334` remains the existing open release handoff for that fanout plus real store/provider, UAT, and physical-device matrices. The owner's retained Dev message/History scope stays active; this audit authorizes no rollback and performs no new qualification. Broader iOS/store launch decisions in `SECUR4ALL-76` remain separate from Android-first shared backend Dev acceptance.
