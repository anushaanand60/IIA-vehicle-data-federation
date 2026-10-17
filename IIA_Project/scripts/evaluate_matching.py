"""Measured comparison of every plate-matching contender -> docs/MATCHING_EVALUATION.md.

    python scripts\\evaluate_matching.py

Deterministic (fixed seed, sorted iteration, seeded context noise). Steps, each tied to the deck:

1. Split the labelled benchmark BY TRUE PLATE 60/20/20 (p. 83: no vehicle in train and test).
2. Estimate OCR confusion costs from the training split only (mediator.confusion).
3. Retrieval (p. 82, evaluated separately): recall and candidates scanned per blocking strategy
   and max_edits; max_edits for the matcher is chosen on VALIDATION.
4. Pairs = every retrieved candidate per read; the non-true ones are the hard negatives (p. 78).
   Camera context is simulated from the registration: make right 85 %, colour right 75 %,
   model right 70 %, all three missing 10 %.
5. Train logistic regression and gradient-boosted trees on train pairs (p. 69-72); fit Platt
   calibration and the ACCEPT / REJECT thresholds on validation (p. 81); report on test.
6. Ablations, per-degradation breakdown, observed (unlabelled) CAM rows, and the decision.
Writes data/match_model.joblib, data/confusion_costs.json and docs/MATCHING_EVALUATION.md.
"""
from __future__ import annotations

import csv
import random
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import joblib  # noqa: E402
import numpy as np  # noqa: E402
import sklearn  # noqa: E402
from sklearn.ensemble import GradientBoostingClassifier  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.pipeline import make_pipeline  # noqa: E402
from sklearn.preprocessing import StandardScaler  # noqa: E402

from mediator import confusion, plate_match as pm  # noqa: E402
from mediator.blocking import QGramIndex, candidate_plates  # noqa: E402
from mediator.similarity import tfidf_measure  # noqa: E402

SEED = 20261017
BENCH = ROOT / "data" / "match_benchmark.csv"
CAM_DB = ROOT / "sources" / "cam" / "cam.db"
DOC = ROOT / "docs" / "MATCHING_EVALUATION.md"
PRECISION_TARGET = 0.98
RECALL_SAFE_LOSS = 0.01      # REJECT may drop at most 1 % of reads the matcher had right
RETRIEVAL_TARGET = 0.99
MAX_EDITS_GRID = (1, 2, 3, 4, 5)
STRATEGIES = ("index", "size", "prefix", "position")
MEASURES = pm.SCORE_NAMES
COMBINERS = ["weighted", "logreg", "gbt"]


# ----------------------------------------------------------------------------- data

def load_rows() -> List[Dict[str, Any]]:
    with BENCH.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def cam_context() -> Dict[str, Dict[str, Any]]:
    con = sqlite3.connect(f"file:{CAM_DB}?mode=ro", uri=True)
    try:
        rows = con.execute("SELECT plate_id, observed_make, observed_model, observed_colour, "
                           "ocr_confidence FROM PLATE_CAPTURES ORDER BY capture_id").fetchall()
    finally:
        con.close()
    out: Dict[str, Dict[str, Any]] = {}
    for p, mk, md, c, conf in rows:
        out.setdefault(p, {"make": mk, "model": md, "colour": c, "conf": conf})
    return out


def simulate_context(row: Dict[str, Any], reg: Dict[str, Dict[str, Any]],
                     pools: Dict[str, List[str]]) -> Dict[str, Optional[str]]:
    """What a road camera would have reported, drawn from the true registration plus noise."""
    rng = random.Random(f"{SEED}|{row['image_path']}")
    truth = reg.get(row["true_plate"])
    if truth is None or rng.random() < 0.10:
        return {"make": None, "model": None, "colour": None}

    def noisy(attr: str, p_right: float) -> str:
        if rng.random() < p_right:
            return truth[attr]
        return rng.choice([v for v in pools[attr] if v != truth[attr]])
    return {"make": noisy("make", 0.85), "model": noisy("model", 0.70),
            "colour": noisy("colour", 0.75)}


