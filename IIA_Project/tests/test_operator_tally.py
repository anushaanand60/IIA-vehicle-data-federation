"""PHASE3-ADDED (C5): the Data Cleansing tiles count what operators did, from the cleansing log, and only
while the effect is still in the data (not undone, record present, value still the one written)."""
from __future__ import annotations

import sys
from pathlib import Path

_APP = Path(__file__).resolve().parents[1] / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

from tabs.cleansing_tab import operator_tally  # noqa: E402

RECORDS = [{"record_id": 7, "plate_number": "DL01AB1234"}, {"record_id": 8, "plate_number": "DL01AB1Z34"}]


def _set(rid, value, undone=None):
    return {"record_id": str(rid), "action": "set", "field": "plate_number", "after_value": f'"{value}"', "undone_at": undone}


def _resolve(rid, undone=None):
    return {"record_id": str(rid), "action": "resolve", "field": "record", "after_value": '"confirm_clone"', "undone_at": undone}


def test_a_fix_counts_only_while_the_record_still_holds_it():
    log = [_set(7, "DL01AB1234"), _set(8, "DL01AB1234"), _set(9, "DL01AB0001")]  # 8 reverted by a reload, 9 gone
    assert operator_tally(log, RECORDS)["fixed"] == 1


def test_undone_entries_do_not_count():
    log = [_set(7, "DL01AB1234", undone="2026-10-22T12:00:00"), _resolve(8, undone="2026-10-22T12:00:00")]
    assert operator_tally(log, RECORDS) == {"fixed": 0, "decided": 0}


def test_resolutions_count_each_record_once():
    assert operator_tally([_resolve(8), _resolve(8), _resolve(7)], RECORDS)["decided"] == 2
