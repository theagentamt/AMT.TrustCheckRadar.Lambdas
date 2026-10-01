# Runtime playbook refinement r1

Internal documented revision: `message-playbook-2026-09-21-r1`. Owner approved
P1–P4 **and** ordinary-invoice minimum constraint for implementation on 2026-09-21.
Approval source: infrastructure review commit
`410439804ecd41ef829afced9a6f95ac6623e2b0` (PR20), file
`docs/MESSAGE-ANALYZER-PLAYBOOK-REVIEW.md`, SHA256
`e6c2b052cdb3143c0ed80515605c1a6b60b612e563e13ffb43a5a39d78ee2af0`.
The historical review retains its original proposed status; the later owner decision
and infrastructure approval record supersede that status for this implementation.

`src/message_evaluator/ai_provider.py` changes only the system instruction string:
claimed-authority handling, language/redaction limits, negated preventive advice,
exact per-category Unicode spans and the ordinary-invoice constraint. The prompt
requires specific support for manipulation rather than treating routine business
context or inability to verify a claim as proof of pretext. Public policy version,
base-policy approval hash, category vocabulary, strict schema, parser, verdict rules,
accounting and immutable candidate contracts are unchanged.

`RUBRIC.en-es.md` supplements the original development rubric without modifying it.
`fixtures.json` contains11 EN/ES pairs (22cases), with explicitly proposed semantic
labels, development family identities and `engineering_only` review. These are
repo-authored synthetic examples, not customer messages, independently reviewed
truth, untouched holdout, provider transmission permission or measured model output.
Pairs1–10 originate in the owner-reviewed examples; pair11 adopts the approved
ordinary-invoice interpretation. The fixtures are a purpose-specific regression
format, **not** an authorized controlled-run corpus/manifest.

The tests validate input/schema/parser and inject proposed responses into real
server policy. Two quoted cases deliberately skip AI. They verify request/data
separation, exact minimized projection, count-body inclusion, partial/inconclusive
withheld-link behavior, span-boundary rejection and per-category overlaps. Related
existing tests preserve independent Google/deterministic evidence and qualification
fail-closed behavior. These tests cannot establish model compliance with instructions,
semantic span accuracy, false-warning rates, billing settlement or device readiness.
Invoice no_warning does not verify an invoice or sender. Protective language does
not cancel a separate supported demand.

`profile-identities.json` records new prompt and full controlled-profile hashes;
the unchanged schema hash is also pinned. The old GPT-4.1 mini controlled profile
`8bf3ddc823d15213cf25ab35b5f4ae3547e161cda06141ec9c6f199345bcb1bf`
and prior eight-case run-readiness references are historical baseline identities,
not authorizations valid for this new prompt. Recount the entire updated request,
review exact corpus/profile/budgets and obtain a new concrete grant before live work.
Existing authorities must remain bound to their original checkout; do not reset or
reinterpret their attempts/costs. No real experiment or qualification exists here.

Both approval/qualification registries stay empty, runtime AI stays disabled, and
no provider calls, credentials, deployments, main changes or CI dispatches occur.
The paused SEC221 sanitizer and Android worktrees are outside this change.

## Local validation

Python3.14.7 focused gate: **460 passed** (27 new refinement cases/tests;
existing evaluator, offline-runner and controlled-adapter regressions included).
This is local engineering evidence, not model semantic quality. From repository root:

```sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -q -p no:cacheprovider tests/message_evaluator tests/message_ai_evaluation tests/message_ai_controlled
```

Hash reproduction (use the same qualified local Python dependencies, no credentials):

```python
from pathlib import Path
import sys
root = Path('.').resolve()
sys.path[:0] = [str(root), str(root / 'src')]
from message_evaluator import ai_provider
from evaluation.message_ai.controlled.protocol import profile
from evaluation.message_ai.profile import digest, MODELS
print(ai_provider.PROMPT_SHA256, ai_provider.SCHEMA_SHA256)
print({model: digest(profile(model)) for model in MODELS})
```

Remaining evidence: independently reviewed bilingual category/context/span labels,
actual model compatibility and quality, complete provider-account/pricing/retention
review, operational budgets/reporting, lifecycle/device acceptance and explicit
activation. Passing these tests closes only this source-refinement slice.
