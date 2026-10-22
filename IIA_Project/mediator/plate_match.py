"""Step 2 of the two-step framework: score, rerank, calibrate and decide (workstream C).

Pipeline for one noisy read (deck p. 76, retrieve then rerank):

    retrieve()                     blocking.candidate_plates, recall-first (step 1, p. 44-65);
                                   widened (max_edits+1, then a full scan) if nothing survives
      -> shortlist                 hand confusion-weighted distance keeps the best SHORTLIST
      -> features()                one vector per (read, candidate) pair (p. 71)
      -> combiner                  rule | one measure | weighted | logreg | gbt  (p. 69, 72)
      -> Platt calibration         score -> probability, fitted on the validation fold (p. 81)
      -> decide()                  ACCEPT / REVIEW / REJECT, thresholds chosen on validation

The learned combiners, every calibrator, the thresholds and the retrieval `max_edits` live in
`data/match_model.joblib`, written by `scripts/evaluate_matching.py`. Loading is lazy; if the
file or scikit-learn is missing the matcher degrades to the hand-weighted combiner and says so
in every candidate's reasons. Nothing about the registry is cached: each call reads the
registry it is given, or REG itself (read-only).
"""
from __future__ import annotations

import hashlib
import math

import numpy as np  # PHASE3-ADDED (C3: explain() reads the stacked model's weights)
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence, Set, Tuple, Union

from mediator import plate_resolve
from mediator import confusion
from mediator.blocking import BlockingStats, QGramIndex, candidate_plates
from mediator.confusion import learned_ocr_similarity
from mediator import open_world  # PHASE3-ADDED (C3: the open-world check bounds every probability)
from mediator.similarity import ALL_MEASURES, _tokens, jaccard, ocr_edit_similarity
from mediator.transforms import norm_plate

ROOT = Path(__file__).resolve().parent.parent
REG_DB = ROOT / "sources" / "reg" / "reg.db"
MODEL_PATH = ROOT / "data" / "match_model.joblib"

PREFILTER = 100        # Jaccard (8 us a pair) cuts a wide candidate set to this first ...
SHORTLIST = 30         # ... then the confusion-weighted distance (120 us) keeps this many
DEFAULT_MAX_EDITS = 2  # used only when no model bundle records the validated value
ACCEPT_MARGIN = 0.10   # the winner must clear the runner-up by this much, else a human looks
# Two or more of make / model / colour contradicting the winner's registration hold an ACCEPT at REVIEW:
# the plate is identified, the vehicle is not (a clone, or the wrong car). Measured on the held-out test
# vehicles (docs/CONTEXT_VETO.md): auto-accepted clones 65 % -> 13 %, ACCEPT recall 0.869 -> 0.785, precision 0.997.
# One contradiction alone does not veto: cameras misjudge colour (recall would fall to 0.417).
CONTEXT_VETO = 2
CONTEXT_KEYS = ("make_agree", "model_agree", "colour_agree")
FALLBACK_THRESHOLDS = {"accept": 0.90, "reject": 0.50}  # uncalibrated: only without a bundle
# PHASE3-ADDED (C3: matching re-evaluated; the examiner's 0L01AB1234 was ACCEPTed as DL01AB1234 at p=1.000)
# A correction inside the state code changes which authority's plate this is, so it is never auto-accepted on
# the characters alone. It is ACCEPTed only when the other evidence agrees (C1): at least CORROBORATING_SOURCES
# authoritative sources hold the corrected plate AND the observed make and colour both match it.
CORROBORATING_SOURCES = 2
# PHASE3-END

STRING_FEATURES: List[str] = list(ALL_MEASURES) + [
    "tfidf", "learned_ocr_similarity", "length_diff", "state_agree"]
CONTEXT_FEATURES = ["make_agree", "model_agree", "colour_agree"]
FEATURE_NAMES: List[str] = STRING_FEATURES + ["ocr_confidence"] + CONTEXT_FEATURES + [
    "read_is_registered"]
SCORE_NAMES = list(ALL_MEASURES) + ["tfidf", "learned_ocr_similarity"]
# Hand weights for `weighted`: string evidence 0.8, camera context 0.2. The learned distance
# carries the domain knowledge, Jaro-Winkler the state-code prefix, Jaccard robustness to one bad
# glyph; make outweighs colour as in plate_resolve (light moves perceived colour, not the badge).
WEIGHTS = {"learned_ocr_similarity": 0.45, "jaro_winkler": 0.20, "jaccard": 0.15,
           "make_agree": 0.12, "colour_agree": 0.08}

Registry = Union[Mapping[str, Mapping[str, Any]], Sequence[str], None]


