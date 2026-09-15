import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest import mock


MODULE = Path(__file__).resolve().parents[2] / "src" / "history_account_deletion_bridge"
sys.path.insert(0, str(MODULE))

boto3_stub = types.ModuleType("boto3")
boto3_stub.resource = lambda *_args, **_kwargs: object()
boto3_stub.client = lambda *_args, **_kwargs: object()
sys.modules.setdefault("boto3", boto3_stub)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


_load("config", MODULE / "config.py")
_load("service", MODULE / "service.py")
app = _load("history_deletion_bridge_app_test", MODULE / "app.py")


class ReconciliationMetricTests(unittest.TestCase):
    def test_success_metric_includes_full_pass_heartbeat_and_age(self):
        result = {
            "scanned": 10, "matched": 2, "started": 1,
            "alreadyPending": 1, "completed": 0,
            "worksetTruncated": False, "completedFullPass": True,
            "fullPassAgeSeconds": 0, "completedAtEpoch": 200,
        }
        with mock.patch.object(app.LOGGER, "info") as info:
            app._reconciliation_metric(result)

        payload = json.loads(info.call_args.args[0])
        self.assertEqual(payload["AccountDeletionReconciliationSuccess"], 1)
        self.assertEqual(payload["AccountDeletionReconciliationFullPassCompleted"], 1)
        self.assertEqual(payload["AccountDeletionReconciliationFullPassAgeSeconds"], 0)
        self.assertEqual(
            payload["_aws"]["CloudWatchMetrics"][0]["Namespace"],
            "AMT/TrustCheckRadar/History",
        )

    def test_truncated_without_completed_pass_omits_unknown_age(self):
        result = {
            "scanned": 100, "matched": 0, "started": 0,
            "alreadyPending": 0, "completed": 0,
            "worksetTruncated": True, "completedFullPass": False,
            "fullPassAgeSeconds": None, "completedAtEpoch": 200,
        }
        with mock.patch.object(app.LOGGER, "info") as info:
            app._reconciliation_metric(result)

        payload = json.loads(info.call_args.args[0])
        self.assertEqual(payload["AccountDeletionReconciliationWorksetTruncated"], 1)
        self.assertNotIn("AccountDeletionReconciliationFullPassAgeSeconds", payload)


if __name__ == "__main__":
    unittest.main()
