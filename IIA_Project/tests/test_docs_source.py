"""CONTEXT-ADDED (R3): the DOCS source - tokeniser, BM25 ranking and OCR-tolerant anchors inside the wrapper.

Every test runs the wrapper in-process over a temporary SQLite file: no port is bound, no cluster is started.
"""
from __future__ import annotations

import re
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sources.docs.textindex import BM25Index, canon, plate_spans, tokenize
from sources.docs.wrapper import add_search_route
from sources.wrapper_template import create_app

ROOT = Path(__file__).resolve().parents[1]
DOCS = [
    ("D1", "FIR_NARRATIVE", "FIR FIR00028/2026 - P.S. Karol Bagh",
     "Brief facts: the silver Maruti Suzuki Swift bearing registration no. DL-05-CD-9876 was stolen near Karol Bagh. "
     "FIR No. FIR00028/2026 registered.", "P.S. Karol Bagh", "2026-07-12"),
    ("D2", "NEWS", "Swift stolen", "A silver Swift registration dl 05 cd 9876 was stolen. Swift owners beware: "
     "Swift thefts, Swift again.", "City Times", "2026-07-13"),
    ("D3", "NEWS", "Creta towed", "A white Hyundai Creta bearing No. DL05CD9877 was towed from Lajpat Nagar.",
     "Delhi Daily", "2026-03-01"),
    ("D4", "ACCIDENT_REPORT", "Accident", "A red Tata Nexon bearing No. HR26EF4455 hit a divider in Gurgaon.",
     "Traffic Police", "2026-04-02"),
    ("D5", "RTO_NOTICE", "Transfer", "Ownership of Kia Seltos UP16GH1122 transferred in Gurugram.", "RTO UP16", "2026-05-05"),
    ("D6", "SURVEYOR_REPORT", "Pre-inspection", "Policy No. POL-000562 for MH12IJ7788, a grey Tata Nexon.",
     "TrustLine Insurers", "2026-06-06"),
]


def make_db(path: Path, docs=DOCS) -> str:
    ddl = re.sub(r"--[^\n]*", "", (ROOT / "sources" / "docs" / "schema.sql").read_text(encoding="utf-8"))
    with sqlite3.connect(path) as con:
        for stmt in [s for s in ddl.split(";") if s.strip()]:
            con.execute(stmt)
        con.executemany("INSERT INTO DOCUMENTS VALUES (?,?,?,?,?,?)", docs)
    return f"sqlite:///{path.as_posix()}"


@pytest.fixture()
def db_path(tmp_path: Path) -> Path:
    path = tmp_path / "docs.db"
    make_db(path)
    return path


@pytest.fixture()
def client(db_path: Path) -> TestClient:
    app = create_app(f"sqlite:///{db_path.as_posix()}", source_id="DOCS", dbms="SQLite", tables=["DOCUMENTS"],
                     admin_enabled=False)
    add_search_route(app)
    return TestClient(app)