@dataclass
class Candidate:
    plate: str
    scores: Dict[str, float] = field(default_factory=dict)
    features: Dict[str, float] = field(default_factory=dict)
    probability: float = 0.0
    decision: str = "REJECT"
    reasons: List[str] = field(default_factory=list)
    # PHASE3-ADDED (C1 + C3): model_p, p_unknown, corrections, state_code_fix, sources, held_by
    evidence: Dict[str, Any] = field(default_factory=dict)
    # PHASE3-END


def load_registry(path: Optional[Path] = None) -> Dict[str, Dict[str, Any]]:
    """plate -> {make, model, colour} from REG, opened read-only. Read fresh on every call."""
    con = sqlite3.connect(f"file:{path or REG_DB}?mode=ro", uri=True)
    try:
        rows = con.execute(
            "SELECT registration_no, make, model, colour FROM VEHICLE_REGISTRATION").fetchall()
    finally:
        con.close()
    return {norm_plate(p): {"make": mk, "model": md, "colour": c} for p, mk, md, c in rows}


def _as_mapping(registry: Registry) -> Dict[str, Dict[str, Any]]:
    if registry is None:
        return load_registry()
    if isinstance(registry, Mapping):
        return {norm_plate(k): dict(v or {}) for k, v in registry.items()}
    return {norm_plate(p): {} for p in registry}


def _agree(observed: Optional[str], registered: Optional[str]) -> float:
    """+1 agree, -1 disagree, 0 when either side is unknown (missing evidence is not evidence)."""
    if not observed or not registered:
        return 0.0
    return 1.0 if plate_resolve._agrees(observed, registered) else -1.0


# ------------------------------------------------------------------ per-registry artefacts

class CachedTfidf:
    """Exactly `similarity.tfidf_cosine(s1, s2, corpus, q)`, with the corpus statistics computed
    once. The original re-tokenises all N documents on every call (3-5 ms a pair over 605
    plates); this keeps its formula and its quirk of appending s1/s2 to the collection when they
    are absent. tests/test_plate_match.py asserts equality against the original."""

    def __init__(self, corpus: Sequence[str], q: int = 2) -> None:
        self.q = q
        docs = [tuple(_tokens(c, q)) for c in corpus]
        self.seen = set(docs)
        self.n = len(docs)
        self.df: Counter = Counter(t for d in docs for t in set(d))

    def __call__(self, s1: str, s2: str) -> float:
        x, y = str(s1 or "").upper(), str(s2 or "").upper()
        if not x or not y:
            return 1.0 if x == y else 0.0
        if x == y:
            return 1.0
        tx, ty = tuple(_tokens(x, self.q)), tuple(_tokens(y, self.q))
        extra = [t for i, t in enumerate((tx, ty)) if t not in self.seen and (i == 0 or t != tx)]
        n = self.n + len(extra)

        def idf(t: str) -> float:
            nd = self.df.get(t, 0) + sum(1 for e in extra if t in e)
            return math.log(n / (nd or 1))
        vx = {t: f * idf(t) for t, f in Counter(tx).items()}
        vy = {t: f * idf(t) for t, f in Counter(ty).items()}
        dot = sum(v * vy.get(t, 0.0) for t, v in vx.items())
        nx = math.sqrt(sum(v * v for v in vx.values()))
        ny = math.sqrt(sum(v * v for v in vy.values()))
        if nx == 0.0 or ny == 0.0:
            return 0.0
        return max(0.0, min(1.0, dot / (nx * ny)))


# Keyed by a hash of the registry's plate list, so an insert or delete by the examiner changes
# the key and forces a rebuild: this memoises a *derivation* of the current registry, never a
# stale copy of it. Make/model/colour are not cached at all -- they are re-read on every call.
_artefacts: Dict[str, Tuple[QGramIndex, CachedTfidf]] = {}
_pair_memo: Dict[Tuple[str, int, str, str], Dict[str, float]] = {}
_MEMO_MAX = 200_000


def registry_artefacts(plates: Sequence[str]) -> Tuple[str, QGramIndex, CachedTfidf]:
    """(content key, q-gram index, TF/IDF statistics) for this exact plate list."""
    key = hashlib.sha1("\n".join(plates).encode("utf-8")).hexdigest()
    hit = _artefacts.get(key)
    if hit is None:
        if len(_artefacts) >= 4:
            _artefacts.clear()
        hit = _artefacts[key] = (QGramIndex.build(plates), CachedTfidf(plates))
    return key, hit[0], hit[1]


def pair_features(key: str, read: str, cand: str, tfidf: Callable[[str, str], float]
                  ) -> Dict[str, float]:
    """`string_features`, memoised on (registry key, cost version, read, candidate)."""
    mk = (key, confusion.costs_version(), read, cand)
    hit = _pair_memo.get(mk)
    if hit is None:
        if len(_pair_memo) >= _MEMO_MAX:
            _pair_memo.clear()
        hit = _pair_memo[mk] = string_features(read, cand, tfidf)
    return hit


