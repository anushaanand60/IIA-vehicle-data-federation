"""
generate_ins.py — synthesizes INS (Insurance Provider, MySQL) data.

Reads source_master.csv (produced by generate_reg_example.py) and:
  - skips ~50 plates entirely -> UNINSURED (no policy at all)
  - gives ~50 plates an EXPIRED policy -> UNINSURED (expired)
  - gives ~10 plates TWO policy rows (an old expired one + a new active one)
    -> tests your "keep only latest by policy_until" aggregation rule
  - gives everyone else ONE active, valid policy -> clean/VALID case

Formats the plate as INS's own broken convention: hyphenated, e.g. 'DL-01-AB-0001'.
Dates are stored as TEXT in DD/MM/YYYY format (representation conflict, on purpose).
is_active is stored as 1/0 (TINYINT), independent of the computed expiry —
   this is realistic: the flag can be stale/wrong, which is exactly why the
   mediator should derive insurance_status from policy_until, not just trust is_active.
"""

import csv
import random
from datetime import date, timedelta
from faker import Faker

SEED = 2023519
random.seed(SEED)
fake = Faker("en_IN")
fake.seed_instance(SEED)

TODAY = date(2026, 9, 4)  # matches "today" referenced in the spec (Fri 4 Sep 2026)

INSURERS = [
    (1, "National Shield Insurance"),
    (2, "Bharat Motor Assure"),
    (3, "SafeDrive General Insurance"),
    (4, "TrustLine Insurers"),
    (5, "HighWay Cover Co."),
]

N_NO_POLICY = 50
N_EXPIRED = 50
N_RENEWAL = 10


def hyphenate_plate(plate: str) -> str:
    """'DL01AB0001' -> 'DL-01-AB-0001' — INS's own convention."""
    return f"{plate[0:2]}-{plate[2:4]}-{plate[4:6]}-{plate[6:10]}"


def fmt_ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def read_master():
    with open("source_master.csv") as f:
        return list(csv.DictReader(f))


def main():
    master = read_master()
    # Shuffle so the "no policy" / "expired" picks aren't just the first N rows. Each generator shuffles with its own
    # stream: ins.py, theft.py and cam.py once all shuffled right after random.seed(SEED), got the same order, and so
    # picked correlated slices of `master` (same vehicles kept popping up in every source's "special case" set).
    random.Random(f"{SEED}-INS").shuffle(master)

    no_policy = set(r["true_plate"] for r in master[:N_NO_POLICY])
    expired = set(r["true_plate"] for r in master[N_NO_POLICY:N_NO_POLICY + N_EXPIRED])
    renewal = set(r["true_plate"] for r in master[N_NO_POLICY + N_EXPIRED:N_NO_POLICY + N_EXPIRED + N_RENEWAL])

    policy_rows = []
    policy_id = 1

    for r in master:
        plate = r["true_plate"]
        if plate in no_policy:
            continue  # this vehicle simply has no row in INS at all -> UC1 returns NONE

        vehicle_reg = hyphenate_plate(plate)
        insurer_id = random.choice(INSURERS)[0]
        policy_type = random.choice(["THIRD_PARTY", "COMPREHENSIVE"])
        premium = round(random.uniform(3500, 18000), 2)

        if plate in expired:
            start = TODAY - timedelta(days=730)
            until = TODAY - timedelta(days=random.randint(5, 200))  # already expired
            policy_rows.append({
                "policy_id": policy_id, "vehicle_reg": vehicle_reg, "insurer_id": insurer_id,
                "policy_type": policy_type, "policy_start": fmt_ddmmyyyy(start),
                "policy_until": fmt_ddmmyyyy(until), "is_active": 0, "premium_inr": premium,
            })
            policy_id += 1

        elif plate in renewal:
            # old, expired policy (a past renewal)
            old_start = TODAY - timedelta(days=1460)
            old_until = TODAY - timedelta(days=730)
            policy_rows.append({
                "policy_id": policy_id, "vehicle_reg": vehicle_reg, "insurer_id": insurer_id,
                "policy_type": policy_type, "policy_start": fmt_ddmmyyyy(old_start),
                "policy_until": fmt_ddmmyyyy(old_until), "is_active": 0, "premium_inr": premium,
            })
            policy_id += 1
            # new, active policy -> mediator must pick THIS one via max(policy_until)
            new_start = TODAY - timedelta(days=100)
            new_until = TODAY + timedelta(days=random.randint(60, 300))
            policy_rows.append({
                "policy_id": policy_id, "vehicle_reg": vehicle_reg, "insurer_id": insurer_id,
                "policy_type": policy_type, "policy_start": fmt_ddmmyyyy(new_start),
                "policy_until": fmt_ddmmyyyy(new_until), "is_active": 1, "premium_inr": premium,
            })
            policy_id += 1

        else:
            start = TODAY - timedelta(days=random.randint(30, 300))
            until = TODAY + timedelta(days=random.randint(60, 365))
            policy_rows.append({
                "policy_id": policy_id, "vehicle_reg": vehicle_reg, "insurer_id": insurer_id,
                "policy_type": policy_type, "policy_start": fmt_ddmmyyyy(start),
                "policy_until": fmt_ddmmyyyy(until), "is_active": 1, "premium_inr": premium,
            })
            policy_id += 1

    with open("ins_insurers.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["insurer_id", "insurer_name"])
        w.writerows(INSURERS)

    with open("ins_policy_records.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=policy_rows[0].keys())
        w.writeheader()
        w.writerows(policy_rows)

    print(f"INS: {len(policy_rows)} policy rows written "
          f"({len(no_policy)} vehicles have NO policy, {len(expired)} EXPIRED, {len(renewal)} with a RENEWAL pair).")


if __name__ == "__main__":
    main()