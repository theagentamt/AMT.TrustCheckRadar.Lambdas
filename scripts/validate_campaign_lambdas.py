#!/usr/bin/env python3
"""Run reproducible local privacy/package/performance checks for campaign Lambdas."""

import argparse
import ast
import importlib.util
import json
from pathlib import Path
import re
import time
import zipfile


ROOT = Path(__file__).resolve().parents[1]
ZIP_FUNCTIONS = (
    "campaign_observation_publisher",
    "campaign_participation",
    "campaign_cluster_aggregator",
    "campaign_lifecycle",
    "campaign_deletion_bridge",
    "campaign_review",
    "campaign_trends",
)
PROHIBITED_LOG_TERMS = (
    "accountid", "contributortoken", "statisticsEventid", "campaignid",
    "requestid", "sanitizedtext", "email", "phonenumber", "reason",
)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--dist-dir", default="dist")
    args = parser.parse_args()
    dist = (ROOT / args.dist_dir).resolve()
    result = {
        "schemaVersion": 1,
        "packages": validate_packages(dist),
        "canonicalContracts": validate_contract_package(dist),
        "contentFreeLogTemplates": validate_logs(),
        "serverFeatureExtractorAbsent": validate_server_extractor_absent(),
        "localScoringBenchmark": benchmark_scoring(),
    }
    print(json.dumps(result, indent=2, sort_keys=True))


def validate_packages(dist):
    hashes = {}
    from hashlib import sha256
    for name in ZIP_FUNCTIONS:
        path = dist / f"{name}.zip"
        if not path.is_file():
            raise SystemExit(f"missing campaign artifact: {path}")
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if "app.py" not in names:
                raise SystemExit(f"{path.name} has no root app.py")
            if any("__pycache__" in item or item.endswith((".pyc", ".pyo")) for item in names):
                raise SystemExit(f"{path.name} contains Python cache files")
        hashes[name] = sha256(path.read_bytes()).hexdigest()
    return hashes


def validate_contract_package(dist):
    from hashlib import sha256
    path = dist / "campaign-contracts-1.0.0.zip"
    if not path.is_file():
        raise SystemExit(f"missing canonical contract artifact: {path}")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        required = {
            "contract-set.json",
            "app-features.schema.json",
            "outbox-record.schema.json",
            "queue-envelope.schema.json",
            "transient-feature-record.schema.json",
            "campaign-aggregate.schema.json",
            "review-transition.schema.json",
            "scam-trends-response.schema.json",
            "taxonomy.en.json",
            "taxonomy.es.json",
            "fixtures/app-features.valid.en.json",
            "fixtures/app-features.valid.es.json",
            "SHA256SUMS",
        }
        missing = required - names
        if missing:
            raise SystemExit(f"canonical contract artifact is incomplete: {sorted(missing)}")
        entries = {}
        for line in archive.read("SHA256SUMS").decode("utf-8").splitlines():
            digest, separator, name = line.partition("  ")
            if not separator or len(digest) != 64:
                raise SystemExit(f"invalid contract checksum entry: {line!r}")
            entries[name] = digest
        for name, digest in entries.items():
            if name not in names or sha256(archive.read(name)).hexdigest() != digest:
                raise SystemExit(f"contract checksum mismatch: {name}")
    return {"artifact": path.name, "sha256": sha256(path.read_bytes()).hexdigest(), "result": "pass"}


def validate_logs():
    inspected = 0
    for folder in ROOT.glob("src/campaign_*"):
        for path in folder.glob("*.py"):
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                    continue
                if node.func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
                    continue
                inspected += 1
                if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                    raise SystemExit(f"non-literal campaign log template: {path}:{node.lineno}")
                normalized = re.sub(r"[^a-z]", "", node.args[0].value.lower())
                for term in PROHIBITED_LOG_TERMS:
                    if re.sub(r"[^a-z]", "", term.lower()) in normalized:
                        raise SystemExit(f"prohibited campaign log term in {path}:{node.lineno}")
    analysis_prohibited = (
        "accountid", "requestid", "appfeatures", "sanitizedtext", "vector", "fingerprint", "indicator"
    )
    for path in (ROOT / "src/conversation_analysis").glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
                continue
            inspected += 1
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                raise SystemExit(f"non-literal analysis log template: {path}:{node.lineno}")
            normalized = re.sub(r"[^a-z]", "", node.args[0].value.lower())
            if any(term in normalized for term in analysis_prohibited):
                raise SystemExit(f"prohibited analysis log term in {path}:{node.lineno}")
    for path in (ROOT / "src/shared_entitlements").glob("*.py"):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr not in {"debug", "info", "warning", "error", "exception", "critical"}:
                continue
            inspected += 1
            if not node.args or not isinstance(node.args[0], ast.Constant) or not isinstance(node.args[0].value, str):
                raise SystemExit(f"non-literal shared entitlement log template: {path}:{node.lineno}")
            normalized = re.sub(r"[^a-z]", "", node.args[0].value.lower())
            if any(term in normalized for term in ("accountid", "requestid")):
                raise SystemExit(f"prohibited entitlement log term in {path}:{node.lineno}")
    return {"templatesInspected": inspected, "result": "pass"}


def validate_server_extractor_absent():
    source = ROOT / "src/campaign_feature_extractor"
    tests = ROOT / "tests/campaign_feature_extractor"
    if any(path.is_file() and path.suffix != ".pyc" for folder in (source, tests) for path in folder.glob("**/*")):
        raise SystemExit("server feature extractor source or tests are still present")
    inspected = [
        ROOT / ".github/workflows/ci.yml",
        ROOT / ".github/workflows/publish.yml",
        ROOT / "scripts/build_lambda_zip.sh",
        ROOT / "README.md",
        ROOT / "docs/GITHUB_PUBLISHING.md",
    ]
    prohibited = ("FEATURE_IMAGE", "campaign-feature-image", "campaign_feature_extractor", "amazon-ecr-login")
    for path in inspected:
        content = path.read_text()
        if any(value in content for value in prohibited):
            raise SystemExit(f"server feature extraction support remains in {path}")
    return {"result": "pass"}


def benchmark_scoring():
    path = ROOT / "src/campaign_cluster_aggregator/scoring.py"
    spec = importlib.util.spec_from_file_location("campaign_scoring_evidence", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    feature = {"taxonomyBucket": "advance_fee", "vector": [0.1] * 384,
               "lexicalFingerprint": ["a", "b"], "signalIds": ["payment_request"], "indicatorIds": []}
    candidate = {"taxonomyBucket": "advance_fee", "centroid": [0.1] * 384,
                 "lexicalFingerprint": ["a", "b"], "signalIds": ["payment_request"], "indicatorIds": []}
    iterations = 10_000
    started = time.perf_counter()
    for _ in range(iterations): module.similarity(feature, candidate)
    duration_ms = (time.perf_counter() - started) * 1000
    return {"iterations": iterations, "durationMs": round(duration_ms, 3),
            "comparisonsPerSecond": round(iterations / (duration_ms / 1000), 1)}


if __name__ == "__main__":
    main()
