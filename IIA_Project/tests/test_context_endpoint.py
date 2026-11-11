"""CONTEXT-ADDED (R2): POST /context -- the in-DBMS scored WITH RECURSIVE query, its bound, guard and dialects.

Tiny contract-shaped SQLite sources (tests/context_fixture.py) served through TestClient: no ports.
The closure mode of the same endpoint is the oracle for the bounded mode.
"""
from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from mediator.context_scoring import load_graph
from sources.context_endpoint import guard_context_sql
from sources.context_sql import build_context_sql
from sources.wrapper_template import QueryRejected, create_app, guard_sql
from tests.context_fixture import STORY, TABLES, build

GRAPH = load_graph()


@pytest.fixture(scope="module")
def urls(tmp_path_factory):
    return build(tmp_path_factory.mktemp("ctx"), seed=7, vehicles=20)


def app_for(urls, sid: str) -> TestClient:
    return TestClient(create_app(urls[sid], source_id=sid, dbms="sqlite", tables=TABLES[sid]))


def ctx(urls, sid: str, **body):
    resp = app_for(urls, sid).post("/context", json=body)
    assert resp.status_code == 200, resp.text
    return resp.json()


def diversified(rows, per_parent: int, theta: float):
    """Central re-ranking of the closure answer with the endpoint's own rules (oracle)."""
    keep = [r for r in rows if r["affinity"] >= theta - 1e-12]
    by_parent = {}
    for r in sorted(keep, key=lambda r: (-r["score"], r["table"], r["pk_key"])):
        parent = r["path"][-2] if len(r["path"]) > 1 else "^" + r["path"][-1]
        by_parent.setdefault(parent, []).append(r)
    elig = [r for rs in by_parent.values() for r in rs[:per_parent]]
    return sorted(elig, key=lambda r: (-r["score"], r["table"], r["pk_key"]))


@pytest.mark.parametrize("sid", ["REG", "INS", "THEFT", "CAM", "PUC"])
def test_top_n_rows_with_path_hops_and_full_row(urls, sid):
    body = ctx(urls, sid, seeds={"plate": [STORY]}, n=4)
    assert 1 <= body["row_count"] == len(body["rows"]) <= 4
    for r in body["rows"]:
        assert r["row"] and 0.0 <= r["score"] <= 1.0 and r["hops"] == len(r["path"]) - 1
        assert r["path"][-1] == f"{r['table']}#{r['pk_key']}"
    scores = [r["score"] for r in body["rows"]]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.parametrize("sid,n", [("REG", 2), ("THEFT", 3), ("CAM", 3), ("INS", 2), ("PUC", 1)])
def test_bounded_equals_closure_reranked_and_bound_is_exact(urls, sid, n):
    seeds, theta, pp = {"plate": [STORY]}, 0.05, 3
    bounded = ctx(urls, sid, seeds=seeds, n=n, theta=theta, per_parent=pp, keywords=["theft"])
    closure = ctx(urls, sid, seeds=seeds, mode="closure", keywords=["theft"])
    elig = diversified(closure["rows"], pp, theta)
    assert [round(r["score"], 6) for r in bounded["rows"]] == [round(r["score"], 6) for r in elig[:n]]
    expected_cut = elig[n]["score"] if len(elig) > n else 0.0
    assert bounded["bound_cut"] == pytest.approx(expected_cut, abs=1e-6)
    returned = {(r["table"], r["pk_key"]) for r in bounded["rows"]}
    unreturned = [r["score"] for r in closure["rows"] if (r["table"], r["pk_key"]) not in returned]
    assert all(s <= bounded["bound"] + 1e-9 for s in unreturned)   # nothing unsent beats the bound


