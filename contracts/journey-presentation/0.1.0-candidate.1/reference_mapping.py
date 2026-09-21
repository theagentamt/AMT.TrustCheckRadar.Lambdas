"""Pure presentation fixture reducer; not an endpoint or evidence verifier.

Only already-verified independent evidence may enter verified_evidence. Never
pass raw client/model output here. No accounting, access or network side effects.
"""
VERSION = '0.1.0-candidate.1'
JOURNEYS = {'message', 'link', 'qr', 'recovery'}
PROCESSING = {'complete', 'partial', 'unavailable', 'blocked', 'invalid_input', 'unsupported', 'inconclusive'}
LIMITATIONS = {'HOSTILE_INPUT_STOP', 'PROVIDER_UNAVAILABLE', 'INSUFFICIENT_EVIDENCE',
               'UNSUPPORTED_CONTENT', 'CLARIFICATION_REQUIRED', 'RECOVERY_CONTENT_UNAVAILABLE'}


def present(*, journey, processing, limitations, verified_evidence=()):
    if journey not in JOURNEYS or processing not in PROCESSING:
        raise ValueError('UNSUPPORTED_PRESENTATION')
    if (not isinstance(limitations, (list, tuple)) or len(limitations) > 6
            or any(type(item) is not str or item not in LIMITATIONS for item in limitations)
            or len(set(limitations)) != len(limitations)):
        raise ValueError('UNSUPPORTED_LIMITATION')
    if not isinstance(verified_evidence, (list, tuple)) or len(verified_evidence) > 8:
        raise ValueError('UNSUPPORTED_EVIDENCE')
    evidence = []
    for item in verified_evidence:
        if (not isinstance(item, dict) or set(item) != {'source', 'outcome', 'targetScope'}
                or item['source'] != 'google_web_risk_lookup' or item['outcome'] not in {'match', 'no_match'}
                or item['targetScope'] not in {'full_submitted_url', 'redirect_hop', 'origin_only'}):
            raise ValueError('UNSUPPORTED_EVIDENCE')
        evidence.append(dict(item))
    limitations = list(limitations)
    if journey != 'recovery' and 'RECOVERY_CONTENT_UNAVAILABLE' in limitations:
        raise ValueError('UNSUPPORTED_LIMITATION')
    if journey == 'recovery':
        if evidence or processing != 'unavailable' or limitations != ['RECOVERY_CONTENT_UNAVAILABLE']:
            raise ValueError('RECOVERY_CONTENT_NOT_APPROVED')
        verdict, action, key = 'unknown', 'use_built_in_help', 'journey.recovery_unavailable'
    elif any(item['outcome'] == 'match' for item in evidence):
        verdict, action = 'high_risk', 'avoid_link'
        if processing != 'complete' or limitations:
            processing = 'partial'
        key = 'journey.known_threat' if processing == 'complete' else 'journey.known_threat_partial'
    elif (journey in {'link', 'qr'} and processing == 'complete' and not limitations
          and any(item['outcome'] == 'no_match' and item['targetScope'] == 'full_submitted_url' for item in evidence)):
        verdict, action, key = 'no_known_threat_detected', 'verify_independently', 'journey.no_known_threat'
    else:
        verdict, action = 'unknown', 'review_input'
        if 'HOSTILE_INPUT_STOP' in limitations:
            processing, key = 'blocked', 'journey.hostile_input_stop'
        elif 'CLARIFICATION_REQUIRED' in limitations:
            processing, key = 'inconclusive', 'journey.clarification_required'
        elif 'UNSUPPORTED_CONTENT' in limitations:
            processing, key = 'unsupported', 'journey.unsupported'
        elif 'PROVIDER_UNAVAILABLE' in limitations:
            processing, action, key = 'unavailable', 'use_built_in_help', 'journey.provider_unavailable'
        else:
            processing, key = 'inconclusive', 'journey.inconclusive'
            if 'INSUFFICIENT_EVIDENCE' not in limitations:
                limitations.append('INSUFFICIENT_EVIDENCE')
    return {'contractVersion': VERSION, 'journey': journey, 'processingOutcome': processing,
            'verdict': verdict, 'limitationCodes': limitations, 'evidence': evidence,
            'nextAction': action, 'messageKey': key}
