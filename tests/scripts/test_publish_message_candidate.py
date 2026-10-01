import base64
import hashlib
import importlib.util
from pathlib import Path
import zipfile

import pytest


spec = importlib.util.spec_from_file_location(
    "publish_message_candidate",
    Path(__file__).parents[2] / "scripts/publish_message_candidate.py",
)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
SHA = "a" * 40


@pytest.fixture
def packages(tmp_path):
    for function in module.FUNCTIONS:
        with zipfile.ZipFile(tmp_path / (function + ".zip"), "w") as archive:
            archive.writestr("app.py", "def lambda_handler(event, context): return None\n")
    checksums(tmp_path)
    return tmp_path


def checksums(path):
    (path / "SHA256SUMS").write_text(
        "".join(
            hashlib.sha256(item.read_bytes()).hexdigest() + "  " + item.name + "\n"
            for item in sorted(path.glob("*.zip"))
        )
    )


def cloud(path, *, existing=False, replace=False):
    calls = []
    heads = {}

    def call(region, operation, *args):
        calls.append((operation, args))
        key = args[args.index("--key") + 1]
        content = (path / key.split("/")[-1]).read_bytes()
        digest = hashlib.sha256(content).digest()
        response = {
            "VersionId": "v1",
            "ContentLength": len(content),
            "Metadata": {"sha256": digest.hex()},
            "ChecksumSHA256": base64.b64encode(digest).decode(),
        }
        if operation == "head-object":
            heads[key] = heads.get(key, 0) + 1
            if heads[key] == 1 and not existing:
                raise module.MissingObject()
            if replace and heads[key] > 1:
                response["VersionId"] = "concurrent-replacement"
                response["Metadata"] = {"sha256": "different"}
        return response

    return call, calls


def test_two_versioned_puts_are_conditional_and_never_deploy(packages):
    call, calls = cloud(packages)
    result = module.publish(packages, module.BUCKET, SHA, module.REGION, call)
    assert len(result["artifacts"]) == 2
    assert result["publicationComplete"] is True
    assert result["runtimeUpdated"] is False
    assert result["routesUpdated"] is False
    assert result["activationApproved"] is False
    assert len(calls) == 6
    assert {operation for operation, _ in calls} == {"head-object", "put-object"}
    for operation, args in calls:
        assert args[args.index("--key") + 1].startswith("releases/" + SHA + "/")
        if operation == "put-object":
            assert args[args.index("--if-none-match") + 1] == "*"
        else:
            assert args[args.index("--checksum-mode") + 1] == "ENABLED"
    assert all(row["versionId"] == "v1" for row in result["artifacts"])


def test_exact_retry_reuses_matching_versions_without_put(packages):
    call, calls = cloud(packages, existing=True)
    module.publish(packages, module.BUCKET, SHA, module.REGION, call)
    assert len(calls) == 4
    assert all(operation == "head-object" for operation, _ in calls)


def test_package_import_smoke_rejects_a_different_runtime_platform(packages, monkeypatch):
    monkeypatch.setattr(module.platform, "system", lambda: "Darwin")
    with pytest.raises(ValueError, match="Linux ARM64 Python 3.14"):
        module.smoke(packages)


def test_package_smoke_imports_from_lambda_style_extracted_directory(packages, monkeypatch):
    calls = []
    monkeypatch.setattr(module.sys, "version_info", (3, 14))
    monkeypatch.setattr(module.platform, "system", lambda: "Linux")
    monkeypatch.setattr(module.platform, "machine", lambda: "aarch64")

    def run(args, *, env, check):
        directory = Path(args[-1])
        assert check is True and directory.is_dir()
        assert directory.suffix != ".zip" and (directory / "app.py").is_file()
        assert env["PYTHONDONTWRITEBYTECODE"] == "1"
        calls.append(directory.name)

    monkeypatch.setattr(module.subprocess, "run", run)
    module.smoke(packages)
    assert len(calls) == 2


def test_concurrent_current_version_change_fails_closed(packages):
    call, calls = cloud(packages, replace=True)
    with pytest.raises(ValueError, match="verification"):
        module.publish(packages, module.BUCKET, SHA, module.REGION, call)
    assert len(calls) == 3


@pytest.mark.parametrize(
    "mutation",
    ["corrupt", "extra", "missing", "duplicate_checksum", "traversal", "no_handler"],
)
def test_entire_scope_is_validated_before_any_aws_call(packages, mutation):
    first = packages / (module.FUNCTIONS[0] + ".zip")
    if mutation == "corrupt":
        first.write_bytes(b"corrupt")
    elif mutation == "extra":
        (packages / "unreviewed.zip").write_bytes(b"content")
    elif mutation == "missing":
        first.unlink()
    elif mutation == "duplicate_checksum":
        manifest = packages / "SHA256SUMS"
        manifest.write_text(manifest.read_text() * 2)
    else:
        with zipfile.ZipFile(first, "w") as archive:
            archive.writestr("../app.py" if mutation == "traversal" else "other.py", "pass")
            if mutation == "traversal":
                archive.writestr("app.py", "pass")
        checksums(packages)

    def no_cloud(*args):
        raise AssertionError("No AWS call allowed")

    with pytest.raises(ValueError):
        module.publish(packages, module.BUCKET, SHA, module.REGION, no_cloud)


@pytest.mark.parametrize(
    "bucket,region,sha",
    [
        ("prod", module.REGION, SHA),
        (module.BUCKET, "us-west-2", SHA),
        (module.BUCKET, module.REGION, "release-V01"),
    ],
)
def test_wrong_target_or_unpinned_source_never_contacts_aws(packages, bucket, region, sha):
    def no_cloud(*args):
        raise AssertionError("No AWS call allowed")

    with pytest.raises(ValueError):
        module.publish(packages, bucket, sha, region, no_cloud)
