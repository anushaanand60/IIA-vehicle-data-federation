"""CONTEXT-ADDED (R2/R3): Context Explorer and Context Evaluation render — with a faked backend, and without one.

The phase-3 backend modules are replaced in `sys.modules` by small fakes returning hand-built
ContextResult / TopKResult / DocResult / Indicator objects, so no cluster and no port is needed.
Setting a module to None in `sys.modules` makes its import fail: that is the "not delivered yet" case.
"""
from __future__ import annotations

import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from streamlit.testing.v1 import AppTest

from mediator.context_types import (Conflict, ContextResult, ContextRow, DocResult, Indicator, Mention,
                                    ParsedQuery, PersonCluster, Predicate, RankedVehicle, RetrievedDoc,
                                    SourceStats, TopKResult)

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ["mediator.query_understanding", "mediator.context_discovery", "mediator.analytics",
           "mediator.doc_retrieval", "mediator.grounded_summary", "mediator.entity_topk"]
PLATE = "DL05CD9876"


def _parse(text: str, use_llm: bool = True) -> ParsedQuery:
    canon = "".join(c for c in text.upper() if c.isalnum())
    if canon.startswith("DL") and len(canon) == 10:
        return ParsedQuery(text=text, plates=[canon])
    return ParsedQuery(text=text, predicates=[Predicate("stolen_status", "=", "STOLEN"),
                                              Predicate("vehicle_colour", "~", "white", confidence=0.83)],
                       keywords=["gurgaon"], parser="rules+llm")


def _context(complete: bool = True) -> ContextResult:
    rows = [
        ContextRow("REG", "VEHICLE_REGISTRATION", 5, {"registration_no": PLATE, "make": "Hyundai"}, 0.95,
                   ["VEHICLE_REGISTRATION#5"], 0),
        ContextRow("REG", "OWNERS", 7, {"full_name": "Asha Menon", "city": "Delhi"}, 0.71,
                   ["VEHICLE_REGISTRATION#5", "OWNERS#7"], 1),
        ContextRow("INS", "POLICY_RECORDS", 3, {"vehicle_reg": "DL-05-CD-9876", "policy_until": "12/07/2026"}, 0.88,
                   ["POLICY_RECORDS#3"], 0, via_bridge="plate", bridge_confidence=0.97),
        ContextRow("THEFT", "STOLEN_PROPERTY", 11, {"chassis_no": "MA3X"}, 0.4,
                   ["VEHICLE_IDENTITY#5", "STOLEN_PROPERTY#11"], 1, via_bridge="chassis"),
    ]
    stats = [SourceStats("REG", 2, 900, 1, 1, 12.0), SourceStats("INS", 1, 400, 1, 1, 9.0),
             SourceStats("THEFT", 1, 300, 2, 2, 15.0, status="OK" if complete else "DOWN")]
    res = ContextResult(query=ParsedQuery(text=PLATE, plates=[PLATE]), rows=rows, stats=stats, complete=complete,
                        baseline_rows=480, persons=[PersonCluster("P1", [("REG", "OWNERS", 7), ("INS", "POLICY_HOLDERS", 2)],
                                                                  {"name": "Asha Menon"}, 0.93)])
    res.trace = [{"round": 1, "tau_n": 0.6, "max_frontier_bound": 0.8, "expanded": 2, "pruned": 1},
                 {"round": 2, "tau_n": 0.7, "max_frontier_bound": 0.5, "expanded": 0, "pruned": 4}]
    return res


def _docs() -> DocResult:
    body = "FIR says vehicle DL 05 CD 9876, a white Hyundai, was recovered near Gurgaon on 3 March."
    return DocResult(docs=[RetrievedDoc(
        "D-1", "FIR_NARRATIVE", "Brief facts, FIR 112/2026", 0.81, body,
        mentions=[Mention("DL 05 CD 9876", "anchor", "plate", 20, 33, PLATE, 0.98, 2.0),
                  Mention("Gurgaon", "descriptor", "place", 70, 77, "Gurgaon", 1.0, 0.5)],
        linked_rows=[("THEFT", "CRIME_RECORDS", 4)],
        conflicts=[Conflict("case_status", "OPEN", "THEFT", "recovered", "D-1", (50, 59), "text says recovered")])],
        mode="context+anchors")


