#!/usr/bin/env python3
"""Bounded synthetic Dev qualification for candidate.3 and governed History.

The default mode validates private inputs without network access. ``--execute``
uses one already-onboarded disposable Dev account. It never prints credentials,
check/result identifiers, proofs, message text, or response bodies.
"""
from __future__ import annotations

import argparse
import http.client
import json
import os
import re
import stat
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from uuid import uuid4

from message_consumer.service import validate_envelope
from shared_governed_history import RETENTION_SECONDS, validate_public_projection


VERSION = "1.0.0-message-candidate.3"
HISTORY_VERSION = "1.0.0-governed-history-candidate.1"
POLICY = "message-ai-2026-09-21-v1"
TRIAL_SECONDS = 7 * 24 * 60 * 60
TRIAL_LIMIT = 10
MAX_REQUESTS = 20
ALLOWED_HOSTS = {"api-dev.andmorethings.net"}
EXECUTE_API_HOST = "icuak34th9.execute-api.us-east-1.amazonaws.com"
PROOF = re.compile(r"^v1_[A-Za-z0-9]{1,8}_[0-9a-f]{8}_[0-9a-f]{16}_[0-9a-f]{24}$")
HEADERS = {"Authorization", "x-device-binding-fingerprint"}
CONFIRMATION = "DISPOSABLE_SYNTHETIC_DEV_ACCOUNT"
CASES = {
    "en": (
        ("inconclusive", "An unfamiliar request outside qualified coverage.", "inconclusive", 0),
        ("complete", "Send me your account password and the login code.", "complete", 1),
    ),
    "es": (
        ("inconclusive", "Una solicitud desconocida fuera de la cobertura calificada.", "inconclusive", 0),
        ("complete", "Por favor, envíenos el código de acceso de un solo uso de su cuenta.", "complete", 1),
    ),
}


class Refused(RuntimeError):
    pass


def need(value: object) -> None:
    if not value:
        raise Refused("QUALIFICATION_UNVERIFIED")


def unique_pairs(values):
    result = {}
    for key, value in values:
        need(key not in result)
        result[key] = value
    return result


def private_headers(path: Path) -> dict[str, str]:
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError:
        raise Refused("QUALIFICATION_UNVERIFIED") from None
    try:
        metadata = os.fstat(descriptor)
        need(stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600
             and metadata.st_uid == os.getuid())
        raw = os.read(descriptor, 32769)
        need(len(raw) <= 32768)
    finally:
        os.close(descriptor)
    try:
        value = json.loads(raw, object_pairs_hook=unique_pairs)
    except (TypeError, ValueError):
        raise Refused("QUALIFICATION_UNVERIFIED") from None
    need(type(value) is dict and set(value) == HEADERS)
    token, fingerprint = value["Authorization"], value["x-device-binding-fingerprint"]
    need(type(token) is str and re.fullmatch(r"Bearer [A-Za-z0-9._-]{32,16377}", token))
    need(type(fingerprint) is str and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,255}", fingerprint))
    return value


def api_target(raw: str) -> tuple[str, str]:
    parsed = urlsplit(raw)
    need(parsed.scheme == "https" and parsed.hostname is not None)
    need(parsed.username is None and parsed.password is None and parsed.port in (None, 443))
    need(not parsed.query and not parsed.fragment)
    host = parsed.hostname.lower()
    need(host in ALLOWED_HOSTS or host == EXECUTE_API_HOST)
    base = parsed.path.rstrip("/")
    need(base == "")
    return host, base


class Budget:
    def __init__(self, maximum=MAX_REQUESTS):
        self.maximum, self.used = maximum, 0

    def take(self):
        need(type(self.maximum) is int and self.maximum == MAX_REQUESTS and self.used < self.maximum)
        self.used += 1


class Client:
    def __init__(self, api_base, headers, budget, *, request=None):
        self.host, self.base = api_target(api_base)
        need(type(headers) is dict and set(headers) == HEADERS)
        self.headers, self.budget = dict(headers), budget
        self.transport = request or self._request

    def _request(self, method, path, body):
        connection = http.client.HTTPSConnection(self.host, timeout=15)
        payload = None if body is None else json.dumps(body, separators=(",", ":"))
        headers = self.headers | {"Accept": "application/json"}
        if payload is not None:
            headers["Content-Type"] = "application/json"
        try:
            connection.request(method, self.base + path, body=payload, headers=headers)
            response = connection.getresponse()
            raw = response.read(65537)
            need(len(raw) <= 65536)
            value = json.loads(raw, object_pairs_hook=unique_pairs)
            need(type(value) is dict)
            return response.status, value
        except Refused:
            raise
        except Exception:
            raise Refused("QUALIFICATION_UNVERIFIED") from None
        finally:
            connection.close()

    def call(self, method, path, body=None):
        need(type(path) is str and path.startswith("/v1/") and "#" not in path)
        self.budget.take()
        return self.transport(method, path, body)


