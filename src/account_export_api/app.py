"""No payload, identifier, token, cursor or exception logging."""
import logging
import os
import time
from .cursor import ExportError, encode
from .service import parse
from .runtime import load
from shared_check_authority.core import AuthorityError


LOGGER = logging.getLogger(__name__)
LOGGER.setLevel(logging.INFO)

def lambda_handler(event, _context):
    operation = 'unknown'
    try:
        service = load()
        body = parse(event)
        operation = 'start' if body['action'] == 'START_EXPORT' else 'continue'
        result = service.page(event,body)
        status = 200
    except ExportError as err:
        status = err.status
        result = {'schemaVersion':1,'error':{'code':err.code,'retryable':status == 503}}
    except AuthorityError as err:
        auth = err.code == 'AUTHENTICATION_REQUIRED'
        status = 401 if auth else 403 if err.code in ('ACCOUNT_UNAVAILABLE','ACTIVE_DEVICE_REQUIRED') else 503
        result = {'schemaVersion':1,'error':{'code':err.code if status != 503 else 'SERVICE_UNAVAILABLE','retryable':status == 503}}
    except Exception:
        status = 503
        result = {'schemaVersion':1,'error':{'code':'SERVICE_UNAVAILABLE','retryable':True}}
    name = 'AccountExportPageSuccess' if status == 200 else 'AccountExportUnavailable' if status >= 500 else 'AccountExportRejected'
    environment = os.environ.get('STAGE')
    if environment not in ('dev','uat','prod'): environment = 'unknown'
    LOGGER.info(encode({'_aws':{'Timestamp':int(time.time()*1000),'CloudWatchMetrics':[{'Namespace':'AMT/TrustCheckRadar/AccountExport','Dimensions':[['Environment','Operation']],'Metrics':[{'Name':name,'Unit':'Count'}]}]},'Environment':environment,'Operation':operation,name:1}).decode())
    return {'statusCode':status,'headers':{'Content-Type':'application/json','Cache-Control':'private, no-store','Pragma':'no-cache'},'body':encode(result).decode()}
