"""Exercise actual HTTP code with in-memory wire and synthetic keys only."""
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT=Path(__file__).resolve().parents[2]
sys.path[:0]=[str(ROOT/'src'),str(ROOT/'tests/message_evaluator'),str(ROOT/'src/url_assessment')]
from test_proposer import Connection, intent, settings, wire
from message_evaluator import proposer
from shared_message_contract.validation import MessageError
from shared_operations.contract import OperationsError
from shared_operations.dispatch import JournalObserver
import lookup_provider as google
from assessment_service import Budget, Unavailable


class Observer:
    def __init__(self,*,start_error=False,finish_error=False):
        self.events=[];self.start_error=start_error;self.finish_error=finish_error
    def start(self):
        self.events.append('start')
        if self.start_error:raise OperationsError('OPERATIONS_SOURCE_UNAVAILABLE')
    def finished(self,outcome,reason,*,request_units=None):
        self.events.append((outcome,reason,request_units))
        if self.finish_error:raise OperationsError('OPERATIONS_SOURCE_UNAVAILABLE')


def call_openai(observer,*,response_wire=None,fail_after_send=False,secret_error=False,connect_error=False):
    connections=[]
    class Watching(Connection):
        def connect(self):
            super().connect()
            if connect_error:raise OSError('synthetic connect failure')
        def request(self,*args,**kwargs):
            assert observer.events==['start']
            super().request(*args,**kwargs)
            if fail_after_send:raise TimeoutError('private payload must not escape')
    def factory(*args,**kwargs):
        value=Watching(response_wire or wire(),*args,**kwargs);connections.append(value);return value
    try:
        def key(*args):
            if secret_error:raise MessageError('PROVIDER_UNAVAILABLE')
            return 'synthetic-test-key'
        value=proposer.propose(intent(),settings(),1000,clock=lambda:0.,
            connection_factory=factory,secret_loader=key,operations_observer=observer)
        return value,connections
    except Exception as error:
        error.connections=connections
        raise


def test_openai_claim_is_before_post_and_finish_after_valid_response():
    observer=Observer();value,connections=call_openai(observer)
    assert value['ruleId']=='UNRESOLVED' and len(connections)==1
    assert observer.events==['start',('succeeded','NONE',1)]
    assert b'POST /v1/responses' in b''.join(connections[0].test_socket.sent)


def test_uncertain_claim_prevents_http_and_never_retries():
    observer=Observer(start_error=True)
    with pytest.raises(MessageError) as error:call_openai(observer)
    assert error.value.connections==[]


def test_post_send_timeout_is_one_attempt_unknown_units_and_cost():
    observer=Observer()
    with pytest.raises(MessageError) as error:call_openai(observer,fail_after_send=True)
    assert len(error.value.connections)==1
    assert observer.events==['start',('failed','PROVIDER_UNAVAILABLE',None)]


@pytest.mark.parametrize('failure',['secret','connect'])
def test_openai_preparation_failures_are_planned_but_proven_not_http(failure):
    observer=Observer()
    with pytest.raises(MessageError) as error:
        call_openai(observer,secret_error=failure=='secret',connect_error=failure=='connect')
    assert observer.events==['start',('failed','PROVIDER_UNAVAILABLE',0)]
    assert not any(c.test_socket.sent for c in error.value.connections)


def test_invalid_assessment_and_finish_failure_do_not_create_another_request(capsys):
    observer=Observer(finish_error=True)
    value,connections=call_openai(observer)
    assert value['ruleId']=='UNRESOLVED' and len(connections)==1
    assert capsys.readouterr().out=='{"event":"operations_finish_unavailable"}\n'
    observer=Observer()
    with pytest.raises(MessageError):call_openai(observer,response_wire=wire({'bad':'private'}))
    assert observer.events[-1]==('failed','PROVIDER_RESPONSE_INVALID',1)


class FakeSocket:
    def __init__(self):self.sent=[];self.chunks=iter([b'HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}'])
    def settimeout(self,value):pass
    def connect(self,address):assert address==('8.8.8.8',443)
    def sendall(self,data):self.sent.append(data)
    def recv(self,size):return next(self.chunks,b'')
    def close(self):pass


@pytest.mark.parametrize('deny',[False,True])
def test_google_claim_precedes_exact_send_and_denial_sends_nothing(monkeypatch,deny):
    import dns.resolver
    resolver=Mock();resolver.resolve.return_value=['8.8.8.8']
    monkeypatch.setattr(dns.resolver,'Resolver',Mock(return_value=resolver))
    sock=FakeSocket();monkeypatch.setattr(google.socket,'socket',Mock(return_value=sock))
    context=Mock();context.wrap_socket.return_value=sock
    monkeypatch.setattr(google.ssl,'create_default_context',Mock(return_value=context))
    observer=Observer(start_error=deny)
    if deny:
        with pytest.raises(Unavailable):google.lookup('https://example.com/','synthetic',Budget(32),operations_observer=observer)
        assert sock.sent==[]
    else:
        assert google.lookup('https://example.com/','synthetic',Budget(32),operations_observer=observer)['threatTypes']==[]
        assert len(sock.sent)==1 and observer.events==['start',('succeeded','NONE',1)]


