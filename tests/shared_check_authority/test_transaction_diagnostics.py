import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'src'))

from shared_check_authority.core import Authority, AuthorityError


class FailedTransaction:
    def __init__(self, response):
        self.response = response

    def transact_write_items(self, **_kwargs):
        error = RuntimeError('secret account message proof')
        error.response = self.response
        raise error


def authority(response):
    value = Authority.__new__(Authority)
    value.client = FailedTransaction(response)
    return value


def test_transaction_failure_logs_only_bounded_classification(capsys):
    value = authority({
        'Error': {'Code': 'AccessDeniedException', 'Message': 'secret account message proof'},
        'CancellationReasons': [
            {'Code': 'ConditionalCheckFailed', 'Message': 'secret', 'Item': {'PK': 'secret'}},
            {'Code': 'None'},
        ],
    })
    items = [
        {'ConditionCheck': {'TableName': 'secret', 'Key': {'PK': 'secret'}}},
        {'Put': {'TableName': 'secret', 'Item': {'message': 'secret'}}},
    ]

    with pytest.raises(AuthorityError, match='TRANSACTION_UNCERTAIN'):
        value._transact(items)

    record = json.loads(capsys.readouterr().out)
    assert record == {
        'event': 'authority_transaction_failure',
        'exceptionType': 'RuntimeError',
        'awsErrorCode': 'AccessDeniedException',
        'cancellationCodes': ['ConditionalCheckFailed', 'None'],
        'actionTypes': ['ConditionCheck', 'Put'],
        'actionCount': 2,
    }
    assert 'secret' not in json.dumps(record)


def test_transaction_failure_rejects_unbounded_error_fields(capsys):
    value = authority({
        'Error': {'Code': 'bad code with secret', 'Message': 'secret'},
        'CancellationReasons': [{'Code': 'secret/value'}] * 101,
    })

    with pytest.raises(AuthorityError, match='TRANSACTION_UNCERTAIN'):
        value._transact([{'Unexpected': {'secret': True}}])

    record = json.loads(capsys.readouterr().out)
    assert record['awsErrorCode'] == 'UNAVAILABLE'
    assert record['cancellationCodes'] == ['UNAVAILABLE'] * 100
    assert record['actionTypes'] == [] and record['actionCount'] == 1
    assert 'secret' not in json.dumps(record)
