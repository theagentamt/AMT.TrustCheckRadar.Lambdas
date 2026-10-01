import importlib.util
from hashlib import sha256
import json
from pathlib import Path
import sys
import unittest

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / "contracts" / "device-recovery" / "v1"
FIXTURES = CONTRACT / "fixtures"
SCHEMA_NAMES = (
    "self-recovery-request.schema.json",
    "self-recovery-success.schema.json",
    "error-response.schema.json",
    "receipt-record.schema.json",
)


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


SCHEMAS = {name: load(CONTRACT / name) for name in SCHEMA_NAMES}


class DeviceRecoveryContractTests(unittest.TestCase):
    def test_schemas_are_valid_draft_2020_12(self):
        for name, schema in SCHEMAS.items():
            with self.subTest(name=name):
                self.assertEqual(
                    schema["$schema"],
                    "https://json-schema.org/draft/2020-12/schema",
                )
                self.assertIn("/device-recovery/1.0.0/", schema["$id"])
                Draft202012Validator.check_schema(schema)

    def test_fixture_manifest_has_exact_schema_expectations(self):
        manifest = load(FIXTURES / "manifest.json")
        self.assertEqual(manifest["contractVersion"], "1.0.0")
        seen = set()
        for fixture in manifest["fixtures"]:
            with self.subTest(path=fixture["path"]):
                self.assertNotIn(fixture["path"], seen)
                seen.add(fixture["path"])
                value = load(FIXTURES / fixture["path"])
                validator = Draft202012Validator(
                    SCHEMAS[fixture["schema"]], format_checker=FormatChecker()
                )
                self.assertEqual(validator.is_valid(value), fixture["valid"])
        self.assertEqual(
            seen,
            {path.name for path in FIXTURES.glob("*.json")} - {"manifest.json"},
        )

    def test_replay_and_stale_fixtures_are_original_outcome_not_status(self):
        original = load(FIXTURES / "valid.success.json")
        replay = load(FIXTURES / "replay.success.json")
        stale = load(FIXTURES / "stale-replay.success.json")
        self.assertEqual(replay, original)
        self.assertEqual(stale, original)
        for value in (original, replay, stale):
            self.assertEqual(value["status"], "COMPLETE")
            for prohibited in (
                "currentBinding",
                "currentBindingFingerprint",
                "isCurrentBinding",
                "bindingVersion",
            ):
                self.assertNotIn(prohibited, value)

    def test_contract_preserves_registration_switching_and_keeps_status_draft_off(self):
        contract = load(CONTRACT / "contract-set.json")
        self.assertEqual(contract["activationStatus"], "source_candidate_disabled")
        self.assertTrue(contract["registrationDecision"]["automaticSwitchingPreserved"])
        self.assertFalse(contract["statusRoute"]["supported"])
        self.assertEqual(
            contract["testEvidence"]["actualDynamoDbIntegration"],
            "gated_not_available_locally",
        )
        self.assertFalse(contract["testEvidence"]["deployedAcceptance"])
        self.assertEqual(
            contract["replaySemantics"]["removedReceiptOperationIdReuse"],
            "owner_decision_pending",
        )
        proposal = load(CONTRACT / contract["statusRoute"]["proposal"])
        self.assertEqual(proposal["proposalStatus"], "unsupported_pending_owner_approval")
        self.assertFalse(proposal["writesAllowed"])
        self.assertFalse(proposal["otherFingerprintDisclosureAllowed"])

    def test_root_contract_hash_binds_complete_non_self_referential_manifest(self):
        contract = load(CONTRACT / "contract-set.json")
        manifest_path = CONTRACT / contract["manifest"]["path"]
        self.assertEqual(
            sha256(manifest_path.read_bytes()).hexdigest(),
            contract["manifest"]["sha256"],
        )
        manifest = load(manifest_path)
        entries = {item["path"]: item["sha256"] for item in manifest["files"]}
        expected = {
            path.relative_to(CONTRACT).as_posix()
            for path in CONTRACT.rglob("*")
            if path.is_file() and path.name not in {"contract-set.json", "contract-manifest.json"}
        }
        self.assertEqual(set(entries), expected)
        for relative_path, expected_digest in entries.items():
            with self.subTest(path=relative_path):
                self.assertEqual(
                    sha256((CONTRACT / relative_path).read_bytes()).hexdigest(),
                    expected_digest,
                )

    def test_runtime_request_validation_matches_valid_and_invalid_fixtures(self):
        module_dir = ROOT / "src" / "device_recovery"
        previous = {name: sys.modules.get(name) for name in ("config", "errors")}
        try:
            for name in ("errors", "config"):
                spec = importlib.util.spec_from_file_location(name, module_dir / f"{name}.py")
                module = importlib.util.module_from_spec(spec)
                sys.modules[name] = module
                assert spec.loader is not None
                spec.loader.exec_module(module)
            spec = importlib.util.spec_from_file_location(
                "device_recovery_contract_validation", module_dir / "validation.py"
            )
            validation = importlib.util.module_from_spec(spec)
            assert spec.loader is not None
            spec.loader.exec_module(validation)

            valid = load(FIXTURES / "valid.request.json")
            self.assertEqual(
                validation.parse_and_validate_self_event({"body": valid}), valid
            )
            for name in (
                "invalid.request.boolean-schema-version.json",
                "invalid.request.extra-field.json",
                "invalid.request.operation-id.json",
            ):
                with self.subTest(name=name), self.assertRaises(Exception) as context:
                    validation.parse_and_validate_self_event(
                        {"body": load(FIXTURES / name)}
                    )
                self.assertEqual(context.exception.code, "INVALID_REQUEST")
        finally:
            for name, module in previous.items():
                if module is None:
                    sys.modules.pop(name, None)
                else:
                    sys.modules[name] = module

    def test_private_receipt_fixture_shape_and_retention_relation(self):
        completed = 1_800_000_000
        receipt = {
            "PK": "USER#user-123",
            "SK": "RECOVERY#3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "recordType": "DEVICE_RECOVERY_RECEIPT",
            "schemaVersion": 1,
            "operationId": "3fefbf1a-caf4-4e72-ab61-4fb36bf925b4",
            "operation": "REPLACE_ACTIVE_BINDING",
            "payloadHash": "a" * 64,
            "result": "RECOVERED",
            "status": "COMPLETE",
            "bindingFingerprint": "installation-key-1",
            "completedAtEpoch": completed,
            "expiresAt": completed + 7 * 86400,
        }
        Draft202012Validator(SCHEMAS["receipt-record.schema.json"]).validate(receipt)
        self.assertEqual(receipt["expiresAt"] - receipt["completedAtEpoch"], 7 * 86400)


if __name__ == "__main__":
    unittest.main()
