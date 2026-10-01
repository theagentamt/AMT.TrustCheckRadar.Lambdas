# SECUR4ALL-240: offline message AI evaluation preparation

This tool exercises the existing candidate.2 request builder, response parser and
final policy with **simulated** model/link responses. It cannot call a provider,
read a secret, deploy a resource, activate the app or create a production model
qualification. The production registry and candidate.1/candidate.2 contracts are
unchanged. All code lives outside packaged Lambda source.

## Run and resume

Use Python 3.14 with repository `requirements-dev.txt` installed, from the repo root:

```sh
python scripts/message_ai_evaluate.py \
  --corpus evaluation/message_ai/fixtures/engineering.json \
  --run-dir /tmp/message-ai-engineering-run
```

The run directory must be new or owned by the current user with mode 0700. The
SQLite journal, lock and report use mode 0600. Run the same command again to resume
without repeating any reserved or finished attempt. To regenerate a report with
no simulation dispatch, append `--report-only`; an existing matching journal is
required. `--model` may select one or more of the three exact allowlisted snapshots.
There is no live/mode/endpoint/credential argument. Unknown options fail before
creating state and errors never echo arguments, input paths or case contents.

The committed corpus is 16 synthetic engineering cases, **not independent labels,
a representative dataset or the proposed 660-call experiment**. Its three-model
run has 45 simulated dispatches and three guard skips. It covers malformed and
duplicate JSON, refusal, truncation, reasoning envelopes, wrong snapshot, invalid
spans, timeout/provider failure, hostile guard, Spanish Unicode, withheld links,
independent link evidence and valid abstention.

## Frozen profiles and current limitations

Allowlisted snapshots are `gpt-4.1-mini-2025-04-14`,
`gpt-5.4-nano-2026-03-17` and `gpt-5.4-mini-2026-03-17`. The experimental 5.4
request explicitly adds reasoning effort `none`; production request code is not
changed. Profiles bind request settings, model, prompt/schema/policy/contract,
relevant source hashes, the research price date and integer token rates.

Output is capped at 512 tokens; model/evaluation deadline profiles are eight/18
seconds. Simulation does not measure latency. **8,192 total input tokens is a
proposed admission ceiling, not an implemented provider token guarantee.**
`admit_live()` always refuses, including when supplied a number or purported
approval. A validated full-input count/bound, account access, provider data handling,
budget authorization and a reviewed live transport remain future work. Character
counts/local token estimates cannot silently satisfy that gate.

The production qualification registry stays empty. Offline parser/policy success
must never produce an approved registry entry or be presented as model accuracy.
Full request-profile binding in the production qualifier remains a future change
before live qualification/activation; this tool only binds its own experiment.

## Manifest and labels

`evaluation/message_ai/fixtures/engineering.json` is an exact schema example.
The manifest has version, opaque corpus ID and cases with:

- Opaque case/family IDs and a `smoke`, `development`, `holdout` or `operational`
  split. Case IDs must be unique. Families and normalized duplicate text cannot
  cross splits. Use one complete split catalog before freezing any real experiment;
  separate unrelated manifests are not automatically compared. Translations and
  paraphrases require correct family tags and independent leakage review.
- A production-validated sanitized intent, including NFC Unicode and numbered
  placeholders. Raw customer submissions are not an allowed provenance kind.
- `synthetic_engineering`, `licensed` or `permitted_public` provenance with source
  and permission-record SHA-256 references. Keep the actual permitted-data evidence
  separately; hashes bind assertions, not proof of rights or independent review.
- A closed expected assessment/reason set and a hash bound to that label. Reviews
  are either `engineering_only` (no claimed human reviewers) or
  `independently_adjudicated`, requiring two distinct reviewer identity hashes,
  an adjudicator hash and a rubric hash. The adjudicator may be one of the two
  reviewers; this does not require a third person. Human independence, bilingual
  competence, agreement and final adjudication evidence require external review.