def string_features(read: str, cand: str, tfidf: Callable[[str, str], float]) -> Dict[str, float]:
    """The string half of the vector; depends on the pair only, so callers may memoise it."""
    out = {name: float(fn(read, cand)) for name, fn in ALL_MEASURES.items()}
    out["tfidf"] = float(tfidf(read, cand))
    out["learned_ocr_similarity"] = learned_ocr_similarity(read, cand)
    out["length_diff"] = float(abs(len(read) - len(cand)))
    out["state_agree"] = 1.0 if len(read) >= 2 and read[:2] == cand[:2] else 0.0
    return out


def context_features(*, confidence: Optional[float], observed_make: Optional[str],
                     observed_colour: Optional[str], observed_model: Optional[str],
                     registry_row: Optional[Mapping[str, Any]], read_registered: bool
                     ) -> Dict[str, float]:
    row = registry_row or {}
    return {
        # 0.5 = "unknown"; the benchmark's mean confidence is 0.69, so this leans sceptical
        "ocr_confidence": 0.5 if confidence is None else float(confidence),
        "make_agree": _agree(observed_make, row.get("make")),
        "model_agree": _agree(observed_model, row.get("model")),
        "colour_agree": _agree(observed_colour, row.get("colour")),
        "read_is_registered": 1.0 if read_registered else 0.0,
    }


def features(read: str, candidate: str, *, confidence: Optional[float],
             observed_make: Optional[str], observed_colour: Optional[str],
             registry_row: Optional[Mapping[str, Any]], corpus: Sequence[str],
             observed_model: Optional[str] = None) -> Dict[str, float]:
    """The full feature vector of p. 71 for one (read, candidate) pair, keyed by FEATURE_NAMES."""
    key, _, tfidf = registry_artefacts(list(corpus))
    out = dict(pair_features(key, read, candidate, tfidf))
    out.update(context_features(confidence=confidence, observed_make=observed_make,
                                observed_colour=observed_colour, observed_model=observed_model,
                                registry_row=registry_row, read_registered=read in set(corpus)))
    return out


# ------------------------------------------------------------------ model bundle

_bundle: Optional[Dict[str, Any]] = None
_bundle_error: Optional[str] = None


def reload_bundle() -> None:
    """Forget the loaded bundle (or the reason it failed) so the next call re-reads the file."""
    global _bundle, _bundle_error
    _bundle, _bundle_error = None, None