def test_per_parent_cap_spreads_the_answer(urls):
    # A police station is a hub: every incident it registered hangs off it.
    body = ctx(urls, "THEFT", seeds={"pks": {"STATIONS": ["PS Rohini"]}}, n=10, per_parent=1, theta=0.0)
    parents = [r["path"][-2] for r in body["rows"] if len(r["path"]) > 1]
    assert parents and len(parents) == len(set(parents))           # at most one child per parent
    wide = ctx(urls, "THEFT", seeds={"pks": {"STATIONS": ["PS Rohini"]}}, n=10, per_parent=50, theta=0.0)
    assert len([r for r in wide["rows"] if len(r["path"]) == 2]) > 1  # without the cap the hub floods the answer
    assert body["bound_diversity"] > 0 and body["bound"] >= body["bound_diversity"]


def test_theta_prunes_paths(urls):
    loose = ctx(urls, "CAM", seeds={"plate": [STORY]}, n=50, theta=0.0)
    tight = ctx(urls, "CAM", seeds={"plate": [STORY]}, n=50, theta=0.5)
    assert len(tight["rows"]) < len(loose["rows"])
    assert all(r["affinity"] >= 0.5 for r in tight["rows"])
    assert 0 < tight["bound_pruned"] < 0.5


def test_keyword_time_and_decision_raise_the_right_rows(urls):
    def score(body, table):
        return max(r["score"] for r in body["rows"] if r["table"] == table)
    plain = ctx(urls, "INS", seeds={"plate": [STORY]}, n=10)
    keyword = ctx(urls, "INS", seeds={"plate": [STORY]}, n=10, keywords=["acme"])
    assert score(keyword, "INSURERS") == pytest.approx(score(plain, "INSURERS") / 0.3 * 0.7)   # base 0.3 -> 0.7
    assert score(keyword, "POLICY_RECORDS") == score(plain, "POLICY_RECORDS")                 # no hit, no change
    cam = ctx(urls, "CAM", seeds={"plate": [STORY]}, n=10)
    windowed = ctx(urls, "CAM", seeds={"plate": [STORY]}, n=10, window=["2026-03-10", "2026-03-10"])
    seen = {r["pk_key"]: r for r in windowed["rows"] if r["table"] == "PLATE_CAPTURES"}
    for r in (r for r in cam["rows"] if r["table"] == "PLATE_CAPTURES" and r["pk_key"] in seen):
        inside = r["row"]["captured_at"].startswith("2026-03-10")
        assert seen[r["pk_key"]]["score"] == pytest.approx(r["score"] * (2.0 if inside else 1.0))
    theft = ctx(urls, "THEFT", seeds={"plate": [STORY]}, n=10)
    crime = next(r for r in theft["rows"] if r["table"] == "CRIME_RECORDS" and r["hops"] == 0)
    assert crime["row"]["stolen_flag"] == "Y" and crime["score"] == pytest.approx(1.0)  # 0.3 + 0.6 + 0.3, clipped


def test_hints_ship_bridge_columns_only_for_unreturned_rows(urls):
    body = ctx(urls, "REG", seeds={"plate": [STORY]}, n=2, theta=0.01)
    assert body["hints"]
    returned = {(r["table"], r["pk_key"]) for r in body["rows"]}
    for h in body["hints"]:
        assert set(h) == {"table", "pk", "affinity", "attr", "value", "hops", "seed"}
        assert (h["table"], h["pk"]) not in returned and h["attr"] in ("plate", "chassis", "person")
    pairs = [(h["attr"], h["value"]) for h in body["hints"]]
    assert len(pairs) == len(set(pairs))                      # one hint per bridge value
    high = ctx(urls, "REG", seeds={"plate": [STORY]}, n=2, theta=0.01, hint_min=0.5)
    assert all(h["affinity"] >= 0.5 for h in high["hints"]) and len(high["hints"]) < len(body["hints"])


