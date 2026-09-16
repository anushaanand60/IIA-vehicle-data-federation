"""Seed the Challan Guard queue from data/challan_candidates.json.

    python scripts/seed_challan_cases.py           # insert any candidate not already queued
    python scripts/seed_challan_cases.py --reset   # delete every case and event first
    python scripts/seed_challan_cases.py --show    # print the queue, change nothing

A candidate is an ANPR event, not a verdict: nothing here decides anything. Pressing Verify in the
Challan Guard tab (or calling mediator.challan_guard.verify) is what queries the federation live.

Idempotent by (plate_read, captured_at): re-running after a demo adds nothing, so the queue can be
topped up without the operator having to remember what is already in it.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # runnable as a plain script

from mediator import catalog  # noqa: E402

CANDIDATES_FILE = ROOT / "data" / "challan_candidates.json"
FIELDS = ("plate_read", "camera_id", "location", "lat", "lon", "captured_at",
          "observed_make", "observed_colour", "ocr_confidence")


def load_candidates(path: Path = CANDIDATES_FILE) -> list[dict[str, Any]]:
    body = json.loads(path.read_text(encoding="utf-8"))
    return list(body.get("candidates") or [])


def cameras(path: Path = CANDIDATES_FILE) -> list[dict[str, Any]]:
    """The camera list the GUI's "new candidate" form offers. Not source data: a picker."""
    try:
        return list(json.loads(path.read_text(encoding="utf-8")).get("cameras") or [])
    except (OSError, ValueError):
        return []


def reset() -> int:
    catalog.init_meta_db()
    with sqlite3.connect(catalog.META_DB_PATH) as con:
        con.execute("DELETE FROM CHALLAN_EVENTS")
        removed = con.execute("DELETE FROM CHALLAN_CASES").rowcount
    return max(removed, 0)


def seed(path: Path = CANDIDATES_FILE) -> int:
    catalog.init_meta_db()
    existing = {(c["plate_read"], c["captured_at"]) for c in catalog.challan_list(limit=1000)}
    written = 0
    for candidate in load_candidates(path):
        key = (candidate.get("plate_read"), candidate.get("captured_at"))
        if key in existing:
            print(f"  skip   {key[0]} @ {key[1]} (already queued)")
            continue
        case_id = catalog.challan_insert({**{f: candidate.get(f) for f in FIELDS},
                                          "status": "CANDIDATE"})
        catalog.challan_event(case_id, "CREATED", "seed",
                              {"story": candidate.get("story"), "source": path.name})
        existing.add(key)
        written += 1
        print(f"  queued case {case_id}: {key[0]} @ {key[1]}")
    return written


def show() -> None:
    rows = catalog.challan_list(limit=1000)
    if not rows:
        print("no challan cases - run: python scripts/seed_challan_cases.py")
        return
    print("CASE  READ        RESOLVED    STATUS     VERDICT  AMOUNT  CAPTURED AT")
    for c in rows:
        print(f"{str(c['case_id']).ljust(5)} {str(c['plate_read']).ljust(11)} "
              f"{str(c['plate_resolved'] or '-').ljust(11)} {str(c['status']).ljust(10)} "
              f"{str(c['verdict'] or '-').ljust(8)} {str(c['amount_inr'] or '-').ljust(7)} "
              f"{c['captured_at']}")
    print(f"\n{len(rows)} case(s) in {catalog.META_DB_PATH}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete every case and event first")
    parser.add_argument("--show", action="store_true", help="print the queue and exit")
    args = parser.parse_args(argv)
    if args.show:
        show()
        return 0
    if args.reset:
        print(f"cleared {reset()} existing case(s)")
    total = seed()
    print(f"seeded {total} candidate(s) into {catalog.META_DB_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
