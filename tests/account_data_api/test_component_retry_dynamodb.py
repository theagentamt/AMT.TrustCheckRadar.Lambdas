"""Real SDK loss after receipt commit; no synthetic overall completion claim."""
import hashlib
import os
import pytest
if os.environ.get('AMT_AUTHORITY_INTEGRATION')!='1':pytest.skip('Isolated SDK only',allow_module_level=True)
from tests.campaign_deletion_bridge.test_qualification_dynamodb import runner,Q

COMPONENTS=('SESSION_REVOCATION','DEVICE_BINDINGS','DEVICE_RECOVERY','ANALYSIS_ABUSE','CAMPAIGN_OUTBOX','USER_PROFILE')


@pytest.mark.parametrize('component',COMPONENTS)
def test_receipt_committed_response_lost_retry_preserves_original_deadline(runner,component):
    r=runner;r.preflight();r.seed()
    resources=Q.account_cleanup.Resources(r)
    service,_=Q.account_cleanup._account_modules()
    now=r.now;pk='USER#'+r.subject;sha=hashlib.sha256(r.subject.encode()).hexdigest()
    table=lambda kind:resources.Table(r.tables[kind])
    identity=Q.account_cleanup.Identity(r.subject)
    try:
        other={'PK':'USER#other','SK':'PRESERVE','synthetic':True}
        for kind in ('devices','recovery','abuse','outbox','users'):r.put(kind,other)
        r.put('devices',{'PK':pk,'SK':'DEVICE#synthetic'})
        r.put('recovery',{'PK':pk,'SK':'RATE#'+str(now),'requestCount':1,'expiresAt':now+100})
        r.put('abuse',{'PK':'ANALYSIS#RATE#'+sha,'SK':'synthetic','expiresAt':now+100})
        eid='77777777-7777-4777-8777-777777777777'
        r.put('outbox',{'PK':'ACCOUNT#'+sha,'SK':'OUTBOX#'+eid,'recordType':'CAMPAIGN_OUTBOX_LOCATOR',
            'schemaVersion':1,'recordVersion':1,'environment':'dev','accountIdHash':sha,'statisticsEventId':eid,
            'eventPK':'EVENT#'+eid,'eventSK':'OBSERVATION_READY','eventExpiresAt':now+100,'expiresAt':now+100+86400})
        r.put('outbox',{'PK':'EVENT#'+eid,'SK':'OBSERVATION_READY','accountId':r.subject,'statisticsEventId':eid,'environment':'dev'})
        r.put('users',r.get('users',pk,'PROFILE')|{'deletionRequestedAtEpoch':r.when})
        if component=='USER_PROFILE':
            # Only this isolated producer's dependencies are synthetic. The
            # all-component runner separately proves every receipt producer.
            for name in service.USER_PROFILE_PREREQUISITES:r.put('ledger',service._component_receipt(r.cmd,name,now))
        def invoke(clock):
            common={'ledger_table':table('ledger'),'now_epoch':clock}
            if component=='SESSION_REVOCATION':return service.ensure_session_revoked(r.cmd,user_pool_id='us-east-1_Synthetic',cognito=identity,**common)
            if component=='DEVICE_BINDINGS':return service.delete_device_bindings(r.cmd,device_table=table('devices'),**common)
            if component=='DEVICE_RECOVERY':return service.delete_device_recovery_control(r.cmd,recovery_table=table('recovery'),**common)
            if component=='ANALYSIS_ABUSE':return service.delete_analysis_abuse_control(r.cmd,abuse_table=table('abuse'),request_dedupe_policy_status='approved',legacy_request_retention_policy_status='approved',consumption_deletion_policy_status='approved',**common)
            if component=='CAMPAIGN_OUTBOX':return service.delete_campaign_outbox(r.cmd,outbox_table=table('outbox'),locator_coverage_status='approved',**common)
            return service.delete_user_profile_state(r.cmd,users_table=table('users'),policy_status='approved',demographic_research_policy_status='approved',**common)
        fired=[]
        def after(name,kw):
            if not fired and name=='put_item' and kw.get('Item',{}).get('component')==component:
                fired.append(True);raise Q.account_cleanup.Interrupted('SYNTHETIC_COMMITTED_ACK_LOSS')
        resources.after=after
        for _ in range(6):
            try:invoke(now)
            except Q.account_cleanup.Interrupted:break
        else:pytest.fail('Receipt fault was not exercised')
        require_key={'PK':r.cmd['PK'],'SK':'ACCOUNT_DELETION#'+component}
        receipt=table('ledger').get_item(Key=require_key,ConsistentRead=True)['Item']
        assert fired==[True] and receipt['retainUntilEpoch']==now+120*86400
        resources.after=lambda *args:None
        result=invoke(now+15)
        assert result is True or result['complete']
        assert table('ledger').get_item(Key=require_key,ConsistentRead=True)['Item']==receipt
        assert r.get('ledger',r.cmd['PK'],r.cmd['SK'])==r.cmd
        assert r.get('ledger',r.cmd['PK'],'ACCOUNT_DELETION#IDENTITY') is None
        for kind in ('devices','recovery','abuse','outbox','users'):assert r.get(kind,other['PK'],other['SK'])==other
        if component=='SESSION_REVOCATION':assert identity.calls==['signout']
    finally:resources.close()
