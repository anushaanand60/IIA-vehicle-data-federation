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

import streamlit as st  # noqa: E402

from mediator.report import generate_report_pdf, list_reports  # noqa: E402


def render() -> None:
    st.subheader("Ministry of Transportation Audit Reports Log (`REPORT_LOG`)")

    reports = list_reports()
    if not reports:
        st.info(
            "No audit reports have been filed yet. File a report from the Investigate tab to "
            "see it here."
        )
        return

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


if __name__ == "__main__":
    st.set_page_config(page_title="Ministry Reports", page_icon=":material/gavel:", layout="wide")
    render()
