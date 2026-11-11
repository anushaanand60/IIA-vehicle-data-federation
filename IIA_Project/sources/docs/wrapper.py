"""CONTEXT-ADDED (R3: sixth autonomous source) DOCS wrapper: the documents agency. SQLite by default, API on :8006.

Same factory as the five structured sources, so /health, /schema and the guarded /query work unchanged
(DOCS_DB_URL selects another engine). One extra route, POST /search, ranks documents INSIDE the source:

    {"terms":   [{"term": "Swift", "weight": 0.42}, ...],     weighted descriptors from the query context
     "anchors": [{"value": "DL05CD9876", "attr": "plate"}],   identifiers, matched typo/OCR-tolerantly
     "k": 20, "doc_types": null | ["FIR_NARRATIVE", ...]}
  -> {"rows": [{doc_id, doc_type, title, issued_on, issuer, score, body, anchor_hits}], "row_count",
      "elapsed_ms", "index": {"docs", "terms", "built_ms"}}

Only the top k documents leave the source (the same "ship the best n, not every match" rule as /context).
"""
from __future__ import annotations

import threading
import time
from typing import Any, List, Optional

from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from sources.defaults import sqlite_default
from sources.docs.textindex import BM25Index
from sources.wrapper_template import from_env, serve

MAX_K = 100


class SearchTerm(BaseModel):
    term: str
    weight: float = 1.0


class SearchAnchor(BaseModel):
    value: str
    attr: Optional[str] = None


class SearchRequest(BaseModel):
    terms: List[SearchTerm] = Field(default_factory=list)
    anchors: List[SearchAnchor] = Field(default_factory=list)
    k: int = 20
    doc_types: Optional[List[str]] = None


def add_search_route(app: Any) -> None:
    """Attach POST /search to a factory-built app. The index lives in the app, one per wrapper process."""
    state = {"index": None, "built_ms": 0.0}
    lock = threading.Lock()

    def current_index() -> BM25Index:
        # Freshness without a full re-read: a cheap signature query per search; the index is rebuilt only
        # when the table changed (a new or edited document). Trade-off: one extra tiny query per call.
        with app.state.engine.connect() as conn:
            sig = tuple(conn.execute(text(
                "SELECT COUNT(*), MAX(doc_id), SUM(LENGTH(body)) FROM DOCUMENTS")).one())
            with lock:
                idx = state["index"]
                if idx is None or idx.signature != sig:
                    t0 = time.perf_counter()
                    rows = conn.execute(text(
                        "SELECT doc_id, doc_type, title, body, issuer, issued_on FROM DOCUMENTS")).mappings().all()
                    idx = BM25Index.build(rows, signature=sig)
                    state["index"], state["built_ms"] = idx, (time.perf_counter() - t0) * 1000
        return idx

    @app.post("/search")
    def search(req: SearchRequest) -> JSONResponse:
        started = time.perf_counter()
        if app.state.engine is None:
            return JSONResponse(status_code=503, content={"error": f"database unreachable: {app.state.engine_error}"})
        try:
            idx = current_index()
        except SQLAlchemyError as exc:
            return JSONResponse(status_code=503, content={"error": f"DOCUMENTS unavailable: {str(exc)[:300]}"})
        rows = idx.search([t.model_dump() for t in req.terms], [a.model_dump() for a in req.anchors],
                          k=max(0, min(req.k, MAX_K)), doc_types=req.doc_types)
        return JSONResponse(content={
            "rows": rows, "row_count": len(rows), "elapsed_ms": int((time.perf_counter() - started) * 1000),
            "index": {"docs": idx.n_docs, "terms": len(idx.postings), "built_ms": round(state["built_ms"], 1)}})


app = from_env(
    "DOCS",
    default_url=sqlite_default("DOCS"),
    default_dbms="SQLite",
    default_tables=["DOCUMENTS"],
)
add_search_route(app)

if __name__ == "__main__":
    serve(app, default_port=8006)
