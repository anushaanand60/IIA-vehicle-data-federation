"""Seed MAPPING_REGISTRY so a fresh clone can answer queries without clicking through the GUI.

    python scripts/seed_mappings.py            # insert/update every validated mapping
    python scripts/seed_mappings.py --reset    # delete existing mappings first
    python scripts/seed_mappings.py --show     # print what is in meta.db now, change nothing

Why this is needed. The schema matcher (Schema Matching tab) discovers *correspondences* - which
source column means which global attribute - and the "Accept & Persist" button writes them with
save_mapping(). What the matcher cannot infer is the other half of a GAV mapping: the transform
that reconciles value formats (DD/MM/YYYY vs ISO vs epoch seconds) and the join path that reaches
a column in a second table. Those are the human validation step of the design.

The validated set already lives in tests/fixtures.py as APPROVED_MAPPINGS. This script is
deliberately not a second copy of it: it imports that list and writes it through
mediator.catalog.save_mapping, which upserts, so re-running updates rather than skips. That makes
the registry identical on every laptop and reproducible from a clean checkout.

Sources absent from SOURCE_CATALOG are skipped, because an orphan mapping row is a registry error.
PUC is normally absent until it is registered live in the Catalog tab - that is the UC6 demo.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # runnable as a plain script

from mediator.catalog import (META_DB_PATH, get_all_mappings, get_source_catalog,  # noqa: E402
                              init_meta_db, save_mapping)
from tests.fixtures import APPROVED_MAPPINGS  # noqa: E402  the team's validated set

VALIDATED_BY = "human_expert"

# Aggregates dropped on purpose, keyed by (source_id, global_attr).
# Empty now: "latest_by:<col>" is the registry's statement of *which column means newest*, and both
# consumers read it. The decomposer decides for itself whether that ordering can be pushed into SQL
# (it refuses for a DD/MM/YYYY column such as INS.policy_until, because reassembling the date needs
# substr() || substr(), and MySQL reads || as logical OR unless PIPES_AS_CONCAT is set - a mis-sorted
# ORDER BY ... LIMIT 1 would silently return the wrong policy rather than fail loudly), and the
# integrator applies the mapping's transform and picks the latest row in Python. Blanking the field
# here used to hide the ordering from the integrator as well, which is not what was intended.
NO_PUSHDOWN: set = set()

# Human-validated mappings that the schema matcher is not expected to propose, and which therefore
# deliberately do NOT appear in data/gold_mapping.json (adding them there would score the matcher
# against correspondences nobody asked it to find). A camera's coordinates are the other half of a
# GAV mapping in exactly the sense this script's docstring describes: a column whose *meaning* for
# the global schema is a human decision. Challan Guard's impossible-travel check reads them through
# the registry like any other global attribute, so no camera table name ever enters that module.
EXTRA_MAPPINGS = [
    {"source_id": "CAM", "source_table": "CAMERAS", "source_attr": "lat", "global_attr": "camera_lat",
     "transform_fn": "none", "aggregate": None,
     "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
    {"source_id": "CAM", "source_table": "CAMERAS", "source_attr": "lon", "global_attr": "camera_lon",
     "transform_fn": "none", "aggregate": None,
     "join_path": "PLATE_CAPTURES.camera_id=CAMERAS.camera_id", "match_score": 1.0},
]


def seed(reset: bool = False) -> int:
    init_meta_db()
    catalogued = set(get_source_catalog())
    if reset:
        with sqlite3.connect(META_DB_PATH) as con:
            removed = con.execute("DELETE FROM MAPPING_REGISTRY").rowcount
        print("cleared " + str(removed) + " existing mapping(s)")

    written: dict[str, int] = {}
    skipped: dict[str, int] = {}
    for m in [*APPROVED_MAPPINGS, *EXTRA_MAPPINGS]:
        source_id = m["source_id"]
        if source_id not in catalogued:
            skipped[source_id] = skipped.get(source_id, 0) + 1
            continue
        aggregate = None if (source_id, m["global_attr"]) in NO_PUSHDOWN else m["aggregate"]
        save_mapping(source_id=source_id, source_table=m["source_table"], source_attr=m["source_attr"],
                     global_attr=m["global_attr"], transform_fn=m["transform_fn"],
                     aggregate=aggregate, join_path=m["join_path"],
                     match_score=m["match_score"], validated_by=VALIDATED_BY)
        written[source_id] = written.get(source_id, 0) + 1

    for source_id in sorted(written):
        print("  " + source_id.ljust(6) + str(written[source_id]) + " mapping(s)")
    for source_id in sorted(skipped):
        print("  " + source_id.ljust(6) + "skipped " + str(skipped[source_id])
              + " mapping(s) - not in SOURCE_CATALOG (register the source first)")
    total = sum(written.values())
    print("seeded " + str(total) + " mapping(s) into " + str(META_DB_PATH))
    return total


def show() -> None:
    rows = get_all_mappings()
    if not rows:
        print("MAPPING_REGISTRY is empty - run: python scripts/seed_mappings.py")
        return
    print("SRC    TABLE                  COLUMN           GLOBAL ATTRIBUTE     TRANSFORM        AGGREGATE")
    for m in sorted(rows, key=lambda r: (r["source_id"], r["global_attr"])):
        print(m["source_id"].ljust(6) + " " + str(m["source_table"]).ljust(22)
              + str(m["source_attr"]).ljust(16) + " " + str(m["global_attr"]).ljust(20)
              + " " + str(m.get("transform_fn") or "none").ljust(16) + " " + str(m.get("aggregate") or ""))
    print("\n" + str(len(rows)) + " mapping(s) in " + str(META_DB_PATH))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--reset", action="store_true", help="delete existing mappings before seeding")
    parser.add_argument("--show", action="store_true", help="print the current registry and exit")
    args = parser.parse_args(argv)
    if args.show:
        show()
        return 0
    seed(reset=args.reset)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
