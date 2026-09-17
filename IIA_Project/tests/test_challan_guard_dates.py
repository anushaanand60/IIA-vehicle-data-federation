"""Challan Guard step 6 as a pure function: was a policy in force on the day of the sighting?

No servers: `insurance_on` reads only an already-integrated profile (ISO dates, as the
`parse_ddmmyyyy` transform leaves them).
"""
from datetime import date

from mediator.challan_guard import insurance_on

SEEN = date(2026, 9, 4)


def test_a_policy_whose_window_covers_the_sighting_insures_it():
    insured, detail = insurance_on({"insurance_start": "2026-01-01", "insurance_expiry": "2027-01-01"}, SEEN)
    assert insured
    assert detail == "insured on 2026-09-04 (policy valid to 2027-01-01) — no offence"


def test_a_policy_that_expired_before_the_sighting_does_not():
    insured, detail = insurance_on({"insurance_start": "2025-07-12", "insurance_expiry": "2026-07-12"}, SEEN)
    assert not insured
    assert detail == "uninsured on 2026-09-04: policy expired 2026-07-12"


def test_no_policy_at_all_is_uninsured():
    insured, detail = insurance_on({"insurance_start": None, "insurance_expiry": None}, SEEN)
    assert not insured
    assert detail == "uninsured on 2026-09-04: no policy on record"


def test_a_policy_bought_after_the_sighting_does_not_clear_the_fine():
    insured, detail = insurance_on({"insurance_start": "2026-09-10", "insurance_expiry": "2027-09-09"}, SEEN)
    assert not insured
    assert detail == "uninsured on 2026-09-04: policy only started 2026-09-10"


def test_a_missing_start_date_falls_back_to_the_expiry_alone():
    insured, detail = insurance_on({"insurance_expiry": "2026-12-31"}, SEEN)
    assert insured
    assert detail == "insured on 2026-09-04 (policy valid to 2026-12-31) — no offence"


def test_the_sighting_day_itself_counts_at_both_ends():
    assert insurance_on({"insurance_start": "2026-09-04", "insurance_expiry": "2026-09-04"}, SEEN)[0]
