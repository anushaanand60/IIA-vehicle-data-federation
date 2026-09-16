"""Evaluate the schema matcher against the hand-made gold mapping (design PDF section 6).

Precision = correct proposals / all proposals. Recall = correct proposals / gold correspondences.
Schemas are read through each wrapper's /schema endpoint, exactly as the Matcher tab does.
"""
from __future__ import annotations

import importlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from mediator.matcher import match_source_schema

ROOT = Path(__file__).resolve().parents[1]
GOLD_PATH = ROOT / "data" / "gold_mapping.json"
SOURCES = ("REG", "INS", "THEFT", "CAM", "PUC")
Triple = tuple[str, str, str]  # (source_table, source_attr, global_attr)


def load_gold(path: Path = GOLD_PATH) -> dict[str, list[Triple]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {sid: [tuple(row) for row in rows] for sid, rows in raw.items() if sid in SOURCES}


def score(predicted: Iterable[Triple], gold: Iterable[Triple]) -> dict[str, Any]:
    # Table and column names compare case-insensitively: PostgreSQL folds them to lower case.
    p = {(t.lower(), c.lower(), g) for t, c, g in predicted}
    g = {(t.lower(), c.lower(), a) for t, c, a in gold}
    return {**_ratios(len(p & g), len(p - g), len(g - p)), "false_positives": sorted(p - g), "missed": sorted(g - p)}


def fetch_schema(source_id: str) -> dict[str, Any]:
    module = importlib.import_module(f"sources.{source_id.lower()}.wrapper")
    return TestClient(module.app).get("/schema").json()


def evaluate(theta: float = 0.55, sources: Iterable[str] = SOURCES) -> dict[str, Any]:
    gold = load_gold()
    per_source = {}
    for source_id in sources:
        result = match_source_schema(fetch_schema(source_id), theta=theta)
        predicted = {(c["source_table"], c["source_attr"], c["global_attr"]) for c in result["correspondences"]}
        per_source[source_id] = score(predicted, gold.get(source_id, []))
    totals = {k: sum(r[k] for r in per_source.values()) for k in ("tp", "fp", "fn")}
    return {"theta": theta, "per_source": per_source, "overall": _ratios(**totals)}  # micro-averaged


def _ratios(tp: int, fp: int, fn: int) -> dict[str, Any]:
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "fp": fp, "fn": fn, "precision": precision, "recall": recall, "f1": f1}
