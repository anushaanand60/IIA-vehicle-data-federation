"""Run EasyOCR over the rendered plate manifest and write the labelled noise benchmark.

Output: `data/match_benchmark.csv` with
    image_path,true_plate,ocr_read,ocr_confidence,degradation,severity,exact_match

Three kinds of row land in that file:

  * `degradation in {clean, blur, motion, jpeg, rotate, glare, dark, occlude}` — a rendered image
    from `scripts/make_plate_images.py`, read by EasyOCR. Ground truth known.
  * `degradation == observed` — the 47 camera plates in `sources/cam/cam.db` that are not
    registered in REG. Real noise, but nobody knows what they *should* have been, so
    `true_plate` is empty and `exact_match` is empty too.
  * `degradation == observed`, `severity == 0`, with truth — the two planted story misreads
    (`DL05CD9B76` -> `DL05CD9876`, `DLO1AB1234` -> `DL01AB1234`) that the demo script relies on.

Why the *raw* read and not `plate_ocr.read_plate`: `read_plate` repairs the reading through
`normalise_candidates` (pattern-licensed confusion swaps) and returns only the single best box.
Both are right for the GUI and wrong for a noise benchmark — the repair is precisely the thing
workstreams B and C have to be measured against, so it cannot be baked into the labels. We use
the module's reader and `canonical()`, join the detected boxes left to right, and report the
post-repair rate separately in the summary.
"""
from __future__ import annotations

import argparse
import csv
import sqlite3
import sys
import time
from collections import Counter, defaultdict
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:          # so `python scripts/run_ocr_benchmark.py` works too
    sys.path.insert(0, str(REPO_ROOT))

from mediator.plate_ocr import ALLOWLIST, canonical, normalise_candidates  # noqa: E402

DEFAULT_MANIFEST = REPO_ROOT / "data" / "plate_images" / "manifest.csv"
DEFAULT_OUT = REPO_ROOT / "data" / "match_benchmark.csv"
CAM_DB = REPO_ROOT / "sources" / "cam" / "cam.db"
REG_DB = REPO_ROOT / "sources" / "reg" / "reg.db"

FIELDS = ["image_path", "true_plate", "ocr_read", "ocr_confidence",
          "degradation", "severity", "exact_match"]

# Planted in the demo data so the story is reproducible; they belong in the benchmark because
# they are the two misreads every walkthrough and every test refers to.
STORY_MISREADS = [("DL05CD9B76", "DL05CD9876"), ("DLO1AB1234", "DL01AB1234")]


def read_image(reader: Any, path: Path) -> tuple[str, float]:
    """One image -> (raw canonical read, confidence). Never raises; no text -> ("", 0.0).

    EasyOCR usually splits a plate into two boxes (`DL01` and `AB 1234`), so the boxes are joined
    in left-to-right order. Confidence is the character-length-weighted mean of the joined boxes:
    a long confident run should not be dragged down by a two-character fragment.
    """
    try:
        raw = reader.readtext(str(path), allowlist=ALLOWLIST)
    except Exception:
        return "", 0.0
    parts = []
    for entry in raw:
        box, text, confidence = entry[0], entry[1], entry[2]
        cleaned = canonical(text)
        if not cleaned:
            continue
        left = min(point[0] for point in box) if box else 0
        parts.append((left, cleaned, float(confidence)))
    if not parts:
        return "", 0.0
    parts.sort(key=lambda p: p[0])
    read = "".join(p[1] for p in parts)
    weight = sum(len(p[1]) for p in parts)
    conf = sum(len(p[1]) * p[2] for p in parts) / weight
    return read, round(conf, 3)


def load_existing(out_path: Path) -> dict[str, dict]:
    if not out_path.exists():
        return {}
    with out_path.open(newline="", encoding="utf-8") as fh:
        return {row["image_path"]: row for row in csv.DictReader(fh)
                if row.get("degradation") != "observed"}


def observed_rows() -> list[dict]:
    """The 47 unregistered CAM plates plus the two planted story misreads."""
    # closing(), not `with sqlite3.connect(...)`: the connection context manager commits a
    # transaction, it does not close the handle, and we must not keep a handle on a source DB.
    with closing(sqlite3.connect(f"file:{REG_DB}?mode=ro", uri=True)) as reg:
        registered = {canonical(r[0]) for r in reg.execute(
            "SELECT registration_no FROM VEHICLE_REGISTRATION")}
    with closing(sqlite3.connect(f"file:{CAM_DB}?mode=ro", uri=True)) as cam:
        captures = cam.execute(
            "SELECT plate_id, MAX(ocr_confidence) FROM PLATE_CAPTURES GROUP BY plate_id"
        ).fetchall()
    rows = []
    for plate, confidence in sorted(captures):
        if canonical(plate) in registered:
            continue
        rows.append({
            "image_path": "",
            "true_plate": "",                 # unknown: this is the whole point of these rows
            "ocr_read": canonical(plate),
            "ocr_confidence": confidence,
            "degradation": "observed",
            "severity": 0,
            "exact_match": "",
        })
    for read, truth in STORY_MISREADS:
        rows.append({
            "image_path": "",
            "true_plate": truth,
            "ocr_read": read,
            "ocr_confidence": "",
            "degradation": "observed",
            "severity": 0,
            "exact_match": int(read == truth),
        })
    return rows


