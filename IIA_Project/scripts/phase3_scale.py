"""CONTEXT-ADDED (R1): clone the whole dataset N times into one SQLite file per source, for scalability runs.

    python scripts/phase3_scale.py --factor 10 --out data/scale/x10/
    python scripts/phase3_scale.py --factor 100 --out data/scale/x100/

Copy 0 is the original data; copy k >= 1 is the same world with new identities, so every distribution,
fan-out and planted scenario repeats N times while no two copies share a key:
- integer keys (owner_id, policy_id, incident_id, ...) are shifted by k * 1,000,000;
- plates get k as extra series letters in each source's own format (DL01AB1234 -> DL01AAB1234,
  DL-01-AB-1234 -> DL-01-AAB-1234, dl 01 ab 1234 -> dl 01 aab 1234, and the same for OCR reads);
- chassis numbers get k in positions 9-10, mobiles a fixed offset, text keys a "-k" suffix;
- names are re-spelt with a per-copy letter permutation: unreadable, but edit distances and q-gram
  overlaps are exactly preserved, so person resolution sees the same difficulty as on x1;
- DOCS text (if present) gets the same plate / chassis / FIR / PUC / POL-no / name rewriting, best effort.
Schemas come from each sources/<id>/schema.sql, CSV->table pairs from scripts/load_source.SOURCES.
Output is SQLite only (VARCHAR lengths are not enforced there; a 4-letter series exceeds the INS width).
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import string
import sys
import time
from pathlib import Path
from random import Random
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.load_source import SOURCES, ddl_statements  # noqa: E402
from scripts.phase3_common import read_csv  # noqa: E402

OFFSET = 1_000_000
INT_KEYS = {"owner_id", "registration_id", "transfer_id", "prev_owner_id", "new_owner_id", "insurer_id",
            "policy_id", "holder_id", "claim_id", "incident_id", "person_id", "capture_id", "zone_id",
            "challan_id", "centre_id", "analyser_id", "test_id"}
PLATES = {"registration_no", "vehicle_reg", "vehicle_number", "plate_id", "regn_number"}
TEXT_KEYS = {"camera_id", "station_name", "police_station", "cert_no", "fir_no", "tested_at", "centre_name", "doc_id"}
NAMES = {"full_name", "holder_name", "name"}
DOC_TEXT = {"title", "body"}
PLATE_RE = re.compile(r"\b([A-Za-z]{2}[\s-]?[0-9OoIiZz]{2}[\s-]?)([A-Za-z]{2}[\s-]?[0-9OoIiZzSsBb]{4})\b")
CHASSIS_RE = re.compile(r"\b[A-HJ-NPR-Z0-9]{17}\b")
FIR_RE = re.compile(r"\b(FIR\d{5}/\d{4})\b")
POL_RE = re.compile(r"\bPOL-(\d{6})\b")
CERT_RE = re.compile(r"\b(PUC-\d{4}-\d{3,})\b")


def series_code(k: int) -> str:
    """1..26 -> A..Z (a 3-letter series); 27.. -> AA.. (4 letters), so copies never collide."""
    return string.ascii_uppercase[k - 1] if k <= 26 else \
        string.ascii_uppercase[(k - 27) // 26] + string.ascii_uppercase[(k - 27) % 26]


def plate_k(raw: str, k: int) -> str:
    """Insert the copy code before the series letters (the 5th alphanumeric), keeping case and separators."""
    seen = 0
    for idx, ch in enumerate(raw):
        if ch.isalnum():
            seen += 1
            if seen == 5:
                code = series_code(k)
                return raw[:idx] + (code.lower() if ch.islower() else code) + raw[idx:]
    return raw + series_code(k)


def chassis_k(chassis: str, k: int) -> str:
    # serials (last 6) are unique per vehicle and the generator's check digit (position 9) is 0-9 or X, so a
    # letter other than X there marks a copy: injective across vehicles and copies (up to 22 x 36 copies)
    code = "ABCDEFGHJKLMNPRSTUVWYZ"[k // 36] + (string.digits + string.ascii_uppercase)[k % 36]
    return chassis[:8] + code + chassis[10:] if len(chassis) == 17 else chassis


def cipher(k: int) -> Callable[[str], str]:
    letters = list(string.ascii_lowercase)
    Random(k).shuffle(letters)
    table = str.maketrans(string.ascii_lowercase + string.ascii_uppercase,
                          "".join(letters) + "".join(letters).upper())
    return lambda text: text.translate(table)


def name_tokens() -> set[str]:
    """Every word that is part of a person's name somewhere, so DOCS text can be re-spelt consistently."""
    out: set[str] = set()
    for csv_file, column in (("reg_owners.csv", "full_name"), ("ins_policy_holders.csv", "holder_name"),
                             ("theft_persons.csv", "name")):
        if (ROOT / csv_file).exists():
            for row in read_csv(csv_file):
                out.update(t.lower() for t in re.findall(r"[A-Za-z]{3,}", row[column]))
    return out


