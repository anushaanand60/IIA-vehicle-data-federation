"""Step 2 of the two-step framework: score, rerank, calibrate and decide (workstream C).

Pipeline for one noisy read (deck p. 76, retrieve then rerank):

    blocking.candidate_plates      retrieve: cheap, recall-first (step 1, p. 44-65)
      -> shortlist                 cheap confusion-weighted distance keeps the best SHORTLIST
      -> features()                one vector per (read, candidate) pair (p. 71)
      -> combiner                  rule | one measure | weighted | logreg | gbt  (p. 69, 72)
      -> Platt calibration         score -> probability, fitted on the validation fold (p. 81)
      -> decide                    ACCEPT / REVIEW / REJECT from thresholds chosen on validation

The learned combiners and every calibrator live in `data/match_model.joblib`, produced by
`scripts/evaluate_matching.py`. Loading is lazy; if the file or scikit-learn is missing the
matcher degrades to the hand-weighted combiner and says so in every candidate's reasons.
Nothing about the registry is cached: every call reads the registry it is given (or REG).
"""
from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Sequence, Tuple, Union

from mediator import plate_resolve
from mediator.blocking import BlockingStats, candidate_plates
from mediator.confusion import learned_ocr_similarity
from mediator.similarity import ALL_MEASURES, ocr_edit_similarity, tfidf_measure
from mediator.transforms import norm_plate

ROOT = Path(__file__).resolve().parent.parent
REG_DB = ROOT / "sources" / "reg" / "reg.db"
MODEL_PATH = ROOT / "data" / "match_model.joblib"

SHORTLIST = 30
DEFAULT_MAX_EDITS = 2
ACCEPT_MARGIN = 0.10   # the winner must clear the runner-up by this much, else a human looks
FALLBACK_THRESHOLDS = {"accept": 0.90, "reject": 0.50}  # uncalibrated, used only without a model

FEATURE_NAMES: List[str] = list(ALL_MEASURES) + [
    "tfidf", "learned_ocr_similarity", "length_diff", "state_agree", "ocr_confidence",
    "make_agree", "model_agree", "colour_agree", "read_is_registered",
]
CONTEXT_FEATURES = ["make_agree", "model_agree", "colour_agree"]
# Hand weights for `weighted`: string evidence 0.8, camera context 0.2, make above colour as in
# plate_resolve (light and weather move perceived colour, not the badge).
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