def load_bundle() -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(bundle, None) or (None, reason). Lazy: scikit-learn is imported only here."""
    global _bundle, _bundle_error
    if _bundle is None and _bundle_error is None:
        if not MODEL_PATH.exists():
            _bundle_error = f"model file {MODEL_PATH.name} not found"
        else:
            try:
                import joblib
                _bundle = joblib.load(MODEL_PATH)
            except Exception as exc:  # ImportError without scikit-learn, or a corrupt file
                _bundle_error = f"model could not be loaded ({exc.__class__.__name__}: {exc})"
    return _bundle, _bundle_error


def logreg_coefficients() -> Dict[str, float]:
    """Logistic-regression weights per standardised feature: + pushes towards "same vehicle"."""
    bundle, _ = load_bundle()
    if not bundle:
        return {}
    model = bundle["models"]["logreg"]
    return dict(zip(bundle["features"], (round(float(c), 3) for c in model[-1].coef_[0])))


def raw_scores(model: str, vectors: List[Dict[str, float]],
               bundle: Optional[Dict[str, Any]]) -> List[float]:
    """Uncalibrated combiner output per vector; higher means more likely the same vehicle."""
    if not vectors:
        return []
    if model in FEATURE_NAMES:
        return [v[model] for v in vectors]
    if model == "weighted":
        return [sum(w * ((v[f] + 1) / 2 if f in CONTEXT_FEATURES else v[f])
                    for f, w in WEIGHTS.items()) for v in vectors]
    # PHASE3-REPLACED (C3: the stacked model has its own, longer feature list):
    # if model in ("logreg", "gbt"):
    #     names = bundle["features"]
    if model in ("logreg", "gbt", "stacked"):
        names = bundle["stacked_features"] if model == "stacked" else bundle["features"]
        X = [[v[n] for n in names] for v in vectors]
        return [float(p) for p in bundle["models"][model].predict_proba(X)[:, 1]]
    raise ValueError(f"unknown model {model!r}")


def platt(params: Optional[Sequence[float]], scores: List[float]) -> List[float]:
    """p = 1 / (1 + exp(-(a*s + b))). Platt, not isotonic: isotonic's step function maps distinct
    scores to the same probability, which would manufacture ties at the ACCEPT margin."""
    if not params:
        return [max(0.0, min(1.0, s)) for s in scores]
    a, b = params
    return [1.0 / (1.0 + math.exp(-max(-500.0, min(500.0, a * s + b)))) for s in scores]


def thresholds_for(model: str, bundle: Optional[Dict[str, Any]]) -> Dict[str, float]:
    return (bundle or {}).get("thresholds", {}).get(model, FALLBACK_THRESHOLDS)


def decide(ranked: List[Candidate], th: Dict[str, float]) -> None:
    """ACCEPT only the top candidate, only above the precision threshold and clear of the rest."""
    second = ranked[1].probability if len(ranked) > 1 else 0.0
    for i, c in enumerate(ranked):
        # PHASE2-REPLACED (context veto: an exact read of DL01AB0118 seen as a red Honda City was ACCEPTed for a
        # registered black Maruti Baleno at 0.948, because an exact string outweighs the context features):
        # if i == 0 and c.probability >= th["accept"] and c.probability - second >= ACCEPT_MARGIN:
        #     c.decision = "ACCEPT"
        #     c.reasons.append(f"p={c.probability:.3f} >= accept threshold {th['accept']:.3f}, "
        #                      f"{c.probability - second:.3f} clear of the runner-up")
        contradicted = [k.split("_")[0] for k in CONTEXT_KEYS if c.features.get(k) == -1.0]
        if i == 0 and c.probability >= th["accept"] and c.probability - second >= ACCEPT_MARGIN:
            if len(contradicted) >= CONTEXT_VETO:
                c.decision = "REVIEW"
                c.reasons.append(f"p={c.probability:.3f} would ACCEPT the plate, but the camera's "
                                 f"{', '.join(contradicted)} contradict its registration: possible clone; needs an operator")
                continue
            # PHASE3-ADDED (C4: the observed car can confirm a plausible read but not rescue an implausible one --
            # with make and colour agreeing, XX05CD9876 reached p=0.916 and was ACCEPTed as DL05CD9876)
            if c.evidence.get("p_unknown", 0.0) >= UNKNOWN_VETO:
                c.decision = "REVIEW"
                c.reasons.append(f"p={c.probability:.3f} would ACCEPT, but the characters alone make a car no agency "
                                 f"holds more likely (p={c.evidence['p_unknown']:.3f}); the car's look cannot prove "
                                 "the plate: needs an operator")
                continue
            # PHASE3-END
            # PHASE3-ADDED (C3: a state-code correction is never accepted on the characters alone; C1: unless
            # the other databases and the observed car confirm it)
            if c.evidence.get("state_code_fix"):
                if not _corroborated(c):
                    c.decision = "REVIEW"
                    c.reasons.append(f"p={c.probability:.3f} would ACCEPT, but the match changes the state code "
                                     f"({c.evidence['state_code_fix']}): another state's plate is possible; needs an "
                                     "operator, or the make and colour plus a second agency to confirm it")
                    continue
                c.reasons.append(f"state-code correction ({c.evidence['state_code_fix']}) corroborated: held by "
                                 f"{', '.join(c.evidence.get('sources') or [])}, make and colour agree")
            # PHASE3-END
            c.decision = "ACCEPT"
            c.reasons.append(f"p={c.probability:.3f} >= accept threshold {th['accept']:.3f}, "
                             f"{c.probability - second:.3f} clear of the runner-up")
        # PHASE3-REPLACED (C3: REJECT means "no plate the sources hold is plausible", a closed-world question, so
        # it is asked of the learned model's own p; the open-world p decides ACCEPT only. Otherwise a very noisy
        # read whose right plate the model ranks first at 0.999 was REJECTed, not shown to a person):
        # elif c.probability >= th["reject"]:
        elif c.probability >= th["reject"] or c.evidence.get("model_p", 0.0) >= th["reject"]:
            c.decision = "REVIEW"
            why = "a rival is too close" if i == 0 and c.probability >= th["accept"] else \
                "between thresholds"
            # PHASE3-ADDED (C3)
            if c.probability < th["reject"]:
                why = (f"a held plate is plausible (model p={c.evidence['model_p']:.3f}), but a car no agency holds "
                       "explains the read better")
            # PHASE3-END
            c.reasons.append(f"p={c.probability:.3f}: {why}; needs an operator")
        else:
            c.decision = "REJECT"
            c.reasons.append(f"p={c.probability:.3f} < reject threshold {th['reject']:.3f}")


# PHASE3-ADDED (C1 + C3: the open-world bound, and the other databases as corroboration)
def _corroborated(c: Candidate) -> bool:
    authoritative = [s for s in c.evidence.get("sources") or [] if s != "CAM"]
    return (len(authoritative) >= CORROBORATING_SOURCES and c.features.get("make_agree") == 1.0
            and c.features.get("colour_agree") == 1.0)


_shape_memo: Dict[str, Dict[str, float]] = {}


def _shapes(reg: Mapping[str, Mapping[str, Any]]) -> Dict[str, float]:
    """open_world.shape_weights of this exact registry, memoised on its plate list like registry_artefacts."""
    key = hashlib.sha1("\n".join(sorted(reg)).encode("utf-8")).hexdigest()
    if key not in _shape_memo:
        if len(_shape_memo) >= 4:
            _shape_memo.clear()
        _shape_memo[key] = open_world.shape_weights(reg)
    return _shape_memo[key]


# The stacked matcher (C3, chosen in scripts/compare_matchers.py -> docs/MATCHER_COMPARISON.md): the 18 phase-2
# features plus the noisy channel's evidence and per-segment agreement, trained also on reads of cars no source
# holds, so it learns to refuse an unknown neighbour instead of being bounded afterwards.
CHANNEL_FEATURES = ["log_channel", "p_open", "p_held"]
SEGMENT_FEATURES = ["seg_state", "seg_district", "seg_series", "seg_number"]


def channel_features(cand: str, ow: "open_world.OpenWorld") -> Dict[str, float]:
    return {"log_channel": math.log10(ow.likelihoods.get(cand, 0.0) + 1e-30),
            "p_open": ow.probabilities.get(cand, 0.0), "p_held": 1.0 - ow.unknown}


def segment_features(read: str, cand: str) -> Dict[str, float]:
    """Per segment of the candidate (state, district, series, number), the share of its characters the read
    has at the same place; the read is cut by the candidate's segment lengths."""
    m = open_world._SHAPE_RE.match(cand)
    if not m:
        return dict.fromkeys(SEGMENT_FEATURES, 0.0)
    out, i = {}, 0
    for name, part in zip(SEGMENT_FEATURES, (cand[:2], cand[2:4], m.group(1), cand[-4:])):
        piece = read[i:i + len(part)]
        out[name] = sum(a == b for a, b in zip(piece, part)) / len(part)
        i += len(part)
    return out


