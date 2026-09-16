"""Ministry Reports tab: the filed audit log (`REPORT_LOG` in `meta.db`) and its PDFs.

Filing itself happens from Investigate (`mediator.report.file_ministry_report`); this tab only
lists what has already been filed and offers the PDF for download.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import group_label, page_head, panel, section  # noqa: E402
from mediator.catalog import get_query_log  # noqa: E402
from mediator.report import generate_report_pdf, list_reports  # noqa: E402


def render() -> None:
    page_head("Reports and audit log",
              "What was filed with the Ministry of Transportation, and every federated query the "
              "mediator ran on the way there.")

    with panel("rp_filed"):
        section("Ministry audit reports",
                "Every decision an operator chose to file (REPORT_LOG), with the evidence bundle "
                "it rested on and its official PDF.")

        reports = list_reports()
        if not reports:
            st.info(
                "No audit reports have been filed yet. File a report from the Investigate page "
                "to see it here."
            )

        for rep in reports:
            report_id = rep["report_id"]
            with st.expander(
                f"Report MOT-{report_id:06d} — Plate: `{rep['plate']}` | "
                f"Decision: `{rep['decision']}` | Confidence: `{rep['confidence']}`"
            ):
                st.write(f"**Audit Timestamp:** {rep['generated_at']}")
                st.write(f"**Sources Contacted:** {', '.join(rep.get('sources_used', []))}")
                st.write(f"**Rule Fired:** {rep.get('rule_fired') or '—'}")
                risk = rep.get("risk")
                risk_text = f"{risk['value']} ({risk['level']})" if risk else "—"
                st.write(f"**Risk Score:** {risk_text}")
                st.markdown("**Reasons:**")
                for r in rep.get("reasons", []):
                    st.markdown(f"- {r}")

                pdf_file = generate_report_pdf(report_id)
                if os.path.exists(pdf_file):
                    with open(pdf_file, "rb") as pf:
                        st.download_button(
                            label=f"Download Official PDF (MOT-{report_id:06d})",
                            data=pf.read(),
                            file_name=os.path.basename(pdf_file),
                            mime="application/pdf",
                            key=f"rp_dl_{report_id}",
                        )

    with panel("rp_audit"):
        _render_audit_log()


def _render_audit_log() -> None:
    """Task 2.6: every federated query the mediator has run, not only the ones an operator chose
    to file. `QUERY_LOG` (`mediator/catalog.py`) stores the decision and the plan trace metadata
    only -- source_id/status pairs and the verdict -- never the raw rows a source returned."""
    section("Query audit log",
            "Every federated query the mediator has run, not only the ones an operator filed. "
            "Decisions and traces only — no source rows are ever stored, which is the "
            "point of virtual integration.")

    group_label("Filter by plate")
    plate_filter = st.text_input(
        "Plate", value="", key="rp_audit_plate", placeholder="DL05CD9876",
        label_visibility="collapsed",
    ).strip().upper()

    rows = get_query_log(limit=100, plate=plate_filter or None)
    if not rows:
        st.info("No queries have been run yet.")
        return

    table = pd.DataFrame([
        {
            "ts": r["ts"],
            "plate": r["plate"],
            "decision": r.get("decision"),
            "confidence": r.get("confidence"),
            "sources_asked": ", ".join(r.get("sources_asked") or []),
            "statuses": ", ".join(
                f"{sid}:{status}" for sid, status in (r.get("statuses") or {}).items()
            ),
            "elapsed_ms": r.get("elapsed_ms"),
        }
        for r in rows
    ])
    st.dataframe(table, width="stretch", hide_index=True, key="rp_audit_table")


if __name__ == "__main__":
    st.set_page_config(page_title="Ministry Reports", page_icon=":material/gavel:", layout="wide")
    render()