- A closed simulation kind, assessment/context, reason/span fixture and link
  outcome. Fault cases deliberately exercise rejected provider output. Spans use
  Unicode code-point offsets; their semantic correctness is not scored here.

Holdout cases require review attestations to validate, but **holdout and operational
cases are never executed by this increment**. They are included only for split
validation. No supplied reviewer claim is displayed as verified qualification.
Corpus files are read with a 16 MiB bound; malformed/deep/duplicate-key JSON fails
before run-state mutation. Input corpora remain user-managed files; the tool does
not copy them into runtime artifacts or report storage.

## Durable conservative reservation model

SQLite transactions use `BEGIN IMMEDIATE` and full synchronization. The complete
corpus/profile/configuration identity must match on resume. A process lock avoids
concurrent CLI execution; database transactions also guard competing reservations.
The proposed Stage A ceilings are fixed: 660 simulated generation attempts total,
20 smoke and 200 development attempts per model, and $5 of simulated reservations.
These are engineering constraints, **not permission to spend $5**.

Before a simulated dispatch, reserve its attempt and the model's worst-case
8,192-input/512-output token cost in integer nanodollars. Never refund a reservation,
even after success. Reserved-but-unfinished records remain unknown and are never
automatically retried; this conservatively counts crashes/timeouts. Finished records
are idempotent; conflicting finalization, malformed journal records, changed corpus
or changed profiles fail closed. Deleting/copying a run database is not a supported
resume operation or an authorization mechanism. A future live ledger must own the
approved budget across all runs; a new offline directory only starts new simulation.

Normal cap exhaustion writes an updated report with
`stopReason: experiment_cap_reached`. Report-only reads cannot reset reservations.
Atomic unique private report staging avoids blocking resume after a report-write
crash. Orphan `.report-*` metadata files may be removed manually after confirming
no run is active; never delete the journal to recover a report. Local filesystem
hash checks detect malformed state and bind identity; they are not tamper-proof
attestation against a person who can rewrite the entire private database.

## Reports and honest denominators

`report.json` exports only fixed enums, allowed model IDs, counts and hashes.
The journal likewise retains hashed attempt IDs and closed result metadata, never
message text, spans, source IDs, reviewer IDs, raw output or paths.

Totals and model/language/split buckets separate simulated dispatches, guard skips,
pending unknowns, valid AI assessments/categories, parser rejection/refusal/truncation,
technical failures and final `verdict|processingOutcome|coverage`. For example,
AI no-warning plus withheld links remains final unknown/inconclusive; an AI warning
with withheld links is partial/suspicious. Valid abstention is separate from a
technical failure. Result-match counts are **not an evidence-retention qualification
rate**. Label comparisons mean exact assessment **and** full reason-set equality;
they are not false-positive/false-negative rates or independent accuracy evidence.

Reports explicitly state zero actual provider calls/cost, unmeasured token usage
and latency, unqualified input admission, unverified review attestations, unscored
semantic spans and unevaluated cohort quality gates. Simulated reservation totals
are not vendor spend. Billing/receipts are **not exercised**: this tool never turns
unknown accounting into zero deductions. Existing separate contract/authority
tests remain the engineering evidence for settlement and candidate.1 recovery.

## Remaining qualification work

The proposed next paid stage remains 20 smoke + 200 development attempts for each
model, then one frozen winner on the untouched 1,800-example holdout and separate
operational sample. Independent permitted corpora/reviewers, agreed numerical gates,
validated input admission, real usage/cost/latency collection, a separately approved
live budget and provider data handling are still missing. See
`docs/sec229-ai-qualification-plan.md` and the infrastructure research/runbook.

Run local checks with:

```sh
python -m pytest -q tests/message_ai_evaluation tests/message_evaluator
python -m compileall -q evaluation scripts/message_ai_evaluate.py tests/message_ai_evaluation
```

No GitHub Actions dispatch, cloud resource or actual model call is required for
this preparation increment.
