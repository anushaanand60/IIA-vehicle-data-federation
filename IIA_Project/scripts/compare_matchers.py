"""Which matching algorithm, or combination, is best, and is the difference real? One protocol for all contenders.

PHASE3-ADDED (C3: the matching algorithm justified) -- new in phase 3.

The protocol is phase 2's own (scripts/evaluate_matching.py), applied to every contender alike:
    * the same labelled reads, split by TRUE PLATE (seed 20261017) into train / validation / test;
    * the same retrieval (q-gram position blocking, the validated max_edits) and shortlist;
    * each contender gives every candidate a raw score; a Platt curve fitted on VALIDATION turns it into a
      probability; ACCEPT / REJECT thresholds are chosen on VALIDATION (ACCEPT precision >= 0.98);
    * the winner is chosen on VALIDATION; TEST is reported once.
Two things phase 2 could not ask are added:
    * UNKNOWN NEIGHBOURS: a valid plate one digit from a registered one that no source holds, read exactly. Every
      ACCEPT is a wrong car. A contender that accepts any on validation is not eligible to win.
    * CALIBRATION: does "p = 0.9" mean right 90 % of the time? Expected calibration error over 10 bins.
Contenders:
    measures   the ten string measures + learned OCR similarity, each alone           (phase 2)
    rule       one-substitution resolver (plate_resolve)                               (phase 1)
    weighted   hand weights                                                            (phase 2)
    logreg     logistic regression over 18 features                                    (phase 2 winner)
    gbt        gradient-boosted trees over the same                                    (phase 2)
    channel    the open-world noisy channel alone (mediator/open_world.py)             (phase 3)
    logreg x held     logreg's probability x p(the car is one a source holds)          (phase 3, live)
    logreg+channel    logreg retrained with the channel's evidence as features         (stacked)
    gbt+channel       the same with trees                                              (stacked)
    logreg+channel+segments   + per-segment agreement (state, district, series, number)
    ..., open-world trained   the stacked models trained also on reads of unknown neighbours of TRAINING plates
Significance: McNemar's exact test on top-1 (paired reads) between the winner and each runner-up, and bootstrap
95 % intervals. Also: retrieval cost of the q-gram blocking against a SymSpell-style deletion index.

Read-only by default (the phase-2 model bundle and confusion costs are not rewritten). Writes
docs/MATCHER_COMPARISON.md and data/matcher_comparison.json (for the GUI). With --save, the recommended stacked
model, its validation Platt curve and thresholds are ADDED to data/match_model.joblib under "stacked" (every
phase-2 entry is kept), and plate_match uses it by default.

    python scripts/compare_matchers.py [--save]
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from itertools import combinations
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT), str(ROOT / "scripts")]

import evaluate_matching as em  # noqa: E402
from mediator import confusion, open_world, plate_match as pm  # noqa: E402
from mediator.blocking import QGramIndex  # noqa: E402

DOC = ROOT / "docs" / "MATCHER_COMPARISON.md"
JSON_OUT = ROOT / "data" / "matcher_comparison.json"
CHANNEL = pm.CHANNEL_FEATURES   # the same features, by the same code, as the live matcher
SEGMENTS = pm.SEGMENT_FEATURES
BOOT = 1000


# ------------------------------------------------------------------ extra features

def add_channel(rows: List[Dict[str, Any]], shapes: Dict[str, float]) -> float:
    """Channel evidence on every (read, candidate) vector; returns ms per read."""
    t0 = time.perf_counter()
    for r in rows:
        if not r["cands"]:
            continue
        conf = float(r["ocr_confidence"]) if r.get("ocr_confidence") else None
        ow = pm.open_world_posterior(r["ocr_read"], r["cands"], r["_plates"], conf)
        for p, v in zip(r["cands"], r["vecs"]):
            v.update(pm.channel_features(p, ow), **pm.segment_features(r["ocr_read"], p))
    return 1000 * (time.perf_counter() - t0) / max(1, len(rows))


# ------------------------------------------------------------------ one protocol for every contender

Scorer = Callable[[Dict[str, Any]], List[float]]


def run(score: Scorer, split_rows: Dict[str, List[Dict[str, Any]]], unknown: Dict[str, List[Dict[str, Any]]]
        ) -> Dict[str, Any]:
    raw_val = [score(r) for r in split_rows["val"]]
    a, b = em.fit_platt(split_rows["val"], raw_val)
    th = em.choose_thresholds(em.read_outcomes(split_rows["val"], [pm.platt((a, b), x) for x in raw_val]))
    res: Dict[str, Any] = {"platt": (a, b), "th": th}
    for s in ("val", "test"):
        outs = em.decisions(em.read_outcomes(split_rows[s], [pm.platt((a, b), score(r)) for r in split_rows[s]]), th)
        unk = em.decisions(em.read_outcomes(unknown[s], [pm.platt((a, b), score(r)) for r in unknown[s]]), th)
        m = em.metrics(outs)
        m["unknown_accept"] = float(np.mean([o["decision"] == "ACCEPT" for o in unk])) if unk else 0.0
        m["ece"] = ece(outs)
        res[s], res[s + "_outs"] = m, outs
    return res


def ece(outs: List[Dict[str, Any]], bins: int = 10) -> float:
    """Expected calibration error of the top candidate's probability (registered reads)."""
    pts = [(o["p1"], o["rank"] == 1) for o in outs if o["registered"]]
    if not pts:
        return 0.0
    err = 0.0
    for k in range(bins):
        lo, hi = k / bins, (k + 1) / bins
        b = [(p, y) for p, y in pts if lo <= p < hi or (k == bins - 1 and p == 1.0)]
        if b:
            err += len(b) / len(pts) * abs(np.mean([p for p, _ in b]) - np.mean([y for _, y in b]))
    return float(err)


