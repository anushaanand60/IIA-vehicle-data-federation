"""scripts/validate_registry.py: coverage matrix on success, loud failure on a malformed registry."""
import json
import subprocess
import sys
from pathlib import Path

from mediator.contract import GLOBAL_ATTRIBUTES

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate_registry.py"
MOCK = ROOT / "sources" / "_mock" / "mock_mappings.json"
MOCK_UC6 = ROOT / "sources" / "_mock" / "mock_mappings_uc6.json"


def _run(path: Path) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), str(path)], capture_output=True, text=True, cwd=ROOT)


def _header(stdout: str) -> list[str]:
    return next(line for line in stdout.splitlines() if "global attribute" in line).split()


def test_complete_registry_prints_a_row_per_attribute_and_exits_zero():
    proc = _run(MOCK)
    assert proc.returncode == 0, proc.stderr
    assert _header(proc.stdout)[-4:] == ["REG", "INS", "THEFT", "CAM"]
    lines = proc.stdout.splitlines()
    for attr in GLOBAL_ATTRIBUTES:
        assert any(line.startswith(attr) for line in lines), attr
    assert any(line.startswith("insurance_status (derived)") for line in lines)


def test_uc6_registry_shows_the_new_attribute_covered_by_the_new_source():
    proc = _run(MOCK_UC6)
    assert proc.returncode == 0, proc.stderr
    assert _header(proc.stdout)[-5:] == ["REG", "INS", "THEFT", "CAM", "PUC"]
    puc_row = next(line for line in proc.stdout.splitlines() if line.startswith("puc_expiry")).split()
    assert puc_row[-5:] == [".", ".", ".", ".", "X"]


def test_registry_with_uncovered_attributes_exits_one_and_names_them(tmp_path):
    raw = json.loads(MOCK.read_text(encoding="utf-8"))
    raw["sources"] = [s for s in raw["sources"] if s["source_id"] != "INS"]
    path = tmp_path / "gappy.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    proc = _run(path)
    assert proc.returncode == 1
    assert "UNCOVERED" in proc.stdout and "insurer_name" in proc.stdout and "insurance_status" in proc.stdout


def test_malformed_registry_exits_two_with_the_reason_on_stderr(tmp_path):
    raw = json.loads(MOCK.read_text(encoding="utf-8"))
    raw["sources"][0]["trust"] = "very"
    path = tmp_path / "broken.json"
    path.write_text(json.dumps(raw), encoding="utf-8")
    proc = _run(path)
    assert proc.returncode == 2
    assert "INVALID" in proc.stderr and "REG" in proc.stderr and "trust" in proc.stderr
