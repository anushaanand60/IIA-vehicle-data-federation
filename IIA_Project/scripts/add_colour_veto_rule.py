"""Workstream H: add the colour/model veto under R5 through the RDR engine's knowledge acquisition.

    python scripts/add_colour_veto_rule.py            # replays the validation (seed 20261017, --local-db)

The cornerstone is the exact case the cleansing validation scored as a wrong plate correction:
noisy read UP16ABO524 (truth UP16AB0521) accepted as UP16AB0524, registered Grey, camera saw Red.
The rule is added with ``RDRTree.add_exception`` (never by editing JSON), so the engine's
cornerstone protection runs; if it refuses, the refusal is printed and nothing is saved.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from mediator import cleansing, cleansing_validation as cv  # noqa: E402
from mediator.rdr import RDRConflict  # noqa: E402

NOISY_READ = "UP16ABO524"
CONDITION = {"all": [{"field": "field", "op": "eq", "value": "plate_number"},
                     {"any": [{"field": "match_best_colour_agrees", "op": "is_false"},
                              {"field": "match_best_model_agrees", "op": "is_false"}]}]}
CONCLUSION = {"action": "quarantine"}
BECAUSE = ("a plate correction is only made when nothing the camera saw contradicts the candidate's "
           "registration (registered colour or model disagrees with the observation)")


def validation_case(seed: int = cv.SEED) -> Dict[str, Any]:
    """Replay the validation and capture the plate case the RDR tree saw for the noisy read."""
    seen: Dict[str, Any] = {}
    real = cleansing.propose

    def spy(record: Dict[str, Any], ctx: Optional[cleansing.Context] = None) -> cleansing.Proposal:
        if record.get(cleansing.PLATE) == NOISY_READ and "case" not in seen:
            seen["case"] = cleansing.features(record, cleansing.PLATE, ctx.profile_for(record), ctx)
            seen["tree"] = ctx.tree
        return real(record, ctx)

    cleansing.propose = spy
    try:
        cv.validate(seed=seed, local_db=True)
    finally:
        cleansing.propose = real
    if "case" not in seen:
        raise SystemExit(f"{NOISY_READ} did not occur in the validation sample (seed {seed})")
    case = seen["case"]
    case["_provenance"] = (f"cleansing_validation seed {seed}: plate_misread_double, noisy read {NOISY_READ} "
                           "of UP16AB0521 corrected by R5 to UP16AB0524 (registered Grey, camera saw Red)")
    return {"case": case, "tree": seen["tree"]}


def main() -> int:
    got = validation_case()
    case, tree = got["case"], got["tree"]
    print("case:", {k: case.get(k) for k in ("value", "match_decision", "match_best", "match_probability",
                                              "match_best_make_agrees", "match_best_colour_agrees",
                                              "match_best_model_agrees")})
    print("before:", tree.evaluate(case)[0])
    try:
        rule = tree.add_exception(case, "R5", CONDITION, CONCLUSION, BECAUSE, branch="except",
                                  created_by="expert (workstream H, from a validation finding)")
    except RDRConflict as exc:
        print(f"REFUSED by the RDR engine: {exc}")
        return 1
    tree.save(cleansing.RULES_PATH)
    print(f"added {rule.id} under R5; after:", tree.evaluate(case)[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
