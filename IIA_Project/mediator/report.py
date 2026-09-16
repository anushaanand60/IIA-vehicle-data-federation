"""
Ministry Report Generator & Audit Logger.
Materializes decision audit trail into REPORT_LOG in meta.db and renders an official
"evidence bundle" PDF using ReportLab: decision + rule fired + risk, per-attribute
provenance, the SQL actually sent to every source, camera evidence and any watchlist
alerts, closed out with the mediator's own git commit for traceability (Task 2.2).

`build_story(report_row) -> list[str]` is the single source of truth for *what* the
report says -- both `generate_report_pdf` and the test suite read it, so a "not checked"
vs "NOT_REPORTED" bug can only exist in one place. It never renders layout (fonts, tables,
colours) -- that is `generate_report_pdf`'s job -- so it can be tested without ReportLab.
"""

import os
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from xml.sax.saxutils import escape as _xml_escape
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from mediator.catalog import META_DB_PATH

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# A value the mediator never actually asked about (an unasked source, an absent camera
# sighting, ...) must never be printed as if it were a real answer such as "NOT_REPORTED"
# or "NONE" -- that would claim a fact nobody checked (the honesty rule from Task 3.1).
NOT_CHECKED = "not checked"


def _nc(value: Any) -> Any:
    """`value`, or the honest placeholder when there is nothing to report."""
    return value if value not in (None, "") else NOT_CHECKED


# Ordered, longest/most-specific decision string first, mirroring mediator/decide.py's own
# ordered-rules structure and mediator/risk.py's BASE_RULES pattern: a decision string this
# table has never seen (a future Task adds a new verdict) degrades to "unmapped" instead of
# raising, rather than mis-attributing it to the wrong rule.
_RULE_FIRED_RULES: Tuple[Tuple[str, str], ...] = (
    ("UNINSURED — ADVISORY", "Rule 5a: insurance lapsed 1-15 days (advisory)"),
    ("UNINSURED — WARNING", "Rule 5b: insurance lapsed 16-30 days (warning)"),
    ("UNINSURED — REPORT", "Rule 5: no policy on record, or lapsed >30 days"),
    ("STOLEN", "Rule 2: reported stolen and police case is OPEN"),
    ("SCRAPPED", "Rule 2b: vehicle officially scrapped/shredded"),
    ("UNKNOWN VEHICLE", "Rule 3b: no record for this plate in any asked source"),
    ("UNREGISTERED", "Rule 3: camera-sighted but no registration record"),
    ("SUSPICIOUS", "Rule 4: visual identity mismatch (possible cloned plate)"),
    ("REGISTRATION INVALID", "Rule 6: registration status is not ACTIVE"),
    ("CLEAR", "Rule 7: all checked sources are clear"),
    ("UNDETERMINED", "Rule 1: a required source is unavailable"),
)


def rule_fired(decision: Optional[str]) -> str:
    """Name of the decision-engine rule that produced `decision`, for the audit trail.

    Derived from the decision string rather than imported from `mediator/decide.py`: that
    module is owned elsewhere, and the mapping only needs the string it already returns.
    """
    text = (decision or "").upper()
    for needle, name in _RULE_FIRED_RULES:
        if needle in text:
            return name
    return "Unmapped decision rule"


_commit_cache: Optional[str] = None


def mediator_commit() -> str:
    """Short git commit of the mediator at report time, cached for the process lifetime.

    "unknown" whenever git is absent, this isn't a git checkout, or the call errors -- a
    report must still file even off a laptop with no git installed.
    """
    global _commit_cache
    if _commit_cache is not None:
        return _commit_cache
    repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_root, capture_output=True, text=True, timeout=3, check=True,
        )
        commit = result.stdout.strip() or "unknown"
    except Exception:
        commit = "unknown"
    _commit_cache = commit
    return commit


