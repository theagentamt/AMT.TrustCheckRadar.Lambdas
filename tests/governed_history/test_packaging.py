import json
from pathlib import Path
import subprocess
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]


def test_package_can_age_message_evidence_without_host_source(tmp_path):
    subprocess.run([
        "bash", str(ROOT / "scripts/build_lambda_zip.sh"), "--function", "governed_history",
        "--skip-dependencies", "--output-dir", str(tmp_path),
    ], cwd=ROOT, check=True, capture_output=True)
    extracted = tmp_path / "extracted"
    with zipfile.ZipFile(tmp_path / "governed_history.zip") as archive:
        archive.extractall(extracted)
    fixture = json.loads((ROOT / "contracts/governed-history/1.0.0-candidate.1/fixtures.json").read_text())
    message = next(item for item in fixture["fixtures"][0]["response"]["items"]
                   if item["resultType"] == "message")
    message.pop("presentation")
    evidence = message["outcome"]["evidence"]
    if evidence:
        evidence[0].update(freshness="current", observedAt="2027-01-15T08:00:00Z",
                           validUntil="2027-01-15T08:00:20Z", threatTypes=["MALWARE"], outcome="match")
    script = (
        "import json,sys;sys.path.insert(0,sys.argv[1]);"
        "from shared_governed_history import present_projection;"
        "value=present_projection(json.loads(sys.argv[2]),1800000030);"
        "assert value['outcome']['evidence'][0]['freshness']=='current';"
        "assert value['presentation']['freshnessStatus']=='historical'"
    )
    subprocess.run([sys.executable, "-c", script, str(extracted), json.dumps(message)],
                   cwd=tmp_path, check=True, env={"PATH": "/usr/bin:/bin"})
