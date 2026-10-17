"""Fixed original-site metadata must not change public responses or expose data."""
from pathlib import Path
import sys
import json
import logging
from unittest.mock import Mock
from types import SimpleNamespace

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from shared_play_verification.proof import TRANSITION_PREDICATES,require_transition,PlayVerificationError
from v1_play_handoff import app,diagnostics

MARKER='private-identity-proof-body-exception-marker'
EVENT={'version':'2.0','routeKey':'POST /v1/purchases/google-play/verify',
       'requestContext':{'http':{'method':'POST'}},'body':MARKER}


@pytest.fixture(autouse=True)
def dev(monkeypatch):
    monkeypatch.setenv('STAGE','dev');monkeypatch.setenv('PLAY_HANDOFF_ENABLED','true')


@pytest.mark.parametrize('predicate',sorted(TRANSITION_PREDICATES))
def test_only_fixed_predicate_enum_is_emitted(predicate,caplog):
    assert diagnostics.record_transition_failure(EVENT,predicate)
    assert json.loads(caplog.records[-1].getMessage())=={'event':'dev_play_transition_failure','schemaVersion':1,'predicate':predicate}
    assert MARKER not in caplog.text


@pytest.mark.parametrize('predicate',[MARKER,None,{},[],True])
def test_unknown_predicate_never_stringifies_or_logs_arbitrary_values(predicate,caplog):
    assert diagnostics.record_transition_failure(EVENT,predicate)
    assert json.loads(caplog.records[-1].getMessage())['predicate']=='UNKNOWN'
    assert MARKER not in caplog.text


@pytest.mark.parametrize('stage',['uat','production',''])
def test_non_dev_is_silent(stage,monkeypatch,caplog):
    monkeypatch.setenv('STAGE',stage)
    assert not diagnostics.record_transition_failure(EVENT,'PAID_HEAD_OUTSIDE_LINEAGE')
    assert not caplog.records


@pytest.mark.parametrize('event',[None,{}, {'version':'2.0','routeKey':EVENT['routeKey'],'requestContext':MARKER},
    {**EVENT,'version':'1.0'}, {**EVENT,'routeKey':'POST /v1/purchases/google-play/prepare'},
    {**EVENT,'requestContext':{'http':{'method':'GET'}}}])
def test_wrong_or_malformed_route_is_silent(event,caplog):
    assert not diagnostics.record_transition_failure(event,'PAID_HEAD_OUTSIDE_LINEAGE')
    assert not caplog.records


def test_closed_gate_and_logger_failure_do_not_change_control_flow(monkeypatch):
    monkeypatch.setenv('PLAY_HANDOFF_ENABLED','false')
    assert not diagnostics.record_transition_failure(EVENT,'OUT_OF_APP_CONTEXT')
    monkeypatch.setenv('PLAY_HANDOFF_ENABLED','true')
    monkeypatch.setattr(logging.getLogger(diagnostics.__name__),'warning',Mock(side_effect=RuntimeError(MARKER)))
    assert not diagnostics.record_transition_failure(EVENT,'OUT_OF_APP_CONTEXT')


@pytest.mark.parametrize('predicate',sorted(TRANSITION_PREDICATES))
def test_internal_metadata_preserves_exact_public_handler_response(predicate,monkeypatch,caplog):
    monkeypatch.setattr('shared_check_authority.engineering.require_engineering_subject',lambda event:None)
    with pytest.raises(PlayVerificationError)as raised:require_transition(False,predicate)
    error=raised.value
    client=SimpleNamespace(close=Mock())
    handle=Mock(side_effect=error)
    runtime=Mock(return_value=SimpleNamespace(client=client,token_cipher=None,handle=handle))
    monkeypatch.setattr(app,'_runtime',runtime)
    request_id='00000000-0000-0000-0000-000000000001'
    event={**EVENT,'body':json.dumps({'schemaVersion':1,'requestId':request_id,'intent':'PURCHASE','purchaseToken':'synthetic-purchase-token'})}
    out=app.lambda_handler(event,None)
    assert out['statusCode']==409
    assert json.loads(out['body'])=={'schemaVersion':1,'contractVersion':app.CONTRACT,'requestId':request_id,
        'error':{'code':'PURCHASE_TRANSITION_UNSUPPORTED','retryable':False}}
    assert len(caplog.records)==2 and json.loads(caplog.records[-1].getMessage())['predicate']==predicate
    assert handle.call_count==runtime.call_count==client.close.call_count==1
    assert MARKER not in caplog.text