# ----------------------------------------------------------------------------- retrieval

def retrieval_table(rows, plates, idx) -> Dict[Tuple[str, int], Dict[str, Dict[str, float]]]:
    out: Dict[Tuple[str, int], Dict[str, Dict[str, float]]] = {}
    settings = [("none", 0), ("index", 0)] + [(s, k) for s in STRATEGIES[1:] for k in MAX_EDITS_GRID]
    for strat, k in settings:
        acc: Dict[str, List[float]] = defaultdict(list)
        for r in rows:
            if r["true_plate"] not in plates:
                continue
            c, _ = candidate_plates(r["ocr_read"], plates, strategy=strat, max_edits=max(k, 1),
                                    index=idx)
            acc[r["split"] + "_hit"].append(float(r["true_plate"] in c))
            acc[r["split"] + "_n"].append(float(len(c)))
        out[(strat, k)] = {s: {"recall": float(np.mean(acc[s + "_hit"])),
                               "cands": float(np.mean(acc[s + "_n"]))}
                           for s in ("train", "val", "test")}
    return out


def choose_max_edits(table) -> Tuple[int, bool]:
    for k in MAX_EDITS_GRID:
        if table[("position", k)]["val"]["recall"] >= RETRIEVAL_TARGET:
            return k, True
    return MAX_EDITS_GRID[-1], False


# ----------------------------------------------------------------------------- pairs

def build_pairs(rows, reg, plates, idx, me):
    tfidf = tfidf_measure(plates)
    plate_set = set(plates)
    memo: Dict[Tuple[str, str], Dict[str, float]] = {}
    for r in rows:
        read = r["ocr_read"]
        cands, _, _ = pm.retrieve(read, plates, strategy="position", max_edits=me, index=idx)
        cands, _ = pm.shortlist(read, cands)
        ctx = r["ctx"]
        vecs = []
        for p in cands:
            if (read, p) not in memo:
                memo[(read, p)] = pm.string_features(read, p, tfidf)
            v = dict(memo[(read, p)])
            v.update(pm.context_features(
                confidence=float(r["ocr_confidence"]) if r["ocr_confidence"] else None,
                observed_make=ctx["make"], observed_colour=ctx["colour"],
                observed_model=ctx["model"], registry_row=reg.get(p),
                read_registered=read in plate_set))
            vecs.append(v)
        r["cands"], r["vecs"] = cands, vecs


def matrix(rows, names):
    X, y = [], []
    for r in rows:
        for p, v in zip(r["cands"], r["vecs"]):
            X.append([v[n] for n in names])
            y.append(int(p == r["true_plate"]))
    return np.array(X), np.array(y)


def train_models(rows, names):
    X, y = matrix(rows, names)
    lr = make_pipeline(StandardScaler(), LogisticRegression(max_iter=5000, C=1.0))
    gbt = GradientBoostingClassifier(n_estimators=150, max_depth=3, random_state=SEED)
    return {"logreg": lr.fit(X, y), "gbt": gbt.fit(X, y)}


# ----------------------------------------------------------------------------- scoring

def scores_for(model, rows, bundle):
    return [pm.raw_scores(model, r["vecs"], bundle) for r in rows]


def fit_platt(rows, raw) -> Tuple[float, float]:
    s = np.concatenate([np.array(x) for x in raw if x]).reshape(-1, 1)
    y = np.concatenate([[int(p == r["true_plate"]) for p in r["cands"]] for r in rows if r["cands"]])
    lr = LogisticRegression(C=1e6, max_iter=5000).fit(s, y)
    return float(lr.coef_[0][0]), float(lr.intercept_[0])


def read_outcomes(rows, probs):
    out = []
    for r, ps in zip(rows, probs):
        ranked = sorted(zip(r["cands"], ps), key=lambda t: (-t[1], t[0]))
        p1 = ranked[0][1] if ranked else 0.0
        p2 = ranked[1][1] if len(ranked) > 1 else 0.0
        rank = next((i + 1 for i, (p, _) in enumerate(ranked) if p == r["true_plate"]), None)
        out.append({"p1": p1, "p2": p2, "rank": rank, "row": r,
                    "registered": r["true_plate"] in r["_plates"]})
    return out


