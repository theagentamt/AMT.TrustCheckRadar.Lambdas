"""Finite optional transport observer; actual in-memory HTTP, no external calls."""
import json
import socket
from types import SimpleNamespace

import pytest
from message_evaluator import proposer as p
from shared_message_contract.validation import MessageError
from . import test_proposer as h


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*_,**__):pytest.fail('No network or credential access in transport diagnostics')
    monkeypatch.setattr(socket,'socket',forbidden);monkeypatch.setattr(socket,'getaddrinfo',forbidden)


def run(data=None,*,observer=None,loader=None,factory=None,clock=lambda:0.,budget=1000,builder=None,parser=None,operations=None):
    connections=[]
    def connection(*args,**kwargs):
        value=h.Connection(data or h.wire(),*args,**kwargs);connections.append(value);return value
    return p.propose(h.intent(),h.settings(),budget,connection_factory=factory or connection,
        secret_loader=loader or (lambda *_:'unit-test-noncredential'),clock=clock,
        body_builder=builder,response_parser=parser,operations_observer=operations,on_transport_rejection=observer)


@pytest.mark.parametrize('headers,expected',[
    (b'Content-Encoding: gzip\r\n','CONTENT_ENCODING'),
    (b'Content-Length: 1\r\n','FRAMING_HEADERS'),
    (b'Transfer-Encoding: chunked\r\n','FRAMING_HEADERS'),
    (b'Transfer-Encoding: gzip\r\n','FRAMING_HEADERS'),
])
def test_header_rejections_are_finite_without_header_values(headers,expected):
    stages=[]
    with pytest.raises(MessageError):run(h.wire(headers=headers),observer=stages.append)
    assert stages==[expected]


@pytest.mark.parametrize('headers,stage',[
    (b'Transfer-Encoding: gzip\r\n','TRANSFER_ENCODING'),
    (b'Content-Length: wrong\r\n','CONTENT_LENGTH_VALUE'),
    (b'Content-Length: 0\r\n','CONTENT_LENGTH_BOUND'),
    (b'Content-Length: -1\r\n','CONTENT_LENGTH_BOUND'),
    (b'Content-Length: 16385\r\n','CONTENT_LENGTH_BOUND'),
])
def test_headers_without_competing_length_stop_at_the_correct_guard(headers,stage):
    stages=[];data=b'HTTP/1.1 200 Test\r\n'+headers+b'\r\n{}'
    with pytest.raises(MessageError):run(data,observer=stages.append)
    assert stages==[stage]


@pytest.mark.parametrize('kind,expected',[
    ('request','REQUEST_BUILD'),('budget','BUDGET_ARGUMENT'),('deadline','DEADLINE_SETUP'),
    ('operations','OPERATIONS_START'),('secret','SECRET_LOAD'),('setup','CONNECTION_SETUP'),
    ('connect','CONNECT'),('dispatch','DISPATCH'),('response','HTTP_RESPONSE'),
    ('status','HTTP_STATUS'),('read','BODY_READ'),('bounds','BODY_BOUNDS'),('callback','RESPONSE_CALLBACK'),
])
def test_each_early_boundary_reports_only_a_closed_stage(monkeypatch,kind,expected,capsys):
    stages=[];kwargs={'observer':stages.append}
    def broken(*_,**__):raise RuntimeError('PRIVATE_EXCEPTION_TEXT')
    class Connection(h.Connection):
        def __init__(self,*args,**kw):super().__init__(h.wire(),*args,**kw)
        def connect(self):
            if kind=='connect':broken()
            super().connect()
        def request(self,*args,**kw):
            if kind=='dispatch':broken()
            super().request(*args,**kw)
        def getresponse(self):
            if kind=='response':broken()
            response=super().getresponse()
            if kind=='read':response.read1=broken
            return response
    if kind=='request':kwargs['builder']=broken
    if kind=='budget':kwargs['budget']=True
    if kind=='deadline':kwargs['clock']=broken
    if kind=='operations':kwargs['operations']=SimpleNamespace(start=broken)
    if kind=='secret':kwargs['loader']=broken
    if kind=='setup':kwargs['factory']=broken
    if kind in ('connect','dispatch','response','read'):kwargs['factory']=Connection
    if kind=='status':kwargs['data']=h.wire(status=401)
    if kind=='bounds':kwargs['data']=b'HTTP/1.1 200 Test\r\n\r\n'+b'x'*16385
    if kind=='callback':kwargs['parser']=broken
    with pytest.raises(Exception):run(**kwargs)
    assert stages[0]==expected and all(v in p.TRANSPORT_REJECTION_STAGES for v in stages)
    assert 'PRIVATE' not in json.dumps(stages)+capsys.readouterr().out


