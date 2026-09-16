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

import live_update

# Never import through the package name "app": under `streamlit run app/app.py` that name is this very
# script, so the import would execute the whole page a second time (duplicate widget IDs).
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from tabs.sql_console import render as render_sql_console
from tabs.source_editor import render_sidebar as render_source_editor_sidebar
from tabs.investigate import render as render_investigate
from theme import inject, masthead

# The whole look of the page comes from app/theme.py: tokens -> CSS variables, injected once.
inject()
masthead(
    "Federated Mediator",
    "Uninsured vehicle detection across autonomous government and observational authorities",
)

# Navigation tabs
tab_investigate, tab_plantrace, tab_matcher, tab_catalog, tab_reports, tab_sqlconsole = st.tabs([
    "🔍 1. Investigate",
    "🗺️ 2. Plan Trace",
    "🧬 3. Matcher & Heatmap",
    "📚 4. Catalog & Registry",
    "📑 5. Ministry Reports",
    "🧪 6. SQL Console"
])

# Each wrapper's /query connection is read-only by construction (CLAUDE.md §5.1) -- that boundary
# is never touched here. Writes go through the source editor sidebar (app/tabs/source_editor.py),
# which asks each wrapper's own GET /admin/actions what it can do and posts to /admin/mutate --
# never arbitrary SQL, and disabled wherever that laptop set <SOURCE>_ADMIN=off. The mediator still
# cannot write through /query; this is the agency's own operator console, reached like any other
# endpoint.
render_source_editor_sidebar()

with st.sidebar:
    st.markdown("---")
    st.caption("Freshness Rule: The mediator holds no source data. Edits in the source databases reflect instantly upon re-querying without ETL.")

# ==============================================================================
# TAB 1: INVESTIGATE
# ==============================================================================
with tab_investigate:
    render_investigate()

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
            st.plotly_chart(fig, width="stretch")

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
            st.dataframe(corr_df, width="stretch")

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
    st.dataframe(cat_df, width="stretch")

    st.markdown("##### GAV Mapping Rules (`MAPPING_REGISTRY`)")
    maps = get_all_mappings()
    if maps:
        map_df = pd.DataFrame(maps)[["source_id", "source_table", "source_attr", "global_attr", "transform_fn", "aggregate", "join_path", "match_score"]]
        st.dataframe(map_df, width="stretch")

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

# ==============================================================================
# TAB 6: SQL CONSOLE
# ==============================================================================
with tab_sqlconsole:
    render_sql_console()
