"""Strict permitted-data manifests with cross-split leakage checks.

Review/provenance hashes bind supplied attestations; they do not verify that a
human reviewed data or that rights/independence claims are true.
"""
import json
import re
import unicodedata
from pathlib import Path

from shared_message_contract.validation import validate_intent, MessageError
from shared_message_contract.validation_v2 import AI_REASONS
from .profile import require, digest, EvaluationError

SHA = re.compile(r'[0-9a-f]{64}')
ID = re.compile(r'[A-Za-z0-9_-]{1,64}')
KINDS = {'valid', 'malformed', 'duplicate_keys', 'refusal', 'truncated', 'reasoning_item',
         'wrong_model', 'invalid_span', 'timeout', 'provider_failure'}


def exact(value, keys):
    require(type(value) is dict and set(value) == set(keys))


def sha(value):
    require(type(value) is str and SHA.fullmatch(value) is not None)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'DUPLICATE_JSON_KEY')
        result[key] = value
    return result


def load(path):
    try:
        with Path(path).open('rb') as handle:
            raw = handle.read(16 * 1024 * 1024 + 1)
        require(len(raw) <= 16 * 1024 * 1024, 'CORPUS_TOO_LARGE')
        value = json.loads(raw, object_pairs_hook=unique_pairs)
        validate(value)
        return value
    except (OSError, UnicodeError, json.JSONDecodeError, MessageError, TypeError, KeyError, RecursionError):
        raise EvaluationError('INVALID_MANIFEST') from None


def validate(value):
    exact(value, ('schemaVersion', 'corpusId', 'cases'))
    require(type(value['schemaVersion']) is int and value['schemaVersion'] == 1)
    require(type(value['corpusId']) is str and ID.fullmatch(value['corpusId']))
    require(type(value['cases']) is list and 1 <= len(value['cases']) <= 5000)
    ids, families, texts = set(), {}, {}
    for case in value['cases']:
        exact(case, ('caseId', 'familyId', 'split', 'intent', 'provenance', 'review', 'expected', 'simulation'))
        for field in ('caseId', 'familyId'):
            require(type(case[field]) is str and ID.fullmatch(case[field]))
        require(case['caseId'] not in ids, 'DUPLICATE_CASE')
        ids.add(case['caseId'])
        split = case['split']
        require(split in ('smoke', 'development', 'holdout', 'operational'))
        try:
            validate_intent(case['intent'])
        except (MessageError, TypeError, KeyError):
            raise EvaluationError('INVALID_SANITIZED_INTENT') from None
        # Detect exact and trivial normalization leakage. Semantic paraphrase and
        # translation leakage still require honest family tagging/reviewer review.
        text = case['intent']['target']['sanitizedText']
        text_key = digest(' '.join(unicodedata.normalize('NFKC', text).casefold().split()))
        for registry, key in ((families, case['familyId']), (texts, text_key)):
            require(key not in registry or registry[key] == split, 'CROSS_SPLIT_LEAKAGE')
            registry[key] = split
        source = case['provenance']
        exact(source, ('kind', 'sourceRecordSha256', 'permissionRecordSha256'))
        require(source['kind'] in ('synthetic_engineering', 'licensed', 'permitted_public'))
        sha(source['sourceRecordSha256']); sha(source['permissionRecordSha256'])
        expected = case['expected']
        exact(expected, ('assessment', 'reasonCodes'))
        require(expected['assessment'] in ('warning', 'no_warning', 'abstain'))
        require(type(expected['reasonCodes']) is list and len(expected['reasonCodes']) <= 5)
        require(all(type(c) is str and c in AI_REASONS for c in expected['reasonCodes']))
        require(len(set(expected['reasonCodes'])) == len(expected['reasonCodes']))
        require(bool(expected['reasonCodes']) == (expected['assessment'] == 'warning'))
        review = case['review']
        exact(review, ('status', 'reviewerIds', 'adjudicatorId', 'rubricSha256', 'labelSha256'))
        require(review['status'] in ('engineering_only', 'independently_adjudicated'))
        sha(review['rubricSha256']); sha(review['labelSha256'])
        require(review['labelSha256'] == digest(expected), 'LABEL_BINDING_MISMATCH')
        require(type(review['reviewerIds']) is list)
        if review['status'] == 'engineering_only':
            require(review['reviewerIds'] == [] and review['adjudicatorId'] is None)
            require(split != 'holdout', 'HOLDOUT_REQUIRES_REVIEW_ATTESTATION')
        else:
            require(len(review['reviewerIds']) == 2)
            for reviewer in review['reviewerIds']:
                sha(reviewer)
            sha(review['adjudicatorId'])
            require(len(set(review['reviewerIds'])) == 2, 'REVIEWERS_NOT_DISTINCT')
        simulation = case['simulation']
        exact(simulation, ('kind', 'assessment', 'context', 'reasons', 'linkOutcome'))
        require(simulation['kind'] in KINDS)
        require(simulation['assessment'] in ('warning', 'no_warning', 'abstain'))
        require(simulation['context'] in ('clear', 'insufficient', 'unsupported', 'suspected_injection', 'contradictory'))
        require(type(simulation['reasons']) is list and len(simulation['reasons']) <= 5)
        for reason in simulation['reasons']:
            exact(reason, ('code', 'spans'))
            require(reason['code'] in AI_REASONS and type(reason['spans']) is list and 1 <= len(reason['spans']) <= 3)
            for span in reason['spans']:
                exact(span, ('start', 'end'))
                require(type(span['start']) is int and type(span['end']) is int and
                        0 <= span['start'] < span['end'] <= len(text))
        require(simulation['linkOutcome'] in ('unavailable', 'known_match', 'no_match'))
    return value