def open_world_posterior(read: str, cands: Sequence[str], reg: Mapping[str, Mapping[str, Any]],
                         confidence: Optional[float]) -> "open_world.OpenWorld":
    priors = {c: float((reg.get(c) or {}).get("prior", 1.0)) for c in cands}
    return open_world.posterior(read, list(cands), priors, confidence=confidence, shapes=_shapes(reg))


# PHASE3-REPLACED (C3: the posterior is computed once in match(), and a stacked model is not bounded again):
# def apply_open_world(read: str, ranked: List[Candidate], reg: Mapping[str, Mapping[str, Any]],
#                      confidence: Optional[float]) -> None:
def apply_open_world(read: str, ranked: List[Candidate], reg: Mapping[str, Mapping[str, Any]],
                     confidence: Optional[float], ow: Optional["open_world.OpenWorld"] = None,
                     bound: bool = True) -> None:
    """Turn the learned, closed-world probability into an open-world one, and record why.

    The learned model was calibrated on reads of registered plates only, so its p is p(c | read, the car is
    one the sources hold); it uses the camera's make, model and colour, so it is the right judge of WHICH
    held plate. The open-world posterior (open_world.py) answers the question it never saw: is the car one
    the sources hold at all? Hence p(c | read) = p(c | read, held) x p(held | read). The model stays the
    ranker it was validated as, and can no longer be surer than the read allows.
    """
    # PHASE3-REPLACED (C3 follow-up: unknown plates weighted by the shapes of the plates the sources hold, live;
    # then computed once in match()):
    # priors = {c.plate: float((reg.get(c.plate) or {}).get("prior", 1.0)) for c in ranked}
    # ow = open_world.posterior(read, [c.plate for c in ranked], priors, confidence=confidence)
    ow = ow or open_world_posterior(read, [c.plate for c in ranked], reg, confidence)
    p_held = 1.0 - ow.unknown
    shares = value_shares(reg)  # PHASE3-ADDED (C4: how common each make / model / colour is among held cars) PHASE3-END
    ch = open_world.channel(confidence)
    for c in ranked:
        row = reg.get(c.plate) or {}
        fixes = open_world.corrections(read, c.plate, ch)
        c.evidence.update(model_p=c.probability, p_unknown=ow.unknown,
                          corrections=[f.words() for f in fixes],
                          state_code_fix="; ".join(f.words() for f in fixes if f.in_state_code),
                          sources=list(row.get("sources") or []), held_by=row.get("held_by"))
        # PHASE3-REPLACED (C3: a min() cap hid the context -- two captures of one plate, one with contradicting make
        # and colour, showed the same probability; the stacked model's p is multiplied below like any other):
        # if not bound:
        #     if c.probability > p_held:
        #         c.reasons.append(...)
        #     c.probability = min(c.probability, p_held)
        #     continue
        # PHASE3-REPLACED (C4: the stacked model's p saturates near 1, so the camera's make, model and colour no
        # longer moved the probability shown -- DL05CD9B76 showed 0.774 whether the car agreed or contradicted):
        # if ow.unknown >= 0.005:
        #     c.reasons.append(f"model p={c.probability:.3f} x p(a held car)={p_held:.3f}: a plate no source holds "
        #                      f"explains this read with p={ow.unknown:.3f}")
        # c.probability *= p_held
        # PHASE3-ADDED (C4: the observed car decides between "this held car" and "an unknown car with a similar
        # plate": each make / model / colour is a likelihood ratio on the odds of p(held))
        lr, why = context_ratio(c, row, shares)
        if ow.unknown >= UNKNOWN_VETO and lr > 1.0:  # an implausible read: the car may lower p, never raise it
            lr, why = 1.0, why + " (not applied upwards: the characters alone favour an unknown car)"
        odds =(p_held / max(ow.unknown, 1e-6)) * lr
        p_held_c = odds / (1.0 + odds)
        c.evidence.update(context_lr=lr, p_held=p_held_c)
        if ow.unknown >= 0.005 or lr != 1.0:
            c.reasons.append(f"model p={c.probability:.3f} x p(a held car)={p_held_c:.3f}: a plate no source holds "
                             f"explains this read with p={ow.unknown:.3f}" + (f"; observed car {why}" if why else ""))
        c.probability *= p_held_c
        # PHASE3-END
