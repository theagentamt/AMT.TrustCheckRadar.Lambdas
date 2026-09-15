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


class PageTable(Table):
    def __init__(self, pages):
        super().__init__()
        self.pages = list(pages)
        self.queries = []

    def query(self, **kwargs):
        self.queries.append(kwargs)
        return self.pages.pop(0)


class Settings:
    schema_version = 1
    default_page_size = 20
    max_page_size = 50
    max_response_bytes = 100_000
    max_summary_bytes = 2048
    max_list_items = 20
    max_text_field_bytes = 512
    badge_catalog = (
        {"id": "checks_1", "threshold": 1, "titleKey": "badges.checks_1.title", "descriptionKey": "badges.checks_1.description"},
        {"id": "checks_5", "threshold": 5, "titleKey": "badges.checks_5.title", "descriptionKey": "badges.checks_5.description"},
        {"id": "checks_20", "threshold": 20, "titleKey": "badges.checks_20.title", "descriptionKey": "badges.checks_20.description"},
    )


def _history_item(request_id, completed_at):
    return {
        "PK": "USER#a#HISTORY#0",
        "SK": f"COMPLETE#{completed_at:013d}#{request_id}",
        "recordType": "HISTORY", "schemaVersion": 1, "recordVersion": 1,
        "requestId": request_id, "historyGeneration": 0, "recognitionGeneration": 0,
        "acceptedSequence": 1, "sourceType": "ocr", "acceptedAtEpochMs": 90,
        "completedAtEpochMs": completed_at, "expiresAt": 999,
        "expiryBucket": "HISTORY#bucket#00",
        "assessment": {
            "schemaVersion": "1.0", "scamScore": 50, "riskLevel": "medium",
            "confidence": 0.7, "summary": "Review.", "signals": [],
            "recommendedActions": ["Verify."],
        },
    }


class HistoryReadTests(unittest.TestCase):
    def test_exact_limit_page_with_dynamodb_continuation_returns_cursor(self):
        state = {"accountStatus": "ACTIVE", "historyGeneration": 0, "recognitionGeneration": 0}
        items = [_history_item("request-1", 200), _history_item("request-2", 100)]
        content = PageTable([{
            "Items": items,
            "LastEvaluatedKey": {"PK": items[-1]["PK"], "SK": items[-1]["SK"]},
        }])
        cursors = CursorStore(table=Table(), secret=b"x" * 32, ttl_seconds=900, now=lambda: 100)
        service = HistoryReadService(
            settings=Settings(), content_table=content,
            control_table=Table({("USER#a", "STATE"): state}),
            cursor_store=cursors, now=lambda: 100,
        )
        result = service.list_history("a", requested_limit="2")
        self.assertEqual([item["requestId"] for item in result["items"]], ["request-1", "request-2"])
        self.assertIn("nextCursor", result)

    def test_expired_items_are_skipped_across_capped_pages_without_losing_cursor(self):
        state = {"accountStatus": "ACTIVE", "historyGeneration": 0, "recognitionGeneration": 0}
        expired = _history_item("expired", 300) | {"expiresAt": 99}
        active = _history_item("active", 200)
        content = PageTable([
            {"Items": [expired], "LastEvaluatedKey": {"PK": expired["PK"], "SK": expired["SK"]}},
            {"Items": [active], "LastEvaluatedKey": {"PK": active["PK"], "SK": active["SK"]}},
        ])
        cursors = CursorStore(table=Table(), secret=b"x" * 32, ttl_seconds=900, now=lambda: 100)
        service = HistoryReadService(
            settings=Settings(), content_table=content,
            control_table=Table({("USER#a", "STATE"): state}),
            cursor_store=cursors, now=lambda: 100,
        )
        result = service.list_history("a", requested_limit="1")
        self.assertEqual([item["requestId"] for item in result["items"]], ["active"])
        self.assertIn("nextCursor", result)

    def test_list_and_export_include_generations_contract_and_server_time(self):
        state = {
            "accountStatus": "ACTIVE", "historyGeneration": 2,
            "recognitionGeneration": 3,
        }
        control = Table({("USER#a", "STATE"): state})
        service = HistoryReadService(
            settings=Settings(), content_table=Table(query_items=[]), control_table=control,
            cursor_store=None, now=lambda: 123,
        )
        listed = service.list_history("a")
        exported = service.export_history("a")
        self.assertEqual(listed["contractVersion"], "1.0.0")
        self.assertEqual(listed["serverTimeEpoch"], 123)
        self.assertEqual(listed["historyGeneration"], 2)
        self.assertEqual(listed["recognitionGeneration"], 3)
        self.assertEqual(exported["exportFormat"], "application/vnd.amt.trustcheckradar.history.v1+json")

    def test_progress_uses_stable_badge_ids_and_localization_keys(self):
        control = Table({
            ("USER#a", "STATE"): {
                "accountStatus": "ACTIVE", "historyGeneration": 2,
                "recognitionGeneration": 3,
            },
            ("USER#a", "PROGRESS#3"): {
                "recognitionGeneration": 3, "qualifyingChecks": 5,
                "awardedBadgeIds": ["checks_1", "checks_5"],
            },
        })
        service = HistoryReadService(
            settings=Settings(), content_table=Table(), control_table=control,
            cursor_store=None, now=lambda: 123,
        )
        result = service.get_progress("a")
        self.assertEqual(result["awardedBadgeIds"], ["checks_1", "checks_5"])
        self.assertEqual(
            [badge["titleKey"] for badge in result["badges"]],
            ["badges.checks_1.title", "badges.checks_5.title", "badges.checks_20.title"],
        )
        self.assertEqual(result["historyGeneration"], 2)
        self.assertEqual(result["recognitionGeneration"], 3)

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
        with self.assertRaises(HistoryError):
            CursorStore(table=table, secret=b"x" * 32, ttl_seconds=300, now=lambda: 401).load(
                handle, "account-1", 2
            )

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
