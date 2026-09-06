import json
import sys
import unittest
from pathlib import Path
from unittest import mock

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "campaign_review"
sys.path.insert(0, str(MODULE_DIR))
sys.modules.pop("service", None)
import service  # noqa: E402


class Dynamo:
    def __init__(self, state="PENDING_REVIEW", contributors=10):
        self.item = {"PK": {"S": "CAMPAIGN#c1"}, "SK": {"S": "AGGREGATE"},
            "state": {"S": state}, "contributorCount": {"N": str(contributors)},
            "version": {"N": "3"}, "periodWeek": {"S": "2026-W35"},
            "expiresAt": {"N": "2000000000"}}
        self.transactions = []
    def get_item(self, **_kwargs): return {"Item": self.item} if self.item else {}
    def transact_write_items(self, TransactItems): self.transactions.append(TransactItems)


def event(action="confirm", reason_code="quality_verified", reason="Reviewed against the approved fixture set.", group=True):
    return {"pathParameters": {"campaignId": "c1"},
        "requestContext": {"authorizer": {"jwt": {"claims": {
            "cognito:groups": ["campaign-reviewer"] if group else ["users"]}}}},
        "body": json.dumps({"schemaVersion": 1, "action": action,
                            "reasonCode": reason_code, "reason": reason})}


class ReviewTests(unittest.TestCase):
    @mock.patch.object(service.uuid, "uuid4", return_value="audit-1")
    def test_authorized_confirmation_is_atomic_and_privacy_safe(self, _uuid):
        dynamo = Dynamo()

        result = service.review(event(), table_name="intelligence", reviewer_group="campaign-reviewer",
                                minimum_contributors=10, dynamodb=dynamo)

        self.assertEqual(result["state"], "CONFIRMED")
        transaction = dynamo.transactions[0]
        self.assertEqual(len(transaction), 2)
        audit = transaction[1]["Put"]["Item"]
        self.assertEqual(audit["reasonCode"], {"S": "quality_verified"})
        self.assertNotIn("Reviewed against", str(audit))
        self.assertNotIn("sub", str(audit).lower())

    def test_publish_adds_sparse_publication_keys(self):
        dynamo = Dynamo(state="CONFIRMED")

        result = service.review(event(action="publish", reason_code="privacy_verified"),
            table_name="intelligence", reviewer_group="campaign-reviewer", minimum_contributors=10, dynamodb=dynamo)

        self.assertEqual(result["state"], "PUBLISHED")
        values = dynamo.transactions[0][0]["Update"]["ExpressionAttributeValues"]
        self.assertEqual(values[":published"], {"S": "STATE#PUBLISHED"})

    def test_emergency_suppression_removes_publication_keys(self):
        dynamo = Dynamo(state="PUBLISHED")

        result = service.review(event(action="emergency_suppress", reason_code="emergency"),
            table_name="intelligence", reviewer_group="campaign-reviewer", minimum_contributors=10, dynamodb=dynamo)

        self.assertEqual(result["state"], "SUPPRESSED")
        self.assertIn("REMOVE GSI1PK, GSI1SK", dynamo.transactions[0][0]["Update"]["UpdateExpression"])

    def test_rejects_unauthorized_low_cohort_and_invalid_transition(self):
        cases = [(event(group=False), Dynamo(), "FORBIDDEN"),
                 (event(), Dynamo(contributors=9), "PRIVACY_THRESHOLD"),
                 (event(action="publish"), Dynamo(), "INVALID_TRANSITION")]
        for submitted, dynamo, code in cases:
            with self.subTest(code=code), self.assertRaises(service.ReviewError) as caught:
                service.review(submitted, table_name="intelligence", reviewer_group="campaign-reviewer",
                               minimum_contributors=10, dynamodb=dynamo)
            self.assertEqual(caught.exception.code, code)
            self.assertEqual(dynamo.transactions, [])

    def test_merge_split_are_fail_closed_until_overlap_safe_contract_exists(self):
        for action in ("merge", "split"):
            with self.subTest(action=action), self.assertRaises(service.ReviewError) as caught:
                service.review(event(action=action), table_name="intelligence", reviewer_group="campaign-reviewer",
                               minimum_contributors=10, dynamodb=Dynamo())
            self.assertEqual(caught.exception.code, "ACTION_REQUIRES_SOURCE_LEDGER")

    def test_rejects_reason_containing_direct_identifier(self):
        with self.assertRaises(service.ReviewError):
            service.review(event(reason="Contact person@example.com about it"), table_name="intelligence",
                           reviewer_group="campaign-reviewer", minimum_contributors=10, dynamodb=Dynamo())


if __name__ == "__main__": unittest.main()
