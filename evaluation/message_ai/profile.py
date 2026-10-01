"""Frozen experimental profiles; a profile is not production qualification."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

from message_evaluator import ai_provider
from shared_message_contract.validation_v2 import POLICY, APPROVAL_SHA

REPO = Path(__file__).resolve().parents[2]
MODELS = {
    'gpt-4.1-mini-2025-04-14': (400, 1600, None),
    'gpt-5.4-nano-2026-03-17': (200, 1250, 'none'),
    'gpt-5.4-mini-2026-03-17': (750, 4500, 'none'),
}
# Integer nanodollars per token, standard uncached prices researched 2026-09-21.
INPUT_CAP = 8192
OUTPUT_CAP = 512
MODEL_MS = 8000
TOTAL_MS = 18000
ATTEMPT_CAP = 660
BUDGET_NANO = 5_000_000_000
SPLIT_CAPS = {'smoke': 20, 'development': 200}


class EvaluationError(ValueError):
    """Only closed codes cross the CLI boundary; never echo offending input."""


def require(condition, code='INVALID_MANIFEST'):
    if not condition:
        raise EvaluationError(code)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def profile(model):
    require(model in MODELS, 'MODEL_NOT_ALLOWLISTED')
    input_rate, output_rate, reasoning = MODELS[model]
    source_paths = ('src/message_evaluator/ai_provider.py', 'src/message_evaluator/proposer.py',
                    'src/message_evaluator/policy.py', 'src/message_evaluator/policy_v2.py',
                    'src/message_evaluator/coverage.py', 'src/shared_message_contract/validation.py',
                    'src/shared_message_contract/validation_v2.py',
                    'src/shared_message_contract/privacy.py',
                    'src/shared_message_contract/runtime.py',
                    'contracts/url-assessment/v1-draft/reference_mapping.py',
                    'evaluation/message_ai/profile.py', 'evaluation/message_ai/corpus.py',
                    'evaluation/message_ai/ledger.py', 'evaluation/message_ai/runner.py',
                    'scripts/message_ai_evaluate.py')
    return {
        'profileVersion': 1, 'mode': 'offline_simulation', 'model': model,
        'endpoint': 'https://api.openai.com/v1/responses',
        'reasoningEffort': reasoning, 'maxOutputTokens': OUTPUT_CAP,
        'totalInputTokenAdmissionCap': INPUT_CAP, 'inputAdmissionStatus': 'unqualified_live_blocked',
        'modelTimeoutMs': MODEL_MS, 'evaluatorTimeoutMs': TOTAL_MS,
        'store': False, 'stream': False, 'background': False, 'tools': [],
        'automaticRetries': 0, 'promptSha256': ai_provider.PROMPT_SHA256,
        'schemaSha256': ai_provider.SCHEMA_SHA256, 'policyVersion': POLICY,
        'approvalSha256': APPROVAL_SHA,
        'contractSha256': hashlib.sha256((REPO / 'contracts/message-consumer/1.0.0-candidate.2/SHA256SUMS').read_bytes()).hexdigest(),
        'sourceSha256': {p: hashlib.sha256((REPO / p).read_bytes()).hexdigest() for p in source_paths},
        'inputTokenRateNano': input_rate, 'outputTokenRateNano': output_rate,
        'priceDate': '2026-09-21', 'realProviderCallsAuthorized': False,
    }


def request_body(intent, model):
    frozen = profile(model)
    # Shared request projection/schema, without loading any environment or secret.
    settings = SimpleNamespace(model=model, max_output_tokens=OUTPUT_CAP)
    body = ai_provider.request_body(intent, settings)
    if frozen['reasoningEffort'] is not None:
        body['reasoning'] = {'effort': frozen['reasoningEffort']}
    return body


def reserve_cost(model):
    require(model in MODELS, 'MODEL_NOT_ALLOWLISTED')
    rates = MODELS[model]
    return INPUT_CAP * rates[0] + OUTPUT_CAP * rates[1]


def admit_live(*_args, **_kwargs):
    # An estimated character/token count, supplied number, or hash is not evidence
    # of a validated provider token upper bound. No bypass exists in this slice.
    raise EvaluationError('LIVE_EXECUTION_NOT_IMPLEMENTED_OR_AUTHORIZED')
