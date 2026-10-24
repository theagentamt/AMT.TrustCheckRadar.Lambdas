"""One-admission capability carried only on the private Message invocation.

The random nonce is deliberately not persisted beside the commitment. Public
receipts, reporting facts and provider requests never contain this context.
"""
import hashlib
import re
import secrets

from shared_history.security import SUBJECT_PATTERN
from .contract import canonical, require
from .publisher import PARTITION, PROOF

MODES = {'rules_only', 'ai', 'ai_primary_v1'}
FIELDS = {'schemaVersion', 'account', 'partition', 'proof', 'executionToken',
          'acceptedAtEpoch', 'generation', 'keyFingerprint', 'contextNonce', 'executionMode'}


def validate(context):
    require(type(context) is dict and set(context) == FIELDS, 'OPERATIONS_CONTEXT_INVALID')
    require(type(context['schemaVersion']) is int and context['schemaVersion'] == 1
            and type(context['account']) is str and SUBJECT_PATTERN.fullmatch(context['account'])
            and type(context['partition']) is str and PARTITION.fullmatch(context['partition'])
            and type(context['proof']) is str and PROOF.fullmatch(context['proof'])
            and type(context['acceptedAtEpoch']) is int and context['acceptedAtEpoch'] >= 0
            and context['executionMode'] in MODES, 'OPERATIONS_CONTEXT_INVALID')
    for name, width in (('executionToken', 32), ('generation', 32), ('keyFingerprint', 64), ('contextNonce', 64)):
        require(type(context[name]) is str and re.fullmatch('[0-9a-f]{' + str(width) + '}', context[name]),
                'OPERATIONS_CONTEXT_INVALID')
    require(context['partition'].split('#')[1] == context['proof'].split('_')[1], 'OPERATIONS_CONTEXT_INVALID')
    return dict(context)


def commitment(context, payload, client_check_id):
    context = validate(context)
    require(type(client_check_id) is str and re.fullmatch('[A-Za-z0-9_-]{1,64}', client_check_id)
            and type(payload) is dict and payload.get('entryPoint') == 'message'
            and payload.get('messageTransportVersion') in ('1.0.0-message-candidate.2', '1.0.0-message-candidate.3'),
            'OPERATIONS_CONTEXT_INVALID')
    primary = payload.get('messageExecutionProfile') == 'ai_primary_v1'
    require(primary == (context['executionMode'] == 'ai_primary_v1')
            and (not primary or payload['messageTransportVersion'].endswith('.3')),
            'OPERATIONS_CONTEXT_INVALID')
    from shared_message_contract.validation_v2 import POLICY
    material = {'domain': 'message-operations-context-v1', 'context': context, 'payload': payload,
                'clientCheckId': client_check_id, 'policyVersion': POLICY}
    return hashlib.sha256(canonical(material)).hexdigest()


def mint(authority, account, partition, proof, token, accepted_at, payload, client_check_id, mode):
    context = validate({'schemaVersion': 1, 'account': account, 'partition': partition, 'proof': proof,
        'executionToken': token, 'acceptedAtEpoch': accepted_at, 'generation': authority.operations.generation,
        'keyFingerprint': hashlib.sha256(authority.s.hmac_keys[proof.split('_')[1]]).hexdigest(),
        'contextNonce': secrets.token_hex(32), 'executionMode': mode})
    return context, commitment(context, payload, client_check_id)
