"""CONTEXT-ADDED (R2): entity top-k (TA and TPUT) over fake SQLite sources. No ports, no LLM."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any, Dict, List

import pytest

from mediator import entity_topk as E
from mediator import topk_sql as S
from mediator.context_types import ParsedQuery, Predicate
from tests.phase3_fakes import FakeClient, use_frozen_registry

GURU = ("gurugram", "gurgaon", "ggn", "cyber city", "mg road", "sector 14", "sector 29", "nh8")
STATIONS = [{"station_name": "Sector 14 PS", "district": "Gurugram", "zone": "South"},
            {"station_name": "Karol Bagh PS", "district": "Central Delhi", "zone": "Central"},
            {"station_name": "Noida Sec 20 PS", "district": "Noida", "zone": "East"}]
CAMERAS = [{"camera_id": "C1", "location_name": "NH8 Toll Plaza"}, {"camera_id": "C2", "location_name": "Ring Road Junction"},
           {"camera_id": "C3", "location_name": "Noida Expressway"}]
ZONES = [{"zone_id": 1, "zone_name": "Gurugram South", "city": "Gurugram"}, {"zone_id": 2, "zone_name": "Delhi Central", "city": "Delhi"},
         {"zone_id": 3, "zone_name": "Noida East", "city": "Noida"}]
CAM_ZONES = [{"camera_id": "C1", "zone_id": 1}, {"camera_id": "C2", "zone_id": 2}, {"camera_id": "C3", "zone_id": 3}]


@pytest.fixture(autouse=True)
def _registry(monkeypatch: pytest.MonkeyPatch) -> None:
    use_frozen_registry(monkeypatch)


def _fmt_ins(p: str) -> str:
    return f"{p[:2]}-{p[2:4]}-{p[4:6]}-{p[6:]}"


def _fmt_theft(p: str) -> str:
    return f"{p[:2]} {p[2:4]} {p[4:6]} {p[6:]}".lower()


def world(seed: int, n: int = 300, skew: bool = False) -> Dict[str, Any]:
    rng = random.Random(seed)
    plates = sorted({f"DL{rng.randint(1, 12):02d}{rng.choice('ABCDEFHJK')}{rng.choice('ACDEFHJK')}{rng.randint(1000, 9999)}"
                     for _ in range(n)})
    reg, owners, crimes, pols, caps = [], [], [], [], []
    for i, p in enumerate(plates, start=1):
        star = skew and i % 50 == 0          # a few vehicles that match everything, the case TA is built for
        make = "Hyundai" if star or rng.random() < 0.2 else rng.choice(["Tata", "Kia", "Honda"])
        colour = "White" if star or rng.random() < 0.2 else rng.choice(["Red", "Blue", "Black"])
        city = "Gurugram" if star or rng.random() < 0.15 else rng.choice(["Delhi", "Noida", "Pune"])
        owners.append({"owner_id": i, "full_name": f"Owner {i}", "city": city})
        reg.append({"registration_id": i, "registration_no": p, "owner_id": i, "make": make, "model": "X", "colour": colour,
                    "reg_status": "ACTIVE"})
        if star or rng.random() < 0.3:
            stolen = star or rng.random() < 0.5
            crimes.append({"incident_id": len(crimes) + 1, "vehicle_number": _fmt_theft(p), "fir_no": f"F{i}",
                           "reported_date": 1780000000 + i, "incident_type": "THEFT", "stolen_flag": "Y" if stolen else "N",
                           "recovered_flag": "N" if star or rng.random() < 0.6 else "Y", "case_status": "OPEN",
                           "police_station": STATIONS[0 if star else rng.randrange(3)]["station_name"]})
        for _ in range(0 if not (star or rng.random() < 0.7) else rng.randint(1, 2)):
            pols.append({"policy_id": len(pols) + 1, "vehicle_reg": _fmt_ins(p), "policy_type": "COMPREHENSIVE",
                         "policy_start": "01/01/2025", "policy_until": "01/01/2026",
                         "is_active": 0 if star else rng.randint(0, 1)})
        for _ in range(0 if not (star or rng.random() < 0.5) else rng.randint(1, 3)):
            caps.append({"capture_id": len(caps) + 1, "plate_id": p, "camera_id": "C1" if star else rng.choice("C1 C2 C3".split()),
                         "captured_at": "2026-10-05T10:00:00", "ocr_confidence": round(rng.uniform(0.6, 1.0), 3)})
    return {"plates": plates, "REG": {"VEHICLE_REGISTRATION": reg, "OWNERS": owners},
            "THEFT": {"CRIME_RECORDS": crimes, "STATIONS": STATIONS}, "INS": {"POLICY_RECORDS": pols},
            "CAM": {"PLATE_CAPTURES": caps, "CAMERAS": CAMERAS, "ZONES": ZONES, "CAMERA_ZONES": CAM_ZONES}}


QUERY = ParsedQuery(text="stolen white hyundai gurgaon expired", predicates=[
    Predicate("vehicle_make", "=", "Hyundai", 0.9), Predicate("vehicle_colour", "=", "White", 0.7),
    Predicate("stolen_status", "=", True, 1.0), Predicate("insurance_status", "=", "EXPIRED", 0.55),
    Predicate("location", "~", "Gurugram", 0.8)])


def brute_force(w: Dict[str, Any]) -> Dict[str, float]:
    """Independent re-statement of the scoring semantics in plain Python over the raw rows."""
    loc = 0.8 / 4
    guru = lambda s: any(a in str(s).lower() for a in GURU)  # noqa: E731
    district = {s["station_name"]: s for s in STATIONS}
    cam_city = {c["camera_id"]: (c["location_name"], ZONES[int(c["camera_id"][1]) - 1]) for c in CAMERAS}
    owner_city = {o["owner_id"]: o["city"] for o in w["REG"]["OWNERS"]}
    canon = lambda s: "".join(ch for ch in s.upper() if ch.isalnum())  # noqa: E731
    total = {p: 0.0 for p in w["plates"]}
    for r in w["REG"]["VEHICLE_REGISTRATION"]:
        total[r["registration_no"]] += 0.9 * (r["make"] == "Hyundai") + 0.7 * (r["colour"] == "White") + loc * guru(owner_city[r["owner_id"]])
    theft: Dict[str, List[Dict[str, Any]]] = {}
    for c in w["THEFT"]["CRIME_RECORDS"]:
        theft.setdefault(canon(c["vehicle_number"]), []).append(c)
    for p, cs in theft.items():
        st = [district[c["police_station"]] for c in cs]
        # CONTEXT-REPLACED (R2: status predicates weigh STATUS_WEIGHT, see topk_sql):
        # total[p] += 1.0 * any(c["stolen_flag"] == "Y" and c["recovered_flag"] == "N" for c in cs)
        total[p] += 2.0 * 1.0 * any(c["stolen_flag"] == "Y" and c["recovered_flag"] == "N" for c in cs)
        # CONTEXT-END
        total[p] += loc * any(guru(s["district"]) or guru(s["zone"]) or guru(s["station_name"]) for s in st)
    ins: Dict[str, List[int]] = {}
    for r in w["INS"]["POLICY_RECORDS"]:
        ins.setdefault(canon(r["vehicle_reg"]), []).append(r["is_active"])
    for p, act in ins.items():
        # CONTEXT-REPLACED (R2: status predicates weigh STATUS_WEIGHT, see topk_sql):
        # total[p] += 0.55 * (0 if any(a == 1 for a in act) else 1)
        total[p] += 2.0 * 0.55 * (0 if any(a == 1 for a in act) else 1)
        # CONTEXT-END
    cams: Dict[str, List[Dict[str, Any]]] = {}
    for c in w["CAM"]["PLATE_CAPTURES"]:
        cams.setdefault(c["plate_id"], []).append(c)
    for p, cs in cams.items():
        hit = any(guru(cam_city[c["camera_id"]][0]) or guru(cam_city[c["camera_id"]][1]["city"]) for c in cs)
        total[p] += loc * hit * max(c["ocr_confidence"] for c in cs)
    return total


def _client(w: Dict[str, Any]) -> FakeClient:
    return FakeClient({k: v for k, v in w.items() if k != "plates"})


@pytest.mark.parametrize("method", ["ta", "tput"])
@pytest.mark.parametrize("seed", range(6))
def test_equals_brute_force_on_random_data(seed: int, method: str) -> None:
    w = world(seed, skew=seed % 2 == 0)
    k = 5
    expected = sorted(((p, s) for p, s in brute_force(w).items() if s > 0), key=lambda x: -x[1])[:k]
    run, _ = E.execute(S.features_for(QUERY), k, _client(w), method)
    got = run.best(k)
    assert [round(s, 9) for _, s in got] == [round(s, 9) for _, s in expected]
    bf = brute_force(w)
    for plate, score in got:
        assert score == pytest.approx(bf[plate])


@pytest.mark.parametrize("method", ["ta", "tput"])
def test_fewer_accesses_than_full_scan(method: str) -> None:
    w = world(4, n=400, skew=True)
    feats = S.features_for(QUERY)
    run, _ = E.execute(feats, 3, _client(w), method)
    full, _ = E.execute(feats, 3, _client(w), "ta", plates=w["plates"])   # the naive plan: every vehicle, every source
    assert run.sorted_accesses + run.random_accesses < full.random_accesses
    assert run.rows_shipped < full.rows_shipped


def test_ocr_noisy_camera_read_is_discounted_not_dropped() -> None:
    data = {"REG": {"VEHICLE_REGISTRATION": [
                {"registration_id": 1, "registration_no": "DL01AB1234", "owner_id": 1, "make": "Hyundai", "model": "Creta", "colour": "White"},
                {"registration_id": 2, "registration_no": "DL01AB1235", "owner_id": 2, "make": "Tata", "model": "Nexon", "colour": "White"}],
                    "OWNERS": [{"owner_id": 1, "city": "Pune"}, {"owner_id": 2, "city": "Pune"}]},
            "CAM": {"PLATE_CAPTURES": [{"capture_id": 1, "plate_id": "DLO1AB1Z34", "camera_id": "C1",
                                        "captured_at": "2026-10-05T10:00:00", "ocr_confidence": 0.9}],
                    "CAMERAS": CAMERAS, "ZONES": ZONES, "CAMERA_ZONES": CAM_ZONES}}
    q = ParsedQuery(text="white creta seen in gurgaon", predicates=[
        Predicate("vehicle_model", "=", "Creta"), Predicate("vehicle_colour", "=", "White"),
        Predicate("last_seen_location", "~", "Gurugram")])
    res = E.topk(q, k=2, client=FakeClient(data), verify=False)
    top = res.vehicles[0]
    assert top.plate == "DL01AB1234"
    assert 0 < top.partial["CAM"] < 1.0 and top.identity_confidence < 1.0
    assert any("DLO1AB1Z34" in line for line in top.explanation)
    assert res.baseline_rows > 0 and res.rows_shipped > 0


def test_dead_source_degrades() -> None:
    w = world(1)
    client = FakeClient({k: v for k, v in w.items() if k != "plates"}, down={"THEFT"})
    res = E.topk(QUERY, k=5, client=client, verify=False)
    assert res.vehicles and all("THEFT" not in v.partial for v in res.vehicles)


def test_time_window_binds_event_tables_only() -> None:
    q = ParsedQuery(text="hyundai seen in noida last month", time_window=("2026-10-01", "2026-10-31"),
                    predicates=[Predicate("vehicle_make", "=", "Hyundai"), Predicate("last_seen_location", "~", "Noida")])
    feats = S.features_for(q)
    assert "captured_at >= '2026-10-01'" in feats["CAM"][0].cond
    assert "registered_on" not in feats["REG"][0].cond


def test_algorithm_has_no_source_specific_names() -> None:
    src = Path(E.__file__).read_text(encoding="utf-8")
    for name in ("CRIME_RECORDS", "POLICY_RECORDS", "PLATE_CAPTURES", "VEHICLE_REGISTRATION", "stolen_flag", "policy_until"):
        assert name not in src
