"""Retired direct lookup: no URL parsing, provider credentials or SDK imports."""
import json


def lambda_handler(_event, _context):
    return {'statusCode': 410, 'headers': {'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
            'body': json.dumps({'error': {'code': 'LEGACY_ENDPOINT_RETIRED',
                'message': 'Use the authorized URL check service when available.', 'retryable': False}})}