def choose_thresholds(outs) -> Dict[str, float]:
    cand_t = sorted({o["p1"] for o in outs})
    t_acc = 1.01
    for t in cand_t:
        acc = [o for o in outs if o["p1"] >= t and o["p1"] - o["p2"] >= pm.ACCEPT_MARGIN]
        if len(acc) >= 10 and np.mean([o["rank"] == 1 for o in acc]) >= PRECISION_TARGET:
            t_acc = t
            break
    right = sorted(o["p1"] for o in outs if o["rank"] == 1)
    t_rej = right[int(len(right) * RECALL_SAFE_LOSS)] if right else 0.0
    return {"accept": float(t_acc), "reject": float(min(t_rej, t_acc))}


def decisions(outs, th):
    for o in outs:
        if o["p1"] >= th["accept"] and o["p1"] - o["p2"] >= pm.ACCEPT_MARGIN:
            o["decision"] = "ACCEPT"
        elif o["p1"] >= th["reject"]:
            o["decision"] = "REVIEW"
        else:
            o["decision"] = "REJECT"
    return outs


def metrics(outs) -> Dict[str, float]:
    reg = [o for o in outs if o["registered"]]
    acc = [o for o in outs if o["decision"] == "ACCEPT"]
    tp = sum(1 for o in acc if o["rank"] == 1)
    prec = tp / len(acc) if acc else 0.0
    rec = tp / len(reg) if reg else 0.0
    return {
        "n": len(outs),
        "top1": float(np.mean([o["rank"] == 1 for o in reg])) if reg else 0.0,
        "mrr": float(np.mean([1 / o["rank"] if o["rank"] else 0 for o in reg])) if reg else 0.0,
        "top5": float(np.mean([bool(o["rank"]) and o["rank"] <= 5 for o in reg])) if reg else 0.0,
        "acc_p": prec, "acc_r": rec, "acc_f1": 2 * prec * rec / (prec + rec) if prec + rec else 0.0,
        "review": float(np.mean([o["decision"] == "REVIEW" for o in outs])),
    }


def rule_outcomes(rows, reg):
    outs = []
    for r in rows:
        ranked = pm.rule_match(r["ocr_read"], reg, r["ctx"]["make"], r["ctx"]["colour"])
        rank = next((i + 1 for i, c in enumerate(ranked) if c.plate == r["true_plate"]), None)
        outs.append({"p1": ranked[0].probability if ranked else 0.0, "p2": 0.0, "rank": rank,
                     "row": r, "registered": r["true_plate"] in reg,
                     "decision": ranked[0].decision if ranked else "REJECT"})
    return outs


def evaluate_contender(model, split_rows, bundle):
    raw_val = scores_for(model, split_rows["val"], bundle)
    a, b = fit_platt(split_rows["val"], raw_val)
    outs_val = read_outcomes(split_rows["val"], [pm.platt((a, b), x) for x in raw_val])
    th = choose_thresholds(outs_val)
    res = {}
    for s in ("train", "val", "test"):
        raw = raw_val if s == "val" else scores_for(model, split_rows[s], bundle)
        res[s] = decisions(read_outcomes(split_rows[s], [pm.platt((a, b), x) for x in raw]), th)
    return (a, b), th, res


# ----------------------------------------------------------------------------- report

def fmt(m):
    return (f"| {m['top1']:.3f} | {m['mrr']:.3f} | {m['top5']:.3f} | {m['acc_p']:.3f} | "
            f"{m['acc_r']:.3f} | {m['acc_f1']:.3f} | {m['review']:.3f} |")