def transform(row: dict[str, str], k: int, spell: Callable[[str], str], tokens: set[str]) -> dict[str, Any]:
    if k == 0:
        return dict(row)
    out: dict[str, Any] = {}
    for col, val in row.items():
        c = col.lower()
        if val in ("", None):
            out[col] = val
        elif c in INT_KEYS:
            out[col] = str(int(float(val)) + k * OFFSET)
        elif c in PLATES:
            out[col] = plate_k(val, k)
        elif c in TEXT_KEYS:
            out[col] = f"{val}-{k}"
        elif c == "chassis_no":
            out[col] = chassis_k(val, k)
        elif c == "mobile" and val.isdigit() and len(val) == 10:
            out[col] = val[0] + f"{(int(val[1:]) + k * 104729) % 10**9:09d}"
        elif c in NAMES:
            out[col] = spell(val)
        elif c in DOC_TEXT:
            out[col] = rewrite_text(val, k, spell, tokens)
        else:
            out[col] = val
    return out


def rewrite_text(text: str, k: int, spell: Callable[[str], str], tokens: set[str]) -> str:
    text = PLATE_RE.sub(lambda m: plate_k(m.group(0), k), text)
    text = CHASSIS_RE.sub(lambda m: chassis_k(m.group(0), k), text)
    text = FIR_RE.sub(lambda m: f"{m.group(1)}-{k}", text)
    text = CERT_RE.sub(lambda m: f"{m.group(1)}-{k}", text)
    text = POL_RE.sub(lambda m: f"POL-{int(m.group(1)) + k * OFFSET:06d}", text)
    return re.sub(r"[A-Za-z]{3,}", lambda m: spell(m.group(0)) if m.group(0).lower() in tokens else m.group(0), text)


def build_source(source_id: str, factor: int, out_dir: Path, tokens: set[str]) -> dict[str, int]:
    creates, _ = ddl_statements(source_id)
    db = out_dir / f"{source_id.lower()}.db"
    db.unlink(missing_ok=True)
    con = sqlite3.connect(db)
    for statement in creates:
        con.execute(statement)
    counts: dict[str, int] = {}
    for csv_file, table in SOURCES[source_id]:
        if not (ROOT / csv_file).exists():
            print(f"  {source_id}.{table}: {csv_file} missing, left empty")
            continue
        rows = read_csv(csv_file)
        if not rows:
            continue
        cols = list(rows[0])
        sql = f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' for _ in cols)})"
        for k in range(factor):
            spell = cipher(k)
            batch = [transform(r, k, spell, tokens) for r in rows]
            con.executemany(sql, [[None if b[c] == "" else b[c] for c in cols] for b in batch])
        counts[table] = len(rows) * factor
    con.commit()
    con.close()
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--factor", type=int, default=10)
    parser.add_argument("--out", default=None, help="default: data/scale/x<factor>/")
    parser.add_argument("--sources", default=",".join(SOURCES), help="comma list, default all incl. DOCS")
    args = parser.parse_args(argv)
    if not 1 <= args.factor <= 702:
        raise SystemExit("--factor must be between 1 and 702")
    out_dir = Path(args.out or f"data/scale/x{args.factor}")
    out_dir = out_dir if out_dir.is_absolute() else ROOT / out_dir
    out_dir.mkdir(parents=True, exist_ok=True)
    tokens, manifest = name_tokens(), {"factor": args.factor, "sources": {}}
    for source_id in [s.strip().upper() for s in args.sources.split(",") if s.strip()]:
        if not (ROOT / "sources" / source_id.lower() / "schema.sql").exists():
            print(f"{source_id}: no schema.sql, skipped")
            continue
        started = time.perf_counter()
        counts = build_source(source_id, args.factor, out_dir, tokens)
        manifest["sources"][source_id] = counts
        print(f"{source_id}: {sum(counts.values())} row(s) in {time.perf_counter() - started:.1f}s -> "
              f"{(out_dir / (source_id.lower() + '.db')).relative_to(ROOT)}")
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
