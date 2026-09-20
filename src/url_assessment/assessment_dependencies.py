"""AWS clients are created only after local input validation; no persistent cache."""
import json
import os
import re

from assessment_service import Unavailable
from lookup_provider import lookup


class Dependencies:
    def __init__(self):
        self.key = None
        self.provider_call_count = 0

    def resolve(self, check_id, url, budget):
        import boto3
        from botocore.config import Config
        arn = os.environ.get('URL_RESOLVER_FUNCTION_ARN', '')
        if not re.fullmatch(r'arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live', arn):
            raise Unavailable('CONFIGURATION_UNAVAILABLE')
        if budget.remaining() < 14:
            raise Unavailable('TIME_BUDGET_EXCEEDED')
        client = boto3.client('lambda', config=Config(connect_timeout=2, read_timeout=12, retries={'total_max_attempts': 1}))
        try:
            response = client.invoke(FunctionName=arn, InvocationType='RequestResponse', Payload=json.dumps({'schemaVersion': 1, 'checkId': check_id, 'url': url}).encode())
            stream = response.get('Payload')
            try:
                raw = stream.read(32769) if stream else b''
            finally:
                if stream:
                    stream.close()
            if response.get('StatusCode') != 200 or response.get('FunctionError') or len(raw) > 32768:
                raise Unavailable('RESOLVER_UNAVAILABLE')
            return json.loads(raw)
        except Exception:
            raise Unavailable('RESOLVER_UNAVAILABLE') from None
        finally:
            client.close()

    def lookup(self, url, budget):
        if self.key is None:
            import boto3
            from botocore.config import Config
            arn = os.environ.get('WEB_RISK_SECRET_ARN', '')
            if not re.fullmatch(r'arn:aws:secretsmanager:us-east-1:107827791950:secret:trustcheckradar/dev/web-risk-api-key-[A-Za-z0-9]{6}', arn):
                raise Unavailable('CONFIGURATION_UNAVAILABLE')
            budget.remaining()
            timeout = max(0.1, budget.remaining(2.0))
            client = boto3.client('secretsmanager', config=Config(connect_timeout=timeout, read_timeout=timeout, retries={'total_max_attempts': 1}))
            try:
                secret = client.get_secret_value(SecretId=arn, VersionStage='AWSCURRENT').get('SecretString')
                if not isinstance(secret, str) or len(secret) > 8192:
                    raise ValueError()
                try:
                    value = json.loads(secret)
                except json.JSONDecodeError:
                    value = secret
                if isinstance(value, dict):
                    value = value.get('apiKey')
                if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{20,256}', value):
                    raise ValueError()
                self.key = value
            except Exception:
                raise Unavailable('SECRET_UNAVAILABLE') from None
            finally:
                client.close()
        budget.remaining()
        self.provider_call_count += 1
        return lookup(url, self.key, budget)
