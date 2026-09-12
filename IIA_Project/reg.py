"""
Example: synthesizing data for ONE source (REG - Registration Authority, PostgreSQL)

Pattern used across the whole project:
  1. Build a "master list" of vehicles with a TRUE plate number + true attributes.
  2. Export a source-specific version: pick which vehicles this source knows about,
     reformat the plate the way THIS agency would store it, inject THIS source's
     specific dirtiness (nulls, typos, duplicates).
  3. Write ground_truth.csv so you can later grade your mediator's decisions.

Run: pip install faker
     python generate_reg_example.py
Produces: reg_vehicle_registration.csv, reg_owners.csv, source_master.csv
"""

import csv
import random
from faker import Faker

SEED = 2023519          # SAME seed used everywhere in the real project -> reproducible
random.seed(SEED)
fake = Faker("en_IN")
fake.seed_instance(SEED)

N_VEHICLES = 600

MAKES_MODELS = [
    ("Maruti Suzuki", "Swift"), ("Maruti Suzuki", "Baleno"),
    ("Hyundai", "Creta"), ("Hyundai", "i20"),
    ("Tata", "Nexon"), ("Tata", "Punch"),
    ("Honda", "City"), ("Toyota", "Innova"),
    ("Mahindra", "XUV700"), ("Kia", "Seltos"),
]
COLOURS = ["White", "Black", "Silver", "Red", "Blue", "Grey"]
FUEL_TYPES = ["PETROL", "DIESEL", "CNG", "EV"]
RTO_CODES = ["DL01", "DL05", "HR26", "UP16", "MH12"]

STATUS_WEIGHTS = [("ACTIVE", 0.90), ("SUSPENDED", 0.06), ("CANCELLED", 0.04)]


def weighted_choice(pairs):
    items, weights = zip(*pairs)
    return random.choices(items, weights=weights, k=1)[0]


def make_true_plate(rto_code: str, seq: int) -> str:
    """The canonical, 'ground truth' plate — REG will store it exactly like this."""
    letters = random.choice(["AB", "CD", "EF", "GH", "IJ"])
    return f"{rto_code}{letters}{seq:04d}"


def dirty_reg_variant(name_row):
    """~2.5% of REG rows get a data-quality problem, on purpose."""
    r = random.random()
    if r < 0.01:
        name_row["make"] = None                         # missing make
    elif r < 0.02:
        if name_row["make"] == "Hyundai":
            name_row["make"] = "Hyundia"                 # misspelling
    return name_row


def main():
    owners = []
    vehicles = []
    ground_truth_rows = []

    for i in range(1, N_VEHICLES + 1):
        owner_id = i
        rto = random.choice(RTO_CODES)
        plate = make_true_plate(rto, i)
        make, model = random.choice(MAKES_MODELS)
        status = weighted_choice(STATUS_WEIGHTS)

        owners.append({
            "owner_id": owner_id,
            "full_name": fake.name(),
            "address_line": fake.street_address(),
            "city": fake.city(),
        })

        row = {
            "registration_id": i,
            "registration_no": plate,             # REG's own clean format: 'DL01AB1234'
            "owner_id": owner_id,
            "make": make,
            "model": model,
            "colour": random.choice(COLOURS),
            "fuel_type": random.choice(FUEL_TYPES),
            "registered_on": fake.date_between(start_date="-6y", end_date="-1y").isoformat(),
            "reg_status": status,
            "rto_code": rto,
        }
        row = dirty_reg_variant(row)
        vehicles.append(row)

        # Stash the TRUE plate + true make/model/colour — every other source's
        # loader script will read THIS same list and distort the plate format,
        # so overlaps across sources are guaranteed and known.
        ground_truth_rows.append({
            "true_plate": plate,
            "true_make": make,
            "true_model": model,
            "true_colour": row["colour"],
            "reg_status": status,
        })

    # --- write REG's own two CSVs (later loaded into Postgres via load_reg.py) ---
    with open("reg_owners.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=owners[0].keys())
        w.writeheader()
        w.writerows(owners)

    with open("reg_vehicle_registration.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=vehicles[0].keys())
        w.writeheader()
        w.writerows(vehicles)

    # --- write the shared master list other sources will read from ---
    with open("source_master.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=ground_truth_rows[0].keys())
        w.writeheader()
        w.writerows(ground_truth_rows)

    print(f"Generated {N_VEHICLES} vehicles into reg_owners.csv / reg_vehicle_registration.csv")
    print("source_master.csv is what INS/THEFT/CAM generators will read from next.")


if __name__ == "__main__":
    main()