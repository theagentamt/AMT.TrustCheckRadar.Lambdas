import json
import sys
import unittest
from pathlib import Path
from unittest import mock

SRC_DIR = Path(__file__).resolve().parents[2] / "src"
MODULE_DIR = SRC_DIR / "campaign_cluster_aggregator"
for path in (SRC_DIR, MODULE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))
for name in ("service", "scoring"): sys.modules.pop(name, None)
import scoring  # noqa: E402
import service  # noqa: E402
from shared_campaign_locators import locator_for_target,serialize as locator_wire
INVENTORY={'PK':'INVENTORY#dev','SK':'CAMPAIGN_LOCATORS','recordType':'CAMPAIGN_LOCATOR_INVENTORY','schemaVersion':1,
'revision':1,'environment':'dev','coverage':'VERIFIED_COMPLETE','manifestSha256':'a'*64,'approvedAtEpoch':1,
'locatorSchemaVersion':1,'minimumPeriodId':0,'priorPeriodsErased':True,'writers':['publisher','cluster','deletion_bridge','lifecycle']}


EVENT_ID = "7fbce2ac-bd2e-4d2e-9ec6-1f895a482abc"


def feature(**updates):
    value = {
        "PK": f"EVENT#{EVENT_ID}",
        "SK": "FEATURE",
        "environment": "dev",
        "schemaVersion": 1,
        "recordVersion": 1,
        "statisticsEventId": EVENT_ID,
        "researchNoticeVersion": "research-consent-2026-09-21-v2",
        "researchPolicyVersion": "independent-research-v1",
        "periodId": 1471,
        "contributorToken": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "GSI1PK": "CONTRIB#1471#aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "GSI1SK": f"EVENT#{EVENT_ID}#FEATURE",
        "GSI3PK": "EXPIRY#dev",
        "GSI3SK": 2_000_000_000,
        "extractorVersion": "android-1.0.0",
        "languageId": "en",
        "taxonomyBucket": "advance_fee",
        "vector": [1.0, 0.0],
        "lexicalFingerprint": ["0123456789abcdef", "fedcba9876543210"],
        "signalIds": ["payment_request"],
        "indicatorIds": ["payment.crypto"],
        "confidence": 0.9,
        "expiresAt": 2_000_000_000,
    }
    value.update(updates)
    return value


def candidate():
    return {"PK": "CANDIDATE#11111111-1111-4111-8111-111111111111", "SK": "SUMMARY", "candidateId": "11111111-1111-4111-8111-111111111111", "periodId": 1471,
            "taxonomyBucket": "advance_fee", "metadataSchemaVersion":1, "researchNoticeVersion":"research-consent-2026-09-21-v2", "researchPolicyVersion":"independent-research-v1", "centroid": [1.0, 0.0],
            "lexicalFingerprint": ["0123456789abcdef", "fedcba9876543210"],
            "signalIds": ["payment_request"], "indicatorIds": [],
            "contributorCount": 9, "submissionCount": 9, "version": 2, "expiresAt": 2_000_000_000}


class Dynamo:
    def __init__(self, feature_item=None, candidates=None, contribution=None, dedupe=None):
        self.feature_item = service.serialize(feature_item) if feature_item else None
        self.candidates = candidates or []
        self.contribution = (contribution | service.serialize({"metadataSchemaVersion":1,"lexicalFingerprint":["0123456789abcdef"],"signalIds":["payment_request"],"indicatorIds":["payment.crypto"]})) if contribution else None
        self.dedupe = dedupe
        self.transactions = []
        self.puts = []
        self.queries = []

    def get_item(self, Key, **_kwargs):
        pk, sk = Key["PK"]["S"], Key["SK"]["S"]
        if sk == 'OBSERVATION_READY': item = service.serialize({'PK':pk,'SK':sk,'eventType':'campaign.observation.ready','statisticsEventId':EVENT_ID,'accountId':'a','environment':'dev','consentEpochId':'15c81ba4-2fa6-43c3-8895-889f08c931bf','campaignConsentGranted':True,'noticeVersion':'research-consent-2026-09-21-v2','expiresAt':2000000000})
        elif sk == 'CAMPAIGN_PARTICIPATION': item = service.serialize({'state':'enrolled','consentEpochId':'15c81ba4-2fa6-43c3-8895-889f08c931bf','environment':'dev','noticeVersion':'research-consent-2026-09-21-v2','policyVersion':'independent-research-v1'})
        elif sk == 'ACCOUNT_DELETION': item = None
        elif sk == 'CAMPAIGN_LOCATORS': item = locator_wire(INVENTORY)
        elif sk.startswith('LOCATOR#EVENT#'): item = locator_wire(locator_for_target(service.deserialize(self.feature_item),'dev'))
        elif sk.startswith('LOCATOR#CANDIDATE#'):
            target = service.deserialize(self.contribution) | {'PK':sk.removeprefix('LOCATOR#'),'SK':'CONTRIB#'+'a'*43,'GSI1PK':'CONTRIB#1471#'+'a'*43,'periodId':1471}
            item = locator_wire(locator_for_target(target,'dev'))
        elif sk == "FEATURE": item = self.feature_item
        elif sk == "CLUSTERED": item = self.dedupe
        elif sk.startswith("CONTRIB#"):
            item = (self.contribution | service.serialize({'PK':pk,'SK':sk,'GSI1PK':'CONTRIB#1471#'+'a'*43,'periodId':1471,'researchNoticeVersion':'research-consent-2026-09-21-v2','researchPolicyVersion':'independent-research-v1'})) if self.contribution else None
        else:
            found = next((c for c in self.candidates if c["PK"] == pk and c["SK"] == sk), None)
            item = service.serialize(found) if found else None
        return {"Item": item} if item else {}

    def query(self, **_kwargs):
        self.queries.append(_kwargs)
        return {"Items": [{"PK": {"S": c["PK"]}, "SK": {"S": c["SK"]}} for c in self.candidates]}

    def transact_write_items(self, TransactItems): self.transactions.append(TransactItems)
    def put_item(self, **kwargs): self.puts.append(kwargs)


