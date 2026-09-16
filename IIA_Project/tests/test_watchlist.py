"""Task 2.3: the watchlist, the alert log, and how both reach the GUI.

Mirrors the UK MIB's Operation Tutelage marker (FIELD_RESEARCH fact 7) and the IIB's uninsured
flags (facts 1, 3): an operator marks a plate with a reason; every later query on that plate files
a timestamped alert carrying whatever camera evidence the sighting sources returned. A stolen or
scrapped verdict raises the same alert without anyone having marked it — that is the hotlist.

Everything runs against a real tmp `meta.db` (`catalog.META_DB_PATH` monkeypatched, the pattern
`tests/test_investigate_tab.py` established); nothing here is mocked.
"""
from __future__ import annotations

import sqlite3

import pytest
from streamlit.testing.v1 import AppTest

from mediator import catalog, watchlist
from mediator.risk import score
# The three-wrapper cluster + its helpers, reused so the GUI assertions run against real sources.
from tests.test_investigate_tab import PLATE, cluster  # noqa: F401

WATCHED = "DL05CD9876"


@pytest.fixture()
def meta(tmp_path, monkeypatch):
    """An empty tmp catalog: WATCHLIST and ALERT_LOG created by init_meta_db, nothing seeded."""
    monkeypatch.setattr(catalog, "META_DB_PATH", str(tmp_path / "meta.db"))
    catalog.init_meta_db()
    return str(tmp_path / "meta.db")


def profile(plate: str = WATCHED, decision: str = "CLEAR", **extra) -> dict:
    base = {
        "plate_number": plate,
        "decision": decision,
        "confidence": "HIGH",
        "conflicts": [],
        "provenance": {},
        "source_availability": {"REG": "OK", "INS": "OK", "THEFT": "OK", "CAM": "OK"},
        "last_seen_location": None,
        "last_seen_time": None,
    }
    base.update(extra)
    return base


# ---------------------------------------------------------- tables & storage

