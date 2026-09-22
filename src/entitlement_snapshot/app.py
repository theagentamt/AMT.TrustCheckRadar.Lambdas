"""Retired snapshot never normalizes a legacy record into an access grant."""
import json


def lambda_handler(_event, _context):
    return {'statusCode': 409, 'headers': {'Content-Type': 'application/json', 'Cache-Control': 'no-store'},
            'body': json.dumps({'error': {'code': 'LEGACY_MIGRATION_REQUIRED',
                'message': 'Use the current access service after migration qualification.', 'retryable': False}})}
