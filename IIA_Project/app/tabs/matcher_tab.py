"""Matcher & Heatmap tab: run the hybrid schema matcher against a live source and review /
persist the discovered correspondences.

Nothing here scores a schema itself -- `mediator/matcher.py` (Teammates A & B) does that; this
module only asks a source for its live `/schema`, calls the matcher, and renders the similarity
heatmap and the candidate mapping table it returns.
"""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:  # `streamlit run` puts only this file's folder on sys.path
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parents[1]
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402
import plotly.express as px  # noqa: E402
import streamlit as st  # noqa: E402

from components import group_label, page_head, panel, section  # noqa: E402
from mediator.catalog import get_source_catalog, save_mapping  # noqa: E402
from mediator.matcher import match_source_schema  # noqa: E402

# /schema runs a DISTINCT sample query per column and may have to cross a hotspot to another
# laptop -- 2 s was enough for localhost but fails the demo outright over a slow link.
SCHEMA_TIMEOUT_S = 15.0


def _fetch_schema(base_url: str, source_id: str) -> dict:
    try:
        r = httpx.get(f"{base_url}/schema", timeout=SCHEMA_TIMEOUT_S)
        return r.json()
    except Exception as exc:
        st.error(f"Failed to fetch live schema from {source_id}: {exc}")
        return {"tables": {}}


def render() -> None:
    page_head("Schema matcher",
              "How a source's own column names become global attributes: the matcher reads a "
              "live schema and proposes the correspondences, a human accepts them.")

    with panel("mt_run_panel"):
        section(
            "Hybrid schema matching",
            "Discovers correspondences between an autonomous source schema and the mediated "
            "VEHICLE_PROFILE. Score = 0.40 · N (lexical + thesaurus) + 0.20 · C "
            "(type/constraint) + 0.40 · I (instance profiles).",
        )

        sources_avail = list(get_source_catalog().keys())
        if not sources_avail:
            st.info("No sources are registered in the catalog yet.")
            return

        c_src, c_btn = st.columns([2, 1])
        chosen_source = c_src.selectbox("Source to match", sources_avail, key="mt_source")
        c_btn.markdown("<div style='height:30px'></div>", unsafe_allow_html=True)
        run_match = c_btn.button("Run schema matcher", type="primary", key="mt_run",
                                 width="stretch")

    state_key = f"mt_res_{chosen_source}"
    if run_match:
        base_url = get_source_catalog()[chosen_source]["base_url"]
        schema_data = _fetch_schema(base_url, chosen_source)
        st.session_state[state_key] = match_source_schema(schema_data, theta=0.55)

    m_res = st.session_state.get(state_key)
    if not m_res:
        return

    sim_matrix = m_res.get("similarity_matrix", {})
    if sim_matrix:
        with panel("mt_heat"):
            section("Similarity matrix",
                    f"Every source column of {chosen_source} scored against every global "
                    f"attribute.")
            df_heat = pd.DataFrame.from_dict(sim_matrix, orient="index")
            fig = px.imshow(
                df_heat,
                labels=dict(x="Global Attribute", y="Source Column", color="Similarity Score"),
                x=df_heat.columns,
                y=df_heat.index,
                color_continuous_scale="Viridis",
                text_auto=".2f",
                aspect="auto",
            )
            fig.update_layout(height=450, margin=dict(t=10, b=10))
            st.plotly_chart(fig, width="stretch", key="mt_heatmap")

    with panel("mt_corrs"):
        section(f"Discovered correspondences — {chosen_source}",
                "Every pair scoring at or above the threshold θ ≥ 0.55. Persisting them writes "
                "the mapping rules the decomposer builds each source's SQL from.")
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
                    "I (Instance)": c["components"]["I"],
                }
                for c in corrs
            ])
            st.dataframe(corr_df, width="stretch", hide_index=True, key="mt_corr_table")

            if st.button("Accept and persist all correspondences to the registry",
                         key="mt_persist", type="primary"):
                for c in corrs:
                    save_mapping(
                        source_id=chosen_source,
                        source_table=c["source_table"],
                        source_attr=c["source_attr"],
                        global_attr=c["global_attr"],
                        match_score=c["score"],
                    )
                st.success(f"Persisted {len(corrs)} correspondences to MAPPING_REGISTRY.")

        unmapped = m_res.get("unmapped", [])
        if unmapped:
            group_label(f"Unmapped source columns ({len(unmapped)})")
            st.markdown(", ".join(f"`{u['source_attr']}`" for u in unmapped))


if __name__ == "__main__":
    st.set_page_config(page_title="Matcher", page_icon=":material/hub:", layout="wide")
    render()
