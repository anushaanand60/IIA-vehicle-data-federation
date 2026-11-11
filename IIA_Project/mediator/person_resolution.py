"""CONTEXT-ADDED (R2): resolve people across agencies (REG owner, INS policy holder, THEFT person).

Fellegi-Sunter record linkage with m/u probabilities learned by EM, no labels (Winkler's EM for FS):
  blocking   -> candidate pairs sharing a mobile, a pincode, or a 3-letter name-token prefix (q-gram prefix)
  comparison -> gamma vector: name (Jaro-Winkler band 0/1/2), mobile (0/1), address (pincode + token overlap 0/1/2);
                a value missing on either side is "unobserved" and simply drops out of the likelihood
  EM         -> latent match class M; m_f[l] = P(gamma_f = l | M), u_f[l] = P(gamma_f = l | U), prior p = P(M)
  decision   -> posterior P(M | gamma) via the log-likelihood ratio; link when >= threshold
  clusters   -> union-find over links; golden record = most frequent (then longest) name, mobile, address

Records: {"source_id","table","pk","name","mobile","address","pincode"} (any field may be None).
"""
from __future__ import annotations

import hashlib
import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

import numpy as np

from mediator.context_types import PersonCluster
from mediator.similarity import jaro_winkler

FIELDS = ("name", "mobile", "address")
LEVELS = {"name": 3, "mobile": 2, "address": 3}
MIN_PAIRS_FOR_EM = 20     # below this EM over-fits; fall back to the priors below
THRESHOLD = 0.5           # Bayes decision with equal costs; raise it to favour precision
# Priors used when there are too few pairs to learn from (also EM's starting point).
DEFAULT_PARAMS: Dict[str, Any] = {
    "p": 0.1,
    "m": {"name": [0.05, 0.15, 0.80], "mobile": [0.02, 0.98], "address": [0.15, 0.25, 0.60]},
    "u": {"name": [0.90, 0.08, 0.02], "mobile": [0.995, 0.005], "address": [0.80, 0.15, 0.05]},
}
_EPS = 1e-4


def _norm_name(s: Any) -> str:
    return re.sub(r"[^a-z ]", "", str(s or "").lower()).strip()


def _digits(s: Any) -> str:
    d = re.sub(r"\D", "", str(s or ""))
    return d[-10:] if d else ""


def _tokens(s: Any) -> set:
    return set(re.findall(r"[a-z0-9]+", str(s or "").lower()))  # house numbers are the most telling tokens


def name_prefixes(name: Any) -> List[str]:
    return sorted({t[:3] for t in _norm_name(name).split() if len(t) >= 3})


def compare(a: Dict[str, Any], b: Dict[str, Any]) -> List[int]:
    """gamma vector, -1 = unobserved."""
    na, nb = _norm_name(a.get("name")), _norm_name(b.get("name"))
    name = -1 if not (na and nb) else (2 if (jw := jaro_winkler(na, nb)) >= 0.92 else 1 if jw >= 0.80 else 0)
    ma, mb = _digits(a.get("mobile")), _digits(b.get("mobile"))
    mobile = -1 if not (ma and mb) else int(ma == mb)
    pa, pb = _digits(a.get("pincode")), _digits(b.get("pincode"))
    ta, tb = _tokens(a.get("address")), _tokens(b.get("address"))
    jac = len(ta & tb) / len(ta | tb) if ta and tb else None
    same_pin = (pa == pb) if pa and pb else None
    if same_pin is None and jac is None:
        address = -1
    elif (same_pin and (jac is None or jac >= 0.5)) or (jac is not None and jac >= 0.75):
        address = 2
    elif same_pin or (jac is not None and jac >= 0.4):
        address = 1
    else:
        address = 0
    return [name, mobile, address]


def candidate_pairs(records: Sequence[Dict[str, Any]]) -> List[Tuple[int, int]]:
    """Blocking: only records sharing a mobile, a pincode or a name-token prefix are compared."""
    blocks: Dict[str, List[int]] = {}
    for i, r in enumerate(records):
        keys = [f"m:{_digits(r.get('mobile'))}" if _digits(r.get("mobile")) else None,
                f"p:{_digits(r.get('pincode'))}" if _digits(r.get("pincode")) else None]
        keys += [f"n:{p}" for p in name_prefixes(r.get("name"))]
        for k in filter(None, keys):
            blocks.setdefault(k, []).append(i)
    pairs = {(i, j) for members in blocks.values() for x, i in enumerate(members) for j in members[x + 1:] if i != j}
    return sorted(pairs)


def _loglik_terms(G: np.ndarray, params: Dict[str, Any]) -> Tuple[np.ndarray, np.ndarray]:
    lm, lu = np.zeros(len(G)), np.zeros(len(G))
    for f_i, f in enumerate(FIELDS):
        col = G[:, f_i]
        seen = col >= 0
        m, u = np.asarray(params["m"][f]), np.asarray(params["u"][f])
        lm[seen] += np.log(m[col[seen]])
        lu[seen] += np.log(u[col[seen]])
    return lm, lu


def posterior(vectors: Sequence[Sequence[int]], params: Dict[str, Any]) -> np.ndarray:
    """P(match | gamma) = 1 / (1 + exp(-(log-likelihood ratio + prior log-odds)))."""
    G = np.asarray(vectors, dtype=int).reshape(-1, len(FIELDS))
    lm, lu = _loglik_terms(G, params)
    logit = lm - lu + math.log(params["p"] / (1 - params["p"]))
    return 1.0 / (1.0 + np.exp(-np.clip(logit, -50, 50)))


