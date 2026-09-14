import json
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator


ROOT = Path(__file__).resolve().parents[2]


class HistoryContractArtifactTests(unittest.TestCase):
    def test_contract_has_exact_limits_routes_badges_and_locales(self):
        contract = json.loads((ROOT / "contracts/history/v1/contract-set.json").read_text())
        self.assertEqual(contract["contractVersion"], "1.0.0")
        schemas = json.loads((ROOT / "contracts/history/v1/api-schemas.json").read_text())
        Draft202012Validator.check_schema(schemas)
        self.assertEqual(
            set(schemas["$defs"]),
            {
                "bootstrapRequest", "mutationRequest", "assessment", "boundedTextList",
                "requestId", "historyItem", "historyListResponse", "historyExportResponse",
                "historyDetailResponse", "badge", "progressResponse", "bootstrapResponse",
                "mutationResponse", "errorResponse",
            },
        )
        self.assertEqual(contract["limits"], {
            "historyRetentionDays": 90, "physicalPurgeSlaHours": 24,
            "pitrDays": 7, "deduplicationAndTombstoneDays": 120,
            "mutationReceiptDays": 7, "defaultPageSize": 20,
            "maximumPageSize": 50, "maximumResponseUtf8Bytes": 262144,
            "cursorTtlSeconds": 900, "maximumSummaryUtf8Bytes": 4096,
            "maximumListItems": 20, "maximumSignalOrActionUtf8Bytes": 1024,
        })
        self.assertEqual([item["id"] for item in contract["badges"]], ["checks_1", "checks_5", "checks_20"])
        routes = {(item["method"], item["path"]) for item in contract["routes"]}
        self.assertIn(("POST", "/v1/users/history/bootstrap"), routes)
        self.assertIn(("GET", "/v1/users/history/export"), routes)
        history_only = next(item for item in contract["routes"] if item["path"] == "/v1/users/history/account")
        self.assertEqual(history_only["semantics"], "history_data_only")
        required_keys = {
            f"badges.checks_{count}.{field}"
            for count in (1, 5, 20) for field in ("title", "description")
        }
        for locale in ("en", "es"):
            translations = json.loads(
                (ROOT / f"contracts/history/v1/badges.{locale}.json").read_text()
            )["translations"]
            self.assertEqual(set(translations), required_keys)


if __name__ == "__main__":
    unittest.main()
