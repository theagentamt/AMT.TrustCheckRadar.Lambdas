"""Explicit synthetic admission qualification, never a production initializer."""
GENERATION='7a1e0c60-8074-47eb-867c-a739b966b0b9'
ARN='arn:aws:kms:us-east-1:107827791950:key/12345678-1234-4234-8234-123456789abc'
def enable(monkeypatch):
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_ACCOUNT_ID','107827791950')
    monkeypatch.setenv('AWS_REGION','us-east-1')
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_ENABLED','true')
    monkeypatch.setenv('CAMPAIGN_PERIOD_ADMISSION_GENERATION',GENERATION)
def fields(now=1,state='OPEN'):
    return {'admissionSchemaVersion':1,'admissionGeneration':GENERATION,'admissionState':state,
        'admissionRevision':1,'admissionManifestSha256':'a'*64,'admissionInventoryRevision':1,'admissionChangedAtEpoch':now}
def row(period,now=1,state='OPEN'):
    return {'PK':f'PERIOD#{period}','SK':'HMAC_KEY','periodId':period,'keyArn':ARN,'status':'ENABLED',
        'retireAfterEpoch':(period+1)*14*86400+7*86400,**fields(now,state)}