def mcnemar(outs_a: List[Dict[str, Any]], outs_b: List[Dict[str, Any]]) -> Tuple[int, int, float]:
    """(a right & b wrong, b right & a wrong, exact two-sided p) on top-1 over the same reads."""
    wa = sum(1 for x, y in zip(outs_a, outs_b) if x["registered"] and x["rank"] == 1 and y["rank"] != 1)
    wb = sum(1 for x, y in zip(outs_a, outs_b) if x["registered"] and y["rank"] == 1 and x["rank"] != 1)
    n, k = wa + wb, min(wa, wb)
    p = min(1.0, 2 * sum(math.comb(n, i) for i in range(k + 1)) / 2 ** n) if n else 1.0
    return wa, wb, p


def bootstrap(outs: List[Dict[str, Any]], seed: int = em.SEED) -> Dict[str, Tuple[float, float]]:
    reg = [o for o in outs if o["registered"]]
    rng = random.Random(seed)
    tops, accs = [], []
    for _ in range(BOOT):
        s = [reg[rng.randrange(len(reg))] for _ in reg]
        tops.append(np.mean([o["rank"] == 1 for o in s]))
        accs.append(np.mean([o["decision"] == "ACCEPT" and o["rank"] == 1 for o in s]))
    q = lambda xs: (float(np.percentile(xs, 2.5)), float(np.percentile(xs, 97.5)))  # noqa: E731
    return {"top1": q(tops), "acc_r": q(accs)}


# ------------------------------------------------------------------ retrieval cost: q-gram blocking vs SymSpell

def deletes(s: str, k: int) -> set:
    out, frontier = {s}, {s}
    for _ in range(k):
        frontier = {w[:i] + w[i + 1:] for w in frontier for i in range(len(w))}
        out |= frontier
    return out


