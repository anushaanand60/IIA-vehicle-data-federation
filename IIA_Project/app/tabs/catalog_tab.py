"""Catalog & Registry tab: the live `SOURCE_CATALOG` / `MAPPING_REGISTRY` in `meta.db`, plus the
Register New Source form that demonstrates UC6 extensibility -- a sixth source joins the
federation with a catalog row and a couple of mappings, no mediator engine code changed.
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

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from components import page_head, panel, section  # noqa: E402
from mediator.catalog import (  # noqa: E402
    get_all_mappings,
    get_source_catalog,
    register_source,
    save_mapping,
)


def _catalog_table() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Source ID": s["source_id"],
            "Name": s["display_name"],
            "DBMS Engine": s["dbms"],
            "Base URL": s["base_url"],
            "Authority": s["authority"],
            "Trust Score": s["trust_score"],
            "Timeout (ms)": s["timeout_ms"],
            "Coverage Count": len(s.get("covers", [])),
        }
        for s in get_source_catalog().values()
    ])


def _mapping_table() -> pd.DataFrame | None:
    maps = get_all_mappings()
    if not maps:
        return None
    return pd.DataFrame(maps)[[
        "source_id", "source_table", "source_attr", "global_attr", "transform_fn",
        "aggregate", "join_path", "match_score",
    ]]


def _register_source_form() -> None:
    section("Register a new source",
            "The extensibility demo (UC6): a sixth agency joins the federation with a catalog "
            "row and a couple of mappings. No mediator engine code changes.")
    with st.form("ct_new_source_form"):
        f_id = st.text_input("Source ID (e.g. PUC)", "PUC", key="ct_id")
        f_name = st.text_input(
            "Display Name", "Pollution-Certificate Authority (PUC)", key="ct_name")
        f_dbms = st.selectbox("DBMS Engine", ["SQLite", "PostgreSQL", "MySQL"], key="ct_dbms")
        f_url = st.text_input("Base URL", "http://127.0.0.1:8005", key="ct_url")
        f_id_attr = st.text_input("Identifier Column", "regn_number", key="ct_id_attr")
        f_auth = st.selectbox("Authority Category", ["OFFICIAL", "OBSERVATIONAL"], key="ct_auth")
        f_trust = st.slider("Trust Score", 0.0, 1.0, 0.85, key="ct_trust")
        f_covers = st.text_input(
            "Covered Global Attributes (comma separated)", "plate_number, puc_expiry",
            key="ct_covers")
        submit_src = st.form_submit_button("Register source in the catalog",
                                           type="primary")

        if submit_src:
            covers_list = [c.strip() for c in f_covers.split(",") if c.strip()]
            register_source(f_id, f_name, f_dbms, f_url, f_id_attr, f_auth, f_trust, covers_list)
            # Demo-only shorthand mapping for the PUC story; a real 6th source is mapped through
            # the Matcher tab instead of hand-typed correspondences.
            save_mapping(f_id, "POLLUTION_CERT", "regn_number", "plate_number", "norm_plate")
            save_mapping(f_id, "POLLUTION_CERT", "valid_upto", "puc_expiry", "none")
            st.success(
                f"Source {f_id} registered and mapped successfully! "
                "No mediator engine code was modified."
            )


def render() -> None:
    page_head("Source catalog",
              "The mediator's own metadata: which agencies it knows about, how far each is "
              "trusted, and which source column stands for which global attribute.")

    with panel("ct_sources"):
        section("Registered data sources",
                "SOURCE_CATALOG in meta.db: where each agency lives, how far it is trusted, and "
                "which global attributes it can answer.")
        st.dataframe(_catalog_table(), width="stretch", hide_index=True, key="ct_catalog_table")

    with panel("ct_mappings"):
        section("GAV mapping rules",
                "MAPPING_REGISTRY: every source column that stands for a global attribute, the "
                "transform applied on the way out, and the score that discovered it.")
        mapping_df = _mapping_table()
        if mapping_df is not None:
            st.dataframe(mapping_df, width="stretch", hide_index=True, key="ct_mapping_table")
        else:
            st.caption("No correspondences have been persisted to the registry yet.")

    with panel("ct_register"):
        _register_source_form()


if __name__ == "__main__":
    st.set_page_config(page_title="Catalog", page_icon=":material/dns:", layout="wide")
    render()