def load_registry(path: Path = REG_DB) -> Dict[str, Dict[str, Any]]:
    """plate -> {make, model, colour} from REG, opened read-only. Read fresh on every call."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
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


def features(read: str, candidate: str, *, confidence: Optional[float],
             observed_make: Optional[str], observed_colour: Optional[str],
             registry_row: Optional[Mapping[str, Any]], corpus: Sequence[str],
             observed_model: Optional[str] = None) -> Dict[str, float]:
    """The feature vector of p. 71 for one (read, candidate) pair, keyed by FEATURE_NAMES."""
    row = registry_row or {}
    out = {name: float(fn(read, candidate)) for name, fn in ALL_MEASURES.items()}
    out["tfidf"] = float(tfidf_measure(corpus)(read, candidate))
    out["learned_ocr_similarity"] = learned_ocr_similarity(read, candidate)
    out["length_diff"] = float(abs(len(read) - len(candidate)))
    out["state_agree"] = 1.0 if read[:2] == candidate[:2] else 0.0
    out["ocr_confidence"] = 0.5 if confidence is None else float(confidence)
    out["make_agree"] = _agree(observed_make, row.get("make"))
    out["model_agree"] = _agree(observed_model, row.get("model"))
    out["colour_agree"] = _agree(observed_colour, row.get("colour"))
    out["read_is_registered"] = 1.0 if read in corpus else 0.0
    return out


# ------------------------------------------------------------------ model bundle

_bundle: Optional[Dict[str, Any]] = None
_bundle_error: Optional[str] = None


def load_bundle(path: Path = MODEL_PATH) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """(bundle, None) or (None, reason). Lazy: scikit-learn is imported only here."""
    global _bundle, _bundle_error
    if _bundle is None and _bundle_error is None:
        if not path.exists():
            _bundle_error = f"model file {path.name} not found"
        else:
            try:
                import joblib
                _bundle = joblib.load(path)
            except Exception as exc:
                _bundle_error = f"model could not be loaded ({exc.__class__.__name__}: {exc})"
    return _bundle, _bundle_error


def logreg_coefficients() -> Dict[str, float]:
    """Per-feature weights of the logistic regression, on standardised features (read aloud)."""
    bundle, _ = load_bundle()
    if not bundle:
        return {}
    pipe = bundle["models"]["logreg"]
    coef = pipe[-1].coef_[0]
    return dict(zip(bundle["features"], [round(float(c), 3) for c in coef]))


def raw_scores(model: str, vectors: List[Dict[str, float]],
               bundle: Optional[Dict[str, Any]]) -> List[float]:
    """Uncalibrated combiner output per vector; higher means more likely the same vehicle."""
    if model in FEATURE_NAMES:
        return [v[model] for v in vectors]
    if model == "weighted":
        return [sum(w * (v[f] + 1) / 2 if f in CONTEXT_FEATURES else w * v[f]
                    for f, w in WEIGHTS.items()) for v in vectors]
    if model in ("logreg", "gbt"):
        names = bundle["features"]
        X = [[v[n] for n in names] for v in vectors]
        return [float(p) for p in bundle["models"][model].predict_proba(X)[:, 1]]
    raise ValueError(f"unknown model {model!r}")


def calibrate(model: str, scores: List[float], bundle: Optional[Dict[str, Any]]) -> List[float]:
    """Platt scaling p = 1 / (1 + exp(-(a*s + b))) with (a, b) fitted on validation."""
    params = (bundle or {}).get("calibration", {}).get(model)
    if not params:
        return [max(0.0, min(1.0, s)) for s in scores]
    a, b = params
    return [1.0 / (1.0 + math.exp(-(a * s + b))) for s in scores]


def thresholds_for(model: str, bundle: Optional[Dict[str, Any]]) -> Dict[str, float]:
    return (bundle or {}).get("thresholds", {}).get(model, FALLBACK_THRESHOLDS)


def decide(ranked: List[Candidate], th: Dict[str, float]) -> None:
    """ACCEPT only the top candidate, only above the precision threshold and clear of the rest."""
    for i, c in enumerate(ranked):
        second = ranked[1].probability if len(ranked) > 1 else 0.0
        if i == 0 and c.probability >= th["accept"] and c.probability - second >= ACCEPT_MARGIN:
            c.decision = "ACCEPT"
            c.reasons.append(f"p={c.probability:.3f} >= accept threshold {th['accept']:.3f}")
        elif c.probability >= th["reject"]:
            c.decision = "REVIEW"
            c.reasons.append(f"p={c.probability:.3f} between thresholds; needs an operator")
        else:
            c.decision = "REJECT"
            c.reasons.append(f"p={c.probability:.3f} < reject threshold {th['reject']:.3f}")


# ------------------------------------------------------------------ entry points

def _rule_match(read: str, reg: Dict[str, Dict[str, Any]], observed_make: Optional[str],
                observed_colour: Optional[str]) -> List[Candidate]:
    def lookup(p: str) -> Dict[str, Any]:
        row = reg.get(p)
        return {"vehicle_make": row.get("make"), "vehicle_colour": row.get("colour"),
                "registration_status": "ACTIVE"} if row is not None else {}
    best, ranked, outcome = plate_resolve.resolve(read, observed_make, observed_colour, lookup)
    decision = {"RESOLVED": "ACCEPT", "AMBIGUOUS": "REVIEW"}.get(outcome, "REJECT")
    out = []
    for i, c in enumerate(ranked):
        out.append(Candidate(c.plate, {"rule": c.score}, {}, c.score,
                             decision if i == 0 else "REVIEW", list(c.reasons)))
    return out


def match(read: str, *, confidence: Optional[float] = None,
          observed_make: Optional[str] = None, observed_colour: Optional[str] = None,
          registry: Registry = None, strategy: str = "position", model: str = "logreg",
          top_k: int = 10, observed_model: Optional[str] = None,
          max_edits: Optional[int] = None) -> Tuple[List[Candidate], BlockingStats]:
    """Rank the registered plates a noisy read could be, with a calibrated decision each."""
    read = norm_plate(read) or ""
    reg = _as_mapping(registry)
    plates = sorted(reg)
    bundle, err = load_bundle()
    notes: List[str] = []
    if model in ("logreg", "gbt") and bundle is None:
        notes.append(f"{model} unavailable: {err}; fell back to weighted")
        model = "weighted"
    me = max_edits if max_edits is not None else (bundle or {}).get("max_edits", DEFAULT_MAX_EDITS)
    cands, stats = candidate_plates(read, plates, strategy=strategy, max_edits=me)
    if model == "rule":
        return _rule_match(read, reg, observed_make, observed_colour)[:top_k], stats
    if len(cands) > SHORTLIST:
        cands = sorted(cands, key=lambda p: -ocr_edit_similarity(read, p))[:SHORTLIST]
        notes.append(f"shortlisted {SHORTLIST} of {stats.candidates_after} by OCR edit similarity")
    vectors = [features(read, p, confidence=confidence, observed_make=observed_make,
                        observed_colour=observed_colour, registry_row=reg.get(p), corpus=plates,
                        observed_model=observed_model) for p in cands]
    probs = calibrate(model, raw_scores(model, vectors, bundle), bundle)
    ranked = [Candidate(p, {m: v[m] for m in list(ALL_MEASURES) + ["tfidf",
                        "learned_ocr_similarity"]}, v, pr, "REJECT", list(notes))
              for p, v, pr in zip(cands, vectors, probs)]
    ranked.sort(key=lambda c: (-c.probability, c.plate))
    decide(ranked, thresholds_for(model, bundle))
    return ranked[:top_k], stats


def reverse_match(plate: str, captures: Sequence[Union[str, Mapping[str, Any]]], *,
                  registry: Registry = None, strategy: str = "position",
                  model: str = "logreg") -> List[Candidate]:
    """Vice versa: the camera reads that could be `plate`, scored by the same forward matcher.

    A capture belongs to `plate` only if a forward match of that capture ranks `plate`; this
    stops a read that is really a *different* registered vehicle from being claimed.
    """
    target = norm_plate(plate) or ""
    reg = _as_mapping(registry)
    info = {}
    for c in captures:
        d = {"read": c} if isinstance(c, str) else dict(c)
        info.setdefault(norm_plate(d.get("read") or d.get("plate_id")), d)
    reads, _ = candidate_plates(target, list(info), strategy=strategy, max_edits=DEFAULT_MAX_EDITS)
    out: List[Candidate] = []
    for r in reads:
        d = info[r]
        ranked, _ = match(r, confidence=d.get("ocr_confidence"),
                          observed_make=d.get("observed_make"),
                          observed_colour=d.get("observed_colour"), registry=reg,
                          strategy=strategy, model=model)
        hit = next((c for c in ranked if c.plate == target), None)
        if hit is not None:
            out.append(Candidate(r, hit.scores, hit.features, hit.probability, hit.decision,
                                 hit.reasons + [f"forward match of {r} ranks {target}"]))
    out.sort(key=lambda c: (-c.probability, c.plate))
    return out