def test_body_deadline_remains_one_shared_budget():
    stages=[];tick={'now':0.}
    class Connection(h.Connection):
        def __init__(self,*a,**k):super().__init__(h.wire(),*a,**k)
        def getresponse(self):
            response=super().getresponse();read=response.read1
            def expired(*a,**k):
                value=read(*a,**k);tick['now']=2.;return value
            response.read1=expired;return response
    with pytest.raises(MessageError) as error:
        run(factory=Connection,clock=lambda:tick['now'],observer=stages.append)
    assert error.value.code=='BUDGET_LIMIT' and stages==['BODY_DEADLINE']


@pytest.mark.parametrize('kind,expected',[('finish','OPERATIONS_FINISH'),('response','RESPONSE_CLOSE'),('connection','CONNECTION_CLOSE')])
def test_cleanup_exceptions_preserve_existing_behavior_and_report_finite_stage(monkeypatch,kind,expected):
    from shared_operations import dispatch
    stages=[]
    def broken(*_,**__):raise OSError('PRIVATE_CLEANUP_BODY')
    class Connection(h.Connection):
        def __init__(self,*a,**k):super().__init__(h.wire(),*a,**k)
        def getresponse(self):
            response=super().getresponse()
            if kind=='response':
                original=response.close
                def fail_once():
                    response.close=original
                    broken()
                response.close=fail_once
            return response
        def close(self):
            if kind=='connection':broken()
            super().close()
    operations=None
    if kind=='finish':
        operations=SimpleNamespace(start=lambda:None)
        monkeypatch.setattr(dispatch,'finished',broken)
    with pytest.raises(OSError):run(factory=Connection,operations=operations,observer=stages.append)
    assert stages==[expected]


def test_observer_failure_cannot_change_rejection_cleanup_or_accounting(monkeypatch):
    stages=[];closed=[];events=[]
    from shared_operations import dispatch
    def finished(*args):events.append(args[1:])
    monkeypatch.setattr(dispatch,'finished',finished)
    def observer(stage):stages.append(stage);raise RuntimeError('PRIVATE_OBSERVER_BODY')
    class Connection(h.Connection):
        def __init__(self,*a,**k):super().__init__(h.wire(status=403),*a,**k)
        def close(self):closed.append(True);super().close()
    with pytest.raises(MessageError) as error:
        run(factory=Connection,observer=observer,operations=SimpleNamespace(start=lambda:None))
    assert error.value.code=='PROVIDER_UNAVAILABLE' and stages==['HTTP_STATUS'] and closed==[True]
    assert events==[('failed','PROVIDER_UNAVAILABLE',1)]


@pytest.mark.parametrize('untrusted',[None,[],{},True,'PRIVATE_NON_ENUM'])
def test_unrecognized_diagnostic_label_is_never_forwarded(untrusted):
    stages=[];p._transport_rejected(stages.append,untrusted)
    assert stages==['TRANSPORT_INTERNAL']


def test_success_emits_no_rejection_and_old_callers_need_no_observer():
    stages=[]
    assert run(observer=stages.append)==run()=={'ruleId':'UNRESOLVED','spans':[]}
    assert stages==[]
