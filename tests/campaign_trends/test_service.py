import sys
import unittest
from pathlib import Path

MODULE_DIR = Path(__file__).resolve().parents[2] / "src" / "campaign_trends"
sys.path.insert(0, str(MODULE_DIR))
for name in ("service", "taxonomy"): sys.modules.pop(name, None)
import service  # noqa: E402


def av(v):
    if isinstance(v, str): return {"S": v}
    if isinstance(v, list): return {"L": [av(x) for x in v]}
    raise TypeError(type(v).__name__)


def item(**updates):
    value = {"campaignId": "c1", "categoryId": "advance_fee", "periodWeek": "2026-W35",
             "riskBand": "high", "contributorCountBand": "10-24", "submissionCountBand": "25-49",
             "languageIds": ["en", "es"], "summaryKey": "campaign.advance_fee", "trendDirection": "new"}
    value.update(updates)
    return {k: av(v) for k, v in value.items()}


class Dynamo:
    def __init__(self, items=None, last_key=None): self.items, self.last_key, self.calls = items or [], last_key, []
    def query(self, **kwargs):
        self.calls.append(kwargs)
        result = {"Items": self.items}
        if self.last_key: result["LastEvaluatedKey"] = self.last_key
        return result


def event(query=None, authenticated=True):
    return {"requestContext": {"authorizer": {"jwt": {"claims": {"sub": "user-1"} if authenticated else {}}}},
            "queryStringParameters": query}


class TrendsTests(unittest.TestCase):
    def call(self, submitted, dynamo, now=1_000):
        return service.list_trends(submitted, environment="dev", table_name="intelligence",
            index_name="PublicationIndex", maximum_page_size=50, token_ttl=900,
            dynamodb=dynamo, now_epoch=now)

    def test_returns_only_banded_fields_with_spanish_label(self):
        dynamo = Dynamo([item()])

        result = self.call(event({"locale": "es"}), dynamo)

        self.assertEqual(result["trends"][0]["categoryLabel"], "Estafa de pago por adelantado")
        serialized = str(result)
        self.assertNotIn("contributorCount'", serialized)
        self.assertNotIn("submissionCount'", serialized)
        self.assertNotIn("centroid", serialized)
        self.assertEqual(dynamo.calls[0]["ExpressionAttributeValues"][":published"], {"S": "STATE#PUBLISHED"})

    def test_suppresses_missing_or_invalid_count_bands(self):
        result = self.call(event(), Dynamo([item(contributorCountBand="0-9"), item(submissionCountBand="exact:12")]))
        self.assertEqual(result["trends"], [])

    def test_filters_category_risk_language_and_week_in_query(self):
        query = {"categoryId": "advance_fee", "riskBand": "high", "languageId": "es",
                 "fromWeek": "2026-W30", "toWeek": "2026-W40", "limit": "10"}
        dynamo = Dynamo([item(), item(categoryId="impersonation")])

        result = self.call(event(query), dynamo)

        self.assertEqual([entry["campaignId"] for entry in result["trends"]], ["c1"])
        self.assertIn("BETWEEN", dynamo.calls[0]["KeyConditionExpression"])
        self.assertEqual(dynamo.calls[0]["Limit"], 10)

    def test_pagination_token_is_environment_bound_and_expires(self):
        key = {"PK": {"S": "CAMPAIGN#c1"}, "SK": {"S": "AGGREGATE"},
               "GSI1PK": {"S": "STATE#PUBLISHED"}, "GSI1SK": {"S": "2026-W35#CAMPAIGN#c1"}}
        token = service.encode_token(key, environment="dev", ttl=60, now_epoch=1_000)
        self.assertEqual(service.decode_token(token, environment="dev", now_epoch=1_001), key)
        for environment, now in (("prod", 1_001), ("dev", 1_061)):
            with self.subTest(environment=environment, now=now), self.assertRaises(service.TrendsError):
                service.decode_token(token, environment=environment, now_epoch=now)

    def test_next_page_uses_validated_exclusive_start_key(self):
        key = {"PK": {"S": "CAMPAIGN#c1"}, "SK": {"S": "AGGREGATE"},
               "GSI1PK": {"S": "STATE#PUBLISHED"}, "GSI1SK": {"S": "2026-W35#CAMPAIGN#c1"}}
        first = self.call(event(), Dynamo([item()], last_key=key))
        second_dynamo = Dynamo([])
        self.call(event({"nextToken": first["nextToken"]}), second_dynamo, now=1_001)
        self.assertEqual(second_dynamo.calls[0]["ExclusiveStartKey"], key)

    def test_rejects_token_that_targets_nonpublished_data(self):
        key = {"PK": {"S": "CAMPAIGN#c1"}, "SK": {"S": "AGGREGATE"},
               "GSI1PK": {"S": "STATE#PENDING_REVIEW"}, "GSI1SK": {"S": "x"}}
        token = service.encode_token(key, environment="dev", ttl=60, now_epoch=1_000)
        with self.assertRaises(service.TrendsError):
            service.decode_token(token, environment="dev", now_epoch=1_001)

    def test_requires_authentication_and_rejects_unknown_filters(self):
        for submitted in (event(authenticated=False), event({"identity": "bad"})):
            with self.subTest(submitted=submitted), self.assertRaises(service.TrendsError):
                self.call(submitted, Dynamo())


if __name__ == "__main__": unittest.main()
