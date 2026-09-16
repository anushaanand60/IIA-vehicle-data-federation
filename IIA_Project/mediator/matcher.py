"""
Hybrid Schema Matching Algorithm (lexical + type/constraint + instance signals).
Score = 0.40 * N + 0.20 * C + 0.40 * I
Produces candidate correspondences and full similarity matrix for heatmap visualization.
"""

import re
from datetime import datetime
from typing import Dict, Any, List, Tuple, Optional
from mediator.schema import GLOBAL_SCHEMA_ATTRIBUTES
from mediator.transforms import norm_plate

THESAURUS = {
    "reg": "registration",
    "registration": "registration",
    "rego": "registration",
    "no": "id",
    "num": "id",
    "number": "id",
    "id": "id",
    "plate": "plate",
    "vehicle_number": "plate",
    "vehicle_reg": "plate",
    "registration_no": "plate",
    "plate_id": "plate",
    "regn_number": "plate",
    "until": "expiry",
    "end": "expiry",
    "expiry": "expiry",
    "valid_till": "expiry",
    "valid_upto": "expiry",
    "start": "start",
    "on": "date",
    "date": "date",
    "dt": "date",
    "make": "make",
    "brand": "make",
    "model": "model",
    "colour": "colour",
    "color": "colour",
    "status": "status",
    "flag": "status",
    "owner": "owner",
    "owners": "owner",
    "name": "name",
    "full_name": "owner_name",
    "full": "owner",
    "insurer": "insurer",
    "insurers": "insurer",
    "cam": "camera",
    "camera": "camera",
    "cameras": "camera",
    "loc": "location",
    "location": "location",
    "location_name": "location",
    "seen": "seen",
    "captured": "seen",
    "reported": "incident",
    "incident": "incident",
    "crime": "theft",
    "stolen": "stolen",
    "recovered": "recovered"
}

VOCABULARIES = {
    "vehicle_make": {"maruti", "maruti suzuki", "hyundai", "hyundia", "tata", "toyota", "mahindra", "kia", "honda"},
    "vehicle_model": {"swift", "baleno", "creta", "i20", "nexon", "punch", "city", "innova", "xuv700", "seltos", "venue"},
    "vehicle_colour": {"white", "black", "silver", "red", "blue", "grey", "gray"},
    "observed_make": {"maruti", "maruti suzuki", "hyundai", "hyundia", "tata", "toyota", "mahindra", "kia", "honda"},
    "observed_model": {"swift", "baleno", "creta", "i20", "nexon", "punch", "city", "innova", "xuv700", "seltos", "venue"},
    "observed_colour": {"white", "black", "silver", "red", "blue", "grey", "gray"},
    "registration_status": {"active", "suspended", "cancelled", "act", "susp", "canc"},
    "case_status": {"open", "closed"},
    "stolen_status": {"stolen", "recovered", "not_reported"},
    "policy_type": {"third_party", "comprehensive"}
}

def tokenize(name: str) -> List[str]:
    """Tokenize on underscores, camelCase transitions, and digits."""
    s = re.sub(r"([a-z])([A-Z])", r"\1_\2", name)
    s = re.sub(r"([A-Z]+)([A-Z][a-z])", r"\1_\2", s)
    tokens = re.split(r"[^A-Za-z0-9]+", s.lower())
    res = []
    for t in tokens:
        if t:
            res.append(t)
    return res

def jaro_winkler(s1: str, s2: str) -> float:
    """Standard Jaro-Winkler string similarity (0.0 to 1.0)."""
    if s1 == s2:
        return 1.0
    len1, len2 = len(s1), len(s2)
    if len1 == 0 or len2 == 0:
        return 0.0

    match_distance = max(len1, len2) // 2 - 1
    s1_matches = [False] * len1
    s2_matches = [False] * len2
    matches = 0

    for i in range(len1):
        start = max(0, i - match_distance)
        end = min(i + match_distance + 1, len2)
        for j in range(start, end):
            if s2_matches[j]:
                continue
            if s1[i] == s2[j]:
                s1_matches[i] = True
                s2_matches[j] = True
                matches += 1
                break

    if matches == 0:
        return 0.0

    t = 0
    k = 0
    for i in range(len1):
        if not s1_matches[i]:
            continue
        while not s2_matches[k]:
            k += 1
        if s1[i] != s2[k]:
            t += 1
        k += 1
    transpositions = t // 2

    jaro = (matches / len1 + matches / len2 + (matches - transpositions) / matches) / 3.0

    # Prefix scale (up to 4 chars)
    prefix = 0
    for i in range(min(4, len1, len2)):
        if s1[i] == s2[i]:
            prefix += 1
        else:
            break
    return jaro + prefix * 0.1 * (1.0 - jaro)