def main() -> None:
    t0 = time.perf_counter()
    rows = load_rows()
    reg = pm.load_registry()
    plates = sorted(reg)
    idx = QGramIndex.build(plates)
    labelled = [r for r in rows if r["true_plate"] and r["degradation"] != "observed"]
    observed = [r for r in rows if r["degradation"] == "observed"]
    split = confusion.split_by_plate([r["true_plate"] for r in labelled], SEED)
    train_plates = sorted(p for p, s in split.items() if s == "train")
    costs, counts = confusion.estimate_costs(labelled, train_plates)
    confusion.save_costs(costs, counts, SEED, train_plates)

    pools = {a: sorted({v[a] for v in reg.values() if v[a]}) for a in ("make", "model", "colour")}
    cam = cam_context()
    for r in labelled:
        r["split"], r["ctx"], r["_plates"] = split[r["true_plate"]], simulate_context(r, reg, pools), reg
    for r in observed:
        c = cam.get(r["ocr_read"], {})
        r["ctx"], r["_plates"] = {k: c.get(k) for k in ("make", "model", "colour")}, reg

    print("retrieval ...", flush=True)
    rtable = retrieval_table(labelled, plates, idx)
    me, met = choose_max_edits(rtable)
    print(f"max_edits={me} (target met: {met})", flush=True)

    print("features ...", flush=True)
    build_pairs(labelled + observed, reg, plates, idx, me)
    split_rows = {s: [r for r in labelled if r["split"] == s] for s in ("train", "val", "test")}

    print("training ...", flush=True)
    names = pm.FEATURE_NAMES
    models = train_models(split_rows["train"], names)
    bundle: Dict[str, Any] = {"features": names, "models": models, "seed": SEED,
                              "max_edits": me, "calibration": {}, "thresholds": {}}
    results = {}
    for model in MEASURES + COMBINERS:
        params, th, res = evaluate_contender(model, split_rows, bundle)
        bundle["calibration"][model], bundle["thresholds"][model] = params, th
        results[model] = res
    results["rule"] = {s: rule_outcomes(split_rows[s], reg) for s in ("train", "val", "test")}

    mets = {m: {s: metrics(results[m][s]) for s in results[m]} for m in results}
    best = max(MEASURES + COMBINERS + ["rule"],
               key=lambda m: (mets[m]["val"]["top1"], mets[m]["val"]["mrr"]))
    bundle["train_metrics"] = {m: mets[m]["train"] for m in COMBINERS}
    bundle["sklearn_version"] = sklearn.__version__
    joblib.dump(bundle, pm.MODEL_PATH)

    print("ablations ...", flush=True)
    ablations = {}
    drop = {"no camera context": pm.CONTEXT_FEATURES, "no learned costs": ["learned_ocr_similarity"],
            "no confidence": ["ocr_confidence"]}
    for label, removed in drop.items():
        sub = [n for n in names if n not in removed]
        b2 = {"features": sub, "models": train_models(split_rows["train"], sub)}
        for model in ("logreg", "gbt"):
            _, _, res = evaluate_contender(model, split_rows, b2)
            ablations[(label, model)] = metrics(res["test"])

    print("\n| contender | top-1 | MRR | top-5 | ACCEPT P | ACCEPT R | F1 | REVIEW |")
    for m in ["rule"] + MEASURES + COMBINERS:
        print(f"| {m} " + fmt(mets[m]["test"]))
    print(f"\nbest on validation: {best}; elapsed {time.perf_counter() - t0:.0f} s")
    write_doc(rtable, me, met, mets, results, best, ablations, observed, bundle, counts,
              time.perf_counter() - t0)