def search(client: TestClient, **body):
    resp = client.post("/search", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


# ------------------------------------------------------------------ tokeniser

def test_plate_spellings_become_one_token():
    for text in ("DL-05-CD-9876", "dl 05 cd 9876", "DL05CD9876", "DL 05 CD 9876"):
        assert tokenize(f"car {text} seen") == ["car", "dl05cd9876", "seen"]


def test_ordinary_words_are_not_plates():
    assert plate_spans("stolen on 12 th 2026 near MG Road") == []
    assert plate_spans("as 12 th 2026") == []          # AS is not in the state-code list on purpose


def test_codes_and_variants():
    assert "fir000282026" in tokenize("FIR No. FIR00028/2026")
    assert "puc2026100" in tokenize("certificate PUC-2026-100")
    assert tokenize("Gurgaon") == tokenize("Gurugram") == ["gurugram"]
    assert tokenize("Suresh Kr. Yadav") == ["suresh", "kumar", "yadav"]
    assert canon("DL-05-CD-9876") == "dl05cd9876"


def test_bm25_prefers_rare_terms_and_term_frequency():
    idx = BM25Index.build([{"doc_id": d[0], "doc_type": d[1], "title": d[2], "body": d[3]} for d in DOCS])
    assert idx.idf("lajpat") > idx.idf("tata")                     # 1 doc vs 2 docs
    ranked = [r["doc_id"] for r in idx.search([{"term": "Swift", "weight": 1}])]
    assert ranked[0] == "D2"                                        # five Swifts beat one


# ------------------------------------------------------------------ the /search route

def test_health_and_guarded_query_still_work(client: TestClient):
    assert client.get("/health").json()["up"] is True
    rows = client.post("/query", json={"sql": "SELECT doc_id FROM DOCUMENTS"}).json()["rows"]
    assert len(rows) == len(DOCS)
    assert client.post("/query", json={"sql": "DELETE FROM DOCUMENTS"}).status_code == 400


def test_search_returns_top_k_with_scores(client: TestClient):
    body = search(client, terms=[{"term": "stolen Swift", "weight": 1.0}], k=1)
    assert body["row_count"] == 1 and len(body["rows"]) == 1
    hit = body["rows"][0]
    assert set(hit) >= {"doc_id", "doc_type", "title", "issued_on", "score", "body"}
    assert hit["doc_id"] in ("D1", "D2") and hit["score"] > 0
    assert body["index"]["docs"] == len(DOCS) and "elapsed_ms" in body


def test_plate_query_matches_every_spelling(client: TestClient):
    ids = {r["doc_id"] for r in search(client, terms=[{"term": "DL05CD9876", "weight": 1}], k=10)["rows"]}
    assert ids == {"D1", "D2"}


def test_anchor_is_ocr_tolerant(client: TestClient):
    rows = search(client, anchors=[{"value": "DLO5CD9B76", "attr": "plate"}], k=10)["rows"]
    assert {r["doc_id"] for r in rows} == {"D1", "D2"}
    hit = rows[0]["anchor_hits"][0]
    assert hit["token"] == "dl05cd9876" and 0.92 <= hit["similarity"] < 1.0


def test_anchor_refuses_one_character_off_plate(client: TestClient):
    # DL05CD9877 is a different car (hard negative): one non-OCR substitution must not match DL05CD9876
    rows = search(client, anchors=[{"value": "DL05CD9877", "attr": "plate"}], k=10)["rows"]
    assert [r["doc_id"] for r in rows] == ["D3"]


def test_anchor_hit_outranks_descriptor_only_match(client: TestClient):
    rows = search(client, terms=[{"term": "Tata Nexon", "weight": 1}],
                  anchors=[{"value": "MH12IJ7788", "attr": "plate"}], k=3)["rows"]
    assert rows[0]["doc_id"] == "D6"


def test_doc_type_filter(client: TestClient):
    rows = search(client, terms=[{"term": "Swift", "weight": 1}], doc_types=["NEWS"], k=10)["rows"]
    assert {r["doc_type"] for r in rows} == {"NEWS"}


def test_index_refreshes_when_documents_change(client: TestClient, db_path: Path):
    assert search(client, terms=[{"term": "superdari", "weight": 1}])["rows"] == []
    with sqlite3.connect(db_path) as con:
        con.execute("INSERT INTO DOCUMENTS VALUES ('D7','NEWS','x','Vehicle released on superdari.','y','2026-01-01')")
    assert [r["doc_id"] for r in search(client, terms=[{"term": "superdari", "weight": 1}])["rows"]] == ["D7"]


def test_bad_body_is_400_and_missing_table_is_503(client: TestClient, tmp_path: Path):
    assert client.post("/search", json={"terms": "not a list"}).status_code == 400
    empty = tmp_path / "empty.db"
    sqlite3.connect(empty).close()
    app = create_app(f"sqlite:///{empty.as_posix()}", source_id="DOCS", dbms="SQLite", tables=["DOCUMENTS"],
                     admin_enabled=False)
    add_search_route(app)
    assert TestClient(app).post("/search", json={"terms": [{"term": "x"}]}).status_code == 503


# ------------------------------------------------------------------ corpus generator (template path, no Ollama)

def test_llm_rewrite_guard_keeps_gold_exact():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    from phase3_docs_extras import faithful
    orig = "On 12/07/2026 at 02:45 hrs a red Swift DL-05-CD-9876 was stolen. FIR No. FIR00028/2026 registered."
    assert faithful(orig, "FIR00028/2026: a red Swift, DL-05-CD-9876, was stolen on 12th July 2026 at 02:45 hrs.", [])
    assert not faithful(orig, "On 13th July 2026 at 02:45 hrs a red Swift DL-05-CD-9876 was stolen. FIR00028/2026.", [])
    assert not faithful(orig, "On 12/07/2026 at 02:45 hrs a red Swift DL-05-CD-9877 was stolen. FIR00028/2026.", [])
    assert not faithful(orig, orig + " Section 379 IPC applies.", [])          # invented number


def test_template_corpus_is_reproducible_and_has_gold():
    import sys
    sys.path.insert(0, str(ROOT / "scripts"))
    import phase3_docs_generate as gen
    a, b = gen.generate(7), gen.generate(7)
    assert [(d.title, d.body) for d in a] == [(d.title, d.body) for d in b]
    assert len(a) > 500 and {d.doc_type for d in a} >= {"FIR_NARRATIVE", "NEWS", "RTO_NOTICE", "ACCIDENT_REPORT"}
    assert all(d.plates or d.doc_type == "NEWS" for d in a)
    planted = [d for d in a if d.conflicts]
    assert 0.02 < len(planted) / len(a) < 0.10
    assert any(d.distractor_for for d in a)
