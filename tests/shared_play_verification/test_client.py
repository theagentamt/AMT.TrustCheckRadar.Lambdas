from unittest.mock import Mock
import sys
from pathlib import Path
import pytest
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'src'))
from shared_play_verification.client import PlayClient,authorized_session
from shared_play_verification.proof import PlayVerificationError

class Response:
 def __init__(self,status=200,chunks=(b'{}',)):self.status_code=status;self.chunks=chunks;self.closed=False
 def iter_content(self,**kwargs):yield from self.chunks
 def close(self):self.closed=True

@pytest.mark.parametrize('status,code',[(301,'PLAY_PROVIDER_UNAVAILABLE'),(401,'PLAY_PROVIDER_UNAVAILABLE'),(403,'PLAY_PROVIDER_UNAVAILABLE'),(404,'PLAY_TOKEN_REJECTED'),(429,'PLAY_PROVIDER_UNAVAILABLE'),(500,'PLAY_PROVIDER_UNAVAILABLE')])
def test_status_fixed_and_response_closed(status,code):
 r=Response(status);s=Mock();s.request.return_value=r;c=PlayClient(s,deadline_monotonic=10,clock=lambda:1)
 with pytest.raises(PlayVerificationError,match=code):c.subscription('synthetic-token')
 assert r.closed and s.request.call_args.kwargs['allow_redirects'] is False
 assert s.request.call_args.kwargs['max_allowed_time']==9

def test_delayed_empty_ack_fails_deadline():
 clock=[1];r=Response(204,());s=Mock()
 def request(*a,**k):clock[0]=11;return r
 s.request.side_effect=request;c=PlayClient(s,deadline_monotonic=10,clock=lambda:clock[0])
 with pytest.raises(PlayVerificationError,match='PLAY_PROVIDER_BUDGET_EXHAUSTED'):c.acknowledge('synthetic-token')
 assert r.closed

@pytest.mark.parametrize('chunks',[(b'{"x":1,"x":2}',),(b'x'*65537,)])
def test_body_bounds(chunks):
 r=Response(chunks=chunks);s=Mock();s.request.return_value=r;c=PlayClient(s,deadline_monotonic=10,clock=lambda:1)
 with pytest.raises(PlayVerificationError):c.subscription('synthetic-token')
 assert r.closed

def test_call_cap_before_provider():
 s=Mock();c=PlayClient(s,deadline_monotonic=10,clock=lambda:1);c.calls=28
 with pytest.raises(PlayVerificationError,match='PLAY_PROVIDER_BUDGET_EXHAUSTED'):c.subscription('synthetic-token')
 s.request.assert_not_called()

def test_secret_wrong_arn_never_read():
 secret=Mock()
 with pytest.raises(PlayVerificationError):authorized_session(secret,'some-other-secret')
 secret.get_secret_value.assert_not_called()