def _camera_evidence(profile: Dict[str, Any], plan_trace: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Camera evidence for the bundle, or None when CAM was not asked or did not answer.

    `last_seen_*`/`observed_*` come from the integrated profile (global attributes); camera
    id and OCR confidence are not global attributes, so they are read, best-effort, straight
    off the raw row the executor kept in `plan_trace["sources_detail"]["CAM"]["rows"]` --
    still raw source data, never transformed here, and never required for the profile itself.
    """
    if (profile.get("source_availability") or {}).get("CAM") != "OK":
        return None
    cam_detail = (plan_trace or {}).get("sources_detail", {}).get("CAM", {}) or {}
    raw_rows = cam_detail.get("rows") or []
    raw_row = raw_rows[0] if raw_rows else {}

    def pick(*names: str) -> Any:
        for name in names:
            if raw_row.get(name) is not None:
                return raw_row[name]
        return None

    return {
        "location": _nc(profile.get("last_seen_location")),
        "captured_at": _nc(profile.get("last_seen_time")),
        "observed_make": _nc(profile.get("observed_make")),
        "observed_model": _nc(profile.get("observed_model")),
        "observed_colour": _nc(profile.get("observed_colour")),
        "camera_id": _nc(pick("camera_id", "CAMERAS__camera_id")),
        "ocr_confidence": _nc(pick("ocr_confidence", "PLATE_CAPTURES__ocr_confidence")),
    }


def build_story(report_row: Dict[str, Any]) -> List[str]:
    """The evidence bundle's content, as plain text lines -- one fact per line.

    `report_row` is the shape `get_report_by_id` returns (or an equivalent dict a test
    builds directly): `evidence` (the integrated profile), `plan_trace`, `risk`,
    `rule_fired`, `mediator_commit`, plus the flat `plate`/`decision`/`confidence`/
    `reasons`/`generated_at`/`report_id` columns. Testable on its own, with no ReportLab or
    PDF parser needed, which is how the "not checked" honesty rule gets pinned down exactly.
    """
    ev = report_row.get("evidence", {}) or {}
    plan_trace = report_row.get("plan_trace", {}) or {}
    risk = report_row.get("risk") or ev.get("risk") or {}
    alerts = report_row.get("alerts") or ev.get("alerts") or []

    lines: List[str] = []
    lines.append(f"Report ID: MOT-{int(report_row.get('report_id') or 0):06d}")
    lines.append(f"Plate: {report_row.get('plate')}")
    lines.append(f"Decision: {report_row.get('decision')} (Confidence: {report_row.get('confidence')})")
    lines.append(f"Rule fired: {report_row.get('rule_fired') or rule_fired(report_row.get('decision'))}")

    lines.append("Reasons:")
    for r in report_row.get("reasons", []) or []:
        lines.append(f"- {r}")

    lines.append("Risk score:")
    if risk:
        lines.append(f"- Value: {risk.get('value')} ({risk.get('level')})")
        for factor in risk.get("factors", []) or []:
            lines.append(f"  - {factor.get('name')}: +{factor.get('points')} pts -- {factor.get('note')}")
    else:
        lines.append(f"- {NOT_CHECKED} (no risk score attached to this profile)")

    lines.append("Vehicle profile:")
    lines.append(f"- Owner: {_nc(ev.get('owner_name'))}")
    lines.append(f"- Make/Model: {_nc(ev.get('vehicle_make'))} {ev.get('vehicle_model') or ''}".rstrip())
    lines.append(f"- Colour: {_nc(ev.get('vehicle_colour'))}")
    lines.append(f"- Registration status: {_nc(ev.get('registration_status'))}")
    lines.append(f"- Insurer: {_nc(ev.get('insurer_name'))}")
    lines.append(f"- Insurance status: {_nc(ev.get('insurance_status'))}")
    lines.append(f"- Policy expiry: {_nc(ev.get('insurance_expiry'))}")
    lines.append(f"- Policy type: {_nc(ev.get('policy_type'))}")
    lines.append(f"- Theft / case status: {_nc(ev.get('stolen_status'))} ({_nc(ev.get('case_status'))})")
    lines.append(f"- PUC certificate: {_nc(ev.get('puc_expiry'))}")

    conflicts = ev.get("conflicts") or []
    if conflicts:
        lines.append("Conflicts:")
        for c in conflicts:
            vals = ", ".join(f"{k}: {v}" for k, v in c.get("values_by_source", {}).items())
            lines.append(f"- {c.get('attribute')}: {vals} -- {c.get('resolution')}")

    lines.append("Provenance:")
    provenance = ev.get("provenance") or {}
    if provenance:
        for attr, pinfo in provenance.items():
            lines.append(
                f"- {attr}: source={pinfo.get('source')} authority={pinfo.get('authority')} "
                f"trust={pinfo.get('trust')} fetched_at={pinfo.get('fetched_at')}"
            )
    else:
        lines.append(f"- {NOT_CHECKED}")

    lines.append("Sources contacted:")
    sources_detail = plan_trace.get("sources_detail", {}) or {}
    sqls = plan_trace.get("sqls", {}) or {}
    all_source_ids = sorted(set(sources_detail) | set(sqls))
    if all_source_ids:
        for source_id in all_source_ids:
            detail = sources_detail.get(source_id, {}) or {}
            lines.append(
                f"- {source_id}: status={detail.get('status', 'NOT ASKED')} "
                f"rows={detail.get('row_count', '-')} elapsed_ms={detail.get('elapsed_ms', '-')}"
            )
            sql = sqls.get(source_id)
            if sql:
                lines.append(f"  SQL: {sql}")
    else:
        lines.append(f"- {NOT_CHECKED}")

    cam = _camera_evidence(ev, plan_trace)
    if cam:
        lines.append("Camera evidence:")
        lines.append(f"- Location: {cam['location']}")
        lines.append(f"- Captured at: {cam['captured_at']}")
        lines.append(
            f"- Observed vehicle: {cam['observed_make']} {cam['observed_model']} "
            f"({cam['observed_colour']})"
        )
        lines.append(f"- Camera ID: {cam['camera_id']}")
        lines.append(f"- OCR confidence: {cam['ocr_confidence']}")

    if alerts:
        lines.append("Watchlist alerts:")
        for a in alerts:
            lines.append(
                f"- {a.get('reason')} (seen_at={_nc(a.get('seen_at'))}, "
                f"location={_nc(a.get('location'))}, raised={a.get('ts')})"
            )

    lines.append(f"Mediator commit: {report_row.get('mediator_commit') or NOT_CHECKED}")
    lines.append(f"Generated at: {report_row.get('generated_at')}")
    return lines


def file_ministry_report(profile: Dict[str, Any], plan_trace: Optional[Dict[str, Any]] = None) -> int:
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    now_ts = datetime.now(timezone.utc).isoformat()
    plate = profile.get("plate_number", "UNKNOWN")
    decision = profile.get("decision", "UNDETERMINED")
    confidence = profile.get("confidence", "LOW")
    reasons_json = json.dumps(profile.get("reasons", []))
    evidence_json = json.dumps(profile, default=str)
    sources_used = json.dumps(list(profile.get("source_availability", {}).keys()))
    plan_trace = plan_trace or {}
    plan_trace_json = json.dumps(plan_trace, default=str)
    risk_json = json.dumps(profile.get("risk") or {}, default=str)
    fired = rule_fired(decision)
    commit = mediator_commit()

    cur.execute("""
    INSERT INTO REPORT_LOG
    (plate, decision, confidence, reasons, evidence_json, sources_used, generated_at,
     plan_trace_json, risk_json, rule_fired, mediator_commit)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (plate, decision, confidence, reasons_json, evidence_json, sources_used, now_ts,
          plan_trace_json, risk_json, fired, commit))

    report_id = cur.lastrowid
    conn.commit()
    conn.close()
    return report_id