# PHASE3-END


# PHASE3-ADDED (C4: context as likelihood ratios. The real car shows its registered value with the rate the
# benchmark simulates for road cameras (make 85 %, model 70 %, colour 75 %; real CAM captures agree 95.5 %, so
# these are deliberately conservative). An unknown car matches the candidate's value only by chance: the share
# of held cars with that value. Agreeing: rate / share; contradicting: (1 - rate) / (1 - share); unknown: 1.)
CONTEXT_RATES = {"make": 0.85, "model": 0.70, "colour": 0.75}
UNKNOWN_VETO = 0.5  # read alone favours an unknown car: context may raise p, never to an automatic ACCEPT
CONTEXT_FEATURE = {"make": "make_agree", "model": "model_agree", "colour": "colour_agree"}


def value_shares(reg: Mapping[str, Mapping[str, Any]]) -> Dict[str, Dict[str, float]]:
    """attribute -> value -> share of held plates with it (lower-cased)."""
    out: Dict[str, Dict[str, float]] = {}
    for attr in CONTEXT_RATES:
        vals = [str(r.get(attr)).strip().lower() for r in reg.values() if r.get(attr)]
        out[attr] = {v: n / len(vals) for v, n in Counter(vals).items()} if vals else {}
    return out


def context_ratio(c: Candidate, row: Mapping[str, Any], shares: Dict[str, Dict[str, float]]) -> Tuple[float, str]:
    """(likelihood ratio, 'make agrees x4.0, colour differs x0.29') for candidate `c`."""
    lr, words = 1.0, []
    for attr, rate in CONTEXT_RATES.items():
        agree = c.features.get(CONTEXT_FEATURE[attr], 0.0)
        share = shares.get(attr, {}).get(str(row.get(attr) or "").strip().lower())
        if not agree or not share:
            continue
        share = min(max(share, 0.01), 0.99)
        r = rate / share if agree > 0 else (1.0 - rate) / (1.0 - share)
        lr *= r
        words.append(f"{attr} {'agrees' if agree > 0 else 'differs'} x{r:.2f}")
    return lr, ", ".join(words)
# PHASE3-END


# ------------------------------------------------------------------ retrieval

def retrieve(read: str, plates: Sequence[str], *, strategy: str, max_edits: int,
             index: Any = None, full_scan: bool = True
             ) -> Tuple[List[str], BlockingStats, List[str]]:
    """Blocking, then widening when nothing survives: max_edits+1, then a full scan (p. 40).

    The q-gram bounds are only recall-safe up to `max_edits`; a read with more errors than that
    can lose its true plate in step 1, and no step-2 scoring can bring it back (p. 82).
    """
    notes: List[str] = []
    kw = {"index": index} if index is not None else {}
    cands, stats = candidate_plates(read, plates, strategy=strategy, max_edits=max_edits, **kw)
    if not cands and read and strategy != "none":
        cands, stats = candidate_plates(read, plates, strategy=strategy,
                                        max_edits=max_edits + 1, **kw)
        notes.append(f"nothing survived blocking at max_edits={max_edits}; "
                     f"widened to {max_edits + 1}")
        if not cands and full_scan:
            cands, stats = candidate_plates(read, plates, strategy="none")
            notes.append("still nothing; fell back to a full scan of the registry")
    return cands, stats, notes


