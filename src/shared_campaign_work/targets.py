"""Existing row-family deadline classification; no payload projection or copying."""
import re
from .records import need,integer,target
from .configuration import plain

DEADLINE_FIELDS=('expiresAt','targetExpiresAtEpoch','deletionDeadlineEpoch','logicalExpiresAt')


def deadline(family,row):
    target(family,row.get('PK'),row.get('SK'))
    sk=row['SK']
    field='logicalExpiresAt' if family=='outbox' and sk.startswith('OUTBOX#') and row.get('schemaVersion')==2 else 'targetExpiresAtEpoch' if sk.startswith('LOCATOR#') else 'deletionDeadlineEpoch' if sk=='TOMBSTONE' else 'expiresAt'
    value=integer(row.get(field),1)
    if family=='pipeline' and 'GSI3SK' in row:need(integer(row['GSI3SK'],1)==value)
    return value


def updated_deadline(family,observed,operation):
    value=deadline(family,observed)
    expression=operation.get('UpdateExpression','')
    need(type(expression) is str and bool(expression))
    for alias,name in operation.get('ExpressionAttributeNames',{}).items():
        expression=re.sub(re.escape(alias)+r'(?![A-Za-z0-9_])',name,expression)
    for field in DEADLINE_FIELDS:
        if re.search(r'\b'+field+r'\b',expression):
            matches=re.findall(r'\b'+field+r'\s*=\s*(:[A-Za-z0-9_]+)(?=\s*(?:,|$|ADD\b|REMOVE\b|DELETE\b))',expression)
            need(len(matches)==1)
            values=plain(operation.get('ExpressionAttributeValues',{}))
            next_value=integer(values.get(matches[0]),1)
            need(next_value<=value);value=next_value
    return value
