"""Task 2.2: the Ministry report's evidence bundle.

`mediator/report.py` gains: per-attribute provenance, the SQL sent to every source, camera
evidence, the rule that fired, an explainable risk score, watchlist alerts and a mediator git
commit + timestamp footer. `REPORT_LOG` gains `plan_trace_json`, `risk_json`, `rule_fired`,
`mediator_commit` columns via an additive migration in `mediator.catalog.init_meta_db`.

`mediator.report` imports `META_DB_PATH` *by value* from `mediator.catalog` (`from
mediator.catalog import META_DB_PATH`), so a test must patch both `catalog.META_DB_PATH` and
`report.META_DB_PATH` -- patching only the former silently leaves `report` pointed at the real
`meta.db` (the same gotcha already documented in `tests/test_tabs_render.py`).
"""
from __future__ import annotations

import sqlite3

import pytest

from mediator import catalog, report

try:
    from pypdf import PdfReader
    HAVE_PYPDF = True
except ImportError:  # pragma: no cover - environment without pypdf
    HAVE_PYPDF = False


@pytest.fixture()
def tmp_meta(tmp_path, monkeypatch):
    meta_path = str(tmp_path / "meta.db")
    monkeypatch.setattr(catalog, "META_DB_PATH", meta_path)
    monkeypatch.setattr(report, "META_DB_PATH", meta_path)
    monkeypatch.setattr(report, "_commit_cache", None)
    catalog.init_meta_db()
    return meta_path


def _full_profile(**overrides) -> dict:
    profile = {
        "plate_number": "DL05CD9876",
        "owner_name": "Ravi Kumar",
        "vehicle_make": "Hyundai",
        "vehicle_model": "Creta",
        "vehicle_colour": "White",
        "registration_status": "ACTIVE",
        "insurer_name": "Bajaj Allianz",
        "policy_type": "Comprehensive",
        "insurance_expiry": "2026-07-12",
        "insurance_status": "EXPIRED",
        "stolen_status": "NOT_REPORTED",
        "case_status": "CLOSED",
        "last_seen_location": "MG Road Camera 3",
        "last_seen_time": "2026-09-01T10:15:00",
        "observed_make": "Hyundai",
        "observed_model": "Creta",
        "observed_colour": "White",
        "puc_expiry": "2027-01-01",
        "provenance": {
            "vehicle_make": {"source": "REG", "authority": "OFFICIAL", "trust": 0.95,
                              "fetched_at": "2026-09-16T00:00:00Z"},
            "insurance_expiry": {"source": "INS", "authority": "OFFICIAL", "trust": 0.90,
                                  "fetched_at": "2026-09-16T00:00:01Z"},
            "last_seen_location": {"source": "CAM", "authority": "OBSERVATIONAL", "trust": 0.60,
                                    "fetched_at": "2026-09-16T00:00:02Z"},
        },
        "conflicts": [],
        "source_availability": {"REG": "OK", "INS": "OK", "THEFT": "OK", "CAM": "OK"},
        "decision": "UNINSURED — REPORT",
        "confidence": "HIGH",
        "reasons": ["Insurance policy has expired (expired on 2026-07-12)."],
        "risk": {
            "value": 75,
            "level": "HIGH",
            "factors": [
                {"name": "Decision", "points": 70, "note": "Decision is UNINSURED — REPORT."},
                {"name": "Low source trust", "points": 5, "note": "Average trust is 0.82."},
            ],
        },
        "alerts": [
            {"reason": "Persistent mismatch", "seen_at": "2026-09-01T10:15:00",
             "location": "MG Road Camera 3", "ts": "2026-09-16T00:00:03Z"},
        ],
    }
    profile.update(overrides)
    return profile


def _plan_trace() -> dict:
    return {
        "sources_detail": {
            "REG": {"status": "OK", "row_count": 1, "elapsed_ms": 12},
            "INS": {"status": "OK", "row_count": 1, "elapsed_ms": 20},
            "THEFT": {"status": "OK", "row_count": 0, "elapsed_ms": 8},
            "CAM": {"status": "OK", "row_count": 1, "elapsed_ms": 15,
                    "rows": [{"camera_id": "CAM03", "ocr_confidence": 0.91}]},
        },
        "sqls": {
            "REG": "SELECT registration_no, make, model FROM VEHICLE_REGISTRATION WHERE registration_no = 'DL05CD9876' LIMIT 200",
            "INS": "SELECT vehicle_reg, policy_until FROM POLICY_RECORDS WHERE vehicle_reg = 'DL-05-CD-9876' LIMIT 200",
        },
    }


# --------------------------------------------------------------------- REPORT_LOG columns

def test_report_log_migration_adds_new_columns(tmp_meta):
    columns = [row[1] for row in
               sqlite3.connect(tmp_meta).execute("PRAGMA table_info(REPORT_LOG);").fetchall()]
    for col in ("plan_trace_json", "risk_json", "rule_fired", "mediator_commit"):
        assert col in columns


def test_filing_a_report_populates_the_new_columns(tmp_meta):
    profile = _full_profile()
    plan_trace = _plan_trace()
    report_id = report.file_ministry_report(profile, plan_trace)

    row = sqlite3.connect(tmp_meta).execute(
        "SELECT plan_trace_json, risk_json, rule_fired, mediator_commit FROM REPORT_LOG "
        "WHERE report_id = ?;", (report_id,)
    ).fetchone()
    plan_trace_json, risk_json, rule_fired_col, commit_col = row
    assert "SELECT" in plan_trace_json
    assert '"value": 75' in risk_json or "75" in risk_json
    assert rule_fired_col == "Rule 5: no policy on record, or lapsed >30 days"
    assert commit_col  # "unknown" off-git, a short hash otherwise -- never blank/NULL