def test_init_meta_db_creates_the_two_new_tables(meta):
    with sqlite3.connect(meta) as conn:
        names = {r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    assert {"WATCHLIST", "ALERT_LOG"} <= names


def test_add_list_remove_round_trip(meta):
    assert watchlist.listing() == []
    watchlist.add(WATCHED, "Repeat uninsured offender")
    rows = watchlist.listing()
    assert len(rows) == 1
    assert rows[0]["plate"] == WATCHED
    assert rows[0]["reason"] == "Repeat uninsured offender"
    assert rows[0]["added_by"] == "operator"
    assert rows[0]["added_at"]
    assert watchlist.is_watched(WATCHED) is True

    watchlist.remove(WATCHED)
    assert watchlist.listing() == []
    assert watchlist.is_watched(WATCHED) is False


def test_the_plate_is_canonicalised_on_the_way_in_and_on_lookup(meta):
    watchlist.add("dl-05 cd 9876", "typed by a patrol officer")
    assert watchlist.listing()[0]["plate"] == WATCHED
    assert watchlist.is_watched("DL 05 CD 9876") is True
    watchlist.remove("dl05cd9876")
    assert watchlist.is_watched(WATCHED) is False


def test_adding_the_same_plate_twice_updates_the_reason_instead_of_duplicating(meta):
    watchlist.add(WATCHED, "first reason")
    watchlist.add(WATCHED, "second reason", added_by="inspector")
    rows = watchlist.listing()
    assert len(rows) == 1
    assert rows[0]["reason"] == "second reason"
    assert rows[0]["added_by"] == "inspector"


def test_removing_a_plate_that_was_never_watched_is_harmless(meta):
    watchlist.remove("XX00XX0000")
    assert watchlist.listing() == []


# -------------------------------------------------------------------- check

def test_an_unwatched_clean_plate_raises_no_alert(meta):
    assert watchlist.check(profile()) == []
    assert watchlist.alerts() == []


def test_a_watched_plate_logs_an_alert_carrying_the_camera_evidence(meta):
    watchlist.add(WATCHED, "flagged for patrol")
    alerts = watchlist.check(profile(
        last_seen_location="NH-48 Gurgaon Toll",
        last_seen_time="2026-09-01T08:14:00",
    ))
    assert len(alerts) == 1
    alert = alerts[0]
    assert alert["plate"] == WATCHED
    assert alert["reason"] == "flagged for patrol"
    assert alert["seen_at"] == "2026-09-01T08:14:00"
    assert alert["location"] == "NH-48 Gurgaon Toll"
    assert alert["decision"] == "CLEAR"
    assert alert["ts"]

    logged = watchlist.alerts()
    assert len(logged) == 1
    assert logged[0]["plate"] == WATCHED
    assert logged[0]["location"] == "NH-48 Gurgaon Toll"


def test_a_stolen_plate_raises_a_hotlist_hit_even_though_nobody_watched_it(meta):
    alerts = watchlist.check(profile("HR26EF4455", "STOLEN — ALERT POLICE"))
    assert len(alerts) == 1
    assert "hotlist" in alerts[0]["reason"].lower()
    assert alerts[0]["decision"] == "STOLEN — ALERT POLICE"
    assert len(watchlist.alerts()) == 1


def test_a_scrapped_plate_raises_the_same_hotlist_hit(meta):
    alerts = watchlist.check(profile("DL03SC5566", "SCRAPPED — ALERT POLICE"))
    assert alerts and "hotlist" in alerts[0]["reason"].lower()


def test_a_clear_verdict_raises_no_hotlist_hit(meta):
    assert watchlist.check(profile("DL01AB1234", "CLEAR")) == []


def test_a_watched_stolen_plate_raises_both_the_watch_and_the_hotlist_alert(meta):
    watchlist.add("HR26EF4455", "owner under investigation")
    alerts = watchlist.check(profile("HR26EF4455", "STOLEN — ALERT POLICE"))
    reasons = [a["reason"].lower() for a in alerts]
    assert len(alerts) == 2
    assert any("investigation" in r for r in reasons)
    assert any("hotlist" in r for r in reasons)


def test_alerts_come_back_newest_first_and_respect_the_limit(meta):
    watchlist.add(WATCHED, "r")
    for _ in range(4):
        watchlist.check(profile(decision="CLEAR"))
    assert len(watchlist.alerts()) == 4
    assert len(watchlist.alerts(limit=2)) == 2
    ids = [a["alert_id"] for a in watchlist.alerts()]
    assert ids == sorted(ids, reverse=True)


def test_check_canonicalises_the_plate_before_matching(meta):
    watchlist.add(WATCHED, "r")
    assert watchlist.check(profile("DL-05-CD-9876")) != []


# ------------------------------------------------- core.py wires both in

def test_run_global_query_attaches_risk_and_alerts(cluster):  # noqa: F811
    from mediator.core import run_global_query

    watchlist.add(PLATE, "attached by the integration test")
    profile_out = run_global_query(PLATE, ["all"])["profile"]

    assert profile_out["risk"]["value"] == score(profile_out).value
    assert profile_out["risk"]["level"] in ("LOW", "MEDIUM", "HIGH", "CRITICAL")
    assert profile_out["risk"]["factors"], "the risk score must name what produced it"
    assert [a["plate"] for a in profile_out["alerts"]] == [PLATE]


def test_core_never_raises_when_the_watchlist_store_is_broken(cluster, monkeypatch):  # noqa: F811
    from mediator.core import run_global_query

    def boom(_profile):
        raise RuntimeError("meta.db is on fire")

    monkeypatch.setattr(watchlist, "check", boom)
    profile_out = run_global_query(PLATE, ["all"])["profile"]
    assert profile_out["alerts"] == []  # degraded, not crashed
    assert profile_out["decision"]  # the decision itself is untouched


# ---------------------------------------------------------------- GUI: tab

def _watchlist_page() -> None:
    # AppTest re-executes this body as its own script, so it must import what it uses.
    from app.tabs.watchlist import render
    render()


def test_watchlist_tab_adds_then_removes_a_plate(cluster):  # noqa: F811
    at = AppTest.from_function(_watchlist_page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert watchlist.listing() == []

    at.text_input(key="wl_plate").set_value("dl-05 cd 9876")
    at.text_input(key="wl_reason").set_value("seen near the toll every night")
    at.button(key="wl_add").click().run()
    assert not at.exception, [e.value for e in at.exception]
    rows = watchlist.listing()
    assert [r["plate"] for r in rows] == [WATCHED]
    assert rows[0]["reason"] == "seen near the toll every night"

    at.button(key="wl_rm_0").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert watchlist.listing() == []


def test_watchlist_tab_shows_recent_alerts(cluster):  # noqa: F811
    watchlist.add(WATCHED, "flagged")
    watchlist.check(profile(last_seen_location="NH-48", last_seen_time="2026-09-01T08:14:00"))
    at = AppTest.from_function(_watchlist_page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.dataframe, "the recent-alerts table did not render"
    rendered = "".join(str(df.value) for df in at.dataframe)
    assert "NH-48" in rendered


# --------------------------------------------------------- GUI: investigate

def _investigate_page() -> None:
    from app.tabs.investigate import render
    render()


def test_investigate_watch_button_toggles_and_then_shows_an_alert_banner(cluster):  # noqa: F811
    at = AppTest.from_function(_investigate_page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    watch = at.button(key="inv_watch")
    assert watch.disabled is False
    assert "Add to watchlist" in watch.label

    at.text_input(key="inv_watch_reason").set_value("repeat offender")
    at.button(key="inv_watch").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert watchlist.is_watched(PLATE) is True
    # The same run that watched it must already say so: the button is now a remove.
    assert "Remove from watchlist" in at.button(key="inv_watch").label

    # Next query on that plate files an alert, and the banner announces it.
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    banners = [m.value for m in at.markdown if "fm-banner--alert" in m.value]
    assert any("Watchlist hit" in b for b in banners), banners
    assert at.session_state["latest_result"]["profile"]["alerts"]

    at.button(key="inv_watch").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert watchlist.is_watched(PLATE) is False


def test_investigate_renders_the_risk_gauge(cluster):  # noqa: F811
    at = AppTest.from_function(_investigate_page, default_timeout=120).run()
    assert not at.exception, [e.value for e in at.exception]
    gauges = [m.value for m in at.markdown if "fm-risk-bar" in m.value]
    assert gauges, "no risk gauge was rendered"
    assert "Risk" in gauges[0]