def install(monkeypatch: pytest.MonkeyPatch, *, complete: bool = True, discover_raises: bool = False) -> None:
    def discover(query: Any, n: int = 10, theta: float = 0.05, mode: str = "bounded", **_: Any) -> ContextResult:
        if discover_raises:
            raise RuntimeError("wrapper exploded")
        return _context(complete)

    fns = {
        "mediator.query_understanding": {"parse": _parse},
        "mediator.context_discovery": {"discover": discover},
        "mediator.analytics": {"indicators_for": lambda plate, context=None, client=None: [
            Indicator("ALIBI_PUC", "high", "PUC test while reported stolen",
                      "A pollution test needs the car present, yet it was tested inside the stolen interval.",
                      [("PUC", "CERT_TESTS", 9), ("THEFT", "CRIME_RECORDS", 4)]),
            Indicator("QUICK_CLAIM", "medium", "Theft claim 9 days after the policy began", "Claim filed early.", [])]},
        "mediator.doc_retrieval": {"retrieve": lambda ctx, k=5, mode="context", client=None: _docs()},
        "mediator.grounded_summary": {"summarise": lambda ctx, docs, use_llm=True: [
            {"sentence": "The policy lapsed on 12 July 2026.", "citations": [["INS", "POLICY_RECORDS", 3]]},
            {"sentence": "The FIR text says the car was recovered.", "citations": [{"doc_id": "D-1"}]}]},
        "mediator.entity_topk": {"topk": lambda q, k=10, client=None: TopKResult(
            q, [RankedVehicle(PLATE, 0.91, {"REG": 0.5, "THEFT": 0.41}, explanation=["white Hyundai", "open FIR"]),
                RankedVehicle("HR26EF4455", 0.77, {"REG": 0.77})], 14, 5, 40, 2000, 3)},
    }
    for name, attrs in fns.items():
        mod = types.ModuleType(name)
        for attr, fn in attrs.items():
            setattr(mod, attr, fn)
        monkeypatch.setitem(sys.modules, name, mod)


def _explorer() -> None:
    from app.tabs.context_explorer import render
    render()


def _evaluation() -> None:
    import os
    from pathlib import Path as P
    from app.tabs import context_evaluation as page
    page.DATA = P(os.environ["CTX_EVAL_JSON"])
    page.render()


