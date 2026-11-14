"""CONTEXT-ADDED (R2/R3: cross-agency timeline) - pure tests of mediator/timeline.py and a render smoke test.

Hand-made ContextRows cover all four date encodings the agencies use (ISO date, DD/MM/YYYY, epoch seconds, ISO
timestamp); the graph is the real data/context_graph.json, so the time columns and formats are the declared ones.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from streamlit.testing.v1 import AppTest

from mediator.context_types import (Conflict, ContextResult, ContextRow, DocResult, ParsedQuery, RetrievedDoc)
from mediator.timeline import build_timeline, takeaway

IST = timezone(timedelta(hours=5, minutes=30))
NOW = datetime(2026, 11, 14)
PLATE = "MH12EF0248"


def _epoch(y: int, m: int, d: int) -> int:
    return int(datetime(y, m, d, tzinfo=IST).timestamp())


def _row(sid: str, table: str, pk, row) -> ContextRow:
    return ContextRow(sid, table, pk, row, 0.9, [f"{table}#{pk}"], 0)


def rows(open_fir: bool = True):
    out = [
        _row("REG", "VEHICLE_REGISTRATION", 248, {"registration_no": PLATE, "registered_on": "2024-06-14",
                                                   "make": "Hyundai", "model": "Creta", "colour": "White"}),
        _row("INS", "POLICY_RECORDS", 203, {"policy_id": 203, "vehicle_reg": "MH-12-EF-0248",
                                            "policy_start": "12/01/2026", "policy_until": "16/08/2027"}),
        _row("THEFT", "CRIME_RECORDS", 5, {"incident_id": 5, "fir_no": "FIR00005/2026", "reported_date": _epoch(2026, 8, 30),
                                           "stolen_flag": "Y", "recovered_flag": "N" if open_fir else "Y",
                                           "case_status": "OPEN" if open_fir else "CLOSED"}),
        _row("CAM", "PLATE_CAPTURES", 377, {"capture_id": 377, "plate_id": PLATE, "camera_id": "CAM002",
                                            "captured_at": "2026-07-13T06:00:00"}),
        _row("CAM", "CAMERAS", "CAM002", {"camera_id": "CAM002", "location_name": "Ring Road Junction"}),
        _row("PUC", "CERT_TESTS", 455, {"test_id": 455, "cert_no": "PUC-2026-215", "test_ts": "2026-09-04T12:05:00"}),
    ]
    if not open_fir:
        out.append(_row("THEFT", "STOLEN_PROPERTY", 5, {"incident_id": 5, "recovered_on": _epoch(2026, 9, 1)}))
    return out


def ctx(rs) -> ContextResult:
    return ContextResult(query=ParsedQuery(text=PLATE, plates=[PLATE]), rows=rs, stats=[])


def doc(doc_id: str, text: str, linked, body: bool = True, conflicts=None) -> RetrievedDoc:
    return RetrievedDoc(doc_id, "FIR_NARRATIVE", f"FIR narrative {doc_id}", 1.0, "" if body else text,
                        linked_rows=linked, body=text if body else "", conflicts=conflicts or [])


def test_four_date_encodings_land_on_one_clock():
    tl = build_timeline(ctx(rows()), None, now=NOW)
    when = {(e.source_id, e.attr): e.when for e in tl.events}
    assert when[("REG", "registered_on")] == datetime(2024, 6, 14)               # ISO date
    assert when[("INS", "policy_start")] == datetime(2026, 1, 12)                # DD/MM/YYYY (second date)
    assert when[("INS", "policy_until")] == datetime(2027, 8, 16)
    assert when[("THEFT", "reported_date")] == datetime(2026, 8, 30)             # epoch seconds, IST midnight
    assert when[("CAM", "captured_at")] == datetime(2026, 7, 13, 6, 0)           # ISO timestamp
    assert [e.when for e in tl.events] == sorted(e.when for e in tl.events)
    labels = {e.label for e in tl.events}
    assert "Insurance policy POL-000203 starts" in labels and "Police FIR FIR00005/2026 filed" in labels
    assert "Camera sighting at Ring Road Junction" in labels                    # place found along the FK edge
    assert any(iv.kind == "cover" and iv.start == datetime(2026, 1, 12) for iv in tl.intervals)
    assert tl.counts["agencies"] == 5 and tl.counts["forks"] == 0


def test_puc_test_inside_open_stolen_interval_is_the_alibi():
    tl = build_timeline(ctx(rows()), None, now=NOW)
    stolen = [iv for iv in tl.intervals if iv.kind == "stolen"]
    assert len(stolen) == 1 and stolen[0].open_ended and stolen[0].end == NOW
    inside = [e for e in tl.events if e.inside_stolen]
    assert [(e.source_id, e.pk) for e in inside] == [("PUC", 455)]               # the July camera read is before it
    assert takeaway(tl) == ("A PUC test on 4 Sep 2026 happened while the car was officially stolen "
                            "(FIR open since 30 Aug 2026).")


def test_recovery_closes_the_interval():
    tl = build_timeline(ctx(rows(open_fir=False)), None, now=NOW)
    stolen = [iv for iv in tl.intervals if iv.kind == "stolen"][0]
    assert not stolen.open_ended and stolen.end.date().isoformat() == "2026-09-01"
    assert not any(e.inside_stolen for e in tl.events)                          # the PUC test came after recovery
    assert "Vehicle recovered" in {e.label for e in tl.events}


def test_text_date_beyond_tolerance_is_a_fork():
    d = doc("D00900", "The white Creta was stolen on 10 August 2026 from Sector 14.", [("THEFT", "CRIME_RECORDS", 5)])
    tl = build_timeline(ctx(rows()), DocResult([d], "context"), now=NOW)
    assert len(tl.forks) == 1
    f = tl.forks[0]
    assert (f.attr, f.text_value, f.db_value, f.delta_days) == ("incident_date", "10 Aug 2026", "30 Aug 2026", -20)
    assert f.row_eid == "THEFT:CRIME_RECORDS#5:reported_date"
    assert any(e.kind == "text" and e.doc_id == "D00900" for e in tl.events)


def test_text_date_within_tolerance_is_no_fork_and_snippet_is_used_when_body_is_empty():
    d = doc("D00804", "I saw them drive it away on the night of 29th August 2026.", [("THEFT", "CRIME_RECORDS", 5)],
            body=False)
    tl = build_timeline(ctx(rows()), DocResult([d], "context"), now=NOW)
    assert tl.forks == [] and tl.counts["text_events"] == 1                     # 1 day off, tolerance 3


def test_recovered_text_against_open_fir_is_a_fork_even_from_a_conflict_only():
    c = Conflict("recovery", "OPEN", "THEFT", "recovered on 22nd September 2026 from an abandoned plot", "D00260",
                 (0, 9), "text says the vehicle was recovered; FIR 5 is OPEN")
    d = doc("D00260", "FIR registered on 30th August 2026. The vehicle was recovered...",
            [("THEFT", "CRIME_RECORDS", 5)], body=False, conflicts=[c])
    tl = build_timeline(ctx(rows()), DocResult([d], "context"), now=NOW)
    rec = [f for f in tl.forks if f.attr == "recovered_on"]
    assert len(rec) == 1 and rec[0].delta_days is None and "still open" in rec[0].db_value


def test_unlinked_or_irrelevant_documents_add_nothing():
    other = doc("D00709", "A blue Creta was stolen on June 14, 2026.", [])
    generic = doc("D01000", "Thefts reported on 1 May 2026.", [("THEFT", "CRIME_RECORDS", 5)])
    generic.relevant = False
    tl = build_timeline(ctx(rows()), DocResult([other, generic], "context"), now=NOW)
    assert tl.counts["text_events"] == 0


def test_empty_input_and_determinism():
    empty = build_timeline(None, None, now=NOW)
    assert empty.events == [] and empty.forks == [] and empty.intervals == []
    assert takeaway(empty) == "No dated records were found for this vehicle."
    a = build_timeline(ctx(rows()), None, now=NOW)
    b = build_timeline(ctx(list(reversed(rows()))), None, now=NOW)
    assert [e.eid for e in a.events] == [e.eid for e in b.events]


def test_quiet_takeaway_without_contradictions():
    rs = [r for r in rows() if r.source_id != "THEFT"]
    assert takeaway(build_timeline(ctx(rs), None, now=NOW)).endswith("no contradictions in time.")


# ------------------------------------------------------------------ render smoke

SCRIPT = """
import sys
sys.path[:0] = [{root!r}, {app!r}]
from tests.test_timeline import ctx, rows, doc
from mediator.context_types import DocResult
from tabs.context_timeline import render_timeline
case = {case!r}
if case == "full":
    d = doc("D00900", "The white Creta was stolen on 10 August 2026.", [("THEFT", "CRIME_RECORDS", 5)])
    render_timeline({{"context": ctx(rows()), "docs": DocResult([d], "context")}})