def shortlist(read: str, cands: List[str]) -> Tuple[List[str], List[str]]:
    """Two cheap stages before the full feature vector. On the benchmark the Jaccard pre-filter
    loses no true plate that the distance-only shortlist kept, at a quarter of the cost."""
    if len(cands) <= SHORTLIST:
        return cands, []
    n = len(cands)
    if n > PREFILTER:
        cands = sorted(cands, key=lambda p: (-jaccard(read, p), p))[:PREFILTER]
    keep = sorted(cands, key=lambda p: (-ocr_edit_similarity(read, p), p))[:SHORTLIST]
    return keep, [f"shortlisted {SHORTLIST} of {n} (Jaccard to {min(n, PREFILTER)}, then OCR "
                  "edit similarity)"]


# ------------------------------------------------------------------ entry points

def rule_match(read: str, reg: Mapping[str, Mapping[str, Any]], observed_make: Optional[str],
               observed_colour: Optional[str]) -> List[Candidate]:
    """The phase-1 resolver (plate_resolve) as a contender: own candidates, own decision."""
    def lookup(p: str) -> Dict[str, Any]:
        row = reg.get(p)
        return {"vehicle_make": row.get("make"), "vehicle_colour": row.get("colour"),
                "registration_status": "ACTIVE"} if row is not None else {}
    _, ranked, outcome = plate_resolve.resolve(read, observed_make, observed_colour, lookup)
    top = {"RESOLVED": "ACCEPT", "AMBIGUOUS": "REVIEW"}.get(outcome, "REJECT")
    return [Candidate(c.plate, {"rule": c.score}, {}, c.score, top if i == 0 else "REVIEW",
                      list(c.reasons) + [f"plate_resolve outcome {outcome}"])
            for i, c in enumerate(ranked)]


def match(read: str, *, confidence: Optional[float] = None,
          observed_make: Optional[str] = None, observed_colour: Optional[str] = None,
          registry: Registry = None, strategy: str = "position", model: str = "logreg",
          top_k: int = 10, observed_model: Optional[str] = None,
          max_edits: Optional[int] = None,
          open_world_check: bool = True,  # PHASE3-ADDED (C3): False reproduces the phase-2 matcher
          ) -> Tuple[List[Candidate], BlockingStats]:
    """Rank the registered plates a noisy read could be, with a calibrated decision each."""
    read = norm_plate(read) or ""
    reg = _as_mapping(registry)
    plates = sorted(reg)
    bundle, err = load_bundle()
    notes: List[str] = []
    if model in ("logreg", "gbt") and bundle is None:
        notes.append(f"{model} unavailable ({err}); fell back to the hand-weighted combiner")
        model = "weighted"
    if bundle is None:
        notes.append("no calibration bundle: probabilities and thresholds are uncalibrated")
    me = max_edits if max_edits is not None else int(
        (bundle or {}).get("max_edits", DEFAULT_MAX_EDITS))
    key, index, tfidf = registry_artefacts(plates)
    cands, stats, more = retrieve(read, plates, strategy=strategy, max_edits=me, index=index)
    notes += more
    if model == "rule":
        ranked = rule_match(read, reg, observed_make, observed_colour)
        for c in ranked:
            c.reasons = notes + c.reasons
        return ranked[:top_k], stats
    cands, more = shortlist(read, cands)
    notes += more
    plate_set = set(plates)
    ctx = dict(confidence=confidence, observed_make=observed_make,
               observed_colour=observed_colour, observed_model=observed_model,
               read_registered=read in plate_set)
    vectors = [{**pair_features(key, read, p, tfidf),
                **context_features(registry_row=reg.get(p), **ctx)} for p in cands]
    # PHASE3-ADDED (C3: the comparison's recommended matcher is the default whenever the bundle holds it)
    ow = open_world_posterior(read, cands, reg, confidence) if open_world_check and cands else None
    stacked = bool(ow) and model == "logreg" and "stacked" in ((bundle or {}).get("models") or {})
    if stacked:
        model = "stacked"
        for p, v in zip(cands, vectors):
            v.update(channel_features(p, ow), **segment_features(read, p))
    # PHASE3-END
    probs = platt((bundle or {}).get("calibration", {}).get(model),
                  raw_scores(model, vectors, bundle))
    ranked = [Candidate(p, {m: v[m] for m in SCORE_NAMES}, v, pr, "REJECT", list(notes))
              for p, v, pr in zip(cands, vectors, probs)]
    # PHASE3-ADDED (C3: bound the learned probability by the open-world posterior before deciding)
    # PHASE3-REPLACED (C3: the posterior from above; the stacked model is not bounded a second time):
    # if open_world_check:
    #     apply_open_world(read, ranked, reg, confidence)
    if open_world_check and ranked:
        apply_open_world(read, ranked, reg, confidence, ow=ow, bound=not stacked)
    # PHASE3-END
    # PHASE3-REPLACED (C3: the learned model stays the ranker it was validated as -- re-ranking by the bounded
    # probability put the right plate first less often, 0.931 -> 0.886; the bound only decides and is shown):
    # ranked.sort(key=lambda c: (-c.probability, c.plate))
    ranked.sort(key=lambda c: (-c.evidence.get("model_p", c.probability), c.plate))
    decide(ranked, thresholds_for(model, bundle))
    return ranked[:top_k], stats


