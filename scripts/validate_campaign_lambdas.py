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
        "contentFreeLogTemplates": validate_logs(),
        "offlineModel": validate_model_image(),
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
    return {"templatesInspected": inspected, "result": "pass"}


def validate_model_image():
    dockerfile = (ROOT / "src/campaign_feature_extractor/Dockerfile").read_text()
    model_source = (ROOT / "src/campaign_feature_extractor/model.py").read_text()
    required = ("HF_HUB_OFFLINE=1", "TRANSFORMERS_OFFLINE=1", "local_files_only=True")
    if not all(value in dockerfile + model_source for value in required):
        raise SystemExit("feature image does not prove offline-only model loading")
    revision = re.search(r"ARG MODEL_REVISION=([0-9a-f]{40})", dockerfile)
    if not revision:
        raise SystemExit("feature model revision is not commit-pinned")
    return {"result": "pass", "modelRevision": revision.group(1)}


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
