"""Data-driven OCR substitution costs, estimated from the labelled benchmark (workstream C).

`similarity.ocr_confusion_cost` charges 0.2 for every pair in `plate_resolve.CONFUSABLE` and
1.0 for everything else. The benchmark (docs/BENCHMARK_NOISE.md, section 4) shows that table is
both too flat -- 0->O is 62 % of all observed substitutions, 5->S is 2 % -- and incomplete:
1/4, H/4, 1/A, D/U and 1/T occur and are not in it. Here the costs are *measured* instead.

Estimate (`estimate_costs`):
    For each labelled read whose length equals its true plate's, every position where the two
    differ is one observed substitution a<->b (positional alignment, as in BENCHMARK_NOISE §4;
    unequal lengths contribute nothing rather than a misaligned guess).
    n(a,b)    symmetric count of a<->b substitutions
    n(a)      all substitutions involving a
    f(a|b)    (n(a,b) + 1) / (n(b) + |alphabet|)      Laplace-smoothed share of b's errors
    cost(a,b) max(FLOOR, 1 - (f(a|b) + f(b|a)) / 2)  symmetric, never free
    Pairs never observed fall back to the hand table (`similarity.ocr_confusion_cost`), so the
    documented confusables keep their 0.2 even when this benchmark happened not to see them.

LEAKAGE RULE (deck p. 83): costs are estimated from the TRAINING split only. The split is by
true plate (`split_by_plate`), so no vehicle's reads inform both its own costs and its test
score. `data/confusion_costs.json` records the seed and the training plates' counts so the rule
is checkable; tests assert that a confusion seen only in held-out rows does not change its cost.
"""
from __future__ import annotations

import json
import random
from collections import Counter
from pathlib import Path
from typing import Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from mediator.similarity import ocr_confusion_cost, weighted_edit_distance

ROOT = Path(__file__).resolve().parent.parent
COSTS_PATH = ROOT / "data" / "confusion_costs.json"
ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
FLOOR = 0.1          # a confusion is cheap, never free: two plates differing only by 0/O are
                     # still two strings, and a zero cost would make them indistinguishable
DEFAULT_SEED = 20261017

_cache: Optional[Dict[Tuple[str, str], float]] = None


def split_by_plate(plates: Iterable[str], seed: int = DEFAULT_SEED,
                   fractions: Tuple[float, float] = (0.6, 0.2)) -> Dict[str, str]:
    """plate -> 'train' | 'val' | 'test'. Sorted before shuffling so the split is deterministic."""
    uniq = sorted(set(plates))
    random.Random(seed).shuffle(uniq)
    n_train = int(len(uniq) * fractions[0])
    n_val = int(len(uniq) * fractions[1])
    out: Dict[str, str] = {}
    for i, p in enumerate(uniq):
        out[p] = "train" if i < n_train else ("val" if i < n_train + n_val else "test")
    return out


def observed_substitutions(pairs: Iterable[Tuple[str, str]]) -> Counter:
    """Symmetric counts of positional substitutions over (true, read) pairs of equal length."""
    counts: Counter = Counter()
    for true, read in pairs:
        if len(true) != len(read):
            continue
        for a, b in zip(true, read):
            if a != b:
                counts[tuple(sorted((a, b)))] += 1
    return counts


def estimate_costs(rows: Sequence[Mapping[str, str]], train_plates: Iterable[str]
                   ) -> Dict[Tuple[str, str], float]:
    """Costs for every observed pair, from rows whose true plate is in `train_plates` only."""
    train = set(train_plates)
    pairs = [(r["true_plate"], r["ocr_read"]) for r in rows
             if r.get("true_plate") in train and r.get("ocr_read")]
    counts = observed_substitutions(pairs)
    per_char: Counter = Counter()
    for (a, b), n in counts.items():
        per_char[a] += n
        per_char[b] += n
    k = len(ALPHABET)
    costs: Dict[Tuple[str, str], float] = {}
    for (a, b), n in counts.items():
        f_ab = (n + 1) / (per_char[b] + k)
        f_ba = (n + 1) / (per_char[a] + k)
        costs[(a, b)] = max(FLOOR, 1.0 - (f_ab + f_ba) / 2)
    return costs


def save_costs(costs: Dict[Tuple[str, str], float], counts: Counter, seed: int,
               train_plates: Sequence[str], path: Path = COSTS_PATH) -> None:
    payload = {
        "seed": seed,
        "floor": FLOOR,
        "n_train_plates": len(train_plates),
        "train_plates": sorted(train_plates),
        "pairs": [{"a": a, "b": b, "count": counts[(a, b)], "cost": round(c, 4)}
                  for (a, b), c in sorted(costs.items(), key=lambda kv: kv[1])],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def load_costs(path: Path = COSTS_PATH) -> Dict[Tuple[str, str], float]:
    global _cache
    if _cache is None:
        _cache = {}
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            for p in data["pairs"]:
                _cache[(p["a"], p["b"])] = float(p["cost"])
    return _cache


def learned_confusion_cost(a: str, b: str) -> float:
    """Measured substitution cost; hand table for pairs the training split never showed."""
    a, b = a.upper(), b.upper()
    if a == b:
        return 0.0
    cost = load_costs().get(tuple(sorted((a, b))))
    return cost if cost is not None else ocr_confusion_cost(a, b)


def learned_ocr_similarity(s1: str, s2: str) -> float:
    """1 - weighted edit distance / max length (p. 17), with the learned substitution costs."""
    x, y = s1.upper(), s2.upper()
    if not x and not y:
        return 1.0
    if not x or not y:
        return 0.0
    return 1.0 - weighted_edit_distance(x, y, learned_confusion_cost) / max(len(x), len(y))
