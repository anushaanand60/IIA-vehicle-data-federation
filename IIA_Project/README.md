# Federated Mediator: Uninsured Vehicle Identification System

A federated database mediator implementing Global-As-View (GAV) schema integration over four autonomous, heterogeneous government and observational source databases (Registration, Insurance, Police Crime, Road Camera) to detect uninsured and non-compliant vehicles.

---

## Key Features

1. **Autonomous Source Wrappers**: FastAPI micro-adapters on ports 8001–8004 (and 8005 for PUC) hiding DBMS dialects, schemas, and connection details.
2. **Hybrid Schema Matcher**: Discovers correspondences dynamically ($0.40 \cdot N + 0.20 \cdot C + 0.40 \cdot I$) with similarity heatmaps.
3. **Metadata-Driven GAV Registry**: Centralized catalog (`meta.db`) storing source capabilities and GAV mappings. Adding new sources requires zero code changes.
4. **Query Planner & Parallel Decomposer**: Pushes down key normalization filters in SQL and contacts only sources covering the requested attributes (UC1 vs UC2).
5. **Conflict Resolution & Decision Engine**: Evaluates compliance against 7 strict ordered rules, tracking attribute provenance, surfacing conflicts, and refusing to guess (`UNDETERMINED`) when data is unavailable.
6. **Ministry Audit Logging & PDF Reports**: Materializes audit trail in `REPORT_LOG` and renders 1-page PDF reports using ReportLab.
7. **Streamlit Interactive GUI**: Multi-tab dashboard featuring vehicle search, plan traces, live schema matcher, registry management, and report downloads.

---

## Quickstart Guide

### 1. Run the Entire System with One Command
```bash
python run_system.py
```
This automatically boots all 5 source database wrappers and launches the Streamlit interface at **http://localhost:8501**.

### 2. Bring Up One Source Laptop (no env-var juggling)
```bash
python scripts/serve.py INS --db-url "mysql+pymysql://iia:pw@127.0.0.1:3306/insdb"
python scripts/serve.py INS                       # later runs: re-loads sources/ins/laptop.env
python scripts/serve.py INS --check               # verify the demo plates, don't serve
```
Settings (`--db-url`, `--port`, `--readonly-admin`) are persisted to that source's gitignored
`sources/<id>/laptop.env` and reloaded on the next run. The command prints this laptop's own IP(s)
and the exact `python scripts/configure_cluster.py --set <ID>=<ip>` line to run on laptop 1.

### 3. Run Automated Test Suite
```bash
python -m pytest tests/ -v
```
Runs 13 automated tests verifying transforms, schema matching, query decomposition, end-to-end ground truth accuracy, and PDF generation.

Grade decisions against every generated plate with `python scripts/evaluate_ground_truth.py --start-wrappers`, and check the schema matcher's precision/recall against the hand-made gold mapping with `python scripts/evaluate_matcher.py`.

---

## Pre-Loaded Demo Scenarios

| License Plate | Scenario Description | Expected Outcome |
| :--- | :--- | :--- |
| `DL01AB1234` | Valid registration, active comprehensive insurance, verified by camera | **`CLEAR`** (High Confidence) |
| `DL05CD9876` | Valid registration, expired insurance policy (expired June 2026) | **`UNINSURED — REPORT`** (High Confidence) |
| `HR26EF4455` | Stolen vehicle with an open police case FIR | **`STOLEN — ALERT POLICE`** (High Confidence) |
| `UP16GH1122` | REG specifies Hyundai Creta (Red), but CAM sighted Kia Seltos (Blue) | **`SUSPICIOUS — POSSIBLE CLONED PLATE`** (Medium Confidence) |
| `MH12IJ7788` | Sighted by traffic camera at Old Delhi Signal, but has no REG record | **`UNREGISTERED / SUSPICIOUS`** (Medium Confidence) |

---

## Repository Structure

```
IIA_Project/
├── data/
│   ├── ground_truth.csv          # Benchmark ground truth dataset for automated testing
│   └── load_all.py               # Loader script populating source databases & demo plates
├── sources/
│   ├── base_wrapper.py           # Reusable FastAPI adapter factory (/health, /schema, /query)
│   ├── server_manager.py         # Multi-threaded server coordinator for local demo/tests
│   ├── reg/                      # Regional Transport Office (PostgreSQL / SQLite :8001)
│   ├── ins/                      # Insurance Authority (MySQL / SQLite :8002)
│   ├── theft/                    # Police Crime Records (SQLite :8003)
│   ├── cam/                      # Road Traffic Camera Network (PostgreSQL / SQLite :8004)
│   └── puc/                      # Pollution Certificate Agency (Extensibility UC6 :8005)
├── mediator/
│   ├── schema.py                 # VEHICLE_PROFILE global schema specification
│   ├── transforms.py             # Pure transformation functions (norm_plate, parse_dates, etc.)
│   ├── matcher.py                # Hybrid schema matching algorithm (N, C, I scoring)
│   ├── catalog.py                # Source catalog & GAV mapping registry in meta.db
│   ├── planner.py                # Query-driven source selection (UC1 vs UC2)
│   ├── decomposer.py             # SQL generation with key predicate pushdown
│   ├── executor.py               # Parallel federated execution with timeouts
│   ├── integrator.py             # Outer join, provenance tracking, conflict detection
│   ├── decide.py                 # 7-rule deterministic decision engine
│   ├── report.py                 # REPORT_LOG persistence and PDF generator
│   └── core.py                   # Central mediator coordinator
├── app/
│   └── app.py                    # Streamlit 5-tab web dashboard
├── tests/
│   ├── test_transforms.py        # Unit tests for transformations
│   ├── test_matcher.py           # Unit tests for schema matching algorithm
│   ├── test_decomposer.py        # Unit tests for query decomposition & pushdown
│   ├── test_demo_plates.py       # End-to-end integration test for UC1-UC5
│   └── test_e2e_groundtruth.py   # Ground truth accuracy validation
├── docs/
│   ├── report.md                 # Full project technical report
│   ├── heterogeneity_table.md    # Heterogeneity checklist
│   └── demo_script.md            # Rehearsal evaluation script with timings
├── run_system.py                 # One-command system runner
└── README.md                     # Documentation
```
