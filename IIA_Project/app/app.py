"""
Streamlit GUI for Federated Mediator / GAV Integration System
Identifying Uninsured Vehicles Across Autonomous Heterogeneous Databases.

Every tab body lives in its own `app/tabs/*.py` module (`render()`, Plan Trace's is
`render_tab()`); this file is only setup, theme, masthead, cluster start-up, the sidebar and
the tab shells. The order below is the order the demo is narrated in — the two things an
operator does (investigate a plate, decide whether a challan is safe to issue) first, the
machinery that proves how it was done last. 9 tabs:
  1. Investigate     (tabs/investigate.py)
  2. Challan Guard   (tabs/challan_guard.py)
  3. Watchlist       (tabs/watchlist.py)
  4. Reports         (tabs/reports.py)
  5. Citizen Check   (tabs/self_check.py)
  6. Plan Trace      (tabs/plan_trace.py)
  7. Catalog         (tabs/catalog_tab.py)
  8. Matcher         (tabs/matcher_tab.py)
  9. SQL Console     (tabs/sql_console.py)
"""

import sys
import os
import streamlit as st

# Ensure project root is in path
ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT_DIR not in sys.path:
    sys.path.insert(0, ROOT_DIR)

from sources.server_manager import get_cluster

st.set_page_config(
    page_title="Uninsured Vehicle Identification",
    page_icon="🚗",
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
from tabs.source_editor import render_sidebar as render_source_editor_sidebar  # noqa: E402
from tabs.investigate import render as render_investigate  # noqa: E402
from tabs.plan_trace import render_tab as render_plan_trace  # noqa: E402
from tabs.matcher_tab import render as render_matcher  # noqa: E402
from tabs.catalog_tab import render as render_catalog  # noqa: E402
from tabs.reports import render as render_reports  # noqa: E402
from tabs.watchlist import render as render_watchlist  # noqa: E402
from tabs.self_check import render as render_self_check  # noqa: E402
from tabs.challan_guard import render as render_challan_guard  # noqa: E402
from theme import inject, masthead  # noqa: E402

# The whole look of the page comes from app/theme.py: tokens -> CSS variables, injected once.
inject()
masthead(
    "Uninsured Vehicle Identification",
    "Live federated check across registration, insurance, police, camera and PUC records. "
    "Nothing is copied; every answer is fetched now.",
)

# Navigation tabs
(tab_investigate, tab_challan, tab_watchlist, tab_reports, tab_selfcheck, tab_plantrace,
 tab_catalog, tab_matcher, tab_sqlconsole) = st.tabs([
    "Investigate",
    "Challan Guard",
    "Watchlist",
    "Reports",
    "Citizen Check",
    "Plan Trace",
    "Catalog",
    "Matcher",
    "SQL Console",
])

# Each wrapper's /query connection is read-only by construction -- that boundary is never touched
# here. Writes go through the source editor sidebar (app/tabs/source_editor.py), which asks each
# wrapper's own GET /admin/actions what it can do and posts to /admin/mutate -- never arbitrary
# SQL, and disabled wherever that laptop set <SOURCE>_ADMIN=off. The mediator still cannot write
# through /query; this is the agency's own operator console, reached like any other endpoint.
render_source_editor_sidebar()

with st.sidebar:
    st.caption(
        "Freshness rule: the mediator holds no source data. Edits in the source databases "
        "reflect instantly upon re-querying, without ETL."
    )

with tab_investigate:
    render_investigate()

with tab_challan:
    render_challan_guard()

with tab_watchlist:
    render_watchlist()

with tab_reports:
    render_reports()

with tab_selfcheck:
    render_self_check()

with tab_plantrace:
    render_plan_trace()

with tab_catalog:
    render_catalog()

with tab_matcher:
    render_matcher()

with tab_sqlconsole:
    render_sql_console()
