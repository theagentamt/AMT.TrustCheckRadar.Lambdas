"""Only the separately authorized IAM route; no stream/scheduled dispatch or logging."""
import json
import os


def lambda_handler(event, context):
    # Must precede SDK initialization and parsing of any supplied proof.
    if os.environ.get('SUPPORT_ACCOUNT_DELETION_ENABLED') != 'true':
        return response(503, {'code': 'SUPPORT_ADMISSION_DISABLED'})
    try:
        from support_account_deletion.runtime import execute
        return response(202, execute(event, context))
    except Exception:
        # Never log the signed record, identity, provider error or support evidence.
        return response(503, {'code': 'SUPPORT_ADMISSION_UNAVAILABLE'})


def response(status, body):
    return {'statusCode': status, 'headers': {'content-type': 'application/json',
            'cache-control': 'no-store'}, 'body': json.dumps(body, separators=(',', ':'))}