def trial_snapshot(body, *, now):
    need(type(body) is dict and body.get("schemaVersion") == 1 and body.get("activeDevice") is True)
    access, allowance, trial = body.get("access"), body.get("allowance"), body.get("trial")
    need(type(access) is dict and type(allowance) is dict and type(trial) is dict)
    need(access.get("basis") == "trial")
    need(allowance.get("limit") == TRIAL_LIMIT and type(allowance.get("completedUsed")) is int
         and type(allowance.get("reserved")) is int and allowance["reserved"] == 0
         and type(allowance.get("remaining")) is int
         and 0 <= allowance["completedUsed"] <= TRIAL_LIMIT
         and allowance["remaining"] == TRIAL_LIMIT - allowance["completedUsed"])
    if allowance["remaining"]:
        need(access.get("externalChecksAllowed") is True and access.get("reason") == "AVAILABLE")
    else:
        need(access.get("externalChecksAllowed") is False and access.get("reason") == "ALLOWANCE_EXHAUSTED")
    activated, expires = trial.get("activatedAtEpoch"), trial.get("expiresAtEpoch")
    need(type(activated) is int and type(expires) is int and expires - activated == TRIAL_SECONDS)
    need(allowance.get("periodEndsAtEpoch") == expires and activated <= now < expires)
    return allowance["completedUsed"], allowance["remaining"]


def message_request(text, language):
    return {"transportVersion": VERSION, "checkId": "sec340-" + uuid4().hex,
            "entryPoint": "message", "language": language,
            "target": {"scope": "sanitized_message", "sourceType": "pasted_text",
                       "sanitizedText": text, "speakerRole": "other", "entities": [],
                       "withheldLinks": False, "reviewedLinks": []}}


def settled(body, check_id, proof, outcome_name, charge):
    validate_envelope(body)
    need(body["transportVersion"] == VERSION and body["checkId"] == check_id
         and body["operationProof"] == proof and body["state"] == "settled"
         and body["errorCode"] is None)
    accounting, outcome = body["accounting"], body["outcome"]
    need(accounting["chargedChecks"] == charge and accounting["requiresReconciliation"] is False)
    need(accounting["state"] == ("charged" if charge else "not_charged"))
    need(outcome["schemaVersion"] == 3 and outcome["policyVersion"] == POLICY
         and outcome["processingOutcome"] == outcome_name
         and outcome["aiAssessmentStatus"] == "not_assessed" and outcome["aiReasonCodes"] == [])
    need(outcome["evidence"] == [])
    if outcome_name == "complete":
        need(outcome["verdict"] == "high_risk" and outcome["assessmentBasis"] == ["qualified_rules"]
             and outcome["coverage"] == "supported_checks_complete"
             and outcome["ruleIds"] == ["REQUEST_SECRET_DISCLOSURE"] and outcome["limitationCodes"] == [])
    else:
        need(outcome["verdict"] == "unknown" and outcome["assessmentBasis"] == []
             and outcome["coverage"] == "not_assessed"
             and outcome["ruleIds"] == [] and outcome["limitationCodes"] == ["INSUFFICIENT_EVIDENCE"])


def history_page(body):
    need(type(body) is dict and set(body) == {"transportVersion", "serverTimeEpoch", "items", "nextCursor"})
    need(body["transportVersion"] == HISTORY_VERSION and type(body["serverTimeEpoch"]) is int
         and type(body["items"]) is list and len(body["items"]) <= 50)
    for item in body["items"]:
        validate_public_projection(item)
    return body