elif case == "none":
    render_timeline({{"context": None, "docs": None}})
else:
    render_timeline({{"context": object(), "docs": 42, "_timeline": "broken"}})
"""


def _app(case: str) -> AppTest:
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    return AppTest.from_string(SCRIPT.format(root=str(root), app=str(root / "app"), case=case), default_timeout=30).run()


def test_render_full_none_and_broken_never_raise():
    full = _app("full")
    assert not full.exception
    assert any("officially stolen" in m.value for m in full.markdown)
    assert not any("could not be drawn" in c.value for c in full.caption)
    assert len(full.expander) == 0 and any("from document D00900" in m.value for m in full.markdown)   # 7 events
    none = _app("none")
    assert not none.exception and "No timeline" in none.caption[0].value
    broken = _app("broken")
    assert not broken.exception and "could not be drawn" in broken.caption[0].value


def test_seen_after_cover_ended():
    rs = [r for r in rows() if r.source_id not in ("THEFT", "PUC")]
    rs[1].row["policy_until"] = "10/06/2026"                                   # DD/MM/YYYY; camera saw it 13 Jul
    assert takeaway(build_timeline(ctx(rs), None, now=NOW)) == (
        "A camera sighting on 13 Jul 2026 came 33 days after the insurance cover ended (10 Jun 2026).")
