# SECUR4ALL-321 controlled evaluation adapter

This is local evaluation tooling under the open SECUR4ALL-240 quality story.
It implements controlled count/generation requests, approval admission and durable
accounting. **No experiment is authorized by this change.** The shipped evaluation
approval registry is empty; the separate production qualification registry,
activation flags, public contracts and customer receipts are unchanged.

## Documentation basis and remaining uncertainty

Reviewed 2026-09-21: the official [counting guide](https://developers.openai.com/api/docs/guides/token-counting)
and [count endpoint reference](https://developers.openai.com/api/reference/typescript/resources/responses/subresources/input_tokens/methods/count).
The count endpoint accepts model/input, reasoning, text configuration including
structured output, tools and truncation. The tool projects the exact generation
input/system instruction, schema and effective token-bearing options into that
request. Only storage/streaming/background/output-cap/service-tier controls are
excluded; unrecognized request fields fail closed. Both canonical requests and
the full profile are bound by one digest. No raw request is stored in the ledger.

The documented count response does not echo model/request identity; binding comes
from the fixed-route request/response operation and durable local record, not a
provider-signed receipt. Live schema acceptance, model/project availability and
count-versus-generation consistency remain unmeasured. The first authorized smoke
may establish compatibility using reviewed documented projection as its starting
evidence; it must not pretend an earlier live validation occurred.

Input-count billing is unresolved. Authorization requires an evidenced maximum
charge per count request, including explicit evidence if zero. Source code does not
assume counting is free. Generation uses dated candidate snapshots and researched
standard uncached token rates. Price/access/retention evidence must be reviewed for
the actual dedicated evaluation project before an approval record is published.

## Approval and authority

One approved experiment has one owner-only authority directory on one host. It
contains a private `authority.sqlite3`, persistent `provisioned` marker, reviewed
`evidence/*.json` records and (only when approved) `provider-key.txt`. Do not store
this directory in the repository, exports or a synced shared folder. Directories
must be 0700; authorization, evidence, marker, database and key files must be 0600.

The authorization is a closed JSON object with these fields:

| Field | Binding |
| --- | --- |
| `schemaVersion`, `experimentId`, `executionMode` | Version 1, bounded identifier, `controlled_live` (`fault_test` only in isolated tests). |
| `corpusSha256`, `profiles` | Exact canonical corpus hash and per-model `digest(protocol.profile(model))`; selected models must be allowlisted dated snapshots. |
| `authorityPathSha256`, `hostSha256` | Canonical hash of the resolved absolute authority path and local hostname. |
| `credentialSha256` | SHA-256 of the exact approved dedicated key bytes. It does not establish project permissions by itself. |
| `expiresAt` | Explicit Unix expiry time; price/data/access review must justify the approved validity window. |
| `generationAttemptCap`, `countAttemptCap` | Positive caps no greater than 660 each, independently including failures. |
| `budgetNano`, `countMaxChargeNano` | Combined reservation ceiling no greater than USD5 in integer nanodollars, plus evidenced maximum per count call. |
| `evidence` | SHA-256 references for `operatorApproval`, `corpusPermission`, `providerAccess`, `providerRetention`, `generationPricing`, `countPricing`, `countCompatibility`. |

Each evidence reference resolves only to the matching private
`evidence/<name>.json`; no arbitrary paths are accepted. The actual manifest digest
must additionally be pinned under its experiment ID in the reviewed
`evaluation/message_ai/controlled/approved_experiments.json`. This registry ships
as `{}`. A supplied manifest, boolean or hash alone grants no authority. Evidence
hashes establish identity, not the truth of rights/access/retention assertions.

Profile hashes include the executable adapter/launcher and shared request/parser
sources, policy/contract/schema, reasoning mode, endpoints, token/deadline controls
and researched price version. The approval JSON registry is deliberately excluded
from the executable hash: adding an approved manifest must not create a circular
hash dependency. Operator approval evidence should identify the reviewed source
commit and registry review, as well as the exact executable digest.

The initialization command exclusively creates the persistent marker **before**
creating the database. A missing database after that marker exists cannot be
silently initialized again. A failed/interrupted init requires operator review;
there is no reset command. `run` never initializes missing state. Changing an
export directory does not create another budget. Copying an authority elsewhere
fails the approved host/path binding.

SQLite transactions serialize reservations and use full synchronization. Every
count/generation attempt reserves its worst-case charge before transport. Count
and generation each enforce 20 smoke/200 development attempts per selected model
as well as aggregate caps. No automatic retry or reservation refund exists.
Unknown/crashed/timeout attempts remain counted; a successful stored count can
permit the corresponding generation on resume, but a reserved generation is never
redispatched. A halted experiment rejects subsequent admission.

These controls prevent ordinary-command resets and concurrent overspending under
the approved assumptions. A trusted local administrator can still replace code,
the registry, marker or database. This is not tamper-proof distributed enforcement
or a provider account-wide/invoice-enforced hard limit. Do not restore a stale
database backup or delete unresolved state to reclaim allowance.

## Commands after a concrete reviewed approval

Use Python 3.14 with `requirements-dev.txt`. All actions require the same source
checkout, corpus, manifest and authority. These illustrative placeholders grant
no execution and contain no credential values:

```sh
python scripts/message_ai_controlled.py init --corpus CORPUS.json --authorization AUTHORIZATION.json --authority-dir AUTHORITY
python scripts/message_ai_controlled.py preflight --corpus CORPUS.json --authorization AUTHORIZATION.json --authority-dir AUTHORITY
python scripts/message_ai_controlled.py run --corpus CORPUS.json --authorization AUTHORIZATION.json --authority-dir AUTHORITY --report-dir EXPORT
python scripts/message_ai_controlled.py report --corpus CORPUS.json --authorization AUTHORIZATION.json --authority-dir AUTHORITY --report-dir EXPORT
```

Init/preflight/report do not read key bytes. Run reads `AUTHORITY/provider-key.txt`
only after durable admission and fresh approval/expiry/halt checks. The file must
contain exactly the approved ASCII key bytes, without a trailing newline. Never
pass keys as CLI arguments, log them or borrow application AWS secrets. The
credential fingerprint and access evidence bind the intended dedicated project.

Reports require a separate private export directory, disjoint from the authority.
Expired or revoked experiments remain readable using the report action and a
SQLite read-only connection; the report labels approval status and cannot reserve,
initialize, load credentials or dispatch. Preserve the original pinned checkout,
corpus, host/path and evidence for this audit. Arbitrary newer code is not an
automatic historical-profile migration.

## Network, counting and failure behavior

Only `api.openai.com:443` and the two fixed `/v1/responses` routes are used.
Transport reuses the bounded public-address DNS/TLS/socket implementation; no
redirects, proxy override, tools or automatic retries exist. Count response bodies
are bounded to 4KiB, generation to16KiB and HTTP wire reads to32KiB. Generation
specifies `store:false`, no background/streaming, disabled input truncation and
service tier `default`. Count processing also transmits message/schema input;
data/retention approval must cover both endpoints.

Each case shares an18-second total budget, with count and generation each limited
to the remaining budget or eight seconds. Deadline tests cover transport behavior,
not measured live latency. Local credential/approval file operations cannot be
forcibly interrupted mid-system-call; remaining-time checks follow them. Revocation,
expiry and persisted halt are rechecked just before dispatch/credential access.
This is not atomic with wire transmission and cannot recall an already admitted or
in-flight request; its reservation still counts.

Generation is admitted only after a bound valid count of at most8,192 and remains
capped at512 output tokens. Bool/string/fractional counts, unknown schema and
failed counting block generation. Validated generation usage is extracted even
when the assessment refuses, truncates or fails schema validation. Missing or
inconsistent usage stays unknown. Observed count/usage disagreement, over-cap usage
or missing/unexpected pricing tier halts further dispatch globally for review.
This catches drift after a response; it cannot undo cost already incurred.

## Reports and corpus packet

Reports retain only closed outcomes/reason categories, hashes and aggregate
counts/tokens. No message, span, raw provider output, key, input/source/reviewer ID
or path is copied into reports/journals. Count and generation reservations are
distinct; reserved-but-unfinished requests may or may not have reached the provider.
They are never described as confirmed responses. Fault-test mode remains separate
from actual provider attempt reservations.

Planned/eligible/ineligible/unattempted counts are derived from the authorized
manifest per model/language/split. Eligibility here only selects incoming-speaker
text: this adapter does **not** execute production guard/Google/final-verdict/billing
paths. Those remain in the offline pipeline harness and separate runtime tests.
Known generation usage totals include an explicit known-attempt denominator and
do not include unknown attempts. Their uncached-rate token-cost upper calculation
is not total experiment spend or a provider invoice. Reservations remain intact.
Actual count cost is unknown because it is not supplied by the count response.

No cohort-quality threshold, semantic span accuracy, human-review claim, measured
provider latency or production qualification is produced. The imported
`evaluation/message_ai/development_packet/` contains40 synthetic development
examples/20 translation families, proposed labels and blank bilingual-review
worksheets. Its engineering assertions cannot become independent holdout evidence.

Local validation uses fault transports and synthetic private key fixtures only;
no real credential retrieval, provider request, AWS deployment or activation is
part of SECUR4ALL-321 implementation acceptance. Larger quality/operational/device
gates remain under SECUR4ALL-240 and linked stories.
