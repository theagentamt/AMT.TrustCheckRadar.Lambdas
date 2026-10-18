"""Independent finite classification and actual local Moto replay/account fences."""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import sys
import traceback
from unittest.mock import Mock

from botocore.exceptions import ClientError
import pytest

sys.path.insert(0, str(Path(__file__).parent))
from test_transaction_errors import world, ACCOUNT, STATE
from history_mutation_api.service import HistoryMutationService
from shared_history.errors import HistoryError

CANARY = 'INDEPENDENT_PRIVATE_TRANSACTION_CANARY'


def cancellation(reasons, *, code='TransactionCanceledException'):
    return ClientError({'Error': {'Code': code, 'Message': CANARY},
                        'CancellationReasons': reasons}, 'TransactWriteItems')


@pytest.mark.parametrize('reasons,expected', [
    ([{'Code': 'ConditionalCheckFailed'}, {'Code': 'None'}, {'Code': 'None'}], 'CONFLICT'),
    ([{'Code': 'None'}, {'Code': 'None'}, {'Code': 'ConditionalCheckFailed'}], 'CONFLICT'),
    ([{'Code': 'ConditionalCheckFailed'}], 'SERVER_UNAVAILABLE'),
    ([{'Code': 'ConditionalCheckFailed'}]*4, 'SERVER_UNAVAILABLE'),
    ([{'Code': 'ConditionalCheckFailed'}, {'Code': 'None'}, {}], 'SERVER_UNAVAILABLE'),
    ([{'Code': 'ConditionalCheckFailed'}, {'Code': 'None'}, {'Code': False}], 'SERVER_UNAVAILABLE'),
    ([{'Code': 'ConditionalCheckFailed'}, {'Code': 'None'}, {'Code': 'ProvisionedThroughputExceeded'}], 'SERVER_UNAVAILABLE'),
    ([{'Code': 'ConditionalCheckFailed'}, {'Code': 'None'}, {'Code': 'TransactionConflict'}], 'SERVER_UNAVAILABLE'),
    ([{'Code': 'None'}]*3, 'SERVER_UNAVAILABLE'),
])
def test_action_vector_includes_both_authority_checks_and_never_leaks_details(reasons, expected):
    for reason in reasons:
        if isinstance(reason, dict):
            reason.update(Message=CANARY, Item={'synthetic_private': CANARY})
    client = Mock()
    client.transact_write_items.side_effect = cancellation(reasons)
    settings = SimpleNamespace(users_table_name='synthetic-users', deletion_ledger_table_name='synthetic-ledger')
    service = HistoryMutationService(settings=settings, control_table=None, dynamodb_client=client)
    with pytest.raises(HistoryError) as caught:
        service._transact([{'Put': {'synthetic': True}}], account_id=ACCOUNT)
    assert caught.value.code == expected
    assert caught.value.status_code == (409 if expected == 'CONFLICT' else 503)
    assert caught.value.retryable is True and caught.value.details == []
    assert caught.value.__suppress_context__ is True
    assert CANARY not in ''.join(traceback.format_exception(caught.type, caught.value, caught.tb))
    assert client.transact_write_items.call_count == 1
    assert len(client.transact_write_items.call_args.kwargs['TransactItems']) == 3


def test_exception_with_broken_response_property_is_finite_unavailable_not_raw_failure():
    class BrokenResponse(RuntimeError):
        @property
        def response(self):
            raise ValueError(CANARY)
    client = Mock(); client.transact_write_items.side_effect = BrokenResponse(CANARY)
    service = HistoryMutationService(settings=object(), control_table=None, dynamodb_client=client)
    with pytest.raises(HistoryError) as caught:
        service._transact([{'Put': {}}])
    assert caught.value.code == 'SERVER_UNAVAILABLE'
    assert CANARY not in ''.join(traceback.format_exception(caught.type, caught.value, caught.tb))
    client.transact_write_items.assert_called_once()


def test_bootstrap_lost_ack_commits_once_then_explicit_replay_reads_without_write(world, monkeypatch):
    service, control, _, client, clock = world
    control.delete_item(Key={'PK':'USER#'+ACCOUNT, 'SK':'STATE'})
    original = client.transact_write_items; calls = []
    def lost(**kw):
        calls.append(deepcopy(kw)); original(**kw); raise TimeoutError(CANARY)
    monkeypatch.setattr(client, 'transact_write_items', lost)
    with pytest.raises(HistoryError) as caught:
        service.bootstrap(ACCOUNT)
    assert caught.value.code == 'SERVER_UNAVAILABLE'
    before = deepcopy(control.scan()['Items']); clock[0] += 100
    recovered = service.bootstrap(ACCOUNT)
    assert recovered['status'] == 'COMPLETE'
    assert recovered['historyGeneration'] == recovered['recognitionGeneration'] == 0
    assert len(calls) == 1 and control.scan()['Items'] == before and len(before) == 2


def test_bootstrap_conditional_race_rechecks_account_before_returning_existing_state(world, monkeypatch):
    service, control, resource, client, _ = world
    control.delete_item(Key={'PK':'USER#'+ACCOUNT, 'SK':'STATE'})
    original = client.transact_write_items; calls = []
    def peer_then_account_change(**kw):
        calls.append(1)
        original(**kw)  # A committed peer result is already present at readback.
        resource.Table('history-users').update_item(Key={'PK':'USER#'+ACCOUNT, 'SK':'PROFILE'},
            UpdateExpression='SET ageVerified=:no', ExpressionAttributeValues={':no':False})
        raise cancellation([{'Code':'None'}]*3 + [{'Code':'ConditionalCheckFailed'}])
    monkeypatch.setattr(client, 'transact_write_items', peer_then_account_change)
    with pytest.raises(HistoryError) as caught:
        service.bootstrap(ACCOUNT)
    assert caught.value.code == 'FORBIDDEN' and len(calls) == 1
    assert len(control.scan()['Items']) == 2  # No compensating write or retry.
