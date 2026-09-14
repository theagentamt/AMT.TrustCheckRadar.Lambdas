import sys
import unittest
from pathlib import Path

SRC = Path(__file__).resolve().parents[2] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from history_read_api.service import CursorStore, HistoryReadService
from shared_history.errors import HistoryError


class Table:
    def __init__(self, items=None, query_items=None):
        self.items = items or {}
        self.query_items = query_items or []
        self.puts = []

    def put_item(self, Item, **_kwargs):
        self.puts.append(Item)
        self.items[(Item["PK"], Item["SK"])] = Item
        return {}

    def get_item(self, Key, **_kwargs):
        item = self.items.get((Key["PK"], Key["SK"]))
        return {"Item": item} if item else {}

    def query(self, **_kwargs):
        return {"Items": list(self.query_items)}


class Settings:
    schema_version = 1
    default_page_size = 20
    max_page_size = 50
    max_response_bytes = 100_000
    max_summary_bytes = 2048
    max_list_items = 20
    max_text_field_bytes = 512
    badge_catalog = ({"id": "one", "threshold": 1}, {"id": "five", "threshold": 5}, {"id": "twenty", "threshold": 20})


class HistoryReadTests(unittest.TestCase):
    def test_cursor_is_random_handle_and_tenant_bound(self):
        table = Table()
        cursors = CursorStore(table=table, secret=b"x" * 32, ttl_seconds=300, now=lambda: 100)
        handle = cursors.create("account-1", 2, "COMPLETE#0000000000100#request-1")
        self.assertNotIn("account-1", handle)
        self.assertNotIn("COMPLETE", handle)
        stored = table.puts[0]
        self.assertNotIn("account-1", repr(stored))
        self.assertEqual(cursors.load(handle, "account-1", 2), "COMPLETE#0000000000100#request-1")
        with self.assertRaises(HistoryError):
            cursors.load(handle, "account-2", 2)

    def test_detail_is_scoped_to_current_account_generation(self):
        control = Table({
            ("USER#account-1", "STATE"): {"PK": "USER#account-1", "SK": "STATE", "accountStatus": "ACTIVE", "historyGeneration": 1, "recognitionGeneration": 0},
            ("USER#account-1", "REQUEST#request-1"): {"status": "ACTIVE", "historyGeneration": 1, "contentSortKey": "COMPLETE#0000000100000#request-1", "contentExpiresAt": 999},
        })
        content = Table({
            ("USER#account-1#HISTORY#1", "COMPLETE#0000000100000#request-1"): {
                "PK": "USER#account-1#HISTORY#1", "SK": "COMPLETE#0000000100000#request-1",
                "recordType": "HISTORY", "schemaVersion": 1, "recordVersion": 1,
                "requestId": "request-1", "historyGeneration": 1, "recognitionGeneration": 0,
                "acceptedSequence": 1, "sourceType": "ocr", "acceptedAtEpochMs": 90,
                "completedAtEpochMs": 100, "expiresAt": 999, "expiryBucket": "HISTORY#bucket#00",
                "assessment": {"schemaVersion": "1.0", "scamScore": 50, "riskLevel": "medium", "confidence": 0.7, "summary": "Review.", "signals": [], "recommendedActions": ["Verify."]},
            }
        })
        service = HistoryReadService(settings=Settings(), content_table=content, control_table=control, cursor_store=None, now=lambda: 200)
        self.assertEqual(service.get_history("account-1", "request-1")["item"]["requestId"], "request-1")
        with self.assertRaises(HistoryError) as raised:
            service.get_history("account-2", "request-1")
        self.assertEqual(raised.exception.code, "SERVER_UNAVAILABLE")


if __name__ == "__main__":
    unittest.main()