def test_journal_has_only_numeric_rounded_estimate_and_one_finish():
    publisher=Mock();publisher.start_attempt.return_value=SimpleNamespace()
    observer=JournalObserver(publisher,'private-account','private-proof','private-token',
        component='message_evaluator',ordinal=1,provider='openai',clock=lambda:0.)
    observer.start()
    observer.observed_usage({'usage':{'inputTokens':10,'outputTokens':1,'cachedTokens':0,
        'reasoningTokens':0,'tokenCostUpperNano':1123},'failureReason':None})
    observer.finished('succeeded','NONE',request_units=1)
    assert publisher.finish_attempt.call_args.kwargs['cost_microusd']==2
    with pytest.raises(OperationsError):observer.finished('succeeded','NONE',request_units=1)
    with pytest.raises(OperationsError):observer.start()
    assert publisher.start_attempt.call_count==publisher.finish_attempt.call_count==1


def test_openai_journal_delay_cannot_send_after_operation_deadline():
    tick=[0.];connections=[]
    class Slow(Observer):
        def start(self):super().start();tick[0]=2.
    observer=Slow()
    def factory(*args,**kwargs):
        connection=Connection(wire(),*args,**kwargs);connections.append(connection);return connection
    with pytest.raises(MessageError,match='BUDGET_LIMIT'):
        proposer.propose(intent(),settings(),1000,clock=lambda:tick[0],connection_factory=factory,
            secret_loader=lambda *args:'synthetic',operations_observer=observer)
    assert connections==[]


def test_google_journal_delay_cannot_send_after_operation_deadline(monkeypatch):
    import dns.resolver
    tick=[0.];real_budget=google.Budget
    monkeypatch.setattr(google,'Budget',lambda seconds:real_budget(seconds,clock=lambda:tick[0]))
    resolver=Mock();resolver.resolve.return_value=['8.8.8.8']
    monkeypatch.setattr(dns.resolver,'Resolver',Mock(return_value=resolver))
    sock=FakeSocket();monkeypatch.setattr(google.socket,'socket',Mock(return_value=sock))
    context=Mock();context.wrap_socket.return_value=sock
    monkeypatch.setattr(google.ssl,'create_default_context',Mock(return_value=context))
    class Slow(Observer):
        def start(self):super().start();tick[0]=5.
    with pytest.raises(Unavailable):google.lookup('https://example.com/','synthetic',real_budget(32,clock=lambda:tick[0]),operations_observer=Slow())
    assert sock.sent==[]


@pytest.mark.parametrize('status,reason',[(401,'PROVIDER_AUTHORIZATION_FAILED'),(403,'PROVIDER_AUTHORIZATION_FAILED'),(429,'PROVIDER_RATE_LIMITED')])
def test_google_fixed_http_reasons_and_confirmed_request_units(monkeypatch,status,reason):
    import dns.resolver
    resolver=Mock();resolver.resolve.return_value=['8.8.8.8']
    monkeypatch.setattr(dns.resolver,'Resolver',Mock(return_value=resolver))
    sock=FakeSocket();sock.chunks=iter([f'HTTP/1.1 {status} Error\r\nContent-Length: 2\r\n\r\n{{}}'.encode()])
    monkeypatch.setattr(google.socket,'socket',Mock(return_value=sock))
    context=Mock();context.wrap_socket.return_value=sock
    monkeypatch.setattr(google.ssl,'create_default_context',Mock(return_value=context))
    observer=Observer()
    with pytest.raises(Unavailable):google.lookup('https://example.com/','synthetic',Budget(32),operations_observer=observer)
    assert observer.events[-1]==('failed',reason,1)


@pytest.mark.parametrize('failure',['dns','tls'])
def test_google_preparation_failures_have_no_http_and_zero_known_units(monkeypatch,failure):
    import dns.resolver
    resolver=Mock();resolver.resolve.return_value=['8.8.8.8']
    if failure=='dns':resolver.resolve.side_effect=OSError('synthetic DNS failure')
    monkeypatch.setattr(dns.resolver,'Resolver',Mock(return_value=resolver))
    sock=FakeSocket();monkeypatch.setattr(google.socket,'socket',Mock(return_value=sock))
    context=Mock();context.wrap_socket.return_value=sock
    if failure=='tls':context.wrap_socket.side_effect=OSError('synthetic TLS failure')
    monkeypatch.setattr(google.ssl,'create_default_context',Mock(return_value=context))
    observer=Observer()
    with pytest.raises(Unavailable):google.lookup('https://example.com/','synthetic',Budget(32),operations_observer=observer)
    assert sock.sent==[] and observer.events==['start',('failed','PROVIDER_UNAVAILABLE',0)]
