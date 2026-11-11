"""CONTEXT-ADDED (R2): Fellegi-Sunter person resolution with EM-learned m/u (no labels)."""
from __future__ import annotations

import random

import numpy as np
import pytest

from mediator.person_resolution import (DEFAULT_PARAMS, candidate_pairs, compare, fit_em, pair_posteriors,
                                        posterior, resolve_persons)

FIRST = ["Ravi", "Sunita", "Amit", "Neha", "Arjun", "Pooja", "Vikram", "Kavya", "Rohan", "Isha", "Manoj", "Divya"]
LAST = ["Kumar", "Sharma", "Verma", "Gupta", "Singh", "Rao", "Das", "Iyer", "Nair", "Joshi"]
SOURCES = [("REG", "OWNERS"), ("INS", "POLICY_HOLDERS"), ("THEFT", "PERSONS")]


def _typo(name: str, rng: random.Random) -> str:
    i = rng.randrange(1, len(name) - 1)
    return rng.choice([name[:i] + name[i] + name[i:], name[:i] + name[i + 1:], name.replace("a", "aa", 1)])


def synthetic(seed: int = 3, people: int = 40, twins: int = 6):
    """Records + the true person id of each. Twins share a full name but nothing else (hard negatives)."""
    rng = random.Random(seed)
    persons = []
    for p in range(people):
        persons.append({"name": f"{rng.choice(FIRST)} {rng.choice(LAST)}", "mobile": f"9{p:09d}",
                        "address": f"{rng.randint(1, 400)} {rng.choice(['MG Road', 'Park Street', 'Lake View'])} "
                                   f"{rng.choice(['Delhi', 'Pune', 'Noida'])}", "pincode": f"{110000 + p * 7}"})
    for t in range(twins):
        twin = dict(persons[t], mobile=f"8{t:09d}", address=f"{900 + t} Hill Road Agra", pincode=f"{282000 + t}")
        persons.append(twin)
    records, truth = [], []
    for pid, person in enumerate(persons):
        for sid, table in rng.sample(SOURCES, rng.randint(1, 3)):
            rec = {"source_id": sid, "table": table, "pk": len(records) + 1, **person}
            if rng.random() < 0.5:
                rec["name"] = _typo(person["name"], rng)
            if sid == "THEFT":
                rec["pincode"] = None                         # THEFT keeps no pincode
            if rng.random() < 0.1:
                rec["mobile"] = None
            records.append(rec)
            truth.append(pid)
    return records, truth


def test_comparison_vector_levels_and_missing_values():
    a = {"name": "Ravi Kumar", "mobile": "9811111111", "address": "1 Ring Road Delhi", "pincode": "110001"}
    assert compare(a, dict(a, name="Ravi Kumaar")) == [2, 1, 2]
    assert compare(a, {"name": "Neha Gupta", "mobile": "9700000000", "address": "9 Lake View Pune", "pincode": "411001"}) == [0, 0, 0]
    assert compare(a, {"name": "Ravi Kumar", "mobile": None, "address": None, "pincode": None}) == [2, -1, -1]


def test_blocking_compares_far_fewer_pairs_than_all_pairs():
    records, truth = synthetic()
    pairs = candidate_pairs(records)
    n = len(records)
    assert len(pairs) < n * (n - 1) / 2 / 3
    true_pairs = {(i, j) for i in range(n) for j in range(i + 1, n) if truth[i] == truth[j]}
    assert len(true_pairs & set(pairs)) / len(true_pairs) > 0.95        # blocking keeps the real matches


def test_em_learns_sensible_m_and_u_without_labels():
    records, _ = synthetic()
    vectors = [compare(records[i], records[j]) for i, j in candidate_pairs(records)]
    params = fit_em(vectors)
    assert params["learned"] and params["iterations"] > 1
    m, u = params["m"], params["u"]
    assert m["mobile"][1] > 0.8 and u["mobile"][1] < 0.1                  # matches share a mobile, non-matches don't
    assert m["name"][2] > 0.5 > u["name"][2]
    assert m["address"][2] > u["address"][2]
    assert 0.0 < params["p"] < 0.6


def test_posterior_orders_agreement_patterns():
    p = posterior([[2, 1, 2], [2, 0, 0], [0, 1, 0], [0, 0, 0], [2, -1, -1]], DEFAULT_PARAMS)
    assert p[0] > 0.99 and p[1] < 0.5 and p[3] < 0.01
    assert p[0] > p[4] > p[3]


def test_clusters_join_duplicates_and_keep_same_name_twins_apart():
    records, truth = synthetic()
    clusters = resolve_persons(records)
    found = {}
    for c in clusters:
        for sid, table, pk in c.members:
            found[pk] = c.cluster_id
    n = len(records)
    same_true = [(i, j) for i in range(n) for j in range(i + 1, n) if truth[i] == truth[j]]
    recall = sum(found[records[i]["pk"]] == found[records[j]["pk"]] for i, j in same_true) / len(same_true)
    assert recall > 0.9
    twins = [(i, j) for i in range(n) for j in range(i + 1, n)
             if truth[i] != truth[j] and records[i]["name"] == records[j]["name"]]
    assert twins and all(found[records[i]["pk"]] != found[records[j]["pk"]] for i, j in twins)


def test_golden_record_prefers_the_most_frequent_then_longest_value():
    recs = [{"source_id": s, "table": t, "pk": k, "name": n, "mobile": "9811111111", "address": a, "pincode": "110001"}
            for k, (s, t, n, a) in enumerate([("REG", "OWNERS", "Ravi Kumar", "1 Ring Road"),
                                               ("INS", "POLICY_HOLDERS", "Ravi Kumaar", "1 Ring Road Delhi"),
                                               ("THEFT", "PERSONS", "Ravi Kumar", "1 Ring Road Delhi")])]
    (cluster,) = [c for c in resolve_persons(recs) if len(c.members) == 3]
    assert cluster.canonical["name"] == "Ravi Kumar" and cluster.canonical["address"] == "1 Ring Road Delhi"
    assert 0.5 <= cluster.probability <= 1.0 and cluster.cluster_id.startswith("P")


def test_too_few_pairs_fall_back_to_priors():
    params = fit_em([[2, 1, 2]] * 3)
    assert not params["learned"] and params["m"] == DEFAULT_PARAMS["m"]


def test_unidentifiable_em_split_is_rejected():
    rng = np.random.default_rng(0)  # names never agree more among "matches": nothing to learn
    vectors = [[0, int(rng.random() < 0.5), int(rng.integers(0, 3))] for _ in range(200)]
    assert fit_em(vectors)["learned"] is False


def test_pair_posteriors_reuse_given_params():
    records, _ = synthetic(people=10, twins=1)
    probs, params = pair_posteriors(records, DEFAULT_PARAMS)
    assert params is DEFAULT_PARAMS and all(0.0 <= p <= 1.0 for p in probs.values())


@pytest.mark.parametrize("seed", [1, 2, 5])
def test_resolution_is_stable_across_synthetic_worlds(seed):
    records, truth = synthetic(seed=seed)
    clusters = resolve_persons(records)
    pure = sum(len({truth[next(i for i, r in enumerate(records) if r["pk"] == m[2])] for m in c.members}) == 1
               for c in clusters)
    assert pure / len(clusters) > 0.95