LOSERS = {
    "soundex_similarity": "plates are not phonetic: Soundex keeps the first letter and codes "
                          "consonants, so every DL plate gets nearly the same code",
    "overlap": "normalised by the smaller q-gram set, so a truncated read contained in many "
               "plates scores 1.0 against all of them",
    "jaccard": "a bag of bigrams ignores position, and plates share most bigrams (state, RTO, "
               "series), so the hard negatives score almost as high as the truth",
    "tfidf": "rare bigrams get more weight, but on plates the discriminating digits are common "
             "bigrams; like Jaccard it ignores order and confusions",
    "jaro": "no prefix bonus and a transposition model plates do not need",
    "jaro_winkler": "rewards the shared state prefix that every hard negative also has",
    "edit_similarity": "charges 0->O as much as 0->7, so the commonest OCR error costs a full edit",
    "ratcliff_obershelp": "longest-common-substring is order-aware but blind to confusions",
    "ocr_edit_similarity": "hand table misses 1/4, H/4, 1/A, D/U, 1/T and prices 0/O like 5/S",
    "learned_ocr_similarity": "the best single measure, but string-only: it cannot use the "
                              "camera's make and colour or the read's confidence",
    "weighted": "hand weights: sensible but not fitted, so it cannot learn interactions",
    "rule": "one substitution from a fixed table: fails on two-error reads and on confusions the "
            "table lacks",
    "logreg": "linear in the features, so it cannot express 'low confidence AND long distance'",
    "gbt": "strong, but trees are harder to read aloud than logistic weights",
}


def bucket_table(results, models) -> List[str]:
    buckets = defaultdict(list)
    for i, o in enumerate(results[models[0]]["test"]):
        buckets[(o["row"]["degradation"], int(o["row"]["severity"]))].append(i)
    head = "| degradation | sev | n | " + " | ".join(models) + " |"
    lines = [head, "|" + "---|" * (3 + len(models))]
    for key in sorted(buckets):
        ix = buckets[key]
        cells = []
        for m in models:
            outs = [results[m]["test"][i] for i in ix]
            cells.append(f"{metrics(outs)['top1']:.2f}")
        lines.append(f"| {key[0]} | {key[1]} | {len(ix)} | " + " | ".join(cells) + " |")
    return lines


def write_doc(rtable, me, met, mets, results, best, ablations, observed, bundle, counts,
              elapsed) -> None:
    L: List[str] = ["# Plate matching evaluation (workstream C)", "",
                    f"Generated by `python scripts/evaluate_matching.py` (seed {SEED}, "
                    f"{elapsed:.0f} s). Split by true plate 60/20/20; all numbers below are on "
                    "the **test** split unless labelled otherwise.", ""]
    L += ["## 1. Retrieval (step 1, p. 82)", "",
          "| strategy | max_edits | recall val | recall test | mean candidates test |",
          "|---|---|---|---|---|"]
    for (strat, k), v in rtable.items():
        L.append(f"| {strat} | {k or '-'} | {v['val']['recall']:.3f} | {v['test']['recall']:.3f} "
                 f"| {v['test']['cands']:.1f} |")
    L += ["", f"Chosen `max_edits` = {me} on validation (target recall {RETRIEVAL_TARGET} "
          f"{'met' if met else 'NOT met'}).", ""]
    L += ["## 2. Scoring (step 2)", "",
          "| contender | top-1 | MRR | top-5 | ACCEPT P | ACCEPT R | F1 | REVIEW |",
          "|---|---|---|---|---|---|---|---|"]
    for m in ["rule"] + MEASURES + COMBINERS:
        L.append(f"| {m} " + fmt(mets[m]["test"]))
    L += ["", "## 3. By degradation and severity (test, top-1)", ""]
    L += bucket_table(results, ["rule", "ocr_edit_similarity", "learned_ocr_similarity",
                                "weighted", "logreg", "gbt"])
    L += ["", "## 4. Ablation (test)", "", "| variant | model | top-1 | MRR | ACCEPT P | ACCEPT R |",
          "|---|---|---|---|---|---|"]
    for (label, model), m in ablations.items():
        L.append(f"| {label} | {model} | {m['top1']:.3f} | {m['mrr']:.3f} | {m['acc_p']:.3f} | "
                 f"{m['acc_r']:.3f} |")
    L += ["", "## 5. Decision", "",
          f"**{best}** is best on validation; on test it scores top-1 "
          f"{mets[best]['test']['top1']:.3f}.", ""]
    for m in ["rule"] + MEASURES + COMBINERS:
        if m != best:
            L.append(f"- `{m}` ({mets[m]['test']['top1']:.3f}): {LOSERS.get(m, '')}")
    DOC.write_text("\n".join(L) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