class Qualification:
    def __init__(self, client, *, language="en", activate_trial=False, clock=lambda: int(time.time()), sleep=time.sleep,
                 fresh_client=None):
        need(language in CASES)
        self.client, self.activate_trial, self.clock, self.sleep = client, activate_trial, clock, sleep
        self.language = language
        self.fresh_client = fresh_client or (lambda: client)

    def access(self):
        status, body = self.client.call("GET", "/v1/access")
        need(status == 200)
        return body

    def ensure_trial(self):
        body = self.access()
        basis = (body.get("access") or {}).get("basis")
        if basis == "none":
            need(self.activate_trial and (body.get("trial") or {}).get("activationAvailable") is True)
            status, body = self.client.call("POST", "/v1/access/trial", {"schemaVersion": 1, "activate": True})
            need(status == 200)
        else:
            need(basis == "trial")
        return body

    def list_history(self, client=None):
        status, body = (client or self.client).call("GET", "/v1/users/analysis-history?" + urlencode({"limit": 50}))
        need(status == 200)
        page = history_page(body)
        need(page["nextCursor"] is None)
        return page

    @staticmethod
    def exact_history(page, expected):
        identities = [item["resultId"] for item in page["items"]]
        need(len(identities) == len(set(identities)) and set(identities) == expected)

    def run_case(self, text, outcome_name, charge, known, *, detail=False):
        request = message_request(text, self.language)
        check_id = request["checkId"]
        status, prepared = self.client.call("POST", "/v1/message-checks/prepare", request)
        need(status == 200 and prepared.get("state") == "prepared")
        validate_envelope(prepared)
        proof = prepared.get("operationProof")
        need(type(proof) is str and PROOF.fullmatch(proof)
             and prepared["accounting"]["chargedChecks"] == 0)
        submit = request | {"operationProof": proof}
        status, first = self.client.call("POST", "/v1/message-checks", submit)
        need(status == 200)
        settled(first, check_id, proof, outcome_name, charge)

        status, replay = self.client.call("POST", "/v1/message-checks", submit)
        need(status == 200)
        settled(replay, check_id, proof, outcome_name, charge)
        replay_page = self.list_history()
        replay_ids = [item["resultId"] for item in replay_page["items"]]
        need(len(replay_ids) == len(set(replay_ids)))
        replay_new = [item for item in replay_page["items"] if item["resultId"] not in known]
        need(len(replay_new) <= 1)
        if replay_new:
            need(set(replay_ids) == known | {replay_new[0]["resultId"]})
        else:
            need(set(replay_ids) == known)
        item = replay_new[0] if replay_new else None

        recovery = {"transportVersion": VERSION, "checkId": check_id, "operationProof": proof}
        status, reconciled = self.client.call("POST", "/v1/message-checks/reconcile", recovery)
        need(status == 200)
        settled(reconciled, check_id, proof, outcome_name, charge)
        need(first["accounting"] == replay["accounting"] == reconciled["accounting"])
        need(first["outcome"] == replay["outcome"] == reconciled["outcome"])
        if item is None:
            self.sleep(2)
        reconciled_page = self.list_history()
        reconciled_new = [value for value in reconciled_page["items"] if value["resultId"] not in known]
        need(len(reconciled_new) == 1)
        item = reconciled_new[0]
        expected = known | {item["resultId"]}
        self.exact_history(reconciled_page, expected)
        if replay_new:
            need(replay_new == [item])
        projected_accounting = {key: first["accounting"][key] for key in ("state", "chargedChecks", "receiptId")}
        need(item["processingOutcome"] == outcome_name and item["accounting"] == projected_accounting
             and item["expiresAtEpoch"] - item["settledAtEpoch"] == RETENTION_SECONDS)

        reopened = self.list_history(self.fresh_client())
        self.exact_history(reopened, expected)
        matched = [value for value in reopened["items"] if value["resultId"] == item["resultId"]]
        need(len(matched) == 1 and matched[0] == item)
        if detail:
            status, detail_body = self.client.call("GET", "/v1/users/analysis-history/" + item["resultId"])
            need(status == 200 and type(detail_body) is dict
                 and set(detail_body) == {"transportVersion", "serverTimeEpoch", "result"})
            need(detail_body["transportVersion"] == HISTORY_VERSION and detail_body["result"] == item)
            validate_public_projection(detail_body["result"])
        need(item["outcome"] == {key: value for key, value in first["outcome"].items() if key != "checkId"})
        known.add(item["resultId"])

    def run(self):
        start = self.ensure_trial()
        before, before_remaining = trial_snapshot(start, now=self.clock())
        need(before_remaining >= 1)
        baseline = self.list_history()
        known = {item["resultId"] for item in baseline["items"]}
        self.exact_history(baseline, known)
        cases = CASES[self.language]
        for _, text, outcome, charge in cases:
            self.run_case(text, outcome, charge, known, detail=True)
        after = self.access()
        completed, remaining = trial_snapshot(after, now=self.clock())
        need(completed == before + 1 and remaining == TRIAL_LIMIT - completed)
        need(self.client.budget.used <= MAX_REQUESTS)
        return {"case": "SECUR4ALL-340_DEV_GOVERNED_HISTORY", "passed": True,
                "syntheticOnly": True, "rulesOnly": True,
                "language": self.language,
                "requestCount": self.client.budget.used, "maxRequests": MAX_REQUESTS,
                "trialLimit": TRIAL_LIMIT, "trialDurationSeconds": TRIAL_SECONDS,
                "settledResults": 2, "chargedChecks": 1, "zeroChargeResults": 1,
                "exactReplayAccounting": True, "proofOnlyReconciliation": True,
                "historyReopen": True, "fixedRetention": True,
                "requiresOperationalEvidence": ["provider_invocations_zero", "rollback_gates_inactive",
                                                "disposable_account_cleanup"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-base", required=True)
    parser.add_argument("--headers-file", type=Path, required=True)
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--activate-trial", action="store_true")
    parser.add_argument("--language", choices=sorted(CASES), default="en")
    parser.add_argument("--confirm-synthetic")
    args = parser.parse_args()
    values = private_headers(args.headers_file)
    api_target(args.api_base)
    if not args.execute:
        print(json.dumps({"validatedLocally": True, "networkCalls": 0}, separators=(",", ":")))
        return
    need(args.confirm_synthetic == CONFIRMATION)
    budget = Budget()
    client = Client(args.api_base, values, budget)
    print(json.dumps(Qualification(client, language=args.language, activate_trial=args.activate_trial,
                                   fresh_client=lambda: Client(args.api_base, values, budget)).run(),
                     separators=(",", ":")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        print(json.dumps({"passed": False, "code": "QUALIFICATION_UNVERIFIED"}, separators=(",", ":")))
        raise SystemExit(2) from None