def test_seed_weights_and_composite_pks(urls):
    body = ctx(urls, "THEFT", seeds={"pks": {"INCIDENT_PERSONS": ["1|1|ACCUSED"]},
                                     "weights": {"pk:INCIDENT_PERSONS#1|1|ACCUSED": 0.5}}, n=5)
    seed = next(r for r in body["rows"] if r["hops"] == 0)
    assert seed["pk"] == [1, 1, "ACCUSED"] and seed["affinity"] == pytest.approx(0.5)
    assert all(r["affinity"] <= 0.5 + 1e-9 for r in body["rows"])


def test_values_travel_as_bind_parameters(urls):
    body = ctx(urls, "REG", seeds={"plate": ["x' OR 1=1 --"], "chassis": ["'; DROP TABLE OWNERS"]},
               keywords=["' OR '1'='1"])
    assert body["rows"] == []
    assert ctx(urls, "REG", seeds={"plate": [STORY]})["rows"]           # OWNERS still there


def test_free_text_seeds_from_keyword_hits(urls):
    body = ctx(urls, "INS", seeds={}, keywords=["investigating"], n=3)
    assert body["rows"] and body["rows"][0]["table"] == "CLAIMS"


def test_unknown_source_is_404_and_bad_body_is_400(urls):
    app = TestClient(create_app(urls["REG"], source_id="NOPE", dbms="sqlite", tables=TABLES["REG"]))
    assert app.post("/context", json={"seeds": {"plate": [STORY]}}).status_code == 404
    reg = app_for(urls, "REG")
    assert reg.post("/context", json={"window": ["2026-01-01"]}).status_code == 400
    assert reg.post("/context", json={"n": 0}).status_code == 400


def test_dead_database_is_503(tmp_path):
    app = TestClient(create_app(f"sqlite:///{(tmp_path / 'nope' / 'x.db').as_posix()}", source_id="REG",
                                dbms="sqlite", tables=TABLES["REG"]))
    assert app.post("/context", json={"seeds": {"plate": [STORY]}}).status_code == 503


def test_tables_the_wrapper_does_not_publish_are_never_walked(urls):
    app = TestClient(create_app(urls["REG"], source_id="REG", dbms="sqlite", tables=["VEHICLE_REGISTRATION", "OWNERS"]))
    body = app.post("/context", json={"seeds": {"plate": [STORY]}, "explain": True}).json()
    assert {r["table"] for r in body["rows"]} <= {"VEHICLE_REGISTRATION", "OWNERS"}
    assert "VEHICLE_IDENTITY" not in body["sql"]


# --------------------------------------------------------------------------- guard

@pytest.mark.parametrize("sql, reason", [
    ("SELECT * FROM OWNERS", "with recursive"),
    ("WITH RECURSIVE w AS (SELECT 1 FROM OWNERS) SELECT * FROM w; DROP TABLE OWNERS", "multiple statements"),
    ("WITH RECURSIVE w AS (SELECT 1 FROM OWNERS) SELECT * FROM w -- x", "comment"),
    ("WITH RECURSIVE w AS (SELECT 1 FROM OWNERS) SELECT * FROM w /* x */", "comment"),
    ("WITH RECURSIVE w AS (SELECT 1 FROM OWNERS) DELETE FROM OWNERS", "forbidden keyword delete"),
    ("WITH RECURSIVE w AS (SELECT 1 FROM OWNERS) SELECT pg_sleep(5) FROM w", "forbidden keyword pg_sleep"),
    ("WITH RECURSIVE w AS (SELECT 1 FROM pg_shadow) SELECT * FROM w", "not in the whitelist"),
    ("WITH RECURSIVE w AS (SELECT 1 FROM OWNERS) SELECT * INTO t FROM w", "forbidden keyword into"),
])
def test_context_guard_rejects_injection(sql, reason):
    with pytest.raises(QueryRejected) as exc:
        guard_context_sql(sql, ["OWNERS"])
    assert reason in str(exc.value).lower()


def test_query_guard_is_not_widened():
    with pytest.raises(QueryRejected):
        guard_sql("WITH RECURSIVE w AS (SELECT 1 FROM OWNERS) SELECT * FROM w", ["OWNERS"])


