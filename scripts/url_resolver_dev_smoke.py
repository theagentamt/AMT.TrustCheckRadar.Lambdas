#!/usr/bin/env python3
"""Invoke a Dev resolver alias; emit only bounded case/results, never raw payloads."""
import argparse
import json
from pathlib import Path
import subprocess
import tempfile
import uuid


def cases(fixture=None, public_https=False, fixture_private_target=False):
    checks = [
        ("metadata", "http://169.254.169.254/latest/meta-data/", "blocked", "NON_PUBLIC_DESTINATION", 0),
        ("private", "http://10.0.0.1/", "blocked", "NON_PUBLIC_DESTINATION", 0),
        ("loopback", "http://127.0.0.1/", "blocked", "NON_PUBLIC_DESTINATION", 0),
        ("userinfo", "https://user:secret@example.com/", "invalid_input", "INVALID_AUTHORITY", 0),
        ("headers", "https://example.com/%0d%0aInjected:yes", "invalid_input", "INVALID_URL_ENCODING", 0),
        ("sensitive", "https://example.com/reset/synthetic", "blocked", "SENSITIVE_LINK_NOT_FETCHED", 0),
        ("nested-sensitive", "https://example.com/%2572eset/synthetic", "blocked", "SENSITIVE_LINK_NOT_FETCHED", 0),
        ("non-http", "file:///etc/passwd", "blocked", "UNSUPPORTED_SCHEME", 0),
    ]
    if public_https:
        checks.append(("public-https", "https://example.com/", "http_chain_complete", "HTTP_TERMINAL_OBSERVED", 1))
    if fixture:
        base = fixture.rstrip("/")
        if fixture_private_target:
            checks.append(("owned-private-ip", base + "/private-ip", "blocked", "NON_PUBLIC_DESTINATION", 1))
        for name, path, status, reason, count in [
            ("owned-terminal", "/terminal", "http_chain_complete", "HTTP_TERMINAL_OBSERVED", 1),
            ("owned-redirect", "/redirect", "http_chain_complete", "HTTP_TERMINAL_OBSERVED", 2),
            ("owned-nested", "/nested", "http_chain_complete", "HTTP_TERMINAL_OBSERVED", 3),
            ("owned-loop", "/loop-a", "partial", "REDIRECT_LOOP", 2),
            ("owned-private", "/private", "blocked", "NON_PUBLIC_DESTINATION", 1),
            ("owned-sensitive", "/sensitive", "blocked", "SENSITIVE_LINK_NOT_FETCHED", 1),
            ("owned-limit", "/limit/0", "partial", "REDIRECT_LIMIT_EXCEEDED", 6),
            ("owned-timeout", "/slow", "partial", "NETWORK_TIMEOUT", 1),
            ("owned-oversized", "/oversized", "partial", "RESPONSE_HEADERS_TOO_LARGE", 1),
            ("owned-duplicate", "/duplicate-location", "partial", "AMBIGUOUS_REDIRECT_LOCATION", 1),
            ("owned-refresh", "/refresh", "partial", "BROWSER_REDIRECT_UNSUPPORTED", 1),
            ("owned-empty-query", "/empty-query?x=1", "http_chain_complete", "HTTP_TERMINAL_OBSERVED", 2),
        ]:
            checks.append((name, base + path, status, reason, count))
    return checks


def invoke(function, region, profile, event):
    with tempfile.TemporaryDirectory(prefix="resolver-smoke-") as temp:
        payload = Path(temp) / "request.json"
        output = Path(temp) / "response.json"
        payload.write_text(json.dumps(event))
        args = ["aws", "--region", region]
        if profile:
            args += ["--profile", profile]
        args += ["lambda", "invoke", "--function-name", function, "--invocation-type", "RequestResponse", "--cli-connect-timeout", "5", "--cli-read-timeout", "20", "--payload", "fileb://" + str(payload), str(output), "--output", "json"]
        # Never automatically retry a request that may already have reached a URL.
        import os
        env = dict(os.environ, AWS_MAX_ATTEMPTS="1", AWS_PAGER="")
        process = subprocess.run(args, capture_output=True, text=True, env=env, timeout=30)
        if process.returncode:
            raise RuntimeError("AWS_INVOKE_FAILED")
        metadata = json.loads(process.stdout)
        if metadata.get("FunctionError") or metadata.get("StatusCode") != 200:
            raise RuntimeError("LAMBDA_INVOCATION_FAILED")
        return json.loads(output.read_text())


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--function", required=True, help="Published Dev alias ARN")
    parser.add_argument("--region", default="us-east-1")
    parser.add_argument("--profile")
    parser.add_argument("--fixture-base", help="Owned source-restricted HTTP fixture URL")
    parser.add_argument("--public-https", action="store_true", help="Explicitly fetch https://example.com/")
    parser.add_argument("--fixture-private-target", action="store_true", help="Fixture was bootstrapped with AMT_FIXTURE_PRIVATE_IP")
    args = parser.parse_args()
    if args.fixture_private_target and not args.fixture_base:
        parser.error("--fixture-private-target requires --fixture-base")
    if ":function:" not in args.function or not args.function.endswith(":live") or "-dev-" not in args.function:
        parser.error("Use a published Dev live alias ARN")
    failed = 0
    for name, url, status, reason, count in cases(args.fixture_base, args.public_https, args.fixture_private_target):
        check_id = "smoke-" + uuid.uuid4().hex
        try:
            result = invoke(args.function, args.region, args.profile, {"schemaVersion": 1, "checkId": check_id, "url": url})
            assert result["schemaVersion"] == 1 and result["checkId"] == check_id
            assert result["resolutionStatus"] == status and reason in result["reasonCodes"]
            assert result["requestCount"] == count
            assert result["httpChainComplete"] == (status == "http_chain_complete")
            if name.startswith("owned-"):
                assert "UNENCRYPTED_CONNECTION" in result["warnings"]
            if count == 0:
                assert result["hops"] == [] and result["lastObservedUrl"] is None
            print(json.dumps({"case": name, "passed": True, "status": status, "reason": reason, "requestCount": count}))
        except Exception:
            failed += 1
            print(json.dumps({"case": name, "passed": False}))
    raise SystemExit(1 if failed else 0)


if __name__ == "__main__":
    main()
