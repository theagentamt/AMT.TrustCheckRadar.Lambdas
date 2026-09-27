"""Synthetic restore and new-subject isolation; never imported by production."""
import importlib.util
import os
import time
import hashlib
from pathlib import Path
from uuid import UUID

from shared_check_authority.core import Authority, AuthorityError, Settings, OWNER_POLICY
from shared_account_finalization.service import Finalizer, REQUIRED_COMPONENTS, validate_inventory
from shared_history.security import assert_authoritative_account_active
from shared_history.errors import HistoryError


def authority(r, resources):
    settings=Settings(r.tables['users'],r.tables['devices'],r.tables['ledger'],r.tables['authority'],
        'https://synthetic-issuer','synthetic-client','checks',OWNER_POLICY,'k1',
        {'k1':b'synthetic-key-material-for-fixture-000000'},60,120,300,480,600,60,100,3,True)
    return Authority(settings,resources,now=lambda:r.now)


def event(r, subject):
    return {'requestContext':{'authorizer':{'jwt':{'claims':{'sub':subject,'iss':'https://synthetic-issuer',
        'client_id':'synthetic-client','token_use':'access','scope':'checks','exp':str(r.now+900),
        'iat':str(r.now),'auth_time':str(r.now)}}}}}


def profile_writer(r, resources):
    path=Path(__file__).with_name('_qualification_profile.py')
    if not path.exists():path=Path(__file__).resolve().parents[2]/'src/post_confirmation/app.py'
    values={'USERS_TABLE_NAME':r.tables['users'],'DELETION_LEDGER_TABLE_NAME':r.tables['ledger'],'AWS_DEFAULT_REGION':'us-east-1'}
    previous={k:os.environ.get(k) for k in values};os.environ.update(values)
    try:
        spec=importlib.util.spec_from_file_location('_qualification_profile',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        module.dynamodb_client.close();module.dynamodb.meta.client.close()
        module.dynamodb_client=resources.raw
        return module
    finally:
        for key,value in previous.items():
            if value is None:os.environ.pop(key,None)
            else:os.environ[key]=value


def profile_event(subject,email):
    return {'request':{'userAttributes':{'sub':subject,'email':email,'given_name':'Synthetic','family_name':'Qualification'}}}


def completed_proof(r, subject, require):
    now=int(time.time())
    terminal=r.get('ledger','ACCOUNT#'+subject,'ACCOUNT_DELETION')
    require(isinstance(terminal,dict) and terminal.get('status')=='COMPLETE')
    original={k:v for k,v in terminal.items() if k not in {'completedAtEpoch','retainUntilEpoch'}}
    original.update(status='REQUESTED',eventType='account.deletion.requested')
    Finalizer._validate_command(original,now,'dev')
    require(Finalizer._completed(terminal,original,now))
    validator=Finalizer(ledger_table=None,ledger_table_name=r.tables['ledger'],client=None,cognito=None,
        user_pool_id='us-east-1_Synthetic',environment='dev',now=lambda:r.now)
    for component in REQUIRED_COMPONENTS:
        row=r.get('ledger',original['PK'],'ACCOUNT_DELETION#'+component)
        validator._validate_receipt(row,original,component,now)
    require(r.get('ledger',original['PK'],'CAMPAIGN_RECOVERY_CONTROL') is None)
    require(not any(row['PK']==original['PK'] and row['SK'].startswith('CAMPAIGN_RECOVERY#') for row in r.rows('ledger')))
    return original


def reregister(r, resources, previous, email, require):
    """Cognito was independently re-created at the same email; never create identities here."""
    require(previous!=r.subject and str(UUID(previous))==previous and str(UUID(r.subject))==r.subject)
    old=completed_proof(r,previous,require)
    before=r.snapshot();a=authority(r,resources)
    newpk='USER#'+r.subject;oldpk='USER#'+previous
    oldpartition=a._partition(previous,'k1');newpartition=a._partition(r.subject,'k1')
    require(oldpartition!=newpartition)
    # Preflight every store before invoking the genuine create-only profile writer.
    for kind,rows in before.items():
        require(not any(row['PK']==newpk or row['PK'].startswith(newpk+'#') or row['PK']==newpartition
            or row['PK']=='ACCOUNT#'+r.subject or row.get('accountId')==r.subject for row in rows))
    usage=[row for row in before['authority'] if row.get('recordType')=='V1_PURCHASE_USAGE']
    require(len(usage)==1 and usage[0]['usedChecks']==7 and usage[0]['reservedChecks']==0)
    writer=profile_writer(r,resources);writer.lambda_handler(profile_event(r.subject,email),None)
    profile=r.get('users',newpk,'PROFILE')
    require(profile['sub']==r.subject and profile['email']==email and profile['status']=='PENDING_AGE_GATE'
        and profile['ageVerified'] is False)
    try:a._account(event(r,r.subject))
    except AuthorityError as exc:require(exc.code=='ACCOUNT_UNAVAILABLE')
    else:raise ValueError('UNINTENDED_ACCESS_GRANTED')
    # A delayed old-signup callback cannot recreate or relink the deleted account.
    try:writer.lambda_handler(profile_event(previous,email),None)
    except RuntimeError as exc:require(str(exc)=='POST_CONFIRMATION_FAILED')
    else:raise ValueError('DELETED_IDENTITY_RECREATED')
    after=r.snapshot();expected=dict(before);expected['users']=sorted(before['users']+[profile],key=lambda row:(row['PK'],row['SK']))
    require(after==expected and r.get('users',oldpk,'PROFILE') is None)
    require(completed_proof(r,previous,require)==old)
    # No automatic purchase restore, trial, allowance, history or token attachment.
    require(not any(row['PK']==newpartition for row in after['authority']+after['tokens']))
    require([row for row in after['authority'] if row.get('recordType')=='V1_PURCHASE_USAGE']==usage)
    return {'newSubjectIsolated':True,'oldSuppressionPreserved':True,'automaticGrantCreated':False,
            'retainedPaidUsedChecks':7,'explicitStoreRestorePerformed':False}


def restore_quarantine(r, resources, seeds, require, campaign_app, stream):
    """Rehydrate synthetic application copies; this is not native backup restore.

    Original suppression/receipts stay authoritative. Residual copied data is
    deliberately retained in the isolated fixture and cannot be called erased.
    """
    from shared_account_finalization.service import FinalizationError
    original=completed_proof(r,r.subject,require)
    ledger_before=r.rows('ledger')
    before=r.snapshot();a=authority(r,resources)
    pk='USER#'+r.subject;partition=a._partition(r.subject,'k1')
    selected={}
    for kind,row in seeds:
        # Replay only bounded representative account data, never receipts,
        # marker approvals, global usage, period metadata or unrelated rows.
        if kind in {'ledger','pipeline'}:continue
        owned=(row['PK']==pk or row['PK'].startswith(pk+'#') or row['PK']==partition
               or row.get('accountId')==r.subject
               or kind=='abuse' and row['PK'].endswith('#'+hashlib.sha256(r.subject.encode()).hexdigest()))
        if owned and kind not in selected:selected[kind]=dict(row)
    require({'users','devices','recovery','abuse','outbox','entitlements','history-control','history-content','authority','tokens'}<=set(selected))
    selected['users']=selected['users']|{'status':'ACTIVE','ageVerified':True}
    for kind,row in selected.items():r.put(kind,row)
    # Unknown restored evidence must stay quarantined, not silently deleted.
    unknown={'PK':pk,'SK':'UNKNOWN_RESTORED','synthetic':True}
    r.put('recovery',unknown)
    restored=r.snapshot()
    try:assert_authoritative_account_active(r.subject,resources.Table(r.tables['users']),resources.Table(r.tables['ledger']))
    except HistoryError as exc:require(exc.code=='FORBIDDEN')
    else:raise ValueError('RESTORED_HISTORY_ACCESSIBLE')
    for attempt in (lambda:a._account(event(r,r.subject)),):
        try:attempt()
        except AuthorityError as exc:require(exc.code=='ACCOUNT_UNAVAILABLE')
        else:raise ValueError('RESTORED_DATA_ACCESSIBLE')
    # A queued authority transaction that observed an active copied profile
    # still loses against the actual durable account fence at commit time.
    delayed={'PK':partition,'SK':'DELAYED_RESTORE_WRITE','synthetic':True}
    try:a._transact(a._account_conditions(r.subject)+[{'Put':{'TableName':r.tables['authority'],'Item':delayed}}])
    except AuthorityError as exc:require(exc.code=='TRANSACTION_UNCERTAIN')
    else:raise ValueError('RESTORED_WRITE_ACCEPTED')
    writer=profile_writer(r,resources)
    try:writer.lambda_handler(profile_event(r.subject,'fixture-'+r.config['run']+'@example.invalid'),None)
    except RuntimeError as exc:require(str(exc)=='POST_CONFIRMATION_FAILED')
    else:raise ValueError('RESTORED_SIGNUP_ACCEPTED')
    # Fresh generation pins must reject copied, absent and stale approvals.
    # These are real validators, not a caller-supplied qualified Boolean.
    marker=r.get('ledger','INVENTORY#dev','ACCOUNT_DATA_INVENTORY')
    for value in (None,marker,marker|{'revision':0}):
        try:validate_inventory(value,'dev','e'*64,expected_revision=2,now_epoch=r.now)
        except FinalizationError:pass
        else:raise ValueError('RESTORED_INVENTORY_ACCEPTED')
    # A restored environment without its suppression ledger is not admitted
    # with a copied old inventory. Exercise the real admission path against
    # independently changed expected generation pins, with no fresh command.
    import sys
    cleanup=sys.modules.get('account_cleanup')
    if cleanup is None:
        spec=importlib.util.spec_from_file_location('_restore_account',Path(__file__).with_name('account_cleanup.py'))
        cleanup=importlib.util.module_from_spec(spec);spec.loader.exec_module(cleanup)
    service,_=cleanup._account_modules()
    admission=service.AccountDeletionService(environment='dev',ledger_table=resources.Table(r.tables['ledger']),
        users_table_name=r.tables['users'],ledger_table_name=r.tables['ledger'],dynamodb_client=resources.raw,
        required_components=REQUIRED_COMPONENTS,now=lambda:r.now,inventory_manifest_sha256='e'*64,
        inventory_revision=2,campaign_recovery_writes_enabled=True)
    terminal=r.get('ledger',original['PK'],original['SK'])
    r.delete('ledger',original['PK'],original['SK'])
    try:
        for missing in (False,True):
            if missing:r.delete('ledger',marker['PK'],marker['SK'])
            held=r.snapshot()
            try:admission.request(r.subject,'99999999-9999-4999-8999-999999999999')
            except service.AppError as exc:require(exc.code=='SERVER_UNAVAILABLE')
            else:raise ValueError('UNQUALIFIED_RESTORE_ADMITTED')
            require(r.snapshot()==held)
    finally:
        r.put('ledger',marker);r.put('ledger',terminal)
    # Terminal event replay acknowledges history, never claims newly copied
    # data was erased and never manufactures a new completion receipt.
    require(campaign_app.lambda_handler(stream,r.context)['completed']==0)
    require(completed_proof(r,r.subject,require)==original)
    require(r.rows('ledger')==ledger_before and r.snapshot()==restored)
    require(r.get('recovery',pk,unknown['SK'])==unknown)
    require(before!=restored and all(r.get(kind,row['PK'],row['SK'])==row for kind,row in selected.items()))