def fit_em(vectors: Sequence[Sequence[int]], max_iter: int = 200, tol: float = 1e-7,
           init: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Learn p, m, u by EM on unlabeled comparison vectors. Returns DEFAULT_PARAMS when too few pairs."""
    params = {k: (dict(v) if isinstance(v, dict) else v) for k, v in (init or DEFAULT_PARAMS).items()}
    if len(vectors) < MIN_PAIRS_FOR_EM:
        return {**params, "iterations": 0, "learned": False}
    G = np.asarray(vectors, dtype=int).reshape(-1, len(FIELDS))
    prev, it = -np.inf, 0
    for it in range(1, max_iter + 1):
        lm, lu = _loglik_terms(G, params)
        a, b = np.log(params["p"]) + lm, np.log(1 - params["p"]) + lu
        top = np.maximum(a, b)
        ll = float(np.sum(top + np.log(np.exp(a - top) + np.exp(b - top))))
        g = 1.0 / (1.0 + np.exp(np.clip(b - a, -50, 50)))           # E-step: P(M | gamma_i)
        params["p"] = float(np.clip(g.mean(), _EPS, 1 - _EPS))       # M-step
        for f_i, f in enumerate(FIELDS):
            col, seen = G[:, f_i], G[:, f_i] >= 0
            for cls, w in (("m", g), ("u", 1 - g)):
                counts = np.array([w[seen & (col == lv)].sum() for lv in range(LEVELS[f])]) + _EPS
                params[cls][f] = list(np.clip(counts / counts.sum(), _EPS, 1.0))
        if abs(ll - prev) < tol:
            break
        prev = ll
    if params["m"]["name"][-1] < params["u"]["name"][-1]:  # label switching: M must be the name-agreeing class
        params["m"], params["u"], params["p"] = params["u"], params["m"], 1 - params["p"]
    # Identifiability guard: in the match class every field must agree more often than among non-matches;
    # if EM converged to some other split (e.g. "same pincode" vs "different"), its m/u cannot be trusted.
    if any(params["m"][f][-1] <= params["u"][f][-1] for f in FIELDS):
        return {**DEFAULT_PARAMS, "iterations": it, "learned": False, "rejected": "EM split not identifiable"}
    return {**params, "iterations": it, "learned": True, "loglik": prev}


class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, i: int) -> int:
        while self.parent[i] != i:
            self.parent[i] = self.parent[self.parent[i]]
            i = self.parent[i]
        return i

    def union(self, i: int, j: int) -> None:
        self.parent[self.find(i)] = self.find(j)


def _golden(members: List[Dict[str, Any]]) -> Dict[str, Any]:
    def pick(field: str) -> Any:
        vals = [str(m[field]).strip() for m in members if m.get(field)]
        if not vals:
            return None
        counts = Counter(vals)
        return max(vals, key=lambda v: (counts[v], len(v)))
    return {"name": pick("name"), "mobile": pick("mobile"), "address": pick("address"), "pincode": pick("pincode")}


def pair_posteriors(records: Sequence[Dict[str, Any]], params: Optional[Dict[str, Any]] = None
                    ) -> Tuple[Dict[Tuple[int, int], float], Dict[str, Any]]:
    pairs = candidate_pairs(records)
    vectors = [compare(records[i], records[j]) for i, j in pairs]
    params = params or fit_em(vectors)
    probs = posterior(vectors, params) if vectors else np.array([])
    return {pair: float(p) for pair, p in zip(pairs, probs)}, params


def resolve_persons(records: List[Dict[str, Any]], threshold: float = THRESHOLD,
                    params: Optional[Dict[str, Any]] = None) -> List[PersonCluster]:
    """Cluster records into people. Singletons are returned too (probability 1.0)."""
    probs, _ = pair_posteriors(records, params)
    uf, kept = _UnionFind(len(records)), {}
    for (i, j), p in sorted(probs.items(), key=lambda kv: -kv[1]):
        if p >= threshold and uf.find(i) != uf.find(j):
            uf.union(i, j)
            kept[(i, j)] = p
    groups: Dict[int, List[int]] = {}
    for i in range(len(records)):
        groups.setdefault(uf.find(i), []).append(i)
    clusters = []
    for idx in groups.values():
        members = [(records[i]["source_id"], records[i]["table"], records[i]["pk"]) for i in idx]
        links = [p for (i, j), p in kept.items() if i in idx]
        cid = hashlib.sha1(repr(sorted(map(str, members))).encode()).hexdigest()[:10]
        clusters.append(PersonCluster(cluster_id=f"P{cid}", members=members,
                                      canonical=_golden([records[i] for i in idx]),
                                      probability=min(links) if links else 1.0))
    return sorted(clusters, key=lambda c: (-len(c.members), c.cluster_id))


def records_from_rows(rows: Iterable[Dict[str, Any]], mapping: Dict[str, str], source_id: str, table: str,
                      pk_col: str) -> List[Dict[str, Any]]:
    """Turn raw source rows into resolution records using the graph's `person` column mapping."""
    out = []
    for r in rows:
        low = {k.lower(): v for k, v in r.items()}
        get = lambda f: low.get(mapping[f].lower()) if f in mapping else None  # noqa: E731
        addr = " ".join(str(x) for x in (get("address"), get("city")) if x)
        out.append({"source_id": source_id, "table": table, "pk": low.get(pk_col.lower()),
                    "name": get("name"), "mobile": get("mobile"), "address": addr or None, "pincode": get("pincode")})
    return out