def reverse_match(plate: str, captures: Sequence[Union[str, Mapping[str, Any]]], *,
                  registry: Registry = None, strategy: str = "position",
                  model: str = "logreg") -> List[Candidate]:
    """Vice versa: the camera reads that could be `plate`, scored by the same forward matcher.

    Captures are strings or dicts with `read` (or `plate_id`), `ocr_confidence`,
    `observed_make`, `observed_colour`, `observed_model`. Blocking runs over the captures, then
    each surviving read is matched *forward* against the whole registry: a read is only claimed
    for `plate` if its own forward ranking contains `plate`, so a read that is really a different
    registered vehicle is never assigned here, and its decision is the forward decision.
    """
    target = norm_plate(plate) or ""
    reg = _as_mapping(registry)
    bundle, _ = load_bundle()
    me = int((bundle or {}).get("max_edits", DEFAULT_MAX_EDITS))
    info: Dict[str, Dict[str, Any]] = {}
    for c in captures:
        d = {"read": c} if isinstance(c, str) else dict(c)
        r = norm_plate(d.get("read") or d.get("plate_id")) or ""
        if r:
            info.setdefault(r, d)
    # No full-scan fallback here: it would turn one lookup into a forward match per capture.
    reads, _, _ = retrieve(target, sorted(info), strategy=strategy, max_edits=me,
                           full_scan=False)
    out: List[Candidate] = []
    for r in reads:
        d = info[r]
        ranked, _ = match(r, confidence=d.get("ocr_confidence"),
                          observed_make=d.get("observed_make"),
                          observed_colour=d.get("observed_colour"),
                          observed_model=d.get("observed_model"), registry=reg,
                          strategy=strategy, model=model)
        hit = next((c for c in ranked if c.plate == target), None)
        if hit is not None:
            # PHASE3-REPLACED (C3: the open-world evidence travels with the reverse match too):
            # out.append(Candidate(r, hit.scores, hit.features, hit.probability, hit.decision,
            #                      hit.reasons + [f"forward match of {r} ranks {target}"]))
            out.append(Candidate(r, hit.scores, hit.features, hit.probability, hit.decision,
                                 hit.reasons + [f"forward match of {r} ranks {target}"], dict(hit.evidence)))
    out.sort(key=lambda c: (-c.probability, c.plate))
    return out


# PHASE3-ADDED (C1 + C2 + C3: why the stacked model chose a candidate, evidence by evidence) --------------------
EVIDENCE_GROUPS: Dict[str, List[str]] = {
    "plate characters": [n for n in STRING_FEATURES] + ["log_channel", "p_open"] + SEGMENT_FEATURES,
    "a car the agencies hold (open world)": ["p_held", "read_is_registered"],
    "OCR confidence": ["ocr_confidence"],
    "make": ["make_agree"], "model": ["model_agree"], "colour": ["colour_agree"],
}


def explain(c: Candidate) -> List[Tuple[str, float]]:
    """(evidence group, log-odds contribution) for a candidate scored by the stacked logistic regression.

    Each feature contributes weight x (value - training mean) / training spread, exactly what the model adds
    up, so the bars sum to the model's log-odds minus the intercept. Positive = towards "this is the car".
    Empty when the candidate was not scored by the stacked model."""
    bundle, _ = load_bundle()
    pipe = ((bundle or {}).get("models") or {}).get("stacked")
    names = (bundle or {}).get("stacked_features") or []
    if pipe is None or not all(n in c.features for n in names):
        return []
    scaler, lr = pipe[0], pipe[-1]
    z = (np.array([c.features[n] for n in names]) - scaler.mean_) / scaler.scale_
    contrib = dict(zip(names, lr.coef_[0] * z))
    return [(g, float(sum(contrib.get(n, 0.0) for n in fs))) for g, fs in EVIDENCE_GROUPS.items()]
# PHASE3-END
