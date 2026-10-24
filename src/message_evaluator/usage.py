"""Allowlisted provider usage only. Unknown usage never means zero billing."""
import json
from shared_message_contract.runtime import unique_pairs

FAILURES = frozenset({'INVALID_JSON', 'MODEL_MISMATCH', 'SERVICE_TIER_MISMATCH',
    'USAGE_ABSENT', 'INVALID_TOKEN_FIELDS', 'TOKEN_TOTAL_MISMATCH',
    'INVALID_TOKEN_DETAILS', 'TOKEN_DETAIL_BOUNDS','INVALID_RATES'})


def usage_report(raw, model, rates):
    def unknown(code):
        return {'usage': None, 'failureReason': code}
    if type(rates) not in (tuple,list) or len(rates)!=2 or any(type(rate) is not int or not 1<=rate<=1_000_000_000 for rate in rates):
        return unknown('INVALID_RATES')
    try:
        if type(raw) is not bytes or not 0 < len(raw) <= 16384:
            return unknown('INVALID_JSON')
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        if type(value) is not dict:
            return unknown('INVALID_JSON')
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return unknown('INVALID_JSON')
    if value.get('model') != model:
        return unknown('MODEL_MISMATCH')
    if value.get('service_tier') != 'default':
        return unknown('SERVICE_TIER_MISMATCH')
    data = value.get('usage')
    if type(data) is not dict:
        return unknown('USAGE_ABSENT')
    names = ('input_tokens', 'output_tokens', 'total_tokens')
    if any(type(data.get(k)) is not int or not 0 <= data[k] <= 2_000_000 for k in names) or data['input_tokens'] == 0:
        return unknown('INVALID_TOKEN_FIELDS')
    if data['total_tokens'] != data['input_tokens'] + data['output_tokens']:
        return unknown('TOKEN_TOTAL_MISMATCH')
    incoming, outgoing = data.get('input_tokens_details'), data.get('output_tokens_details')
    if type(incoming) is not dict or type(outgoing) is not dict:
        return unknown('INVALID_TOKEN_DETAILS')
    cached, reasoning = incoming.get('cached_tokens'), outgoing.get('reasoning_tokens')
    if type(cached) is not int or type(reasoning) is not int:
        return unknown('INVALID_TOKEN_DETAILS')
    if not 0 <= cached <= data['input_tokens'] or not 0 <= reasoning <= data['output_tokens']:
        return unknown('TOKEN_DETAIL_BOUNDS')
    # Additive fields are ignored. Their names/values are never persisted.
    usage = {'inputTokens': data['input_tokens'], 'outputTokens': data['output_tokens'],
        'cachedTokens': cached, 'reasoningTokens': reasoning,
        'tokenCostUpperNano': data['input_tokens'] * rates[0] + data['output_tokens'] * rates[1]}
    return {'usage': usage, 'failureReason': None}