def test_migration_is_idempotent_and_preserves_existing_rows(tmp_meta):
    report_id = report.file_ministry_report(_full_profile(), _plan_trace())
    catalog.init_meta_db()  # re-run the migration against an already-migrated db
    catalog.init_meta_db()
    row = report.get_report_by_id(report_id)
    assert row["plate"] == "DL05CD9876"


# --------------------------------------------------------------------------- rule_fired()

@pytest.mark.parametrize("decision,expected_substring", [
    ("STOLEN — ALERT POLICE", "Rule 2"),
    ("SCRAPPED — ALERT POLICE", "Rule 2b"),
    ("UNKNOWN VEHICLE — NOT REGISTERED", "Rule 3b"),
    ("UNREGISTERED / SUSPICIOUS", "Rule 3"),
    ("SUSPICIOUS — POSSIBLE CLONED PLATE", "Rule 4"),
    ("UNINSURED — ADVISORY", "Rule 5a"),
    ("UNINSURED — WARNING", "Rule 5b"),
    ("UNINSURED — REPORT", "Rule 5"),
    ("REGISTRATION INVALID — REPORT", "Rule 6"),
    ("CLEAR", "Rule 7"),
    ("UNDETERMINED", "Rule 1"),
    ("SOME FUTURE VERDICT", "Unmapped"),
])
def test_rule_fired_maps_every_known_decision(decision, expected_substring):
    assert expected_substring in report.rule_fired(decision)


# -------------------------------------------------------------------------- build_story()

def test_build_story_contains_core_facts():
    report_id = 7
    row = {
        "report_id": report_id,
        "plate": "DL05CD9876",
        "decision": "UNINSURED — REPORT",
        "confidence": "HIGH",
        "reasons": ["Insurance policy has expired."],
        "rule_fired": report.rule_fired("UNINSURED — REPORT"),
        "mediator_commit": "abc1234",
        "generated_at": "2026-09-16T00:00:00Z",
        "evidence": _full_profile(),
        "plan_trace": _plan_trace(),
        "risk": _full_profile()["risk"],
        "alerts": _full_profile()["alerts"],
    }
    lines = report.build_story(row)
    text = "\n".join(lines)
    assert "DL05CD9876" in text
    assert "UNINSURED — REPORT" in text
    assert "SELECT" in text
    assert "Risk score:" in text
    assert "75 (HIGH)" in text
    assert "MG Road Camera 3" in text  # camera evidence location
    assert "Persistent mismatch" in text  # watchlist alert


def test_build_story_prints_not_checked_for_none_stolen_status_not_not_reported():
    row = {
        "report_id": 8,
        "plate": "DL09KL3321",
        "decision": "CLEAR",
        "confidence": "MEDIUM",
        "reasons": ["Sources not checked in this query: THEFT."],
        "rule_fired": report.rule_fired("CLEAR"),
        "mediator_commit": "unknown",
        "generated_at": "2026-09-16T00:00:00Z",
        "evidence": _full_profile(stolen_status=None, case_status=None,
                                   source_availability={"REG": "OK", "INS": "OK", "CAM": "OK"}),
        "plan_trace": _plan_trace(),
        "risk": None,
        "alerts": [],
    }
    lines = report.build_story(row)
    text = "\n".join(lines)
    assert "not checked" in text
    assert "NOT_REPORTED" not in text


# ------------------------------------------------------------------------ generate_report_pdf

def test_pdf_contains_plate_decision_sql_risk_and_camera_evidence(tmp_meta):
    report_id = report.file_ministry_report(_full_profile(), _plan_trace())
    pdf_path = report.generate_report_pdf(report_id)

    with open(pdf_path, "rb") as fh:
        pdf_bytes = fh.read()
    assert pdf_bytes.startswith(b"%PDF")

    if HAVE_PYPDF:
        reader = PdfReader(pdf_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "DL05CD9876" in text
        assert "UNINSURED" in text
        assert "SELECT" in text
        assert "Risk" in text
        assert "MG Road Camera" in text


def test_pdf_prints_not_checked_never_not_reported_for_unasked_theft(tmp_meta):
    profile = _full_profile(
        stolen_status=None, case_status=None,
        source_availability={"REG": "OK", "INS": "OK", "CAM": "OK"},
        decision="CLEAR", confidence="MEDIUM",
        reasons=["Sources not checked in this query: THEFT."],
    )
    report_id = report.file_ministry_report(profile, _plan_trace())
    pdf_path = report.generate_report_pdf(report_id)

    if HAVE_PYPDF:
        reader = PdfReader(pdf_path)
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        assert "not checked" in text
        assert "NOT_REPORTED" not in text


def test_list_reports_and_get_report_by_id_expose_new_fields(tmp_meta):
    report_id = report.file_ministry_report(_full_profile(), _plan_trace())

    listed = report.list_reports()
    assert listed[0]["report_id"] == report_id
    assert listed[0]["rule_fired"] == "Rule 5: no policy on record, or lapsed >30 days"
    assert listed[0]["risk"]["value"] == 75

    single = report.get_report_by_id(report_id)
    assert single["plan_trace"]["sqls"]["REG"].startswith("SELECT")
    assert single["risk"]["level"] == "HIGH"
    assert single["mediator_commit"]