def compute_name_similarity(source_col: str, global_attr: str, table_name: str = "") -> float:
    s_tokens = tokenize(source_col)
    g_tokens = tokenize(global_attr)
    if not s_tokens or not g_tokens:
        return 0.0

    # Include table tokens if column itself has a name/id component
    all_source_tokens = list(s_tokens)
    if any(k in s_tokens for k in ("name", "id", "no", "num", "code")):
        t_tokens = tokenize(table_name)
        all_source_tokens += [t for t in t_tokens if t in ("owners", "owner", "insurers", "insurer", "camera", "cameras", "crime", "theft")]

    s_expanded = [THESAURUS.get(t, t) for t in all_source_tokens]
    g_expanded = [THESAURUS.get(t, t) for t in g_tokens]

    scores = []
    for s_t in s_expanded:
        best_sim = 0.0
        for g_t in g_expanded:
            if s_t == g_t:
                sim = 1.0
            else:
                sim = jaro_winkler(s_t, g_t)
            if sim > best_sim:
                best_sim = sim
        scores.append(best_sim)

    # Global token coverage
    g_covered = []
    for g_t in g_expanded:
        best_sim = 0.0
        for s_t in s_expanded:
            sim = 1.0 if s_t == g_t else jaro_winkler(s_t, g_t)
            if sim > best_sim:
                best_sim = sim
        g_covered.append(best_sim)

    col_sim = sum(scores) / len(scores) if scores else 0.0
    global_cov = sum(g_covered) / len(g_covered) if g_covered else 0.0
    return 0.5 * col_sim + 0.5 * global_cov

def compute_type_compatibility(source_type: str, global_info: Dict[str, Any], is_pk: bool, is_fk: bool) -> float:
    s_type = (source_type or "").upper()
    g_type = global_info.get("type", "string")

    score = 0.0
    if g_type == "string":
        if any(t in s_type for t in ["VARCHAR", "TEXT", "CHAR", "STRING"]):
            score = 1.0
        else:
            score = 0.3
    elif g_type in ("date", "datetime"):
        if any(t in s_type for t in ["DATE", "TIME", "TIMESTAMP"]):
            score = 1.0
        elif any(t in s_type for t in ["INT", "INTEGER", "TEXT", "VARCHAR"]):
            score = 0.8
        else:
            score = 0.1
    elif g_type == "boolean":
        if any(t in s_type for t in ["BOOL", "TINYINT", "INT"]):
            score = 1.0
        elif "CHAR" in s_type or "TEXT" in s_type:
            score = 0.8
        else:
            score = 0.1
    else:
        score = 0.4

    if global_info.get("is_key", False) and (is_pk or "plate" in s_type.lower() or "reg" in s_type.lower()):
        score = min(1.0, score + 0.3)

    return score

def compute_instance_similarity(samples: List[Any], global_attr: str, global_info: Dict[str, Any], N: float) -> float:
    if not samples:
        return 0.1

    total = len(samples)
    matches = 0
    plate_regex = re.compile(r"^[A-Z]{2}\d{2}[A-Z]{1,2}\d{4}$")

    # 1. Plate regex check
    if global_attr == "plate_number":
        for s in samples:
            norm = norm_plate(s)
            if norm and plate_regex.match(norm):
                matches += 1
        return matches / total

    # 2. Vocabulary checks
    if global_attr in VOCABULARIES:
        vocab = VOCABULARIES[global_attr]
        for s in samples:
            if s is not None and str(s).strip().lower() in vocab:
                matches += 1
        return matches / total

    # 3. Date / datetime checks
    if global_info.get("type") in ("date", "datetime"):
        for s in samples:
            if s is None:
                continue
            str_s = str(s).strip()
            # Try ISO
            try:
                datetime.fromisoformat(str_s.replace("Z", "+00:00"))
                matches += 1
                continue
            except Exception:
                pass
            # Try DD/MM/YYYY
            try:
                datetime.strptime(str_s, "%d/%m/%Y")
                matches += 1
                continue
            except Exception:
                pass
            # Try epoch int
            try:
                val = int(str_s)
                if 1000000000 < val < 2000000000:
                    matches += 1
                    continue
            except Exception:
                pass
        return matches / total

    # 4. If no specific value profile (regex, vocabulary, date parser) matches, instance similarity is 0.0
    return 0.0

