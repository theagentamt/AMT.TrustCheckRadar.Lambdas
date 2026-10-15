"""Run one redacted, complimentary current-authority check against Dev.

The default mode validates local inputs only. ``--execute`` performs one
deterministic rules-only message check, exact replay, and proof-only reconcile.
Bearer credentials and device fingerprints are read from a mode-600 JSON file
and are never written to output.
"""

from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import stat
from pathlib import Path
from urllib.parse import urlsplit
from uuid import uuid4


VERSION = "1.0.0-message-candidate.1"
MESSAGE = "Send me your account password and the login code."
ALLOWED_HOSTS = {"api-dev.andmorethings.net"}
EXECUTE_API = re.compile(r"^[a-z0-9]+\.execute-api\.us-east-1\.amazonaws\.com$")
PROOF = re.compile(r"^v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}$")
HEADER_FIELDS = {"Authorization", "x-device-binding-fingerprint"}
SYNTHETIC_CONFIRMATION = "DISPOSABLE_SYNTHETIC_DEV_ACCOUNT"


class Refused(RuntimeError):
    """Qualification input or response did not meet the reviewed contract."""


def need(value: object) -> None:
    if not value:
        raise Refused("QUALIFICATION_UNVERIFIED")


def unique_pairs(values):
    result = {}
    for key, value in values:
        need(key not in result)
        result[key] = value
    return result


def read_json(path: Path) -> object:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError:
        raise Refused("QUALIFICATION_UNVERIFIED") from None
    try:
        metadata = os.fstat(descriptor)
        need(stat.S_ISREG(metadata.st_mode) and metadata.st_mode & 0o077 == 0)
        with os.fdopen(descriptor, "rb", closefd=False) as source:
            raw = source.read(32769)
        need(len(raw) <= 32768)
    finally:
        os.close(descriptor)
    try:
        return json.loads(raw, object_pairs_hook=unique_pairs)
    except (TypeError, ValueError):
        raise Refused("QUALIFICATION_UNVERIFIED") from None


def headers(path: Path) -> dict[str, str]:
    value = read_json(path)
    need(type(value) is dict and set(value) == HEADER_FIELDS)
    token = value["Authorization"]
    fingerprint = value["x-device-binding-fingerprint"]
    need(type(token) is str and re.fullmatch(r"Bearer [A-Za-z0-9._-]{32,16377}", token))
    need(type(fingerprint) is str and 1 <= len(fingerprint) <= 256)
    need("\n" not in token and "\r" not in token and "\n" not in fingerprint and "\r" not in fingerprint)
    return value


def api_target(raw: str) -> tuple[str, str]:
    parsed = urlsplit(raw)
    need(parsed.scheme == "https" and parsed.hostname is not None)
    need(parsed.username is None and parsed.password is None and parsed.port in (None, 443))
    need(not parsed.query and not parsed.fragment)
    host = parsed.hostname.lower()
    need(host in ALLOWED_HOSTS or EXECUTE_API.fullmatch(host))
    base_path = parsed.path.rstrip("/")
    need(not base_path or re.fullmatch(r"/[A-Za-z0-9._~/-]{1,128}", base_path))
    return host, base_path


