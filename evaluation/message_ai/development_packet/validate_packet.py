"""Local structural checks only. No live transport, reviewer fabrication, or quality claim."""
import argparse, copy, csv, hashlib, json, socket, subprocess, sys, unicodedata
import urllib.request
from collections import Counter
from pathlib import Path

def no_network(*args, **kwargs):
    raise AssertionError('NETWORK_NOT_ALLOWED_IN_PACKET_VALIDATION')
socket.create_connection=no_network
socket.socket.connect=no_network
urllib.request.urlopen=no_network
p=argparse.ArgumentParser();p.add_argument('--lambda-repo',required=True);p.add_argument('--output');args=p.parse_args()
repo=Path(args.lambda_repo).resolve();sys.path[:0]=[str(repo),str(repo/'src')]
from evaluation.message_ai.corpus import validate
from evaluation.message_ai.profile import canonical, digest
from message_evaluator.ai_provider import parse
P=Path(__file__).resolve().parent
v=json.loads((P/'engineering/development-corpus.json').read_text());validate(v)
meta=json.loads((P/'engineering/cohort-manifest.json').read_text());by={c['caseId']:c for c in v['cases']};groups={}
assert len(by)==len(v['cases'])==40
assert Counter(c['intent']['language'] for c in v['cases'])=={'en':20,'es':20}
assert len(meta['cases'])==40 and meta['qualificationEligible'] is False and meta['holdoutEligible'] is False
fields={'sourceRecordSha256':'engineering/source-record.json','permissionRecordSha256':'engineering/permission-scope.json','rubricSha256':'review/RUBRIC.en-es.md'}
for c in v['cases']:
 groups.setdefault(c['familyId'],[]).append(c)
 assert c['split']=='development' and c['review']['status']=='engineering_only'
 assert c['review']['reviewerIds']==[] and c['review']['adjudicatorId'] is None
 assert c['review']['labelSha256']==digest(c['expected'])
 for key,path in fields.items():
  assert (c['review'] if key=='rubricSha256' else c['provenance'])[key]==hashlib.sha256((P/path).read_bytes()).hexdigest()
 text=c['intent']['target']['sanitizedText'];assert text==unicodedata.normalize('NFC',text)
 assert len(text)<=8000 and len(canonical(c['intent']).encode())<32768
 proposal={k:c['simulation'][k] for k in ['assessment','context','reasons']}
 for reason in proposal['reasons']:
  end=0
  for span in reason['spans']:
   assert end<=span['start']<span['end']<=len(text);end=span['end']
   assert any(x.isalpha() for x in text[span['start']:span['end']])
 wire={'model':'offline-synthetic','status':'completed','output':[{'type':'message','role':'assistant','status':'completed','content':[{'type':'output_text','text':canonical(proposal)}]}]}
 assert parse(canonical(wire).encode(),text,expected_model='offline-synthetic')==proposal
assert len(groups)==20
for pair in groups.values():
 assert {c['intent']['language'] for c in pair}=={'en','es'} and len(pair)==2
 a,b=pair;assert a['expected']==b['expected']
 for field in ['speakerRole','sourceType','withheldLinks','reviewedLinks','entities']:
  assert a['intent']['target'][field]==b['intent']['target'][field]
 assert a['simulation']['kind']==b['simulation']['kind'] and a['simulation']['context']==b['simulation']['context']
for c in meta['cases']:
 assert c['caseId'] in by and c['pairedTranslation'] in by
 assert by[c['caseId']]['familyId']==by[c['pairedTranslation']]['familyId']==c['familyId']
 for field in ['expectedAiAssessment','expectedReasonCategories']:
  assert c[field]==by[c['caseId']]['expected']['assessment' if field=='expectedAiAssessment' else 'reasonCodes']
cohorts={lang:dict(Counter(c['cohort'] for c in meta['cases'] if c['language']==lang)) for lang in ['en','es']}
assert cohorts['en']==cohorts['es']=={'warning':8,'benign':5,'ambiguous':3,'adversarial':4}
categories={lang:dict(Counter(r for c in v['cases'] if c['intent']['language']==lang for r in c['expected']['reasonCodes'])) for lang in ['en','es']}
assert categories['en']==categories['es'] and len(categories['en'])==5
with (P/'review/blind-worksheet.csv').open() as f:rows=list(csv.DictReader(f))
assert len(rows)==40
shown={'case_id','language','speaker_role','source_type','sanitized_text'}
for row in rows:
 assert row['case_id'] in by and row['sanitized_text']==by[row['case_id']]['intent']['target']['sanitizedText']
 assert all(value=='' for k,value in row.items() if k not in shown)
 assert not any(k in row for k in ['cohort','expected','expectedPolicySkip','family_id'])
with (P/'review/adjudication-worksheet.csv').open() as f:adjudication=list(csv.DictReader(f))
assert len(adjudication)==40 and all(all(value=='' for k,value in row.items() if k!='case_id') for row in adjudication)
for cid in ['dev-019-en','dev-019-es']:
 text=by[cid]['intent']['target']['sanitizedText'];assert len(text.encode('utf-16-le'))//2==len(text)+1
 invalid=copy.deepcopy(v);case=next(c for c in invalid['cases'] if c['caseId']==cid)
 case['intent']['target']['sanitizedText']=unicodedata.normalize('NFD',text)
 try:validate(invalid)
 except Exception:pass
 else:raise AssertionError('NFD unexpectedly accepted')
invalid=copy.deepcopy(v);invalid['cases'][0]['split']='holdout'
try:validate(invalid)
except Exception:pass
else:raise AssertionError('Engineering-only holdout unexpectedly accepted')
commit=subprocess.check_output(['git','-C',str(repo),'rev-parse','HEAD'],text=True).strip()
result={'schemaVersion':1,'status':'structural_checks_passed','lambdaSourceCommit':commit,'cases':40,'translationFamilies':20,'languages':{'en':20,'es':20},'cohorts':cohorts,'proposedCategoryCounts':categories,'realProviderCalls':0,'humanReviews':0,'qualificationEligible':False,'holdoutEligible':False,'checks':['real existing corpus schema validator','actual source/scope/rubric/label hash bindings','EN/ES paired role/source/entity/link/label structural parity','all five proposed categories per language','NFC and codepoint span boundaries','synthetic response shapes accepted by shared parser','NFD rejected at existing intake boundary','engineering-only holdout rejected','all human-review and adjudication worksheet cells blank'],'limitations':['Semantic translation correctness and evidence support are not independently reviewed','Simulation deliberately mirrors proposed labels; no model accuracy claim','No cohort threshold or representative sample claim','No billing/recovery or provider transport exercised','Explicit backend-only independent-link cases do not match current Android reviewedLinks-empty scope']}
if args.output:Path(args.output).write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({'status':result['status'],'cases':40,'realProviderCalls':0,'humanReviews':0,'qualificationEligible':False}))