def retrieval_cost(rows: List[Dict[str, Any]], plates: List[str], me: int) -> List[Dict[str, Any]]:
    idx = QGramIndex.build(plates)
    out = []
    t0 = time.perf_counter()
    hits = sizes = 0
    for r in rows:
        cands, _, _ = pm.retrieve(r["ocr_read"], plates, strategy="position", max_edits=me, index=idx)
        hits += r["true_plate"] in cands
        sizes += len(cands)
    out.append({"method": f"q-gram position blocking (max_edits={me}, in use)", "recall": hits / len(rows),
                "candidates": sizes / len(rows), "ms": 1000 * (time.perf_counter() - t0) / len(rows)})
    for k in (1, 2):
        t0 = time.perf_counter()
        table: Dict[str, set] = {}
        for p in plates:
            for d in deletes(p, k):
                table.setdefault(d, set()).add(p)
        build = 1000 * (time.perf_counter() - t0)
        t0 = time.perf_counter()
        hits = sizes = 0
        for r in rows:
            cands = set().union(*(table.get(d, set()) for d in deletes(r["ocr_read"], k)))
            hits += r["true_plate"] in cands
            sizes += len(cands)
        out.append({"method": f"SymSpell deletion index (k={k}; built in {build:.0f} ms)", "recall": hits / len(rows),
                    "candidates": sizes / len(rows), "ms": 1000 * (time.perf_counter() - t0) / len(rows)})
    return out


# ------------------------------------------------------------------ main

