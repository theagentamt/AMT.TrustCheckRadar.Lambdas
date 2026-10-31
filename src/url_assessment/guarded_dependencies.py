"""Google credentials stay in assessment; the fetch worker receives only a URL."""
import json
import os
import re
from assessment_dependencies import Dependencies as LegacyDependencies
from assessment_service import Unavailable
from url_redirect_resolver.resolver import dns_addresses, validated_addresses


class Dependencies(LegacyDependencies):
    def validate_destination(self, target, budget):
        validated_addresses(dns_addresses(target.host, budget))

    def step(self, check_id, url, budget):
        import boto3
        from botocore.config import Config
        arn = os.environ.get('URL_RESOLVER_FUNCTION_ARN', '')
        if not re.fullmatch(r'arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-resolver:live', arn):
            raise Unavailable('CONFIGURATION_UNAVAILABLE')
        # Both SDK phases fit within the same remaining allocation; no SDK retry.
        seconds = budget.remaining(10)
        if seconds < .1:
            raise Unavailable('TIME_BUDGET_EXCEEDED')
        client = boto3.client('lambda', config=Config(connect_timeout=min(1, seconds / 4),
            read_timeout=seconds * .75, retries={'total_max_attempts': 1}))
        try:
            response = client.invoke(FunctionName=arn, InvocationType='RequestResponse', Payload=json.dumps({
                'schemaVersion': 2, 'checkId': check_id, 'url': url,
                'executionBudgetMs': max(1, int(seconds * .7 * 1000))}).encode())
            stream = response.get('Payload')
            try:
                raw = stream.read(32769) if stream else b''
            finally:
                if stream:
                    stream.close()
            budget.remaining()
            if response.get('StatusCode') != 200 or response.get('FunctionError') or len(raw) > 32768:
                raise ValueError()
            def unique(pairs):
                result = {}
                for key, value in pairs:
                    if key in result:
                        raise ValueError()
                    result[key] = value
                return result
            return json.loads(raw, object_pairs_hook=unique)
        except Exception:
            raise Unavailable('RESOLVER_UNAVAILABLE') from None
        finally:
            client.close()
