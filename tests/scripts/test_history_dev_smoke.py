import base64
import importlib.util
import json
from pathlib import Path
import stat
import tempfile
import time
import types
import unittest


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "history_dev_smoke", ROOT / "scripts" / "history_dev_smoke.py"
)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


def token_for(subject, *, token_use="access", expires=None):
    payload = {
        "sub": subject,
        "token_use": token_use,
        "exp": expires or int(time.time()) + 600,
    }
    encoded = base64.urlsafe_b64encode(
        json.dumps(payload).encode("utf-8")
    ).decode("ascii").rstrip("=")
    return f"header.{encoded}.signature"


def execute_args(**overrides):
    values = {
        "approval_reference": "approval-1",
        "expected_account_id": "123456789012",
        "expected_release_id": "a" * 40,
        "api_base_url": "https://dev.example.test",
        "analysis_path": "/analysis",
        "primary_subject": "disposable-primary",
        "secondary_subject": "disposable-secondary",
        "primary_token_file": Path("primary.token"),
        "secondary_token_file": Path("secondary.token"),
        "primary_fingerprint_file": Path("primary.fingerprint"),
        "secondary_fingerprint_file": Path("secondary.fingerprint"),
        "conversation_function": "conversation",
        "history_read_function": "read",
        "history_mutation_function": "mutation",
        "history_lifecycle_function": "lifecycle",
        "history_content_table": "content",
        "history_control_table": "control",
        "analysis_abuse_table": "abuse",
        "environment": "dev",
        "confirm_disposable_subjects": smoke.CONFIRMATION,
        "max_lifecycle_invocations": 12,
    }
    values.update(overrides)
    return types.SimpleNamespace(**values)


class HistoryDevSmokeSafetyTests(unittest.TestCase):
    def test_plan_is_content_free_and_has_no_actions(self):
        value = smoke.plan()

        self.assertEqual(value["networkActions"], 0)
        self.assertEqual(value["awsActions"], 0)
        self.assertEqual(value["paidModelCalls"], 0)
        smoke.assert_no_prohibited_evidence(value)

    def test_execute_gate_requires_dev_confirmation_and_distinct_subjects(self):
        smoke.validate_execute_args(execute_args())
        for overrides in (
            {"environment": "prod"},
            {"confirm_disposable_subjects": "yes"},
            {"secondary_subject": "disposable-primary"},
            {"expected_release_id": "short"},
            {"api_base_url": "http://dev.example.test"},
            {"approval_reference": "contains spaces"},
        ):
            with self.subTest(overrides=overrides), self.assertRaises(smoke.SmokeFailure):
                smoke.validate_execute_args(execute_args(**overrides))

    def test_token_must_match_explicit_subject_and_be_active_access_token(self):
        smoke.validate_token_subject(token_for("disposable-primary"), "disposable-primary")

        for token in (
            token_for("another-subject"),
            token_for("disposable-primary", token_use="id"),
            token_for("disposable-primary", expires=int(time.time()) - 1),
            "not-a-jwt",
        ):
            with self.subTest(token=token), self.assertRaises(smoke.SmokeFailure):
                smoke.validate_token_subject(token, "disposable-primary")

    def test_private_input_files_reject_group_or_other_permissions(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "token"
            path.write_text("secret\n", encoding="utf-8")
            path.chmod(stat.S_IRUSR | stat.S_IWUSR)
            self.assertEqual(smoke.read_private_file(path, "token"), "secret")

            path.chmod(stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP)
            with self.assertRaises(smoke.SmokeFailure):
                smoke.read_private_file(path, "token")

    def test_fixed_fixture_guarantees_no_model_path(self):
        smoke.assert_fixture_short_circuits_model()

    def test_evidence_allowlist_rejects_identifiers_and_content(self):
        smoke.assert_no_prohibited_evidence({"step": "safe", "count": 1})

        for key in smoke.PROHIBITED_EVIDENCE_KEYS:
            with self.subTest(key=key), self.assertRaises(smoke.SmokeFailure):
                smoke.assert_no_prohibited_evidence({"nested": [{key: "value"}]})


if __name__ == "__main__":
    unittest.main()