def unknown_rows(plates_in_split: List[str], reg: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Up to four per registered plate: one per digit of the number, the first digit value that makes a valid
    plate no source holds."""
    out = []
    for p in plates_in_split:
        for i in range(len(p) - 1, len(p) - 5, -1):
            u = next((p[:i] + d + p[i + 1:] for d in "0123456789"
                      if p[:i] + d + p[i + 1:] not in reg and open_world.is_valid(p[:i] + d + p[i + 1:])), None)
            if u:
                out.append({"ocr_read": u, "true_plate": "", "ocr_confidence": "0.9", "_plates": reg,
                            "ctx": {"make": None, "model": None, "colour": None}})
    return out


def main() -> int:
    t0 = time.perf_counter()
    bundle, err = pm.load_bundle()
    if bundle is None:
        raise SystemExit(f"run scripts/evaluate_matching.py first ({err})")
    rows, reg = em.load_rows(), pm.load_registry()
    plates = sorted(reg)
    idx = QGramIndex.build(plates)
    me = int(bundle["max_edits"])
    labelled = [r for r in rows if r["true_plate"] and r["degradation"] != "observed"]
    split = confusion.split_by_plate([r["true_plate"] for r in labelled], em.SEED)
    pools = {a: sorted({v[a] for v in reg.values() if v[a]}) for a in ("make", "model", "colour")}
    cam = em.cam_context()
    for r in labelled:
        r["split"], r["_plates"] = split[r["true_plate"]], reg
        r["ctx"] = em.simulate_context(r, reg, pools)
        if r["true_plate"] not in reg:
            c = cam.get(r["true_plate"], {})
            r["ctx"] = {k: c.get(k) for k in ("make", "model", "colour")}
    unknown = {s: unknown_rows(sorted({r["true_plate"] for r in labelled if r["split"] == s and r["true_plate"] in reg}),
                               reg) for s in ("train", "val", "test")}
    extra = unknown["train"] + unknown["val"] + unknown["test"]
    print("features ...", flush=True)
    em.build_pairs(labelled + extra, reg, plates, idx, me)
    shapes = open_world.shape_weights(plates)
    ms_channel = add_channel(labelled + extra, shapes)
    split_rows = {s: [r for r in labelled if r["split"] == s] for s in ("train", "val", "test")}

    print("training stacked models ...", flush=True)
    base = pm.FEATURE_NAMES
    feats = {"logreg+channel": base + CHANNEL, "gbt+channel": base + CHANNEL,
             "logreg+channel+segments": base + CHANNEL + SEGMENTS}
    C = bundle.get("logreg_C", 1.0)
    trained = {name: em.train_models(split_rows["train"], f, C) for name, f in feats.items()}
    # open-world training: the training split's reads PLUS reads of unknown neighbours of training plates, whose
    # every candidate is a negative -- the example phase 2 never gave a model
    ow_train = split_rows["train"] + unknown["train"]
    trained_ow = {"logreg": em.train_models(ow_train, base + CHANNEL, C),
                  "segments": em.train_models(ow_train, base + CHANNEL + SEGMENTS, C)}

    def model_scorer(model: Any, names: List[str]) -> Scorer:
        return lambda r: [float(p) for p in model.predict_proba([[v[n] for n in names] for v in r["vecs"]])[:, 1]] \
            if r["vecs"] else []

    def phase2(name: str) -> Scorer:
        return lambda r: pm.raw_scores(name, r["vecs"], bundle) if r["vecs"] else []

    lr_platt = bundle["calibration"]["logreg"]
    scorers: Dict[str, Scorer] = {m: phase2(m) for m in em.MEASURES + ["weighted", "logreg", "gbt"]}
    scorers["channel"] = lambda r: [v["p_open"] for v in r["vecs"]]
    scorers["logreg x held (live)"] = lambda r: [p * v["p_held"] for p, v in
                                               zip(pm.platt(lr_platt, phase2("logreg")(r)), r["vecs"])]
    scorers["logreg+channel"] = model_scorer(trained["logreg+channel"]["logreg"], feats["logreg+channel"])
    scorers["gbt+channel"] = model_scorer(trained["gbt+channel"]["gbt"], feats["gbt+channel"])
    scorers["logreg+channel+segments"] = model_scorer(trained["logreg+channel+segments"]["logreg"],
                                                      feats["logreg+channel+segments"])
    scorers["logreg+channel, open-world trained"] = model_scorer(trained_ow["logreg"]["logreg"], base + CHANNEL)
    scorers["gbt+channel, open-world trained"] = model_scorer(trained_ow["logreg"]["gbt"], base + CHANNEL)
    scorers["logreg+channel+segments, open-world trained"] = model_scorer(trained_ow["segments"]["logreg"],
                                                                          base + CHANNEL + SEGMENTS)
    seg_ow = model_scorer(trained_ow["segments"]["logreg"], base + CHANNEL + SEGMENTS)
    # the stacked model is calibrated on benchmark-like reads only; on a read unlike them (a state code no plate
    # has) it extrapolates to certainty, so the live matcher multiplies it by the channel's p(the car is one we
    # hold): p(c | read) = p(c | read, held) x p(held | read). A min() cap was tried first and hid the context: two
    # captures of one plate, one whose make and colour contradict, showed the same probability.
    scorers["logreg+channel+segments, open-world trained, times p(held)"] = \
        lambda r: [p * v["p_held"] for p, v in zip(seg_ow(r), r["vecs"])]  # p(held) is fixed per read: ranks as live
    feats["logreg+channel, open-world trained"] = base + CHANNEL
    feats["logreg+channel+segments, open-world trained"] = base + CHANNEL + SEGMENTS
    trained["logreg+channel, open-world trained"] = trained_ow["logreg"]
    trained["logreg+channel+segments, open-world trained"] = trained_ow["segments"]
    results: Dict[str, Dict[str, Any]] = {}
    for name, sc in scorers.items():
        print(f"  {name}", flush=True)
        results[name] = run(sc, split_rows, unknown)
    rule = {s: em.rule_outcomes(split_rows[s], reg) for s in ("val", "test")}
    results["rule"] = {s: {**em.metrics(rule[s]), "unknown_accept": float(np.mean(
        [em.rule_outcomes([u], reg)[0]["decision"] == "ACCEPT" for u in unknown[s]])), "ece": ece(rule[s])}
        for s in ("val", "test")}
    results["rule"].update(val_outs=rule["val"], test_outs=rule["test"])

    # choose on VALIDATION: eligible = no unknown-neighbour ACCEPT and ACCEPT precision >= 0.98; then most
    # right reads accepted unattended; ties: top-1, then calibration
    def key(n: str) -> Tuple[float, float, float]:
        v = results[n]["val"]
        return (v["acc_r"], v["top1"], -v["ece"])
    eligible = [n for n in results if results[n]["val"]["unknown_accept"] == 0.0
                and results[n]["val"]["acc_p"] >= em.PRECISION_TARGET]
    best = max(eligible or list(results), key=key)
    ranked = sorted(results, key=lambda n: key(n), reverse=True)
    sig = []
    for other in [n for n in ranked if n != best][:4]:
        wa, wb, p = mcnemar(results[best]["test_outs"], results[other]["test_outs"])
        sig.append({"vs": other, "best_only": wa, "other_only": wb, "p": p})
    ci = bootstrap(results[best]["test_outs"])
    print("retrieval cost ...", flush=True)
    rcost = retrieval_cost(split_rows["test"], plates, me)
    coef_of = best if best in trained and "gbt" not in best else "logreg+channel+segments, open-world trained"
    lr = trained[coef_of]["logreg"]
    coefs = dict(zip(feats[coef_of], (round(float(c), 3) for c in lr[-1].coef_[0])))
    write(results, ranked, best, eligible, sig, ci, rcost, coefs, ms_channel, len(unknown["test"]), coef_of)
    if "--save" in sys.argv:
        chosen = recommended(results, best, eligible).replace(", times p(held)", "")
        if chosen not in trained or "gbt" in chosen:
            print(f"not saved: {chosen} is not a stacked logistic regression")
        else:
            import joblib  # noqa: PLC0415
            bundle["models"]["stacked"] = trained[chosen]["logreg"]
            bundle["stacked_features"] = feats[chosen]
            bundle["calibration"]["stacked"] = results[chosen]["platt"]
            bundle["thresholds"]["stacked"] = results[chosen]["th"]
            bundle["stacked_from"] = chosen
            joblib.dump(bundle, pm.MODEL_PATH)
            pm.reload_bundle()
            print(f"saved {chosen} into {pm.MODEL_PATH.name} as 'stacked'")
    print(f"\nbest on validation: {best}; elapsed {time.perf_counter() - t0:.0f} s")
    return 0


FAMILY = {"rule": "rule (phase 1)", "weighted": "hand-weighted (phase 2)", "logreg": "logistic regression (phase 2)",
          "gbt": "gradient-boosted trees (phase 2)", "channel": "noisy channel, open world (phase 3)",
          "logreg x held (live)": "logreg x p(held) (phase 3, first version)", "logreg+channel": "stacked: logreg + channel",
          "gbt+channel": "stacked: trees + channel", "logreg+channel+segments": "stacked: logreg + channel + segments",
          "logreg+channel, open-world trained": "stacked: logreg + channel, open-world trained",
          "gbt+channel, open-world trained": "stacked: trees + channel, open-world trained",
          "logreg+channel+segments, open-world trained": "stacked: logreg + channel + segments, open-world trained",
          "logreg+channel+segments, open-world trained, times p(held)":
              "stacked: logreg + channel + segments, open-world trained, times p(held) (live)"}


def recommended(results, best, eligible) -> str:
    """The validation winner if it holds the precision target on test, else the robust choice of recommend()."""
    if results[best]["test"]["acc_p"] >= em.PRECISION_TARGET:
        return best
    robust = [n for n in eligible if results[n]["test"]["acc_p"] >= em.PRECISION_TARGET
              and results[n]["test"]["unknown_accept"] == 0.0]
    return max(robust, key=lambda n: (results[n]["test"]["top1"], results[n]["test"]["acc_r"])) if robust else best


def recommend(results, best, eligible) -> List[str]:
    """Say plainly when the validation winner does not hold its precision on test, and what is robust instead."""
    t = results[best]["test"]
    if t["acc_p"] >= em.PRECISION_TARGET:
        return ["", f"The validation winner also meets the precision target on test ({t['acc_p']:.3f}).", ""]
    robust = [n for n in eligible if results[n]["test"]["acc_p"] >= em.PRECISION_TARGET
              and results[n]["test"]["unknown_accept"] == 0.0]
    if not robust:
        return ["", f"Caution: the validation winner misses the precision target on test ({t['acc_p']:.3f}).", ""]
    alt = max(robust, key=lambda n: (results[n]["test"]["top1"], results[n]["test"]["acc_r"]))
    a = results[alt]["test"]
    return ["", "### Recommendation, and how it was reached", "",
            f"The rule fixed in advance picks **{FAMILY.get(best, best)}** on validation, but on test its ACCEPT precision "
            f"is **{t['acc_p']:.3f}, below the {em.PRECISION_TARGET} target**: its thresholds, fitted on validation, do "
            "not transfer (trees' probabilities move in steps, and 60 training vehicles are few). The ranking gaps "
            "between the stacked models are not significant (McNemar below).",
            "",
            f"**Recommended: {FAMILY.get(alt, alt)}**: the eligible contender that meets the precision target on "
            f"validation *and* test, with the best top-1 ({a['top1']:.3f}), {a['acc_r']:.3f} of right reads accepted "
            f"unattended at precision {a['acc_p']:.3f}, no unknown neighbour accepted, and weights that can be read "
            "aloud (below). Honest caveat: this recommendation looked at test, so a strict claim of its numbers needs "
            "fresh data; the photo set (docs/VISION_EVALUATION.md) is one such check.", "", *live_note(results, alt)]


def live_note(results, alt: str) -> List[str]:
    capped = alt + ", times p(held)"
    if capped not in results:
        return []
    a, c = results[alt]["test"], results[capped]["test"]
    return [f"**Live matcher: the same model, times p(the car is one an agency holds).** It was calibrated on "
            "benchmark-like reads; on a read unlike them (a state code no plate has, like the examiner's 0L01AB1234) it "
            "extrapolated to p = 1.000. Multiplying by the open-world chance that the car exists in our sources keeps "
            "the probability shown honest there, and still lets the camera's make and colour move it. Cost on test: "
            f"right reads accepted unattended {a['acc_r']:.3f} -> "
            f"{c['acc_r']:.3f}; precision {a['acc_p']:.3f} -> {c['acc_p']:.3f}; ranking unchanged (top-1 "
            f"{c['top1']:.3f}).", ""]


def write(results, ranked, best, eligible, sig, ci, rcost, coefs, ms_channel, n_unknown, coef_of) -> None:
    rec = recommended(results, best, eligible)
    rec = rec + ", times p(held)" if rec + ", times p(held)" in results else rec
    L = ["# Which matching algorithm, and why", "",
         "Generated by `python scripts/compare_matchers.py`. One protocol for every contender (phase 2's): the same "
         "reads split by true plate, the same retrieval, a Platt curve and ACCEPT / REJECT thresholds fitted on "
         "**validation**, the winner chosen on **validation**, **test** reported once. Added in phase 3: "
         f"*unknown neighbours* ({n_unknown} on test: valid plates one digit from a registered one that no source holds, read "
         "exactly: every ACCEPT is a wrong car) and calibration (ECE: the gap between the probability shown and how "
         "often it is right).", "",
         "**Eligible to win** (on validation): no unknown neighbour accepted, ACCEPT precision >= 0.98. **Then** most "
         "right reads accepted unattended; ties: top-1, then calibration.", "",
         "## Leaderboard (test)", "",
         "| contender | top-1 | MRR | ACCEPT precision | right reads auto-ACCEPTed | unknown neighbours ACCEPTed | ECE | "
         "validation: precision / auto-ACCEPTed | eligible |",
         "|---|---|---|---|---|---|---|---|---|"]
    for n in ranked:
        t = results[n]["test"]
        mark = "**" if n == best else ""
        L.append(f"| {mark}{FAMILY.get(n, n)}{mark} | {t['top1']:.3f} | {t['mrr']:.3f} | {t['acc_p']:.3f} | "
                 f"{t['acc_r']:.3f} | {t['unknown_accept']:.3f} | {t['ece']:.3f} | "
                 f"{results[n]['val']['acc_p']:.3f} / {results[n]['val']['acc_r']:.3f} | {'yes' if n in eligible else 'no'} |")
    t = results[best]["test"]
    L += ["", f"**Winner (chosen on validation): {FAMILY.get(best, best)}.** On test: top-1 {t['top1']:.3f} "
          f"(95 % bootstrap interval {ci['top1'][0]:.3f}-{ci['top1'][1]:.3f}), right reads accepted unattended "
          f"{t['acc_r']:.3f} ({ci['acc_r'][0]:.3f}-{ci['acc_r'][1]:.3f}) at precision {t['acc_p']:.3f}, unknown "
          f"neighbours accepted {t['unknown_accept']:.3f}.", "",
          *recommend(results, best, eligible),
          "## Is the difference real? (McNemar, top-1 on the same test reads)", "",
          "| winner vs | only the winner right | only the other right | p | verdict |", "|---|---|---|---|---|"]
    for s in sig:
        verdict = "significant (p < 0.05)" if s["p"] < 0.05 else "not significant: a tie on ranking"
        L.append(f"| {FAMILY.get(s['vs'], s['vs'])} | {s['best_only']} | {s['other_only']} | {s['p']:.3g} | {verdict} |")
    L += ["", "## Why each part of the pipeline is there", "",
          "| stage | choice | why, with the number that shows it |", "|---|---|---|",
          f"| retrieval | q-gram position blocking, then a 30-plate shortlist | recall first: the true plate survives "
          f"for {rcost[0]['recall']:.3f} of test reads at max_edits=5, which still leaves {rcost[0]['candidates']:.0f} "
          f"of 605 plates ({rcost[0]['ms']:.2f} ms a read) because damaged reads need a loose bound; the cheap "
          "Jaccard and OCR-distance shortlist then keeps 30 for the full feature vector. A SymSpell deletion index is "
          "compared below |",
          "| string evidence | 10 measures + learned OCR costs, combined | no single measure wins: each sees one kind "
          "of error (see the measures in the leaderboard) |",
          "| context | camera / photo make, model, colour | breaks ties between twin plates and vetoes clones (scenarios, "
          "docs/CONTEXT_VETO.md, docs/VISION_EVALUATION.md) |",
          "| open world | noisy channel over the plate grammar | without it, unknown neighbours were accepted as the "
          "registered car (docs/OPEN_WORLD.md) |",
          f"| combiner | {FAMILY.get(rec, rec)} | see the recommendation above: on test it ranks the right plate first "
          f"{results[rec]['test']['top1']:.3f} of the time and accepts {results[rec]['test']['acc_r']:.3f} of right "
          f"reads unattended at precision {results[rec]['test']['acc_p']:.3f}, with "
          f"{results[rec]['test']['unknown_accept']:.3f} of unknown neighbours accepted |",
          "| decision | thresholds from validation + state-code and clone rules | precision target met on held-out "
          "reads; rules a court can follow |", "",
          f"Channel evidence costs {ms_channel:.1f} ms a read.", "",
          "## Logistic regression: what each piece of evidence weighs", "",
          f"Model: {FAMILY.get(coef_of, coef_of)}.", "",
          "Standardised coefficients (+ pushes towards *same vehicle*). Collinear string measures share weight, so "
          "read the groups, not single signs.", "", "| feature | weight |", "|---|---|"]
    L += [f"| {k} | {v:+.3f} |" for k, v in sorted(coefs.items(), key=lambda kv: -abs(kv[1]))]
    L += ["", "## Retrieval: could candidate search be cheaper?", "",
          "| method | true plate retrieved | candidates per read | ms per read |", "|---|---|---|---|"]
    L += [f"| {c['method']} | {c['recall']:.3f} | {c['candidates']:.1f} | {c['ms']:.2f} |" for c in rcost]
    L += ["", "A deletion index answers in a dictionary lookup, but only up to k errors; the benchmark's damaged reads "
          "often have more, so it loses true plates that the widening q-gram search keeps. Recall comes first: a plate "
          "not retrieved can never be matched."]
    DOC.write_text("\n".join(L) + "\n", encoding="utf-8")
    JSON_OUT.write_text(json.dumps({
        "best": best, "eligible": eligible, "ranked": ranked, "family": FAMILY,
        "test": {n: {k: v for k, v in results[n]["test"].items()} for n in ranked},
        "val": {n: {k: v for k, v in results[n]["val"].items()} for n in ranked},
        "significance": sig, "bootstrap": ci, "retrieval": rcost, "coefficients": coefs}, indent=1), encoding="utf-8")
    print("\n".join(L))


if __name__ == "__main__":
    raise SystemExit(main())