def run(manifest: Path, out_path: Path, force: bool, limit: int | None) -> list[dict]:
    with manifest.open(newline="", encoding="utf-8") as fh:
        jobs = list(csv.DictReader(fh))
    if limit:
        jobs = jobs[:limit]
    cached = {} if force else load_existing(out_path)
    todo = [j for j in jobs if j["image_path"] not in cached]
    print(f"{len(jobs)} manifest rows, {len(jobs) - len(todo)} cached, {len(todo)} to read")

    reader = None
    if todo:
        from mediator.plate_ocr import _get_reader
        started = time.time()
        reader = _get_reader()
        print(f"EasyOCR reader loaded in {time.time() - started:.1f}s")

    rows: list[dict] = []
    started = time.time()
    for index, job in enumerate(jobs, start=1):
        if job["image_path"] in cached:
            rows.append(cached[job["image_path"]])
            continue
        path = Path(job["image_path"])
        if not path.is_absolute():
            path = REPO_ROOT / path
        read, confidence = read_image(reader, path)
        rows.append({
            "image_path": job["image_path"],
            "true_plate": job["true_plate"],
            "ocr_read": read,
            "ocr_confidence": confidence,
            "degradation": job["degradation"],
            "severity": job["severity"],
            "exact_match": int(read == job["true_plate"]),
        })
        if index % 100 == 0:
            rate = (time.time() - started) / index
            print(f"  {index}/{len(jobs)}  {rate:.2f}s/image")
    rows.extend(observed_rows())
    with out_path.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    return rows


def load_benchmark(path: Path) -> list[dict]:
    """Read `match_benchmark.csv` back. The one loader every later workstream should use."""
    with Path(path).open(newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def confusions(rows: Iterable[dict]) -> Counter:
    """true char -> read char, by position, only where the lengths line up.

    Deliberately simple: a positional comparison cannot see insertions or deletions, so a
    length mismatch contributes nothing rather than a misaligned guess.
    """
    counter: Counter = Counter()
    for row in rows:
        truth, read = row["true_plate"], row["ocr_read"]
        if not truth or not read or len(truth) != len(read):
            continue
        for a, b in zip(truth, read):
            if a != b:
                counter[(a, b)] += 1
    return counter


def _repaired(row: dict) -> bool:
    """Would `plate_ocr.normalise_candidates` turn this read into the true plate?

    Reported alongside the raw exact-read rate because the raw rate is crushed by one systematic
    confusion (EasyOCR reads the digit 0 in `DL01` as the letter O on almost every image), which
    would otherwise make every bucket read 0% and tell us nothing about the degradation ladder.
    """
    candidates = normalise_candidates(row["ocr_read"])
    return bool(candidates) and candidates[0] == row["true_plate"]


def summarise(rows: list[dict]) -> None:
    labelled = [r for r in rows if r["true_plate"] and r["degradation"] != "observed"]
    observed = [r for r in rows if r["degradation"] == "observed"]
    print(f"\nimages read: {len(labelled)}   observed rows: {len(observed)}")
    if not labelled:
        print("no labelled rows — nothing to summarise")
        return
    hits = sum(int(r["exact_match"]) for r in labelled)
    repaired = sum(_repaired(r) for r in labelled)
    confs = [float(r["ocr_confidence"]) for r in labelled if r["ocr_confidence"] != ""]
    empty = sum(1 for r in labelled if not r["ocr_read"])
    print(f"exact-read rate overall: {hits}/{len(labelled)} = {hits / len(labelled):.1%}")
    print(f"exact after plate_ocr repair: {repaired}/{len(labelled)} = "
          f"{repaired / len(labelled):.1%}")
    print(f"mean confidence: {sum(confs) / len(confs):.3f}" if confs else "mean confidence: n/a")
    print(f"images OCR returned nothing for: {empty}")

    buckets: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in labelled:
        buckets[(row["degradation"], int(row["severity"]))].append(row)
    print("\ndegradation  sev     n   exact  repaired  mean conf")
    for key in sorted(buckets):
        group = buckets[key]
        hit = sum(int(r["exact_match"]) for r in group) / len(group)
        rep = sum(_repaired(r) for r in group) / len(group)
        conf = sum(float(r["ocr_confidence"]) for r in group) / len(group)
        print(f"{key[0]:<12} {key[1]:>3} {len(group):>5}  {hit:>6.1%}  {rep:>7.1%}     {conf:.3f}")

    print("\ntop character confusions (true -> read, aligned by position):")
    for (a, b), count in confusions(rows).most_common(10):
        print(f"  {a} -> {b}   {count}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--force", action="store_true", help="re-read images already in --out")
    parser.add_argument("--limit", type=int, default=None, help="first N manifest rows only")
    args = parser.parse_args(argv)
    rows = run(args.manifest, args.out, args.force, args.limit)
    summarise(rows)
    print(f"\nwrote {len(rows)} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
