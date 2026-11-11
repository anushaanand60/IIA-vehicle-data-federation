"""CONTEXT-ADDED (R3: document retrieval ablation) nDCG@10 / MRR / link P-R / conflict recall for the three modes.

    python scripts/evaluate_docs.py               # live: DOCS on :8006, context from context discovery
    python scripts/evaluate_docs.py --offline     # no ports: DOCS app in-process over sources/docs/docs.db,
                                                  # context = FK closure over the root CSVs
    python scripts/evaluate_docs.py --offline --show DL05CD9876   # one retrieval, printed

The live run needs the wrappers up; do not run it while the test suite owns ports 8001-8006.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from mediator.context_types import SourceStats  # noqa: E402
from mediator.doc_eval import csv_context, evaluate_docs, pick_plates, load_gold  # noqa: E402
from mediator.doc_retrieval import retrieve  # noqa: E402


class LocalDocsClient:
    """SourceClient look-alike that serves DOCS from the wrapper app in-process (no network, no port)."""

    def __init__(self) -> None:
        from fastapi.testclient import TestClient
        from sources.docs.wrapper import app
        self.http = TestClient(app)
        self._stats = SourceStats("DOCS")

    def post(self, source_id: str, path: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        if source_id != "DOCS":
            return None
        resp = self.http.post(path, json=payload)
        self._stats.round_trips += 1
        self._stats.bytes_shipped += len(resp.content)
        if resp.status_code != 200:
            self._stats.status, self._stats.error = "ERROR", resp.text[:200]
            return None
        body = resp.json()
        self._stats.rows_shipped += len(body.get("rows", []))
        return body

    def query(self, source_id: str, sql: str) -> List[Dict[str, Any]]:
        return []

    def stats(self) -> List[SourceStats]:
        return [self._stats]


def show(plate: str, client: Any) -> None:
    ctx = csv_context(plate)
    gold = load_gold().get("by_plate", {}).get(plate, {})
    print(f"context rows: {len(ctx.rows)}  gold docs for {plate}: {gold}")
    for mode in ("query_only", "context", "context+anchors"):
        res = retrieve(ctx, k=5, mode=mode, client=client)
        print(f"\n== {mode}  ({res.rows_shipped} docs shipped, {res.elapsed_ms} ms)")
        for d in res.docs:
            print(f"  {d.doc_id} {d.doc_type:<17} {d.score:7.2f} gold={gold.get(d.doc_id, 0)}  {d.title}")
            print(f"      snippet: {d.snippet[:160]}")
            print(f"      mentions: {[(m.attr, m.text, m.similarity) for m in d.mentions][:6]}")
            print(f"      linked: {d.linked_rows[:6]}")
            for c in d.conflicts:
                print(f"      CONFLICT {c.attr}: db={c.db_value} text={c.text_value} ({c.note})")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--plates", type=int, default=40)
    ap.add_argument("--show", help="print one plate's retrieval in every mode")
    ap.add_argument("--out", help="write the JSON result here")
    args = ap.parse_args()
    if args.offline:
        client: Any = LocalDocsClient()
    else:
        from mediator.source_client import SourceClient
        client = SourceClient()
    if args.show:
        show(args.show.upper(), client)
        return
    plates = pick_plates(load_gold(), n=args.plates)
    contexts = {p: csv_context(p) for p in plates} if args.offline else None
    result = evaluate_docs(contexts, client=client, k=args.k, plates=plates)
    if args.offline:
        result["context_builder"] = "csv_fk_closure"
    for mode, m in result["modes"].items():
        print(f"  {mode:<16} " + "  ".join(f"{k}={v:.3f}" if isinstance(v, float) else f"{k}={v}" for k, v in m.items()))
    if args.out:
        Path(args.out).write_text(json.dumps(result, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
