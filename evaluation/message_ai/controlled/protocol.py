"""Exact count projection, frozen profile and independent usage validation."""
import hashlib
import json
from pathlib import Path

from .. import profile as base
from ..corpus import unique_pairs
from ..profile import EvaluationError, canonical, digest, require

COUNT_PATH = '/v1/responses/input_tokens'
GENERATION_PATH = '/v1/responses'
COUNT_FIELDS = {'model', 'input', 'text', 'tools', 'reasoning', 'truncation'}
NON_INPUT_FIELDS = {'store', 'stream', 'background', 'max_output_tokens', 'service_tier'}


def profile(model):
    value = base.profile(model)
    value.update(mode='controlled_evaluation', inputAdmissionStatus='provider_count_evidence_required',
                 countEndpoint='https://api.openai.com' + COUNT_PATH, countTimeoutMs=8000,
                 countAttemptCap=660, serviceTier='default', truncation='disabled')
    for path in sorted(Path(__file__).parent.glob('*.py')):
        value['sourceSha256'][str(path.relative_to(base.REPO))] = hashlib.sha256(path.read_bytes()).hexdigest()
    value['sourceSha256']['scripts/message_ai_controlled.py'] = hashlib.sha256((base.REPO / 'scripts/message_ai_controlled.py').read_bytes()).hexdigest()
    return value


def generation_body(intent, model):
    value = base.request_body(intent, model)
    value.update(service_tier='default', truncation='disabled')
    return value


def count_body(generation):
    require(type(generation) is dict and set(generation) <= COUNT_FIELDS | NON_INPUT_FIELDS,
            'UNSUPPORTED_COUNT_PROJECTION')
    required = {'model', 'input', 'text', 'tools', 'truncation'} | NON_INPUT_FIELDS
    require(required <= set(generation) and generation['model'] in base.MODELS and
            generation['store'] is generation['stream'] is generation['background'] is False and
            generation['tools'] == [] and generation['max_output_tokens'] == base.OUTPUT_CAP and
            generation['truncation'] == 'disabled' and generation['service_tier'] == 'default',
            'UNSUPPORTED_COUNT_PROJECTION')
    # The count endpoint supports text.format/schema and reasoning. Do not count
    # only user text: the system prompt resides in input and is included verbatim.
    return json.loads(canonical({k: v for k, v in generation.items() if k in COUNT_FIELDS}))


def parse_json(raw, limit=16384):
    try:
        require(type(raw) is bytes and 0 < len(raw) <= limit, 'INVALID_PROVIDER_JSON')
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        require(type(value) is dict, 'INVALID_PROVIDER_JSON')
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise EvaluationError('INVALID_PROVIDER_JSON') from None


def count_result(raw):
    value = parse_json(raw, 4096)
    require(set(value) == {'object', 'input_tokens'} and value['object'] == 'response.input_tokens'
            and type(value['input_tokens']) is int and 0 < value['input_tokens'] <= 2_000_000,
            'INVALID_COUNT_RESPONSE')
    return value['input_tokens']


def usage(raw, model):
    """Validate usage independently of refusal/incomplete/assessment validity.

    Returned token costs are uncached-rate upper calculations, not invoices.
    Unavailable/inconsistent usage is unknown, never zero.
    """
    try:
        value = parse_json(raw)
        require(value.get('model') == model and value.get('service_tier') == 'default', 'UNKNOWN_USAGE')
        data = value.get('usage')
        require(type(data) is dict and set(data) == {'input_tokens', 'input_tokens_details',
                'output_tokens', 'output_tokens_details', 'total_tokens'}, 'UNKNOWN_USAGE')
        for name in ('input_tokens', 'output_tokens', 'total_tokens'):
            require(type(data[name]) is int and 0 <= data[name] <= 2_000_000, 'UNKNOWN_USAGE')
        require(data['input_tokens'] > 0 and data['total_tokens'] == data['input_tokens'] + data['output_tokens'], 'UNKNOWN_USAGE')
        require(type(data['input_tokens_details']) is dict and set(data['input_tokens_details']) == {'cached_tokens'}
                and type(data['output_tokens_details']) is dict and set(data['output_tokens_details']) == {'reasoning_tokens'}, 'UNKNOWN_USAGE')
        cached = data['input_tokens_details']['cached_tokens']; reasoning = data['output_tokens_details']['reasoning_tokens']
        require(type(cached) is int and 0 <= cached <= data['input_tokens'] and type(reasoning) is int
                and 0 <= reasoning <= data['output_tokens'], 'UNKNOWN_USAGE')
        rates = base.MODELS[model]
        return {'inputTokens': data['input_tokens'], 'outputTokens': data['output_tokens'],
                'cachedTokens': cached, 'reasoningTokens': reasoning,
                'tokenCostUpperNano': data['input_tokens'] * rates[0] + data['output_tokens'] * rates[1]}
    except (EvaluationError, KeyError, TypeError):
        return None


def request_binding(intent, model):
    generation = generation_body(intent, model)
    count = count_body(generation)
    return generation, count, digest({'profile': profile(model), 'generation': generation, 'count': count})