def body(**updates):
    value = {"schemaVersion": 1, "eventType": "campaign.cluster.requested", "environment": "dev",
             "statisticsEventId": EVENT_ID, "recordVersion": 1}
    value.update(updates)
    return json.dumps(value)


class ScoringTests(unittest.TestCase):
    def test_applies_approved_weights(self):
        self.assertEqual(scoring.similarity(feature(), candidate()), 0.9)

    def test_category_conflict_cannot_match(self):
        self.assertEqual(scoring.similarity(feature(), candidate() | {"taxonomyBucket": "impersonation"}), 0.0)

    def test_indicator_collision_alone_cannot_merge_campaigns(self):
        unrelated = feature(
            vector=[0.0, 1.0],
            lexicalFingerprint=["aaaaaaaaaaaaaaaa"],
            signalIds=["credential_request"],
            indicatorIds=["payment.crypto"],
        )
        self.assertEqual(scoring.similarity(unrelated, candidate() | {"indicatorIds": ["payment.crypto"]}), 0.1)

    def test_language_does_not_force_a_false_split_when_approved_features_match(self):
        self.assertEqual(scoring.similarity(feature(languageId="es"), candidate()), 0.9)

    def test_centroid_uses_one_vector_per_new_contributor(self):
        self.assertEqual(scoring.updated_centroid([1.0, 0.0], 1, [0.0, 1.0]), [0.5, 0.5])