def run(fn: Any) -> AppTest:
    at = AppTest.from_function(fn, default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    return at


def html(at: AppTest) -> str:
    return " ".join(m.value for m in at.markdown) + " ".join(c.value for c in at.caption)


def ask(at: AppTest, text: str) -> AppTest:
    at.text_input(key="cx_q").set_value(text)
    at.button(key="cx_go").click().run()
    assert not at.exception, [e.value for e in at.exception]
    return at


# ------------------------------------------------------------------ Context Explorer

def test_explorer_starts_with_only_the_search(monkeypatch):
    install(monkeypatch)
    at = run(_explorer)
    assert at.info and not at.tabs


def test_plate_query_shows_verdict_counter_and_detail_tabs(monkeypatch):
    install(monkeypatch)
    at = ask(run(_explorer), "dl-05-cd-9876")
    page = html(at)
    assert "cv-banner--alert" in page and "2 warning sign(s), 1 serious" in page
    assert "Less data moved" in page and "instead of 480" in page
    assert "skipped by the bound: 5 of 7" in page
    assert [t.label for t in at.tabs][:1] == ["Related records (4)"] and len(at.tabs) == 5
    assert "VEHICLE_REGISTRATION → OWNERS" in page and "via plate" in page
    assert "Text and database disagree on case_status" in page and "<mark" in page
    assert at.get("graphviz_chart"), "context graph missing"
    assert not at.warning


def test_free_text_ranks_vehicles_then_opens_one(monkeypatch):
    install(monkeypatch)
    at = ask(run(_explorer), "stolen white Creta Gurgaon")
    page = html(at)
    assert "Vehicles that fit best" in page and "LLM 83%" in page and "instead of 2,000" in page
    at.button(key="cx_open_0").click().run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.session_state["cx_result"]["plate"] == PLATE
    assert any(b.key == "cx_back_btn" for b in at.button)


def test_partial_answer_says_undetermined(monkeypatch):
    install(monkeypatch, complete=False)
    page = html(ask(run(_explorer), PLATE))
    assert "cv-banner--undetermined" in page and "THEFT did not answer" in page


def test_a_failing_discover_is_a_warning_not_a_crash(monkeypatch):
    install(monkeypatch, discover_raises=True)
    at = ask(run(_explorer), PLATE)
    assert any("wrapper exploded" in w.value for w in at.warning)
    assert "could not be gathered" in html(at)


@pytest.mark.parametrize("query", [PLATE, "stolen white Creta Gurgaon"])
def test_explorer_without_any_backend_module(monkeypatch, query):
    for name in BACKEND:
        monkeypatch.setitem(sys.modules, name, None)
    at = ask(run(_explorer), query)
    assert "Not available yet" in html(at)


# ------------------------------------------------------------------ Context Evaluation

def _evaluate_with_fakes() -> dict:
    spec = importlib.util.spec_from_file_location("evaluate_context", ROOT / "scripts" / "evaluate_context.py")
    script = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(script)
    from tests.test_context_eval import fake_discover, fake_topk
    from mediator.context_eval_run import Backend

    docs = lambda client=None, k=10: {"query_only": {"nDCG@10": 0.41, "MRR": 0.5},  # noqa: E731
                                      "context": {"nDCG@10": 0.66, "MRR": 0.7}, "context+anchors": {"nDCG@10": 0.8, "MRR": 0.86}}
    return script.evaluate(quick=True, backend=Backend(discover=fake_discover, topk=fake_topk, evaluate_docs=docs))


def test_evaluation_script_output_renders(monkeypatch, tmp_path):
    data = _evaluate_with_fakes()
    assert data["exactness_summary"] and data["movement_summary"] and data["retrieval"]
    # A second scale, so the line form of the chart is exercised too.
    data["movement_summary"] += [dict(r, scale="x10", rows=r["rows"] * 10) for r in data["movement_summary"]]
    path = tmp_path / "context_eval.json"
    path.write_text(json.dumps(data, default=str), encoding="utf-8")
    monkeypatch.setenv("CTX_EVAL_JSON", str(path))
    at = run(_evaluation)
    page = html(at)
    for title in ("Does it find what a central warehouse would find?", "Data moved as the data grows",
                  "Why the search can stop early", "How sensitive are the two knobs?", "Finding the best 10 vehicles",
                  "Documents: does the context help"):
        assert title in page, title
    # Hub seeds go straight to each wrapper's /context when discover() takes no seeds, so their share
    # depends on the real wrapper; the faked plate queries alone would be 100%.
    assert "Ours keeps" in page and "of the rows the central oracle picks" in page
    at.segmented_control(key="ce_metric").set_value("Latency").run()
    assert not at.exception, [e.value for e in at.exception]
    assert "lecture" not in page.lower() and "week" not in page.lower()


def test_evaluation_without_results_file(monkeypatch, tmp_path):
    monkeypatch.setenv("CTX_EVAL_JSON", str(tmp_path / "missing.json"))
    at = run(_evaluation)
    assert any("evaluate_context.py" in i.value for i in at.info)


# ------------------------------------------------------------------ widgets (pure markup)

def test_bridge_steps_render_in_the_chain_and_as_a_dashed_graph_edge():
    sys.path.insert(0, str(ROOT / "app"))
    from tabs import context_widgets as w
    path = ["VEHICLE_REGISTRATION#603", "OWNERS#603", "plate->INS", "POLICY_RECORDS#51"]
    assert "plate ⇒ INS" in w.path_chain(path) and "OWNERS" in w.path_chain(path)
    r = ContextRow("INS", "POLICY_RECORDS", 51, {}, 0.39, path, 3, via_bridge="plate")
    composite = ContextRow("THEFT", "INCIDENT_PERSONS", [12, 18, "ACCUSED"], {}, 0.1, [], 1)
    dot = w.context_dot([r, composite], {"VEHICLE_REGISTRATION": "REG", "OWNERS": "REG", "POLICY_RECORDS": "INS"})
    assert '"REG:OWNERS#603" -> "INS:POLICY_RECORDS#51" [style=dashed, label="plate"]' in dot
    # CONTEXT-REPLACED (R2: composite keys now print once as a|b|c, and a query node links the starting records):
    # assert "plate->INS" not in dot and "[12, 18, 'ACCUSED']" in dot
    assert "plate->INS" not in dot and "12|18|ACCUSED" in dot
    q = w.context_dot([r], {"VEHICLE_REGISTRATION": "REG", "OWNERS": "REG", "POLICY_RECORDS": "INS"}, query="DL01AB1234")
    assert '"__q" -> "REG:VEHICLE_REGISTRATION#603"' in q and 'label="DL01AB1234"' in q
    # CONTEXT-END
