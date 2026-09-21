# Synthetic bilingual development preparation — review draft

**Not human reviewed. Not a holdout. Not model qualification. No live calls authorized.**

This owner-requested packet contains 40 synthetic examples in 20 paired EN/ES
families. All cases and all future translations/paraphrases derived from them stay
`development`. They have already been exposed during engineering and cannot become
untouched holdout material, even after actual human review. Twenty translation pairs
are correlated; this packet does not claim 40 independent quality observations.

No real customer messages, account identifiers, secrets, provider output, network
requests or external dataset were used. Three pairs intentionally reuse existing
owner-repository qualified synthetic examples, attributed in source-record.json.
The remainder was drafted for this packet. All semantic labels and spans are proposals.

## Files and separation

- `engineering/development-corpus.json`: existing Lambda schemaVersion 1, usable for
  offline structural/simulation tooling. Every review has `engineering_only`, empty
  reviewer IDs and null adjudicator. Hashes bind actual files/labels, not human review.
- `engineering/cohort-manifest.json`: supplemental explicit cohort, translation-family,
  challenge, current-mobile/backend-only scope and proposed pipeline expectations.
  This does not change the closed runtime or corpus schema.
- `engineering/review-notes.md`: unresolved multi-label alternatives and coverage gaps;
  these are engineering questions, not adjudicated labels.
- `engineering/proposed-labels-and-spans.json`: proposed answer key. Keep it away from
  reviewers until both independent worksheets are complete.
- `engineering/source-record.json` and `permission-scope.json`: actual source/scope
  records. The latter records local engineering authorization, not a fabricated
  external license, public-publication grant or provider-execution approval.
- `engineering/unicode-span-checks.json`: exact code-point versus UTF-16/UTF-8 examples;
  an in-range but shifted selection can still be semantically wrong.
- `review/RUBRIC.en-es.md`: bilingual proposed rubric matching current category limits.
- `review/blind-worksheet.csv`: copy separately for each actual bilingual reviewer;
  no proposed answer, cohort, skip hint or descriptive family name is included.
- `review/adjudication-worksheet.csv`: blank fields for actual evidence and decisions.
- `engineering/build_packet.py`: deterministic packet builder; no network/provider path.
- `validate_packet.py` and `structural-validation.json`: local structural/parity evidence,
  not accuracy or semantic-translation certification.

The simulation values deliberately mirror proposed labels to exercise plumbing.
Agreement with those values is tautological and must never be reported as model
accuracy, independent review, span-grounding quality or paid-provider compatibility.

## Coverage and interpretation

Each language has 8 warning-cohort cases, 5 benign-cohort cases, 3 ambiguous cases
and 4 adversarial cases. Cohort names describe the engineering challenge; proposed
model labels and final pipeline outcomes remain separate. In particular:

- The benign quoted-advice case is out of this profile's quotation scope and proposes
  abstention; it is not an eligible clear-benign completion denominator.
- Qualified fixed-text examples and known hostile/context guards should skip AI.
  Their correctness contributes to pipeline testing, never model accuracy/latency.
- Warning text with withheld links can remain partial/suspicious. No-warning text
  with withheld links is final unknown/inconclusive. No link has been cleared.
- The independent-threat pair supplies a **simulated** lookup match for a reviewed
  reserved-example URL. It is backend-only joint-link contract coverage. Current
  Android submits `reviewedLinks=[]`; this fixture does not authorize or enable
  joint-link submission or prove a real Google result.
- Warning categories may overlap. Exact category-set disagreement differs from
  warning/no-warning disagreement. Counts do not satisfy the proposed Stage A/B sizes.

The packet proposes no settled charge and creates no receipts or run journal.
Complete-only deduction, unknown accounting, same-check replay, immutable candidate.1
recovery and seven-day retention still require their existing separate backend/mobile
tests. No simulated failure is proof of zero vendor cost or customer settlement.

## Review and future splits

Distribute only rubric plus separate blank worksheets first. Do not provide the
engineering directory, generator, hints or simulated outputs until reviewers have
independently recorded their labels and semantic evidence. Preserve both original
records; then a real adjudicator resolves disagreements and records the rationale.
Actual rights/source and bilingual semantic parity review remains outstanding.

Keep a global family/split registry spanning this packet, existing engineering
fixtures, prompts, model-selection examples, translations and paraphrases. Detect
exact normalized duplicates automatically, and review semantic similarity manually.
Freeze fresh independent holdout families before seeing model output. Never migrate
these exposed examples into holdout by changing their split field or case IDs.
Reviewer completion alone does not make this development packet qualification data.

Owner/root reviews repository integration. A separately approved live experiment
requires permitted sources, actual review records, accepted thresholds, frozen
profiles, input-token admission and explicit attempt/cost/privacy authorization.
No such approval is created by this packet. Mobile/provider activation stays separate.

## Reproduce structural checks

From this directory, using the current local Lambda source checkout:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 validate_packet.py --lambda-repo ../../..
```

Validation is read-only by default. Use `--output /path/to/private-validation.json`
to save a fresh structural report outside this versioned packet. The included
`structural-validation.json` records the original validation checkout; rerunning
checks does not silently rewrite that evidence or invalidate `SHA256SUMS`.

The validator blocks socket connection and HTTP open functions, imports the real
corpus validator/parser, and checks hashes, paired metadata, blank human-review fields,
NFC, Unicode offsets, closed parser shape and exclusion from holdout. It performs no
model request and no policy/ledger settlement. Structural EN/ES parity is not a claim
that translations or labels have been validated by bilingual human reviewers.
