"""Keep build and immutable-publication inventories aligned with handlers."""

from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[2]


def _shell_array(path: Path, name: str) -> list[str]:
    source = path.read_text(encoding="utf-8")
    match = re.search(rf"^{name}=\((.*?)^\)", source, re.MULTILINE | re.DOTALL)
    assert match is not None, f"missing {name} array in {path}"
    return re.findall(r"[a-z][a-z0-9_]+", match.group(1))


def test_build_and_upload_cover_every_lambda_handler_once():
    handlers = {
        path.parent.name for path in (ROOT / "src").glob("*/app.py")
    }
    build = _shell_array(ROOT / "scripts/build_lambda_zip.sh", "FUNCTIONS")
    required = _shell_array(
        ROOT / "scripts/upload_lambda_zips.sh", "REQUIRED_FUNCTIONS"
    )
    optional = _shell_array(
        ROOT / "scripts/upload_lambda_zips.sh", "OPTIONAL_FUNCTIONS"
    )

    assert len(build) == len(set(build))
    assert len(required + optional) == len(set(required + optional))
    assert set(build) == handlers
    assert set(required + optional) == handlers
