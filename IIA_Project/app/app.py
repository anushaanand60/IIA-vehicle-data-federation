"""
Streamlit GUI for Federated Mediator / GAV Integration System
Identifying Uninsured Vehicles Across Autonomous Heterogeneous Databases.
5 Tabs:
  1. Investigate
  2. Plan Trace
  3. Matcher & Heatmap
  4. Catalog & Registry
  5. Ministry Audit Reports
"""

import sys
import os
import json
import pandas as pd
import plotly.express as px
import streamlit as st

# Ensure project root is in path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sources.server_manager import get_cluster
from mediator.core import run_global_query
from mediator.catalog import (
    get_source_catalog, get_all_mappings, get_mappings_for_source,
    save_mapping, register_source
)
from mediator.matcher import match_source_schema
from mediator.executor import check_all_sources_health
from mediator.report import file_ministry_report, generate_report_pdf, list_reports, get_report_by_id

# Page configuration
st.set_page_config(
    page_title="Mediator: Uninsured Vehicle Identification",
    page_icon="🚗",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Auto-start source wrappers in background if not already started
@st.cache_resource
def ensure_cluster_running():
    cluster = get_cluster()
    cluster.start_all(include_puc=True)
    return cluster

cluster = ensure_cluster_running()

# Custom Header Styling
st.markdown("""
<style>
    .reportview-container { background: #f8fafc; }
    .main-title { font-size: 26px; font-weight: 700; color: #1e293b; margin-bottom: 2px; }
    .sub-title { font-size: 14px; color: #64748b; margin-bottom: 20px; }
    .status-card-ok { background: #f0fdf4; border: 1px solid #bbf7d0; border-radius: 8px; padding: 10px; text-align: center; }
    .status-card-down { background: #fef2f2; border: 1px solid #fecaca; border-radius: 8px; padding: 10px; text-align: center; }
    .status-card-timeout { background: #fffbeb; border: 1px solid #fde68a; border-radius: 8px; padding: 10px; text-align: center; }
    .decision-banner-clear { background: #dcfce7; border-left: 6px solid #16a34a; padding: 16px; border-radius: 6px; margin-bottom: 15px; }
    .decision-banner-danger { background: #fee2e2; border-left: 6px solid #dc2626; padding: 16px; border-radius: 6px; margin-bottom: 15px; }
    .decision-banner-warn { background: #fef3c7; border-left: 6px solid #d97706; padding: 16px; border-radius: 6px; margin-bottom: 15px; }
    .decision-banner-unknown { background: #f1f5f9; border-left: 6px solid #64748b; padding: 16px; border-radius: 6px; margin-bottom: 15px; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-title">🚗 Federated Mediator: Uninsured Vehicle Detection</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-title">Global-As-View (GAV) virtual data integration across autonomous government & observational authorities.</div>', unsafe_allow_html=True)

import live_update

# Navigation tabs
tab_investigate, tab_plantrace, tab_matcher, tab_catalog, tab_reports = st.tabs([
    "🔍 1. Investigate",
    "🗺️ 2. Plan Trace",
    "🧬 3. Matcher & Heatmap",
    "📚 4. Catalog & Registry",
    "📑 5. Ministry Reports"
])

# Sidebar: Live Source Mutation (For Real-Time Demo / Prof Testing)
with st.sidebar:
    st.markdown("### ⚡ Live Source Data Mutator")
    st.markdown("Use this to simulate real-time edits inside the autonomous source databases on stage.")

    mut_action = st.selectbox(
        "Simulation Action",
        [
            "Renew Insurance Policy (INS)",
            "Expire Insurance Policy (INS)",
            "Report Vehicle Stolen (THEFT)",
            "Log Camera Sighting (CAM)",
            "Register New Vehicle (REG)"
        ]
    )

    mut_plate = st.text_input("Target Plate", "DL05CD9876", key="mut_plate")

    if "Renew" in mut_action:
        new_exp = st.text_input("New Expiry Date (DD/MM/YYYY)", "31/12/2027")
        if st.button("Apply Renewal to INS DB", type="primary"):
            live_update.renew_insurance(mut_plate, new_exp)
            st.success(f"Updated INS database: {mut_plate} renewed until {new_exp}! Re-run query now.")
    elif "Expire" in mut_action:
        old_exp = st.text_input("Expired Date (DD/MM/YYYY)", "10/01/2026")
        if st.button("Expire Policy in INS DB", type="primary"):
            live_update.expire_insurance(mut_plate, old_exp)
            st.warning(f"Updated INS database: {mut_plate} expired on {old_exp}! Re-run query now.")
    elif "Stolen" in mut_action:
        fir = st.text_input("FIR Number", "FIR-LIVE-101/2026")
        if st.button("Log Theft in THEFT DB", type="primary"):
            live_update.report_stolen(mut_plate, fir_no=fir)
            st.error(f"Inserted into THEFT database: {mut_plate} reported STOLEN! Re-run query now.")
    elif "Sighting" in mut_action:
        c_make = st.text_input("Observed Make", "Hyundai")
        c_model = st.text_input("Observed Model", "Creta")
        c_col = st.text_input("Observed Colour", "White")
        if st.button("Record Sighting in CAM DB", type="primary"):
            live_update.add_camera_sighting(mut_plate, "NH8 Toll Plaza", c_make, c_model, c_col)
            st.info(f"Inserted into CAM database: {mut_plate} sighted by camera! Re-run query now.")
    elif "Register" in mut_action:
        r_owner = st.text_input("Owner Name", "Rajesh Khanna")
        r_make = st.text_input("Make", "Maruti Suzuki")
        r_model = st.text_input("Model", "Swift")
        r_col = st.text_input("Colour", "White")
        if st.button("Register in REG DB", type="primary"):
            live_update.register_vehicle(mut_plate, r_owner, r_make, r_model, r_col)
            st.success(f"Inserted into REG database: {mut_plate} registered for {r_owner}! Re-run query now.")

    st.markdown("---")
    st.caption("Freshness Rule: The mediator holds no source data. Edits in the source databases reflect instantly upon re-querying without ETL.")

# ==============================================================================
# TAB 1: INVESTIGATE
# ==============================================================================
with tab_investigate:
    st.subheader("Vehicle Compliance Investigation")

    # Quick demo buttons row
    st.markdown("**Quick Demo Scenarios (Named Test Plates):**")
    demo_cols = st.columns(5)
    demo_plates = [
        ("DL01AB1234", "Clean / Clear", "DL01AB1234"),
        ("DL05CD9876", "Expired Insurance", "DL-05-cd-9876"),
        ("HR26EF4455", "Stolen Vehicle", "hr 26 ef 4455"),
        ("UP16GH1122", "Cloned Plate / Conflict", "UP16-GH-1122"),
        ("MH12IJ7788", "Unregistered Camera Only", "mh12ij7788")
    ]
    if "selected_plate" not in st.session_state:
        st.session_state["selected_plate"] = "DL01AB1234"

    for idx, (label_p, desc, raw_val) in enumerate(demo_plates):
        if demo_cols[idx].button(f"**{label_p}**\n\n_{desc}_", key=f"btn_{label_p}", use_container_width=True):
            st.session_state["selected_plate"] = raw_val

    # Search bar & Query Type
    c_input, c_type, c_submit = st.columns([3, 2, 1])
    plate_input = c_input.text_input("License Plate Number (accepts any format)", value=st.session_state["selected_plate"])
    query_type = c_type.selectbox("Query Scope", ["UC2: Full Vehicle Profile (all sources)", "UC1: Insurance Verification Only (INS + REG)"])
    run_btn = c_submit.button("🔎 Run Global Query", type="primary", use_container_width=True)

    # Perform query
    if plate_input:
        req_attrs = ["insurance_status", "insurance_expiry"] if "UC1" in query_type else ["all"]
        result = run_global_query(plate_input, req_attrs)
        profile = result["profile"]
        plan_trace = result["plan_trace"]
        st.session_state["latest_result"] = result

        # Top status cards (Source availability)
        st.markdown("##### Live Autonomous Source Health & Latency")
        source_cols = st.columns(len(plan_trace["sources_detail"]))
        for idx, (s_id, s_info) in enumerate(plan_trace["sources_detail"].items()):
            status = s_info.get("status", "DOWN")
            latency = s_info.get("elapsed_ms", 0.0)
            status_cls = "status-card-ok" if status == "OK" else ("status-card-timeout" if status == "TIMEOUT" else "status-card-down")
            badge = "🟢 ONLINE" if status == "OK" else ("🟡 TIMEOUT" if status == "TIMEOUT" else "🔴 OFFLINE")

            with source_cols[idx]:
                st.markdown(f"""
                <div class="{status_cls}">
                    <b>{s_id}</b><br>
                    <small>{badge}</small><br>
                    <code>{latency} ms</code>
                </div>
                """, unsafe_allow_html=True)

        st.markdown("<br>", unsafe_allow_html=True)

        # Decision Banner
        dec = profile.get("decision", "UNDETERMINED")
        conf = profile.get("confidence", "LOW")
        reasons = profile.get("reasons", [])

        if "CLEAR" in dec:
            banner_cls = "decision-banner-clear"
            icon = "✅"
        elif any(k in dec for k in ["REPORT", "ALERT"]):
            banner_cls = "decision-banner-danger"
            icon = "🚨"
        elif "SUSPICIOUS" in dec:
            banner_cls = "decision-banner-warn"
            icon = "⚠️"
        else:
            banner_cls = "decision-banner-unknown"
            icon = "❓"

        st.markdown(f"""
        <div class="{banner_cls}">
            <h3 style="margin:0; font-size:20px;">{icon} Decision: {dec} &nbsp;&nbsp;<span style="font-size:14px; font-weight:normal; background:#fff; padding:3px 8px; border-radius:4px; border:1px solid #ccc;">Confidence: <b>{conf}</b></span></h3>
            <ul style="margin: 8px 0 0 16px; font-size:14px;">
                {''.join(f'<li>{r}</li>' for r in reasons)}
            </ul>
        </div>
        """, unsafe_allow_html=True)

        # Conflict Panel if detected
        conflicts = profile.get("conflicts", [])
        if conflicts:
            st.error(f"⚠️ **{len(conflicts)} Attribute Conflict(s) Detected Across Sources:**")
            for c in conflicts:
                st.markdown(f"- **{c['attribute'].upper()}**: REG states `{c['values_by_source'].get('REG')}` but Camera observed `{c['values_by_source'].get('CAM')}`. **Resolution:** *{c['resolution']}* -> Selected: `{c['chosen']}`")

        # Integrated Vehicle Profile Card
        st.markdown("#### Integrated Virtual Vehicle Profile (`VEHICLE_PROFILE`)")
        c1, c2, c3 = st.columns(3)

        with c1:
            st.markdown("##### 📋 Registration (REG)")
            st.write(f"**Plate:** `{profile.get('plate_number')}`")
            st.write(f"**Owner:** {profile.get('owner_name') or 'N/A'}")
            st.write(f"**Make / Model:** {profile.get('vehicle_make') or 'N/A'} {profile.get('vehicle_model') or ''}")
            st.write(f"**Colour:** {profile.get('vehicle_colour') or 'N/A'}")
            st.write(f"**Registered On:** {profile.get('registration_date') or 'N/A'}")
            st.write(f"**Status:** `{profile.get('registration_status') or 'N/A'}`")

        with c2:
            st.markdown("##### 🛡️ Insurance (INS)")
            st.write(f"**Insurer:** {profile.get('insurer_name') or 'N/A'}")
            st.write(f"**Policy Type:** {profile.get('policy_type') or 'N/A'}")
            st.write(f"**Start Date:** {profile.get('insurance_start') or 'N/A'}")
            st.write(f"**Expiry Date:** {profile.get('insurance_expiry') or 'N/A'}")
            ins_badge = f":green[{profile.get('insurance_status')}]" if profile.get('insurance_status') == "VALID" else f":red[{profile.get('insurance_status')}]"
            st.write(f"**Derived Status:** {ins_badge}")

        with c3:
            st.markdown("##### 📸 Sighting & Crime (CAM / THEFT)")
            st.write(f"**Theft Status:** `{profile.get('stolen_status')}`")
            st.write(f"**Case Status:** {profile.get('case_status') or 'N/A'}")
            st.write(f"**Last Seen Location:** {profile.get('last_seen_location') or 'Never Sighted'}")
            st.write(f"**Last Seen Time:** {str(profile.get('last_seen_time') or 'N/A')[:19]}")
            st.write(f"**Observed Vehicle:** {profile.get('observed_make') or ''} {profile.get('observed_model') or ''} ({profile.get('observed_colour') or ''})")
            if profile.get("puc_expiry"):
                st.write(f"**PUC Validity:** {profile.get('puc_expiry')}")

        # Provenance expander
        with st.expander("🔍 View Per-Attribute Provenance & Trust Metadata"):
            prov_rows = []
            for attr, pinfo in profile.get("provenance", {}).items():
                prov_rows.append({
                    "Attribute": attr,
                    "Source": pinfo.get("source"),
                    "Authority": pinfo.get("authority"),
                    "Trust Score": pinfo.get("trust"),
                    "Fetched At": pinfo.get("fetched_at")
                })
            if prov_rows:
                st.dataframe(pd.DataFrame(prov_rows), use_container_width=True)

        # File Report button
        st.markdown("---")
        c_rep1, c_rep2 = st.columns([1, 3])
        if c_rep1.button("📑 File Report to Ministry", type="secondary", use_container_width=True):
            rep_id = file_ministry_report(profile, plan_trace)
            pdf_path = generate_report_pdf(rep_id)
            st.success(f"Report MOT-{rep_id:06d} successfully filed in Ministry Audit Log and PDF generated!")

            with open(pdf_path, "rb") as f:
                c_rep2.download_button(
                    label=f"⬇️ Download Official Ministry Audit PDF (MOT-{rep_id:06d})",
                    data=f.read(),
                    file_name=os.path.basename(pdf_path),
                    mime="application/pdf"
                )

# ==============================================================================
# TAB 2: PLAN TRACE
# ==============================================================================
with tab_plantrace:
    st.subheader("Query Decomposition & Federation Plan Trace")
    st.markdown("""
    The query planner minimizes network traffic and execution latency by reading attribute coverage
    from `SOURCE_CATALOG` and contacting only the sources that can answer the query.
    """)

    if "latest_result" in st.session_state:
        trace = st.session_state["latest_result"]["plan_trace"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Canonical Plate", trace["canonical_plate"])
        c2.metric("Sources Contacted", f"{trace['source_count']} sources", delta=f"{', '.join(trace['sources_contacted'])}")
        c3.metric("Total Latency", f"{trace['total_elapsed_ms']} ms")

        st.markdown("##### Generated Source Sub-Queries (Pushdown Predicates)")
        for s_id, sql_text in trace["sqls"].items():
            s_det = trace["sources_detail"].get(s_id, {})
            status = s_det.get("status", "OK")
            elapsed = s_det.get("elapsed_ms", 0.0)
            rows_ret = s_det.get("row_count", 0)

            with st.expander(f"**{s_id}** — Status: `{status}` | Rows: `{rows_ret}` | Time: `{elapsed} ms`", expanded=True):
                st.code(sql_text, language="sql")
    else:
        st.info("Execute a query in Tab 1 to inspect its execution plan trace.")

# ==============================================================================
# TAB 3: MATCHER & HEATMAP
# ==============================================================================
with tab_matcher:
    st.subheader("Hybrid Schema Matching Algorithm")
    st.markdown("""
    Discovers correspondences between autonomous source schemas and the mediated `VEHICLE_PROFILE`
    using: **Score = 0.40 · N (lexical + thesaurus) + 0.20 · C (type/constraint) + 0.40 · I (instance profiles)**.
    """)

    c_msrc, c_mbtn = st.columns([2, 1])
    sources_avail = list(get_source_catalog().keys())
    chosen_source = c_msrc.selectbox("Select Source to Match", sources_avail)
    run_match = c_mbtn.button("🚀 Run Schema Matcher", type="primary")

    if run_match or f"matcher_res_{chosen_source}" in st.session_state:
        if run_match:
            cat = get_source_catalog()
            burl = cat[chosen_source]["base_url"]
            try:
                import httpx
                # 2 s was enough for localhost, but /schema runs a DISTINCT sample query per
                # column and has to cross the hotspot to another laptop. A timeout here fails the
                # schema-matching demo outright, so allow for a slow link.
                r = httpx.get(f"{burl}/schema", timeout=15.0)
                schema_data = r.json()
            except Exception as e:
                st.error(f"Failed to fetch live schema from {chosen_source}: {e}")
                schema_data = {"tables": {}}

            m_res = match_source_schema(schema_data, theta=0.55)
            st.session_state[f"matcher_res_{chosen_source}"] = m_res
        else:
            m_res = st.session_state[f"matcher_res_{chosen_source}"]

        # Plotly Heatmap
        sim_matrix = m_res.get("similarity_matrix", {})
        if sim_matrix:
            df_heat = pd.DataFrame.from_dict(sim_matrix, orient="index")
            fig = px.imshow(
                df_heat,
                labels=dict(x="Global Attribute", y="Source Column", color="Similarity Score"),
                x=df_heat.columns,
                y=df_heat.index,
                color_continuous_scale="Viridis",
                text_auto=".2f",
                aspect="auto"
            )
            fig.update_layout(title=f"Similarity Matrix Heatmap ({chosen_source} vs Global Schema)", height=450)
            st.plotly_chart(fig, use_container_width=True)

        # Candidate mappings review table
        st.markdown(f"##### Discovered Correspondences for {chosen_source} (Threshold θ ≥ 0.55)")
        corrs = m_res.get("correspondences", [])
        if corrs:
            corr_df = pd.DataFrame([
                {
                    "Table": c["source_table"],
                    "Source Column": c["source_attr"],
                    "Discovered Global Attribute": c["global_attr"],
                    "Total Score": c["score"],
                    "N (Name)": c["components"]["N"],
                    "C (Type)": c["components"]["C"],
                    "I (Instance)": c["components"]["I"]
                }
                for c in corrs
            ])
            st.dataframe(corr_df, use_container_width=True)

            if st.button("💾 Accept & Persist All Correspondences to Registry"):
                for c in corrs:
                    save_mapping(
                        source_id=chosen_source,
                        source_table=c["source_table"],
                        source_attr=c["source_attr"],
                        global_attr=c["global_attr"],
                        match_score=c["score"]
                    )
                st.success(f"Persisted {len(corrs)} correspondences to MAPPING_REGISTRY in meta.db!")

        unmapped = m_res.get("unmapped", [])
        if unmapped:
            st.markdown(f"**Unmapped Attributes ({len(unmapped)}):** " + ", ".join(f"`{u['source_attr']}`" for u in unmapped))

# ==============================================================================
# TAB 4: CATALOG & REGISTRY
# ==============================================================================
with tab_catalog:
    st.subheader("Metadata Registry & Source Catalog (`meta.db`)")

    cat_view = get_source_catalog()
    st.markdown("##### Registered Data Sources (`SOURCE_CATALOG`)")
    cat_df = pd.DataFrame([
        {
            "Source ID": s["source_id"],
            "Name": s["display_name"],
            "DBMS Engine": s["dbms"],
            "Base URL": s["base_url"],
            "Authority": s["authority"],
            "Trust Score": s["trust_score"],
            "Timeout (ms)": s["timeout_ms"],
            "Coverage Count": len(s.get("covers", []))
        }
        for s in cat_view.values()
    ])
    st.dataframe(cat_df, use_container_width=True)

    st.markdown("##### GAV Mapping Rules (`MAPPING_REGISTRY`)")
    maps = get_all_mappings()
    if maps:
        map_df = pd.DataFrame(maps)[["source_id", "source_table", "source_attr", "global_attr", "transform_fn", "aggregate", "join_path", "match_score"]]
        st.dataframe(map_df, use_container_width=True)

    st.markdown("---")
    st.markdown("##### ➕ Register New Source (Extensibility Live Demo UC6)")
    with st.form("new_source_form"):
        f_id = st.text_input("Source ID (e.g. PUC)", "PUC")
        f_name = st.text_input("Display Name", "Pollution-Certificate Authority (PUC)")
        f_dbms = st.selectbox("DBMS Engine", ["SQLite", "PostgreSQL", "MySQL"])
        f_url = st.text_input("Base URL", "http://127.0.0.1:8005")
        f_id_attr = st.text_input("Identifier Column", "regn_number")
        f_auth = st.selectbox("Authority Category", ["OFFICIAL", "OBSERVATIONAL"])
        f_trust = st.slider("Trust Score", 0.0, 1.0, 0.85)
        f_covers = st.text_input("Covered Global Attributes (comma separated)", "plate_number, puc_expiry")
        submit_src = st.form_submit_button("Register Source in Catalog")

        if submit_src:
            covers_list = [c.strip() for c in f_covers.split(",") if c.strip()]
            register_source(f_id, f_name, f_dbms, f_url, f_id_attr, f_auth, f_trust, covers_list)
            # Add mapping for PUC
            save_mapping(f_id, "POLLUTION_CERT", "regn_number", "plate_number", "norm_plate")
            save_mapping(f_id, "POLLUTION_CERT", "valid_upto", "puc_expiry", "none")
            st.success(f"Source {f_id} registered and mapped successfully! No mediator engine code was modified.")

# ==============================================================================
# TAB 5: REPORTS
# ==============================================================================
with tab_reports:
    st.subheader("Ministry of Transportation Audit Reports Log (`REPORT_LOG`)")

    reports = list_reports()
    if not reports:
        st.info("No audit reports have been filed yet. File a report from Tab 1 to see it here.")
    else:
        for rep in reports:
            with st.expander(f"**Report MOT-{rep['report_id']:06d}** — Plate: `{rep['plate']}` | Decision: `{rep['decision']}` | Confidence: `{rep['confidence']}`"):
                st.write(f"**Audit Timestamp:** {rep['generated_at']}")
                st.write(f"**Sources Contacted:** {', '.join(rep.get('sources_used', []))}")
                st.markdown("**Reasons:**")
                for r in rep.get("reasons", []):
                    st.markdown(f"- {r}")

                pdf_file = generate_report_pdf(rep["report_id"])
                if os.path.exists(pdf_file):
                    with open(pdf_file, "rb") as pf:
                        st.download_button(
                            label=f"⬇️ Download Official PDF (MOT-{rep['report_id']:06d})",
                            data=pf.read(),
                            file_name=os.path.basename(pdf_file),
                            mime="application/pdf",
                            key=f"dl_rep_{rep['report_id']}"
                        )
