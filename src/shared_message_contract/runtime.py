"""Pure package-safe JSON and URL contract helpers; no AWS/authority imports."""
import importlib.util
from pathlib import Path


def unique_pairs(pairs):
    result={}
    for key,value in pairs:
        if key in result:raise ValueError('DUPLICATE_FIELD')
        result[key]=value
    return result


def url_mapper():
    path=Path(__file__).parent/'url_contract'/'reference_mapping.py'
    if not path.exists():path=Path(__file__).resolve().parents[2]/'contracts/url-assessment/v1-draft/reference_mapping.py'
    spec=importlib.util.spec_from_file_location('message_url_mapping',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module
