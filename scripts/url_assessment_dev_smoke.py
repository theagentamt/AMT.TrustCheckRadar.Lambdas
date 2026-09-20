#!/usr/bin/env python3
"""Narrow Dev alias smoke; only explicit synthetic destinations and safe output."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile
import uuid

ALIAS = 'arn:aws:lambda:us-east-1:107827791950:function:trustcheckradar-dev-url-assessment:live'


def invoke(event):
    with tempfile.TemporaryDirectory(prefix='assessment-smoke-') as directory:
        payload = Path(directory) / 'request.json'
        output = Path(directory) / 'response.json'
        payload.write_text(json.dumps(event))
        process = subprocess.run(['aws', '--region', 'us-east-1', 'lambda', 'invoke', '--function-name', ALIAS,
                                  '--invocation-type', 'RequestResponse', '--cli-connect-timeout', '5', '--cli-read-timeout', '40',
                                  '--payload', 'fileb://' + str(payload), str(output), '--output', 'json'],
                                 env=dict(os.environ, AWS_MAX_ATTEMPTS='1', AWS_PAGER=''), capture_output=True, text=True, timeout=50)
        if process.returncode:
            raise RuntimeError('AWS_INVOKE_FAILED')
        metadata = json.loads(process.stdout)
        if metadata.get('FunctionError') or metadata.get('StatusCode') != 200:
            raise RuntimeError('LAMBDA_INVOCATION_FAILED')
        return json.loads(output.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--function', choices=[ALIAS], required=True)
    parser.add_argument('--public-https', action='store_true')
    parser.add_argument('--google-test', action='store_true')
    args = parser.parse_args()
    cases = [
        ('metadata', 'http://169.254.169.254/latest/meta-data/', 'unknown', 0),
        ('private', 'http://10.0.0.1/', 'unknown', 0),
        ('userinfo', 'https://user:secret@example.com/', 'unknown', 0),
        ('header-injection', 'https://example.com/%0d%0aInjected:yes', 'unknown', 0),
        ('sensitive', 'https://example.com/?token=synthetic', 'unknown', 0),
        ('non-http', 'file:///etc/passwd', 'unknown', 0),
    ]
    if args.public_https:
        cases.append(('public-https', 'https://example.com/', 'no_known_threat_detected', 1))
    if args.google_test:
        cases.append(('google-documented-malware-test', 'http://testsafebrowsing.appspot.com/s/malware.html', 'high_risk', None))
    failures = 0
    for name, url, verdict, count in cases:
        try:
            result = invoke({'schemaVersion': 1, 'checkId': 'smoke-' + uuid.uuid4().hex, 'url': url, 'scope': 'full_url'})
            assert result['consumerAccessEnabled'] is False and result['schemaVersion'] == 1
            assert result['verdict'] == verdict
            assert 0 <= result['providerCallCount'] <= result['lookupCount'] <= 6
            if count is not None:
                assert result['providerCallCount'] == count
            if count == 0:
                assert result['observedHopCount'] == 0
                assert result['processingOutcome'] in ('blocked', 'invalid_input')
            if verdict == 'high_risk':
                assert 'MALWARE' in result['threatTypes']
            print(json.dumps({'case': name, 'passed': True, 'verdict': verdict,
                              'status': result['processingOutcome'], 'providerCallCount': result['providerCallCount']}))
        except Exception:
            failures += 1
            print(json.dumps({'case': name, 'passed': False}))
    raise SystemExit(1 if failures else 0)


if __name__ == '__main__':
    main()
