"""AEAD capabilities: no cleartext account, device or database positions."""
import base64
import json
import re
import secrets
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


class ExportError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code, self.status = code, status


def require(condition, code='INVALID_REQUEST', status=400):
    if not condition:
        raise ExportError(code, status)


def unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result)
        result[key] = value
    return result


def encode(value):
    return json.dumps(value, separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode('utf-8')


class Cursor:
    def __init__(self, environment, active_key_id, keys):
        require(environment in ('dev', 'uat', 'prod') and type(keys) is dict
                and 1 <= len(keys) <= 4 and active_key_id in keys
                and all(type(k) is str and re.fullmatch('[A-Za-z0-9]{1,8}', k)
                        and type(v) is bytes and len(v) == 32 for k, v in keys.items()),
                'SERVICE_UNAVAILABLE', 503)
        self.environment, self.active, self.keys = environment, active_key_id, keys

    def seal(self, value):
        nonce = secrets.token_bytes(12)
        aad = f'amt-account-export-v1:{self.environment}:{self.active}'.encode()
        data = nonce + AESGCM(self.keys[self.active]).encrypt(nonce, encode(value), aad)
        token = self.active + '.' + base64.urlsafe_b64encode(data).decode().rstrip('=')
        require(len(token) <= 8192, 'EXPORT_LIMIT_EXCEEDED', 413)
        return token

    def open(self, token):
        try:
            require(type(token) is str and 40 <= len(token) <= 8192)
            kid, encoded = token.split('.')
            require(kid in self.keys and re.fullmatch('[A-Za-z0-9_-]+', encoded))
            data = base64.urlsafe_b64decode(encoded + '=' * (-len(encoded) % 4))
            require(base64.urlsafe_b64encode(data).decode().rstrip('=') == encoded)
            aad = f'amt-account-export-v1:{self.environment}:{kid}'.encode()
            plain = AESGCM(self.keys[kid]).decrypt(data[:12], data[12:], aad)
            value = json.loads(plain, object_pairs_hook=unique_pairs)
            require(type(value) is dict)
            return value
        except Exception:
            raise ExportError('INVALID_CURSOR') from None