def test_generated_sql_passes_its_own_guard_in_every_dialect():
    req = {"seeds": {"plate": [STORY], "chassis": ["MA3"], "pks": {"OWNERS": ["1"]}}, "keywords": ["theft"],
           "window": ["2026-01-01", "2026-12-31"]}
    for sid, src in GRAPH["sources"].items():
        for d in ("sqlite", "postgresql", "mysql"):
            weights = [(1.0, 0.5)] * len(src["edges"])
            sql, params = build_context_sql(GRAPH, sid, d, req, weights)
            if sql:
                guard_context_sql(sql, src["tables"])
                assert all(f":{k}" in sql for k in params)


# --------------------------------------------------------------------------- dialects

def render(sid: str, dialect: str) -> str:
    req = {"seeds": {"plate": [STORY]}, "keywords": ["open"], "window": ["2026-01-01", "2026-12-31"]}
    return build_context_sql(GRAPH, sid, dialect, req, [(1.0, 0.5)] * len(GRAPH["sources"][sid]["edges"]))[0]


def test_postgresql_fragments():
    sql = render("THEFT", "postgresql") + render("INS", "postgresql")
    assert "REGEXP_REPLACE(CAST(x.vehicle_number AS TEXT), '[^A-Za-z0-9]', '', 'g')" in sql
    assert "to_date(CAST(x.loss_date AS TEXT), 'DD/MM/YYYY')" in sql
    assert "to_timestamp(x.reported_date) AT TIME ZONE 'UTC'" in sql
    assert "strpos(" in sql and "AS DOUBLE PRECISION)" in sql
    assert "LEAST(" in sql and "CONCAT(" not in sql


def test_mysql_fragments():
    sql = render("THEFT", "mysql") + render("INS", "mysql")
    assert "||" not in sql                                   # MySQL reads || as OR
    assert "CONCAT(" in sql and "AS CHAR(4000)" in sql       # recursive CTE typed from its anchor
    assert "REGEXP_REPLACE(CAST(x.vehicle_number AS CHAR), '[^A-Za-z0-9]', '')" in sql
    assert "STR_TO_DATE(CAST(x.loss_date AS CHAR), '%d/%m/%Y')" in sql
    assert "DATE_ADD('1970-01-01', INTERVAL x.reported_date SECOND)" in sql
    assert "AS DOUBLE)" in sql and "INSTR(" in sql


def test_sqlite_fragments():
    sql = render("THEFT", "sqlite") + render("CAM", "sqlite")
    assert "date(x.reported_date, 'unixepoch')" in sql and "MIN(1, MAX(0," in sql
    assert "date(substr(CAST(x.captured_at AS TEXT), 1, 10))" in sql and "INSTR(" in sql


@pytest.mark.parametrize("env", ["CONTEXT_TEST_PG_URL", "CONTEXT_TEST_MYSQL_URL"])
def test_same_answer_on_a_real_server(env, urls, tmp_path):
    """Opt-in: point the env var at an empty scratch database (e.g. postgresql+psycopg2://u@127.0.0.1:55432/ctx)."""
    target = os.environ.get(env)
    if not target:
        pytest.skip(f"{env} not set")
    from tests.context_fixture import generate, load
    load(target, "THEFT", generate(7, 20)["THEFT"])
    remote = TestClient(create_app(target, source_id="THEFT", dbms="x", tables=TABLES["THEFT"], statement_timeout_s=10))
    body = {"seeds": {"plate": [STORY]}, "n": 5, "keywords": ["theft"], "window": ["2026-01-01", "2026-12-31"]}
    a, b = ctx(urls, "THEFT", **body), remote.post("/context", json=body).json()
    assert [(r["table"], r["pk_key"], round(r["score"], 6)) for r in a["rows"]] == \
           [(r["table"], r["pk_key"], round(r["score"], 6)) for r in b["rows"]]
