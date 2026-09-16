"""Write data/ground_truth.csv: the expected decision for every generated plate (design PDF section 4).

Run after the generators and data/inject_demo_fixtures.py:

    python reg.py && python ins.py && python theft.py && python cam.py && python puc.py
    python data/inject_demo_fixtures.py
    python data/make_ground_truth.py

then grade the mediator against it with python scripts/evaluate_ground_truth.py.
"""
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))  # runnable as a plain script

from mediator.ground_truth import write_ground_truth  # noqa: E402

if __name__ == "__main__":
    sys.stdout.reconfigure(errors="replace")  # decisions contain an em dash
    truth = write_ground_truth(ROOT)
    print(f"Created data/ground_truth.csv with {len(truth)} plates")
    for label, count in Counter(f"{r['expected_decision']} ({r['expected_confidence']})" for r in truth).most_common():
        print(f"  {count:4d}  {label}")
