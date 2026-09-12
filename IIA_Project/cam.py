"""
generate_cam.py — synthesizes CAM (Road Camera Network, PostgreSQL 2nd instance) data.

Reads source_master.csv and creates PLATE_CAPTURES rows with several
deliberate categories, matching the spec's population slices:

  - ~450 plates: normal sighting(s), make/model/colour MATCH registration
  - ~25 plates: sighting where make/model/colour DISAGREE with registration
    -> triggers decision rule 4 (SUSPICIOUS — possible cloned plate)
  - ~15 EXTRA plates that do NOT exist in REG at all
    -> triggers decision rule 3 (UNREGISTERED / SUSPICIOUS)
  - ~20 plates: the captured plate_id is OCR-CORRUPTED (O<->0, I<->1 swaps)
    -> shows the limits of exact-match linking (Part B hook)
  - some plates get MULTIPLE captures at different times
    -> tests your "keep only max(captured_at)" aggregation rule

Plate is stored as plate_id, OCR output, e.g. 'DL01AB0001' (same shape as REG,
but a slice of rows are deliberately corrupted).
"""

import csv
import random
from datetime import datetime, timedelta

SEED = 2023519
random.seed(SEED)

NOW = datetime(2026, 9, 4, 12, 0, 0)

CAMERAS = [
    ("CAM001", "NH8 Toll Plaza", 28.4595, 77.0266),
    ("CAM002", "Ring Road Junction", 28.6139, 77.2090),
    ("CAM003", "Cyber City Entrance", 28.4949, 77.0870),
    ("CAM004", "Sector 29 Crossing", 28.4601, 77.0648),
    ("CAM005", "Old Delhi Signal", 28.6562, 77.2410),
]

OTHER_MAKES_MODELS = [
    ("Honda", "City"), ("Toyota", "Innova"), ("Kia", "Seltos"), ("Tata", "Punch"),
]
COLOURS = ["White", "Black", "Silver", "Red", "Blue", "Grey"]

N_CONFLICT = 25
N_UNREGISTERED = 15
N_OCR_CORRUPT = 20
N_NO_CAPTURE = 60  # these plates simply never get seen by a camera (partial-coverage case)


def corrupt_plate(plate: str) -> str:
    """Swap O<->0 and I<->1 in a random position to simulate OCR misread."""
    chars = list(plate)
    idx = random.randrange(len(chars))
    swap_map = {"O": "0", "0": "O", "I": "1", "1": "I"}
    for i in list(range(idx, len(chars))) + list(range(0, idx)):
        if chars[i] in swap_map:
            chars[i] = swap_map[chars[i]]
            break
    return "".join(chars)


def make_fake_unregistered_plate(seq: int) -> str:
    """A plate that was never issued by REG at all."""
    rto = random.choice(["DL02", "DL09", "HR51", "UP32"])
    letters = random.choice(["XY", "ZZ", "QQ"])
    return f"{rto}{letters}{9000 + seq}"


def read_master():
    with open("source_master.csv") as f:
        return list(csv.DictReader(f))


def main():
    master = read_master()
    random.shuffle(master)

    conflict_set = master[:N_CONFLICT]
    no_capture_set = {r["true_plate"] for r in master[N_CONFLICT:N_CONFLICT + N_NO_CAPTURE]}
    ocr_set = {r["true_plate"] for r in master[N_CONFLICT + N_NO_CAPTURE:N_CONFLICT + N_NO_CAPTURE + N_OCR_CORRUPT]}
    conflict_plates = {r["true_plate"] for r in conflict_set}

    captures = []
    capture_id = 1

    for r in master:
        plate = r["true_plate"]
        if plate in no_capture_set:
            continue  # never photographed

        n_captures = random.choice([1, 1, 1, 2, 2, 3])  # most seen once, some multiple times
        for _ in range(n_captures):
            camera_id, loc, lat, lon = random.choice(CAMERAS)
            captured_at = NOW - timedelta(days=random.randint(0, 60), hours=random.randint(0, 23))

            plate_id = plate
            if plate in ocr_set:
                plate_id = corrupt_plate(plate_id)

            if plate in conflict_plates:
                o_make, o_model = random.choice(OTHER_MAKES_MODELS)
                o_colour = random.choice(COLOURS)
            else:
                o_make, o_model, o_colour = r["true_make"], r["true_model"], r["true_colour"]

            captures.append({
                "capture_id": capture_id,
                "plate_id": plate_id,
                "camera_id": camera_id,
                "captured_at": captured_at.isoformat(),
                "observed_make": o_make,
                "observed_model": o_model,
                "observed_colour": o_colour,
                "ocr_confidence": round(random.uniform(0.55, 0.99) if plate in ocr_set
                                         else random.uniform(0.85, 1.0), 3),
            })
            capture_id += 1

    # extra unregistered plates seen only by camera, never in REG
    for i in range(N_UNREGISTERED):
        plate_id = make_fake_unregistered_plate(i)
        camera_id, loc, lat, lon = random.choice(CAMERAS)
        captured_at = NOW - timedelta(days=random.randint(0, 30))
        o_make, o_model = random.choice(OTHER_MAKES_MODELS)
        captures.append({
            "capture_id": capture_id,
            "plate_id": plate_id,
            "camera_id": camera_id,
            "captured_at": captured_at.isoformat(),
            "observed_make": o_make,
            "observed_model": o_model,
            "observed_colour": random.choice(COLOURS),
            "ocr_confidence": round(random.uniform(0.85, 1.0), 3),
        })
        capture_id += 1

    with open("cam_cameras.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["camera_id", "location_name", "lat", "lon"])
        w.writerows(CAMERAS)

    with open("cam_plate_captures.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=captures[0].keys())
        w.writeheader()
        w.writerows(captures)

    print(f"CAM: {len(captures)} capture rows written "
          f"({N_CONFLICT} conflict plates, {N_UNREGISTERED} unregistered-only plates, "
          f"{N_OCR_CORRUPT} OCR-corrupted, {N_NO_CAPTURE} never captured).")


if __name__ == "__main__":
    main()