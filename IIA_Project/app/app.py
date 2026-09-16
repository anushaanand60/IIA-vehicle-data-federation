"""
Streamlit GUI for Federated Mediator / GAV Integration System
Identifying Uninsured Vehicles Across Autonomous Heterogeneous Databases.

Every page body lives in its own `app/tabs/*.py` module (`render()`, Plan Trace's is
`render_tab()`); this file is only setup, theme, navigation and cluster start-up.

Navigation is a sidebar menu, not a row of tabs: ten pages do not fit in one tab strip without
becoming unreadable, and a menu lets the pages be grouped by who uses them — the two enforcement
jobs first, the citizen's own page next, the machinery that proves how an answer was assembled
last. The menu is one `st.radio`, so exactly one page is open and the choice survives a rerun;
`?page=<name>` opens the app straight on a page, which is also how the screenshots are taken.
"""

import sys
import os

import httpx
import streamlit as st

# Ensure project root is in path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sources.server_manager import get_cluster

st.set_page_config(
    page_title="Vehicle Compliance Mediator",
    page_icon=":material/directions_car:",
    layout="wide",
    initial_sidebar_state="expanded",
)


# Auto-start source wrappers in background if not already started
@st.cache_resource
def ensure_cluster_running():
    cluster = get_cluster()
    cluster.start_all(include_puc=True)
    return cluster


cluster = ensure_cluster_running()

import live_update  # noqa: E402

# Never import through the package name "app": under `streamlit run app/app.py` that name is this
# very script, so the import would execute the whole page a second time (duplicate widget IDs).
_APP_DIR = os.path.dirname(os.path.abspath(__file__))
if _APP_DIR not in sys.path:
    sys.path.insert(0, _APP_DIR)
from tabs.sql_console import render as render_sql_console  # noqa: E402
from tabs.source_editor import render as render_source_editor  # noqa: E402
from tabs.investigate import render as render_investigate  # noqa: E402
from tabs.plan_trace import render_tab as render_plan_trace  # noqa: E402
from tabs.matcher_tab import render as render_matcher  # noqa: E402
from tabs.catalog_tab import render as render_catalog  # noqa: E402
from tabs.reports import render as render_reports  # noqa: E402
from tabs.watchlist import render as render_watchlist  # noqa: E402
from tabs.self_check import render as render_self_check  # noqa: E402
from tabs.challan_guard import render as render_challan_guard  # noqa: E402
from components import chip, chip_strip, group_label  # noqa: E402
from mediator.catalog import get_source_catalog  # noqa: E402
from theme import TOKENS, inject  # noqa: E402

# Grouped in the sidebar as Enforcement (1-4), Citizens (5), Mediator (6-10); the group headings
# are drawn by `theme.py` off these positions, so reordering here means reordering there.
PAGES: dict[str, object] = {
    "Investigate": render_investigate,
    "Challan Guard": render_challan_guard,
    "Watchlist": render_watchlist,
    "Reports": render_reports,
    "Citizen Check": render_self_check,
    "Plan Trace": render_plan_trace,
    "Catalog": render_catalog,
    "Matcher": render_matcher,
    "SQL Console": render_sql_console,
    "Source Editor": render_source_editor,
}

BRAND = (
    '<div class="cv-brand"><span class="mark">MOT</span>'
    '<span><span class="t">Vehicle Compliance Mediator</span>'
    '<span class="s">Ministry of Transportation · federated check</span></span></div>'
)


@st.cache_data(ttl=5, show_spinner=False)
def source_health() -> dict[str, bool]:
    """Is each agency's wrapper answering right now? For the sidebar badges only.

    Cached for five seconds because the badges repaint on every rerun; a decision never reads
    this. `mediator/executor.py` contacts every source again at query time, which is the whole
    freshness claim — a cached health check must never stand in for a live answer.
    """
    states: dict[str, bool] = {}
    for source_id, meta in get_source_catalog().items():
        base = str(meta.get("base_url") or "").rstrip("/")
        try:
            states[source_id] = httpx.get(f"{base}/health", timeout=0.6,
                                          trust_env=False).status_code == 200
        except Exception:  # unreachable is a state, not an error: it is what the badge says
            states[source_id] = False
    return states


def sidebar() -> str:
    """Brand block, the page menu, and the cluster's health. Returns the chosen page."""
    st.sidebar.markdown(BRAND, unsafe_allow_html=True)
    names = list(PAGES)
    requested = st.query_params.get("page")
    index = names.index(requested) if requested in names else 0
    page = st.sidebar.radio("Go to", names, index=index, key="nav",
                            label_visibility="collapsed")
    with st.sidebar:
        st.markdown("<div style='height:28px'></div>", unsafe_allow_html=True)
        group_label("Sources")
        health = source_health()
        chip_strip([chip(f"{sid} · {'UP' if up else 'DOWN'}",
                         TOKENS["ok"] if up else TOKENS["down"])
                    for sid, up in health.items()])
        st.caption("The mediator holds no source data. An edit in an agency's database shows up "
                   "on the next query, with no ETL in between.")
    return page


# The whole look of the page comes from app/theme.py: tokens -> CSS variables, injected once.
inject()
PAGES[sidebar()]()