class Harness:
    def __init__(self, api_base: str, credential_headers: dict[str, str], *, request=None):
        self.host, self.base_path = api_target(api_base)
        need(type(credential_headers) is dict and set(credential_headers) == HEADER_FIELDS)
        self.headers = dict(credential_headers)
        self.request = request or self._request

    def _request(self, method: str, path: str, body: object | None = None):
        connection = http.client.HTTPSConnection(self.host, timeout=15)
        payload = None if body is None else json.dumps(body, separators=(",", ":"))
        request_headers = self.headers | {"Accept": "application/json"}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        try:
            connection.request(method, self.base_path + path, body=payload, headers=request_headers)
            response = connection.getresponse()
            raw = response.read(65537)
            need(len(raw) <= 65536)
            try:
                parsed = json.loads(raw, object_pairs_hook=unique_pairs)
            except (TypeError, ValueError):
                raise Refused("QUALIFICATION_UNVERIFIED") from None
            need(type(parsed) is dict)
            return response.status, parsed
        finally:
            connection.close()

    @staticmethod
    def _complimentary_snapshot(body: object) -> None:
        need(type(body) is dict and body.get("schemaVersion") == 1)
        need(body.get("activeDevice") is True)
        access = body.get("access")
        need(type(access) is dict and access.get("basis") == "complimentary")
        need(access.get("externalChecksAllowed") is True and access.get("reason") == "AVAILABLE")
        allowance = body.get("allowance")
        need(
            type(allowance) is dict
            and set(allowance)
            == {"limit", "completedUsed", "reserved", "remaining", "periodEndsAtEpoch"}
            and set(allowance.values()) == {None}
        )

    @staticmethod
    def _settled(body: object, check_id: str, proof: str) -> None:
        need(type(body) is dict and body.get("transportVersion") == VERSION)
        need(body.get("checkId") == check_id and body.get("operationProof") == proof)
        need(body.get("state") == "settled" and body.get("errorCode") is None)
        access = body.get("access")
        need(type(access) is dict and access.get("basis") == "complimentary")
        need(access.get("externalChecksAllowed") is True and access.get("unlimited") is True)
        accounting = body.get("accounting")
        need(type(accounting) is dict and accounting.get("chargedChecks") == 0)
        need(accounting.get("state") == "not_charged" and accounting.get("requiresReconciliation") is False)
        outcome = body.get("outcome")
        need(type(outcome) is dict and outcome.get("processingOutcome") == "complete")
        need(outcome.get("verdict") == "high_risk")
        need("REQUEST_SECRET_DISCLOSURE" in outcome.get("ruleIds", []))
        need(outcome.get("limitationCodes") == [] and outcome.get("evidence") == [])

    def run(self) -> dict[str, object]:
        status, before = self.request("GET", "/v1/access")
        need(status == 200)
        self._complimentary_snapshot(before)

        check_id = "sec334-current-" + uuid4().hex[:16]
        message = {
            "transportVersion": VERSION,
            "checkId": check_id,
            "entryPoint": "message",
            "language": "en",
            "target": {
                "scope": "sanitized_message",
                "sourceType": "pasted_text",
                "sanitizedText": MESSAGE,
                "speakerRole": "other",
                "entities": [],
                "withheldLinks": False,
                "reviewedLinks": [],
            },
        }
        status, prepared = self.request("POST", "/v1/message-checks/prepare", message)
        need(status == 200 and prepared.get("state") == "prepared")
        proof = prepared.get("operationProof")
        need(type(proof) is str and PROOF.fullmatch(proof))
        need(prepared.get("accounting", {}).get("chargedChecks") == 0)

        submit = message | {"operationProof": proof}
        status, first = self.request("POST", "/v1/message-checks", submit)
        need(status == 200)
        self._settled(first, check_id, proof)

        status, replay = self.request("POST", "/v1/message-checks", submit)
        need(status == 200)
        self._settled(replay, check_id, proof)

        reconcile_request = {
            "transportVersion": VERSION,
            "checkId": check_id,
            "operationProof": proof,
        }
        status, reconciled = self.request("POST", "/v1/message-checks/reconcile", reconcile_request)
        need(status == 200)
        self._settled(reconciled, check_id, proof)
        need(first["accounting"] == replay["accounting"] == reconciled["accounting"])
        need(first["outcome"] == replay["outcome"] == reconciled["outcome"])

        status, after = self.request("GET", "/v1/access")
        need(status == 200)
        self._complimentary_snapshot(after)
        need(before["allowance"] == after["allowance"])

        return {
            "case": "SECUR4ALL-334_DEV_CURRENT_AUTHORITY",
            "passed": True,
            "syntheticOnly": True,
            "basis": "complimentary",
            "rulesOnly": True,
            "processingOutcome": "complete",
            "verdict": "high_risk",
            "rule": "REQUEST_SECRET_DISCLOSURE",
            "chargedChecks": 0,
            "exactReplayAccounting": True,
            "proofOnlyReconciliation": True,
            "allowanceUnchanged": True,
        }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--headers-file", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-synthetic")
    args = parser.parse_args()
    credential_headers = headers(args.headers_file)
    if not args.execute:
        api_target(args.api_base)
        print(json.dumps({"validatedLocally": True, "networkCalls": 0}, separators=(",", ":")))
        return
    need(args.confirm_synthetic == SYNTHETIC_CONFIRMATION)
    harness = Harness(args.api_base, credential_headers)
    print(json.dumps(harness.run(), separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"passed": False, "code": "QUALIFICATION_UNVERIFIED"}, separators=(",", ":")))
        raise SystemExit(2) from None
