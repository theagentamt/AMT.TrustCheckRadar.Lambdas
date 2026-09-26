#!/usr/bin/env python3
"""Local-only clean-source builder for an isolated qualification Lambda ZIP."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]


def require(value, message):
    if not value: raise ValueError(message)


def git(*args):
    return subprocess.check_output(['git', *args], cwd=ROOT)


def build(source, output):
    require(re.fullmatch('[0-9a-f]{40}', source) is not None, 'Exact source SHA required')
    require(git('rev-parse','HEAD').decode().strip()==source, 'Source must equal HEAD')
    require(not git('status','--porcelain').strip(), 'Clean tree required')
    require(subprocess.run(['git','merge-base','--is-ancestor','origin/release-V01',source],cwd=ROOT).returncode==0,
            'Source must include current fetched release')
    output=Path(output).resolve();output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='campaign-qualification-build-') as temp:
        subprocess.run(['bash','scripts/build_lambda_zip.sh','--function','campaign_deletion_bridge',
            '--python-version','3.14','--arch','arm64','--output-dir',temp],cwd=ROOT,check=True)
        production=Path(temp)/'campaign_deletion_bridge.zip'
        payload=production.read_bytes();prod_digest=hashlib.sha256(payload).hexdigest()
        (output/'campaign_deletion_bridge.zip').write_bytes(payload)
        members={}
        with zipfile.ZipFile(production) as archive:
            require(len(archive.namelist())==len(set(archive.namelist())), 'Duplicate archive members')
            for name in archive.namelist():
                require(not name.startswith('/') and '..' not in Path(name).parts, 'Unsafe archive path')
                require(name.endswith('.py'), 'Unexpected dependency: qualify explicitly before adding')
                source_path=('src/campaign_deletion_bridge/'+name if '/' not in name else 'src/'+name)
                data=archive.read(name)
                require(data==git('show',source+':'+source_path), 'Production source mismatch: '+name)
                compile(data,name,'exec');members[name]=data
        production_archives={'campaign_deletion_bridge':{'sha256':prod_digest,'sizeBytes':len(payload),'handler':'app.lambda_handler'}}
        for function in ('campaign_observation_publisher','campaign_cluster_aggregator','campaign_lifecycle'):
            subprocess.run(['bash','scripts/build_lambda_zip.sh','--function',function,
                '--python-version','3.14','--arch','arm64','--output-dir',temp],cwd=ROOT,check=True)
            path=Path(temp)/(function+'.zip');data=path.read_bytes()
            (output/path.name).write_bytes(data)
            production_archives[function]={'sha256':hashlib.sha256(data).hexdigest(),'sizeBytes':len(data),'handler':'app.lambda_handler'}
            with zipfile.ZipFile(path) as archive:
                require(len(archive.namelist())==len(set(archive.namelist())), 'Duplicate archive members')
                for name in archive.namelist():
                    require(name.endswith('.py') and not name.startswith('/') and '..' not in Path(name).parts,'Unexpected archive member')
                    source_path=('src/'+function+'/'+name if '/' not in name else 'src/'+name)
                    value=archive.read(name)
                    require(value==git('show',source+':'+source_path),'Production source mismatch: '+source_path)
                    compile(value,name,'exec')
                    if '/' not in name:
                        members['_qualification_workers/'+function+'/'+name]=value
                    else:
                        require(name not in members or members[name]==value,'Shared source mismatch')
                        members[name]=value
        # Fixture-only finalizer uses injected Cognito; production campaign ZIPs
        # do not gain identity permissions or a finalization handler.
        for path in sorted((ROOT/'src/shared_account_finalization').glob('*.py')):
            name='shared_account_finalization/'+path.name
            data=git('show',source+':src/'+name)
            require(data==path.read_bytes(),'Finalizer fixture source mismatch')
            compile(data,name,'exec');members[name]=data
        harness=git('show',source+':scripts/qualification/campaign_qualification.py')
        require(harness==(ROOT/'scripts/qualification/campaign_qualification.py').read_bytes(), 'Runner source mismatch')
        members['campaign_qualification.py']=harness
        manifest={'schemaVersion':1,'sourceSha':source,'handler':'campaign_qualification.lambda_handler',
            'runtime':'python3.14','architecture':'arm64','productionZipSha256':prod_digest,
            'productionArchive':'campaign_deletion_bridge.zip','productionZipSizeBytes':len(payload),
            'productionHandler':'app.lambda_handler','productionArchives':production_archives,
            'syntheticOnly':True,'historicalCoverageApproved':False,
            'memberSha256':{name:hashlib.sha256(data).hexdigest() for name,data in sorted(members.items())}}
        members['qualification-manifest.json']=(json.dumps(manifest,indent=2,sort_keys=True)+'\n').encode()
        target=output/'campaign_completion_qualification.zip'
        with zipfile.ZipFile(target,'w',compression=zipfile.ZIP_DEFLATED) as archive:
            for name,data in sorted(members.items()):
                info=zipfile.ZipInfo(name,date_time=(2020,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
                info.external_attr=0o100644<<16;archive.writestr(info,data)
        require(not git('status','--porcelain').strip(), 'Build changed repository')
        manifest|={'qualificationZipSha256':hashlib.sha256(target.read_bytes()).hexdigest(),
                   'qualificationZipSizeBytes':target.stat().st_size,'archive':target.name}
        (output/'manifest.json').write_text(json.dumps(manifest,indent=2,sort_keys=True)+'\n')
        return manifest


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-sha',required=True);parser.add_argument('--output-dir',required=True)
    args=parser.parse_args()
    print(json.dumps(build(args.source_sha,args.output_dir),sort_keys=True))