class ClusterServiceTests(unittest.TestCase):
    def process(self, dynamo, message=None):
        return service.process_message(message or body(), environment="dev", schema_version=1,
            table_name="pipeline", retention_days=21, max_submissions=3, dynamodb=dynamo,
            users_table_name="users",deletion_ledger_table_name="ledger",outbox_table_name="outbox",
            now_epoch=1_780_000_100,locator_manifest_sha256="a"*64,locator_inventory_revision=1)

    def test_matches_high_score_and_writes_one_contribution(self):
        dynamo = Dynamo(feature(), [candidate()])

        self.assertEqual(self.process(dynamo), "matched")

        transaction = dynamo.transactions[0]
        updated = service.deserialize(transaction[0]["Put"]["Item"])
        self.assertEqual(updated["candidateId"], "11111111-1111-4111-8111-111111111111")
        self.assertEqual(updated["contributorCount"], 10)
        self.assertEqual(updated["submissionCount"], 10)
        self.assertEqual(updated["centroid"], [1.0, 0.0])
        self.assertEqual(len(transaction), 10)

        contribution = service.deserialize(transaction[2]["Put"]["Item"])
        self.assertEqual(contribution["languageId"], "en")
        self.assertEqual(contribution["signalIds"], ["payment_request"])
        self.assertEqual(contribution["indicatorIds"], ["payment.crypto"])
        self.assertEqual(contribution["GSI3PK"], "EXPIRY#dev")
        self.assertEqual(contribution["GSI3SK"], contribution["expiresAt"])

    @mock.patch.object(service.uuid, "uuid4", return_value="22222222-2222-4222-8222-222222222222")
    def test_conflict_creates_separate_candidate(self, _uuid):
        dynamo = Dynamo(feature(taxonomyBucket="impersonation"), [candidate()])

        self.assertEqual(self.process(dynamo), "candidate-created")
        transaction = dynamo.transactions[0]
        created = service.deserialize(transaction[0]["Put"]["Item"])
        self.assertEqual(created["candidateId"], "22222222-2222-4222-8222-222222222222")
        self.assertEqual(len(transaction), 11)
        creation_control = transaction[2]["Put"]
        self.assertEqual(creation_control["Item"]["SK"], {"S": "CREATION_CONTROL"})
        self.assertIn("attribute_not_exists", creation_control["ConditionExpression"])

    def test_existing_creation_control_uses_optimistic_serialization(self):
        class ControlledDynamo(Dynamo):
            def get_item(self, Key, **kwargs):
                if Key["SK"]["S"] == "CREATION_CONTROL":
                    return {"Item": {"version": {"N": "4"}, "expiresAt": {"N": "1780500000"}}}
                return super().get_item(Key, **kwargs)

        dynamo = ControlledDynamo(feature())
        self.assertEqual(self.process(dynamo), "candidate-created")
        control = dynamo.transactions[0][2]["Update"]
        self.assertEqual(control["ExpressionAttributeValues"][":version"], {"N": "4"})
        self.assertEqual(control["ExpressionAttributeValues"][":next_version"], {"N": "5"})
        self.assertEqual(control["ExpressionAttributeValues"][":expiry"], {"N": "1780500000"})

    def test_repeat_contribution_keeps_original_expiration_deadline(self):
        dynamo = Dynamo(feature(), [candidate()], contribution={
            "submissionCount": {"N": "2"}, "expiresAt": {"N": "1780500000"},
        })

        self.assertEqual(self.process(dynamo), "counted-repeat")

        update = dynamo.transactions[0][1]["Update"]
        self.assertEqual(update["ExpressionAttributeValues"][":expiry"], {"N": "1780500000"})
        self.assertEqual(update["ExpressionAttributeValues"][":expiry_partition"], {"S": "EXPIRY#dev"})

    def test_caps_fourth_submission_without_second_vector(self):
        dynamo = Dynamo(feature(), [candidate()], contribution={
            "submissionCount": {"N": "3"}, "expiresAt": {"N": "1900000000"},
        })

        self.assertEqual(self.process(dynamo), "contributor-capped")
        self.assertEqual(len(dynamo.transactions), 1)
        self.assertIn("ConditionCheck", dynamo.transactions[0][0])
        self.assertEqual(dynamo.puts, [])

    def test_all_write_paths_include_atomic_tombstone_absence(self):
        for existing in (None, {"submissionCount":{"N":"2"},"expiresAt":{"N":"1900000000"}},
                         {"submissionCount":{"N":"3"},"expiresAt":{"N":"1900000000"}}):
            dynamo = Dynamo(feature(), [candidate()], contribution=existing)
            self.process(dynamo)
            self.assertTrue(dynamo.transactions)
            for transaction in dynamo.transactions:
                conditions = [action["ConditionCheck"] for action in transaction if "ConditionCheck" in action and action["ConditionCheck"]["Key"]["SK"] == {"S":"TOMBSTONE"}]
                self.assertEqual(len(conditions),1)
                self.assertEqual(conditions[0]["Key"]["SK"], {"S":"TOMBSTONE"})
                self.assertIn("attribute_not_exists",conditions[0]["ConditionExpression"])
            self.assertFalse(dynamo.puts)

    def test_missing_suppressed_and_duplicate_are_noops(self):
        self.assertEqual(self.process(Dynamo()), "missing")
        self.assertEqual(self.process(Dynamo(feature(suppressed=True))), "suppressed")
        self.assertEqual(self.process(Dynamo(feature(expiresAt=1_780_000_000, GSI3SK=1_780_000_000))), "suppressed")
        self.assertEqual(self.process(Dynamo(feature(), dedupe={"PK": {"S": "x"}})), "duplicate")

    def test_contributor_tombstone_wins_over_queued_work(self):
        class TombstonedDynamo(Dynamo):
            def get_item(self, Key, **kwargs):
                if Key["SK"]["S"] == "TOMBSTONE":
                    return {"Item": {"expiresAt": {"N": "2000000000"}}}
                return super().get_item(Key, **kwargs)

        dynamo = TombstonedDynamo(feature(), [candidate()])
        self.assertEqual(self.process(dynamo), "suppressed")
        self.assertEqual(dynamo.transactions, [])

    def test_rejects_cross_environment_and_extra_fields(self):
        for message in (body(environment="prod"), body(extra="bad")):
            with self.subTest(message=message), self.assertRaises(ValueError):
                self.process(Dynamo(feature()), message)

    def test_rejects_malformed_persisted_features_before_scoring(self):
        cases = [
            feature(environment="prod"),
            feature(schemaVersion=2),
            feature(recordVersion=2),
            feature(vector=[float("nan")]),
            feature(vector=[True]),
            feature(confidence=1.1),
            feature(signalIds=["payment_request", "payment_request"]),
            feature(GSI3PK="EXPIRY#prod"),
            feature(GSI3SK=1_999_999_999),
            feature(unknown="field"),
            {key: value for key, value in feature().items() if key != "extractorVersion"},
        ]
        for persisted in cases:
            with self.subTest():
                dynamo = Dynamo(persisted, [candidate()])
                with self.assertRaises(ValueError):
                    self.process(dynamo)
                self.assertEqual(dynamo.queries, [])
                self.assertEqual(dynamo.transactions, [])


if __name__ == "__main__": unittest.main()
