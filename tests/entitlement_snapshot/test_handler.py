import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]

class RetiredEndpointTests(unittest.TestCase):
    def test_fixed_handlers_do_not_import_sdk_or_echo_input(self):
        for package, status, code in (("entitlement_snapshot",409,"LEGACY_MIGRATION_REQUIRED"),
                                      ("web_risk_communication",410,"LEGACY_ENDPOINT_RETIRED")):
            with self.subTest(package=package):
                spec=importlib.util.spec_from_file_location("retired_"+package,ROOT/"src"/package/"app.py")
                module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
                self.assertEqual(set(k for k in vars(module) if not k.startswith("__")), {"json","lambda_handler"})
                response=module.lambda_handler({"url":"private-marker","accountId":"private-account"},None)
                self.assertEqual(response["statusCode"],status)
                self.assertEqual(json.loads(response["body"])["error"]["code"],code)
                self.assertNotIn("private-",response["body"])
                self.assertEqual(response["headers"]["Cache-Control"],"no-store")

if __name__ == "__main__": unittest.main()