def match_source_schema(source_schema: Dict[str, Any], theta: float = 0.55) -> Dict[str, Any]:
    """
    Runs the hybrid schema matching algorithm.
    Score = 0.40 * N + 0.20 * C + 0.40 * I
    Returns:
      - correspondences: List of top-1 matches with score >= theta
      - unmapped: List of source attributes without valid correspondence
      - similarity_matrix: Full NxM matrix of scores for UI heatmap
    """
    tables = source_schema.get("tables", {})
    correspondences = []
    unmapped = []
    matrix = {}

    all_source_cols = []
    for t_name, t_data in tables.items():
        for col in t_data.get("columns", []):
            all_source_cols.append((t_name, col))

    for t_name, col in all_source_cols:
        col_name = col["name"]
        full_col_name = f"{t_name}.{col_name}"
        samples = col.get("sample_values", [])
        is_pk = col.get("is_pk", False)
        is_fk = col.get("is_fk", False)
        c_type = col.get("type", "TEXT")

        matrix[full_col_name] = {}
        best_attr = None
        best_score = -1.0
        details = {}

        for g_attr, g_info in GLOBAL_SCHEMA_ATTRIBUTES.items():
            if g_info.get("is_derived", False):
                continue

            N = compute_name_similarity(col_name, g_attr, t_name)
            C = compute_type_compatibility(c_type, g_info, is_pk, is_fk)
            I = compute_instance_similarity(samples, g_attr, g_info, N)

            # Score = 0.40 * N + 0.20 * C + 0.40 * I
            score = round(0.40 * N + 0.20 * C + 0.40 * I, 3)

            # Domain contextual adjustments & tie-breaking:
            if g_attr == "insurance_expiry" and any(w in col_name.lower() for w in ["until", "expiry", "end"]):
                score = min(1.0, score + 0.15)
            elif g_attr == "insurance_start" and "start" in col_name.lower():
                score = min(1.0, score + 0.15)
            elif g_attr == "puc_expiry" and any(w in col_name.lower() for w in ["valid", "upto"]):
                score = min(1.0, score + 0.20)  # PUC certificates say "valid_upto", never "expiry"
            elif g_attr == "owner_name" and "name" in col_name.lower() and ("owner" in t_name.lower() or "owner" in col_name.lower()):
                score = min(1.0, score + 0.20)
            elif g_attr == "last_seen_location" and "location" in col_name.lower():
                score = min(1.0, score + 0.20)
            elif g_attr == "vehicle_colour" and "colour" in col_name.lower() and "observed" not in col_name.lower():
                score = min(1.0, score + 0.10)
            elif g_attr == "observed_colour" and "observed" in col_name.lower() and "colour" in col_name.lower():
                score = min(1.0, score + 0.15)
            elif g_attr == "vehicle_make" and "make" in col_name.lower() and "observed" not in col_name.lower():
                score = min(1.0, score + 0.10)
            elif g_attr == "observed_make" and "observed" in col_name.lower() and "make" in col_name.lower():
                score = min(1.0, score + 0.10)
            elif g_attr == "vehicle_model" and "model" in col_name.lower() and "observed" not in col_name.lower():
                score = min(1.0, score + 0.10)
            elif g_attr == "observed_model" and "observed" in col_name.lower() and "model" in col_name.lower():
                score = min(1.0, score + 0.10)

            score = min(1.0, score)
            matrix[full_col_name][g_attr] = score

            if score > best_score:
                best_score = score
                best_attr = g_attr
                details = {"N": round(N, 3), "C": round(C, 3), "I": round(I, 3), "score": score}

        if best_score >= theta and best_attr:
            correspondences.append({
                "source_table": t_name,
                "source_attr": col_name,
                "global_attr": best_attr,
                "score": best_score,
                "components": details
            })
        else:
            unmapped.append({
                "source_table": t_name,
                "source_attr": col_name,
                "best_candidate": best_attr,
                "best_score": best_score
            })

    return {
        "source_id": source_schema.get("source_id"),
        "correspondences": correspondences,
        "unmapped": unmapped,
        "similarity_matrix": matrix
    }
