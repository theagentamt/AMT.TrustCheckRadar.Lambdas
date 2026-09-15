import importlib.util
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "src"
    / "web_risk_communication"
    / "cache.py"
)


class _FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item):
        self.items[(Item["PK"], Item["SK"])] = dict(Item)
        return {}


class _FakeResource:
    def __init__(self, table):
        self.table = table

    def Table(self, _name):
        return self.table


def _load_cache():
    table = _FakeTable()
    boto3_stub = types.ModuleType("boto3")
    boto3_stub.resource = lambda *_args, **_kwargs: _FakeResource(table)
    config_stub = types.ModuleType("config")
    config_stub.TABLE_NAME = "web-risk-cache"
    module_name = "web_risk_cache_under_test"
    spec = importlib.util.spec_from_file_location(module_name, MODULE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    with mock.patch.dict(sys.modules, {"boto3": boto3_stub, "config": config_stub}):
        spec.loader.exec_module(module)
    return module, table


class WebRiskCachePrivacyTests(unittest.TestCase):
    def test_new_cache_item_does_not_persist_lookup_uri(self):
        cache, table = _load_cache()
        normalized_url = "https://example.test/path?private=value"
        cache.put_cache_item(
            cache_scope="FULL_URL",
            cache_hash=cache.hash_value(normalized_url),
            lookup_value=normalized_url,
            threats=[{"threatType": "MALWARE", "confidenceLevel": "HIGH"}],
            expires_at=2_000_000_000,
            highest_confidence_fn=lambda _threats: "HIGH",
        )

        item = next(iter(table.items.values()))
        self.assertNotIn("uri", item)
        self.assertNotIn(normalized_url, repr(item))

    def test_legacy_uri_is_not_returned_from_cache(self):
        cache, _table = _load_cache()
        legacy = {
            "uri": "https://example.test/path?private=value",
            "threats": [{"threatType": "MALWARE", "confidenceLevel": "HIGH"}],
        }

        result = cache.deserialize_cache(legacy)

        self.assertIsNone(result["uri"])
        self.assertEqual(result["threats"], legacy["threats"])


if __name__ == "__main__":
    unittest.main()
