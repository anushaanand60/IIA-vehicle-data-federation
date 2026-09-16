"""
Streamlit GUI for Federated Mediator / GAV Integration System
Identifying Uninsured Vehicles Across Autonomous Heterogeneous Databases.

Every tab body lives in its own `app/tabs/*.py` module (`render()`, Plan Trace's is
`render_tab()`); this file is only setup, theme, masthead, cluster start-up, the sidebar and
the tab shells. 8 Tabs:
  1. Investigate         (tabs/investigate.py)
  2. Plan Trace          (tabs/plan_trace.py)
  3. Matcher & Heatmap   (tabs/matcher_tab.py)
  4. Catalog & Registry  (tabs/catalog_tab.py)
  5. Ministry Reports    (tabs/reports.py)
  6. SQL Console         (tabs/sql_console.py)
  7. Watchlist & Alerts  (tabs/watchlist.py)
  8. Citizen Self-Check  (tabs/self_check.py)
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
    page_title="Federated Mediator",
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
from theme import inject, masthead  # noqa: E402

# The whole look of the page comes from app/theme.py: tokens -> CSS variables, injected once.
inject()
masthead(
    "Federated Mediator",
    "Uninsured vehicle detection across autonomous government and observational authorities",
)

# Navigation tabs
(tab_investigate, tab_plantrace, tab_matcher, tab_catalog, tab_reports, tab_sqlconsole,
 tab_watchlist, tab_selfcheck) = st.tabs([
    "🔍 1. Investigate",
    "🗺️ 2. Plan Trace",
    "🧬 3. Matcher & Heatmap",
    "📚 4. Catalog & Registry",
    "📑 5. Ministry Reports",
    "🧪 6. SQL Console",
    "🚨 7. Watchlist & Alerts",
    "🙋 8. Citizen Self-Check",
])

# Each wrapper's /query connection is read-only by construction -- that boundary is never touched
# here. Writes go through the source editor sidebar (app/tabs/source_editor.py), which asks each
# wrapper's own GET /admin/actions what it can do and posts to /admin/mutate -- never arbitrary
# SQL, and disabled wherever that laptop set <SOURCE>_ADMIN=off. The mediator still cannot write
# through /query; this is the agency's own operator console, reached like any other endpoint.
render_source_editor_sidebar()

with st.sidebar:
    st.markdown("---")
    st.caption(
        "Freshness Rule: The mediator holds no source data. Edits in the source databases "
        "reflect instantly upon re-querying without ETL."
    )

with tab_investigate:
    render_investigate()

with tab_plantrace:
    render_plan_trace()

with tab_matcher:
    render_matcher()

with tab_catalog:
    render_catalog()

with tab_reports:
    render_reports()

with tab_sqlconsole:
    render_sql_console()

with tab_watchlist:
    render_watchlist()

with tab_selfcheck:
    render_self_check()