def list_reports() -> List[Dict[str, Any]]:
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM REPORT_LOG ORDER BY report_id DESC;")
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    for r in rows:
        r["reasons"] = json.loads(r["reasons"]) if r.get("reasons") else []
        r["sources_used"] = json.loads(r["sources_used"]) if r.get("sources_used") else []
        r["risk"] = json.loads(r["risk_json"]) if r.get("risk_json") else None
    return rows

def get_report_by_id(report_id: int) -> Optional[Dict[str, Any]]:
    conn = sqlite3.connect(META_DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    cur.execute("SELECT * FROM REPORT_LOG WHERE report_id = ?;", (report_id,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    d["reasons"] = json.loads(d["reasons"]) if d.get("reasons") else []
    d["evidence"] = json.loads(d["evidence_json"]) if d.get("evidence_json") else {}
    d["sources_used"] = json.loads(d["sources_used"]) if d.get("sources_used") else []
    d["plan_trace"] = json.loads(d["plan_trace_json"]) if d.get("plan_trace_json") else {}
    d["risk"] = json.loads(d["risk_json"]) if d.get("risk_json") else None
    d["alerts"] = d["evidence"].get("alerts") or []
    return d

def generate_report_pdf(report_id: int, output_path: Optional[str] = None) -> str:
    rep = get_report_by_id(report_id)
    if not rep:
        raise ValueError(f"Report ID {report_id} not found.")

    plate = rep["plate"]
    if not output_path:
        output_path = os.path.join(REPORTS_DIR, f"ministry_report_{report_id}_{plate}.pdf")

    doc = SimpleDocTemplate(
        output_path,
        pagesize=letter,
        rightMargin=36,
        leftMargin=36,
        topMargin=36,
        bottomMargin=36
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "MinistryTitle",
        parent=styles["Heading1"],
        fontSize=14,
        leading=16,
        textColor=colors.HexColor("#1A365D"),
        alignment=1, # Center
        spaceAfter=4
    )
    subtitle_style = ParagraphStyle(
        "MinistrySubtitle",
        parent=styles["Normal"],
        fontSize=9,
        leading=11,
        textColor=colors.HexColor("#4A5568"),
        alignment=1,
        spaceAfter=12
    )
    header_style = ParagraphStyle(
        "SectionHead",
        parent=styles["Heading3"],
        fontSize=11,
        leading=13,
        textColor=colors.HexColor("#2B6CB0"),
        spaceBefore=6,
        spaceAfter=4
    )
    body_style = ParagraphStyle("BodyTextCustom", parent=styles["Normal"], fontSize=8, leading=10)
    mono_style = ParagraphStyle("MonoCustom", parent=styles["Normal"], fontName="Courier",
                                 fontSize=7, leading=9, leftIndent=12)

    elements = []

    # Title Banner
    elements.append(Paragraph("MINISTRY OF TRANSPORTATION & HIGHWAYS — EVIDENCE BUNDLE", title_style))
    elements.append(Paragraph("Federated Intelligent Vehicle Enforcement & Compliance Investigation System", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2B6CB0"), spaceAfter=8))

    # Meta Overview Box
    meta_data = [
        [
            Paragraph("<b>Report ID:</b>", body_style), Paragraph(f"MOT-{report_id:06d}", body_style),
            Paragraph("<b>Audit Date:</b>", body_style), Paragraph(str(rep.get("generated_at", ""))[:19], body_style)
        ],
        [
            Paragraph("<b>Target Plate:</b>", body_style), Paragraph(f"<b>{_xml_escape(str(plate))}</b>", body_style),
            Paragraph("<b>Sources Contacted:</b>", body_style), Paragraph(", ".join(rep.get("sources_used", [])), body_style)
        ],
        [
            Paragraph("<b>Compliance Decision:</b>", body_style),
            Paragraph(f"<font color='{'red' if 'REPORT' in rep['decision'] or 'ALERT' in rep['decision'] else 'green'}'><b>{_xml_escape(str(rep['decision']))}</b></font>", body_style),
            Paragraph("<b>Confidence Level:</b>", body_style), Paragraph(f"<b>{rep['confidence']}</b>", body_style)
        ],
        [
            Paragraph("<b>Rule Fired:</b>", body_style),
            Paragraph(rep.get("rule_fired") or rule_fired(rep.get("decision")), body_style),
            Paragraph("<b>Risk Score:</b>", body_style),
            Paragraph(
                f"{rep['risk']['value']} ({rep['risk']['level']})" if rep.get("risk") else NOT_CHECKED,
                body_style
            )
        ]
    ]
    t_meta = Table(meta_data, colWidths=[90, 180, 110, 160])
    t_meta.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#EDF2F7")),
        ("BOX", (0, 0), (-1, -1), 1, colors.HexColor("#CBD5E0")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("INNERGRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#E2E8F0")),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elements.append(t_meta)
    elements.append(Spacer(1, 10))

    # Body: driven entirely by build_story so the PDF and the tested text agree by
    # construction. Section headers (lines ending in ":") get the header style, a
    # "  SQL: ..." line gets monospace so the exact statement sent is legible, everything
    # else is a plain body paragraph.
    for line in build_story(rep):
        safe = _xml_escape(line)
        if line.startswith("  SQL:"):
            elements.append(Paragraph(safe.strip(), mono_style))
        elif line.endswith(":") and not line.startswith(("-", " ")):
            elements.append(Paragraph(f"<b>{safe.upper()}</b>", header_style))
        elif line.startswith("- ") or line.startswith("  - "):
            elements.append(Paragraph(safe, body_style))
        else:
            elements.append(Paragraph(f"<b>{safe}</b>", body_style))
    elements.append(Spacer(1, 14))

    # Signature block
    sig_data = [
        [Paragraph("<b>Audit Verified By:</b> Federated Mediator Engine", body_style),
         Paragraph(f"<b>Mediator Commit:</b> {rep.get('mediator_commit') or NOT_CHECKED}", body_style)]
    ]
    t_sig = Table(sig_data, colWidths=[270, 270])
    elements.append(t_sig)
    elements.append(Paragraph(
        f"Generated at {datetime.now(timezone.utc).isoformat()}Z | Digital Seal: VALIDATED-OFFICIAL-RECORD",
        subtitle_style
    ))

    doc.build(elements)
    return output_path
