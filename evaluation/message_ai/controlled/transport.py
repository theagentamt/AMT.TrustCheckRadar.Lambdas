"""Fixed-host bounded HTTPS transport. Used only after durable authorization.

No credential discovery, redirect following, retry or arbitrary endpoint exists.
An operator-owned credential callback is supplied by a future authorized launcher.
"""
import re
import time

from message_evaluator import proposer
from ..profile import EvaluationError, require
from .protocol import COUNT_PATH, GENERATION_PATH


class OfficialTransport:
    mode = 'controlled_live'

    def __init__(self, credential_loader, *, clock=time.monotonic):
        self.credential_loader = credential_loader
        self.clock = clock

    def post(self, phase, body, deadline):
        require(phase in ('count', 'generation') and type(body) is bytes and
                0 < len(body) <= 65536, 'INVALID_TRANSPORT_REQUEST')
        connection = response = None
        try:
            proposer.remaining(deadline, self.clock)
            key = self.credential_loader()
            require(type(key) is str and re.fullmatch(r'[!-~]{16,512}', key), 'CREDENTIAL_UNAVAILABLE')
            proposer.remaining(deadline, self.clock)
            connection = proposer.FixedConnection(proposer.HOST, port=443,
                                                  timeout=proposer.remaining(deadline, self.clock))
            connection.deadline, connection.clock = deadline, self.clock
            connection.connect()
            connection.sock = proposer.DeadlineSocket(connection.sock, deadline, self.clock)
            connection.request('POST', COUNT_PATH if phase == 'count' else GENERATION_PATH,
                body=body, headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                   'Accept': 'application/json', 'Accept-Encoding': 'identity'})
            response = connection.getresponse()
            require(response.status == 200, 'PROVIDER_HTTP_FAILURE')
            require(response.getheader('Content-Encoding', 'identity') == 'identity', 'INVALID_HTTP_RESPONSE')
            lengths = response.headers.get_all('Content-Length', [])
            transfers = response.headers.get_all('Transfer-Encoding', [])
            require(len(lengths) <= 1 and len(transfers) <= 1 and not (lengths and transfers)
                    and (not transfers or transfers == ['chunked']), 'INVALID_HTTP_RESPONSE')
            limit = 4096 if phase == 'count' else 16384
            expected = int(lengths[0]) if lengths else None
            require(expected is None or 0 < expected <= limit, 'INVALID_HTTP_RESPONSE')
            chunks, size = [], 0
            while size <= limit:
                proposer.remaining(deadline, self.clock)
                block = response.read1(min(4096, limit + 1 - size))
                proposer.remaining(deadline, self.clock)
                if not block:
                    break
                size += len(block); chunks.append(block)
            require(size <= limit and (expected is None or size == expected), 'INVALID_HTTP_RESPONSE')
            return b''.join(chunks)
        except Exception:
            raise EvaluationError('PROVIDER_TRANSPORT_FAILURE') from None
        finally:
            if response is not None:
                try:
                    response.close()
                except Exception:
                    pass
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass
