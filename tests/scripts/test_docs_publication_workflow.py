"""Independent SEC341 checks: real local workflow bodies, never service calls."""
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("sec341_scope_independent", ROOT / "scripts/lambda_release_scope.py")
SCOPE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SCOPE)
HEAD = "a" * 40
BASE = "b" * 40


def steps():
    # Standard-library extractor for this reviewed indentation, not a YAML engine.
    text = (ROOT / ".github/workflows/publish.yml").read_text()
    job = re.search(r"(?ms)^  publish:\n(.*?)(?=^  [a-zA-Z][\w-]*:|\Z)", text).group(1)
    found = []
    for match in re.finditer(r"(?ms)^      - name: ([^\n]+)\n(.*?)(?=^      - name:|\Z)", job):
        step = {"name": match.group(1)}
        block = match.group(2)
        for key in ("id", "uses", "if"):
            field = re.search(r"(?m)^        " + key + r": ([^\n]+)$", block)
            if field:
                assert field.group(1) not in {">-", "|"}, "folded guard needs extractor review"
                step[key] = field.group(1)
        script = re.search(r"(?ms)^        run: \|\n((?:          [^\n]*\n|\n)*)", block)
        if script:
            step["run"] = "\n".join(line[10:] if line.startswith("          ") else line for line in script.group(1).splitlines()) + "\n"
        found.append(step)
    assert found and any(s.get("id") == "scope" for s in found)
    return found


def docs_scope():
    result = SCOPE.classify(["docs/sec341-independent.md", "docs/evidence/result.json"])
    assert result not in {"all", "resolver", "contracts", "assessment_manual", "authority_manual", "entitlements"}
    assert isinstance(result, str) and result
    return result


def scope_step():
    return next(s for s in steps() if s.get("id") == "scope")


def guard_allows(condition, scope):
    """Interpret only the finite equality/Boolean grammar of AWS step guards."""
    assert isinstance(condition, str), "AWS/publication step needs an explicit scope guard"
    expression = condition.strip().removeprefix("${{").removesuffix("}}").strip()
    expression = re.sub(r"steps\.scope\.outputs\.value\s*==\s*'([^']+)'", lambda m: str(scope == m[1]), expression)
    expression = re.sub(r"steps\.scope\.outputs\.value\s*!=\s*'([^']+)'", lambda m: str(scope != m[1]), expression)
    expression = expression.replace("&&", " and ").replace("||", " or ")
    assert re.fullmatch(r"[\s()TrueFalsandor]+", expression), "unrecognized guard requires independent review"
    return eval(expression, {"__builtins__": {}}, {})


def run_validator(tmp_path, manifest, *, optimize=False):
    (tmp_path / "dist").mkdir(exist_ok=True)
    (tmp_path / "dist/release-scope.json").write_text(json.dumps(manifest))
    output = tmp_path / "outputs"
    env = dict(os.environ, SOURCE_SHA=HEAD, GITHUB_OUTPUT=str(output), AWS_EC2_METADATA_DISABLED="true")
    if optimize:
        env["PYTHONOPTIMIZE"] = "1"
    else:
        env.pop("PYTHONOPTIMIZE", None)
    result = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", scope_step()["run"]], cwd=tmp_path, env=env, text=True, capture_output=True)
    return result, output.read_text() if output.exists() else ""


def test_docs_scope_real_workflow_validator_and_every_aws_step_skip(tmp_path):
    scope = docs_scope()
    result, outputs = run_validator(tmp_path, {"schemaVersion": 1, "headSha": HEAD, "baseSha": BASE, "scope": scope})
    assert result.returncode == 0, result.stderr
    assert f"value={scope}\n" in outputs
    hazardous = []
    for step in steps():
        body = step.get("run", "")
        if ("aws-actions/configure-aws-credentials" in step.get("uses", "") or
                re.search(r"\baws\s+(?:sts|s3|s3api|lambda)\b", body) or
                "scripts/upload_lambda_zips.sh" in body or "scripts/publish_url_resolver.py" in body):
            hazardous.append(step["name"])
            assert not guard_allows(step.get("if"), scope), step["name"]
    assert len(hazardous) >= 4, "review must cover configure, account/bucket, resolver and broad upload"
    assert not any("configure-aws-credentials" in s.get("uses", "") for s in steps()[:steps().index(scope_step())])


@pytest.mark.parametrize("manifest", [
    {"schemaVersion": 1, "headSha": "c" * 40, "baseSha": BASE, "scope": "all"},
    {"schemaVersion": 1, "headSha": HEAD, "baseSha": BASE, "scope": "unknown"},
    {"schemaVersion": 2, "headSha": HEAD, "baseSha": BASE, "scope": "all"},
    {"schemaVersion": 1, "headSha": HEAD, "baseSha": "not-a-sha", "scope": "all"},
])
def test_scope_validator_rejects_invalid_manifests_before_output(tmp_path, manifest):
    result, outputs = run_validator(tmp_path, manifest)
    assert result.returncode != 0
    assert not outputs


def test_docs_summary_explicitly_reports_no_credentials_or_artifact_upload(tmp_path):
    scope = docs_scope()
    summary = next(s for s in steps() if s["name"] == "Write release summary")
    body = re.sub(r"\$\{\{.*?\}\}", "dev", summary["run"])
    destination = tmp_path / "summary"
    env = dict(os.environ, RELEASE_SCOPE=scope, RELEASE_ID=HEAD, SOURCE_RUN_ID="123", ARTIFACT_BUCKET="not-a-real-bucket", GITHUB_STEP_SUMMARY=str(destination))
    completed = subprocess.run(["bash", "-e", "-o", "pipefail", "-c", body], env=env, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    text = destination.read_text()
    assert scope in text
    assert "s3://" not in text
    assert "credentials" in text.lower() and ("skip" in text.lower() or "no aws" in text.lower())
    assert "upload" in text.lower() and ("skip" in text.lower() or "no upload" in text.lower())
    assert "No scope deploys or updates a Lambda runtime or alias" in text


def test_docs_classification_cli_uses_actual_nonempty_git_diff(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=SEC341 Local Test", "-c", "user.email=sec341@example.invalid", *args], cwd=repo, text=True).strip()
    git("init", "--quiet")
    (repo / "seed").write_text("seed")
    git("add", "seed")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repo / "docs").mkdir()
    (repo / "docs/only.md").write_text("docs-only")
    git("add", "docs")
    git("commit", "--quiet", "-m", "docs")
    head = git("rev-parse", "HEAD")
    output = tmp_path / "manifest.json"
    subprocess.run([sys.executable, "-B", str(ROOT / "scripts/lambda_release_scope.py"), "--base", base, "--head", head, "--output", str(output)], cwd=repo, check=True)
    assert json.loads(output.read_text())["scope"] == docs_scope()
    subprocess.run([sys.executable, "-B", str(ROOT / "scripts/lambda_release_scope.py"), "--base", head, "--head", head, "--output", str(output)], cwd=repo, check=True)
    assert json.loads(output.read_text())["scope"] == "all"


@pytest.mark.parametrize("paths", [[], ["README.md"], ["unknown.bin"], ["docs-only/not-docs.md"], ["docs/change.md", "scripts/build_lambda_zip.sh"], ["docs/change.md", "src/conversation_analysis/app.py"]])
def test_docs_cannot_hide_empty_unknown_or_package_changes(paths):
    assert SCOPE.classify(paths) == "all"


@pytest.mark.parametrize("paths,expected", [
    (["src/url_redirect_resolver/app.py", "docs/url-resolver-review.md"], "resolver"),
    (["src/url_assessment/app.py", "docs/url-assessment-review.md"], "assessment_manual"),
    (["src/shared_check_authority/core.py", "docs/v1-check-authority.md"], "authority_manual"),
    (["contracts/url-assessment/v1-draft/contract-set.json", "docs/GITHUB_PUBLISHING.md"], "contracts"),
])
def test_specialized_runtime_and_contract_scopes_remain_unchanged(paths, expected):
    assert SCOPE.classify(paths) == expected


def test_docs_scope_also_skips_aws_environment_requirements():
    all_steps = steps()
    environment = next(s for s in all_steps if s["name"] == "Validate environment configuration")
    assert all_steps.index(scope_step()) < all_steps.index(environment)
    assert not guard_allows(environment.get("if"), docs_scope())
    assert guard_allows(environment.get("if"), "all")
    assert guard_allows(environment.get("if"), "resolver")


@pytest.mark.parametrize("old_path,new_path", [
    ("src/conversation_analysis/app.py", "docs/moved-app.py"),
    ("docs/moved-app.py", "src/conversation_analysis/app.py"),
])
def test_real_git_move_across_docs_boundary_never_becomes_docs_only(tmp_path, old_path, new_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    def git(*args):
        return subprocess.check_output(["git", "-c", "user.name=SEC341 Local Test", "-c", "user.email=sec341@example.invalid", *args], cwd=repo, text=True).strip()
    git("init", "--quiet")
    original = repo / old_path
    original.parent.mkdir(parents=True)
    original.write_text("# exact content retained so Git recognizes the rename\n" * 12)
    git("add", ".")
    git("commit", "--quiet", "-m", "base")
    base = git("rev-parse", "HEAD")
    (repo / new_path).parent.mkdir(parents=True, exist_ok=True)
    git("mv", old_path, new_path)
    git("commit", "--quiet", "-m", "move boundary")
    head = git("rev-parse", "HEAD")
    assert git("diff", "--name-status", "-M", base, head).startswith("R100")
    output = tmp_path / "manifest.json"
    subprocess.run([sys.executable, "-B", str(ROOT / "scripts/lambda_release_scope.py"), "--base", base, "--head", head, "--output", str(output)], cwd=repo, check=True)
    assert json.loads(output.read_text())["scope"] == "all"
