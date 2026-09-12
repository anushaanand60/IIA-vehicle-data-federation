"""
Ministry Report Generator & Audit Logger.
Materializes decision audit trail into REPORT_LOG in meta.db and renders
official one-page PDF audit reports using ReportLab.
"""

import os
import json
import sqlite3
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional
from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib import colors
from mediator.catalog import META_DB_PATH

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

def file_ministry_report(profile: Dict[str, Any], plan_trace: Optional[Dict[str, Any]] = None) -> int:
    conn = sqlite3.connect(META_DB_PATH)
    cur = conn.cursor()
    now_ts = datetime.now(timezone.utc).isoformat()
    plate = profile.get("plate_number", "UNKNOWN")
    decision = profile.get("decision", "UNDETERMINED")
    confidence = profile.get("confidence", "LOW")
    reasons_json = json.dumps(profile.get("reasons", []))
    evidence_json = json.dumps(profile)
    sources_used = json.dumps(list(profile.get("source_availability", {}).keys()))

    cur.execute("""
    INSERT INTO REPORT_LOG 
    (plate, decision, confidence, reasons, evidence_json, sources_used, generated_at)
    VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (plate, decision, confidence, reasons_json, evidence_json, sources_used, now_ts))

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
    bold_style = ParagraphStyle("BoldCustom", parent=styles["Normal"], fontSize=8, leading=10, fontName="Helvetica-Bold")

    elements = []

    # Title Banner
    elements.append(Paragraph("MINISTRY OF TRANSPORTATION & HIGHWAYS — AUDIT REPORT", title_style))
    elements.append(Paragraph("Federated Intelligent Vehicle Enforcement & Compliance Investigation System", subtitle_style))
    elements.append(HRFlowable(width="100%", thickness=1.5, color=colors.HexColor("#2B6CB0"), spaceAfter=8))

    # Meta Overview Box
    meta_data = [
        [
            Paragraph("<b>Report ID:</b>", body_style), Paragraph(f"MOT-{report_id:06d}", bold_style),
            Paragraph("<b>Audit Date:</b>", body_style), Paragraph(rep.get("generated_at", "")[:19], body_style)
        ],
        [
            Paragraph("<b>Target Plate:</b>", body_style), Paragraph(f"<b>{plate}</b>", bold_style),
            Paragraph("<b>Sources Contacted:</b>", body_style), Paragraph(", ".join(rep.get("sources_used", [])), body_style)
        ],
        [
            Paragraph("<b>Compliance Decision:</b>", body_style),
            Paragraph(f"<font color='{'red' if 'REPORT' in rep['decision'] or 'ALERT' in rep['decision'] else 'green'}'><b>{rep['decision']}</b></font>", bold_style),
            Paragraph("<b>Confidence Level:</b>", body_style), Paragraph(f"<b>{rep['confidence']}</b>", bold_style)
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

    # Vehicle Profile Table
    ev = rep.get("evidence", {})
    elements.append(Paragraph("INTEGRATED VEHICLE PROFILE", header_style))

    prof_data = [
        [Paragraph("Owner Name:", bold_style), Paragraph(str(ev.get("owner_name") or "N/A"), body_style),
         Paragraph("Reg. Status:", bold_style), Paragraph(str(ev.get("registration_status") or "N/A"), body_style)],
        [Paragraph("Make & Model:", bold_style), Paragraph(f"{ev.get('vehicle_make') or 'N/A'} {ev.get('vehicle_model') or ''}", body_style),
         Paragraph("Official Colour:", bold_style), Paragraph(str(ev.get("vehicle_colour") or "N/A"), body_style)],
        [Paragraph("Insurer Name:", bold_style), Paragraph(str(ev.get("insurer_name") or "None on Record"), body_style),
         Paragraph("Insurance Status:", bold_style), Paragraph(f"<b>{ev.get('insurance_status') or 'UNKNOWN'}</b>", body_style)],
        [Paragraph("Policy Expiry:", bold_style), Paragraph(str(ev.get("insurance_expiry") or "N/A"), body_style),
         Paragraph("Policy Type:", bold_style), Paragraph(str(ev.get("policy_type") or "N/A"), body_style)],
        [Paragraph("Theft / Case Status:", bold_style), Paragraph(f"{ev.get('stolen_status') or 'NOT_REPORTED'} ({ev.get('case_status') or 'N/A'})", body_style),
         Paragraph("Camera Sighting:", bold_style), Paragraph(f"{ev.get('last_seen_location') or 'None'} ({str(ev.get('last_seen_time') or '')[:16]})", body_style)],
        [Paragraph("Observed Vehicle:", bold_style), Paragraph(f"{ev.get('observed_make') or ''} {ev.get('observed_model') or ''} ({ev.get('observed_colour') or ''})", body_style),
         Paragraph("PUC Certificate:", bold_style), Paragraph(str(ev.get("puc_expiry") or "Not Checked"), body_style)]
    ]
    t_prof = Table(prof_data, colWidths=[110, 160, 110, 160])
    t_prof.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F7FAFC")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#F7FAFC")),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    elements.append(t_prof)
    elements.append(Spacer(1, 10))

    # Conflicts Section if any
    conflicts = ev.get("conflicts", [])
    if conflicts:
        elements.append(Paragraph("DETECTED CONFLICTS & DISCREPANCIES", header_style))
        conf_rows = [[Paragraph("Attribute", bold_style), Paragraph("Conflicting Source Values", bold_style), Paragraph("Resolution Policy", bold_style)]]
        for c in conflicts:
            vals_str = ", ".join(f"{k}: {v}" for k, v in c.get("values_by_source", {}).items())
            conf_rows.append([
                Paragraph(c.get("attribute", ""), body_style),
                Paragraph(vals_str, body_style),
                Paragraph(c.get("resolution", ""), body_style)
            ])
        t_conf = Table(conf_rows, colWidths=[110, 210, 220])
        t_conf.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#FEB2B2")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#FFF5F5")),
            ("TOPPADDING", (0, 0), (-1, -1), 3),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ]))
        elements.append(t_conf)
        elements.append(Spacer(1, 10))

    # Reasons Section
    elements.append(Paragraph("ENFORCEMENT JUSTIFICATION & REASONS", header_style))
    for r in rep.get("reasons", []):
        elements.append(Paragraph(f"• {r}", body_style))
    elements.append(Spacer(1, 10))

    # Provenance Summary Table
    elements.append(Paragraph("DATA PROVENANCE & SOURCE AUDIT", header_style))
    prov_rows = [[Paragraph("Attribute", bold_style), Paragraph("Source Authority", bold_style), Paragraph("Trust", bold_style), Paragraph("Fetched At", bold_style)]]
    for attr, pinfo in ev.get("provenance", {}).items():
        prov_rows.append([
            Paragraph(attr, body_style),
            Paragraph(f"{pinfo.get('source')} ({pinfo.get('authority')})", body_style),
            Paragraph(str(pinfo.get("trust")), body_style),
            Paragraph(str(pinfo.get("fetched_at", ""))[:19], body_style)
        ])
    t_prov = Table(prov_rows, colWidths=[130, 170, 70, 170])
    t_prov.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#CBD5E0")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#EDF2F7")),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    elements.append(t_prov)
    elements.append(Spacer(1, 14))

    # Signature block
    sig_data = [
        [Paragraph("<b>Audit Verified By:</b> Federated Mediator Engine v2.0", body_style),
         Paragraph("<b>Digital Seal:</b> VALIDATED-OFFICIAL-RECORD", body_style)]
    ]
    t_sig = Table(sig_data, colWidths=[270, 270])
    elements.append(t_sig)

    doc.build(elements)
    return output_path
