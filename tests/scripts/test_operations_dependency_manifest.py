"""Coordinated package negatives; local synthetic archives, no cloud access."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import importlib.util
import zipfile

import pytest

SPEC=importlib.util.spec_from_file_location('operations_dependency_checker',Path(__file__).resolve().parents[2]/'scripts/verify_operations_dependencies.py')
checker=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(checker)


@pytest.fixture
def joined(tmp_path):
    source=tmp_path/'source';artifacts=tmp_path/'archives';source.mkdir();artifacts.mkdir()
    pins={};entries=[]
    for name in sorted(checker.PACKAGES):
        members={}
        if name in checker.AUTHORITY_PACKAGES:
            members.update({member:'src/'+member for member in checker.PROTOCOL_FILES})
        else:members[name+'/policy.py']='src/'+name+'/policy.py'
        if name in checker.CALLERS:
            members['service.py' if name=='url_consumer' else name+'/service.py']='src/'+name+'/service.py'
            members['app.py' if name=='url_consumer' else name+'/app.py']='src/'+name+'/app.py'
        values={'app.py':f'from {name}.app import lambda_handler\n'.encode(),'contract/schema.json':b'# synthetic contractPins\n'}
        for member,path in members.items():
            raw=('# current '+path+'\n').encode();target=source/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(raw)
            pins[path]=sha256(raw).hexdigest();values[member]=raw
        target=artifacts/(name+'.zip')
        with zipfile.ZipFile(target,'w') as archive:
            for member,raw in values.items():archive.writestr(member,raw)
        entries.append({'function':name,'file':target.name,'bytes':target.stat().st_size,'sha256':sha256(target.read_bytes()).hexdigest(),
            'skipDependencies':True,'distributionQualified':False,'sourceMembers':members,'contractMembers':{'contract/schema.json':'contracts/current/schema.json'}})
    extra={}
    for group,path in [('contractPins','contracts/current/schema.json'),('requirementsPins','src/url_consumer/requirements.txt')]:
        target=source/path;target.parent.mkdir(parents=True,exist_ok=True);target.write_text('# synthetic '+group+'\n')
        extra[group]={path:sha256(target.read_bytes()).hexdigest()}
    return {'schemaVersion':1,**extra,'actionReady':False,'distributionQualified':False,'awsReadPerformed':False,'artifacts':entries,'sourcePins':pins},artifacts,source


def test_complete_source_closure_passes_without_claiming_distribution(joined):
    manifest,archives,source=joined
    assert checker.verify(manifest,archives,source_root=source)==15


@pytest.mark.parametrize('drift',['missing-cleanup','old-retirement','old-paid-closure','missing-caller','extra-kernel','wrong-source','claims-ready','old-contract-member','unknown-python'])
def test_missing_or_old_coordinated_package_is_rejected_even_with_updated_zip_digest(joined,drift):
    manifest,archives,source=joined;manifest=deepcopy(manifest)
    if drift=='missing-cleanup':manifest['artifacts']=[entry for entry in manifest['artifacts'] if entry['function']!='url_lease_recovery']
    elif drift=='claims-ready':manifest['actionReady']=True
    elif drift=='wrong-source':(source/'src/shared_check_authority/core.py').write_text('# stale\n')
    else:
        name='url_lease_recovery' if drift=='old-retirement' else 'v1_play_handoff' if drift=='old-paid-closure' else 'url_consumer'
        entry=next(entry for entry in manifest['artifacts'] if entry['function']==name);target=archives/entry['file']
        with zipfile.ZipFile(target) as archive:values={key:archive.read(key) for key in archive.namelist()}
        if drift=='old-retirement':values['shared_check_authority/operations_retirement.py']=b'# previous cleanup\n'
        elif drift=='old-paid-closure':values['shared_check_authority/purchase_usage.py']=b'# previous paid compatibility\n'
        elif drift=='missing-caller':
            values.pop('shared_check_authority/operations_callers.py');entry['sourceMembers'].pop('shared_check_authority/operations_callers.py')
        elif drift=='old-contract-member':values['contract/schema.json']=b'# previous contract\n'
        elif drift=='unknown-python':values['unreviewed.py']=b'# extra code\n'
        else:values['operations_reporting/app.py']=b'# unauthorized runtime\n'
        with zipfile.ZipFile(target,'w') as archive:
            for member,raw in values.items():archive.writestr(member,raw)
        entry.update(bytes=target.stat().st_size,sha256=sha256(target.read_bytes()).hexdigest())
    with pytest.raises((ValueError,KeyError)):
        checker.verify(manifest,archives,source_root=source)


@pytest.mark.parametrize('group',['contractPins','requirementsPins'])
def test_declared_contract_and_requirements_pin_drift_is_rejected(joined,group):
    manifest,archives,source=joined;manifest=deepcopy(manifest)
    key=next(iter(manifest[group]));manifest[group][key]='0'*64
    with pytest.raises(ValueError):checker.verify(manifest,archives,source_root=source)
