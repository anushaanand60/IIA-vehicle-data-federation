# Visibility UI + Challan Guard Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** (1) Re-skin the Streamlit GUI on the AirSentinel "Visibility" design system so every screen reads at a glance (big real headings, one accent, legible buttons, no caption-as-heading clutter). (2) Ship one standout end-to-end feature, **Challan Guard**: verify-before-fine for ANPR-triggered uninsured-vehicle e-challans, with a citizen dispute loop, built on the live federation.

**Architecture:** UI = single token block injected once (`app/theme.py`), shared primitives in `app/components.py` (`section`, `kpi_row`, `styled_table`, `stepper`, `chip`), every tab composed from those. Challan Guard = mediator-side case store in `meta.db` (`CHALLAN_CASES`, `CHALLAN_EVENTS`) + three pure verification modules (`plate_resolve`, `travel_check`, `challan_guard`) that only call the existing planner/executor/integrator, so every source fact still comes live over the wrappers via the mapping registry. No source is ever written by the mediator; cases live only in the mediator's own meta.db.

**Tech Stack:** Python 3.11, Streamlit 1.57, FastAPI wrappers (unchanged), SQLite meta.db, pytest + `streamlit.testing.v1.AppTest`.

## Global Constraints

- **Commits carry NO `Co-Authored-By` line.** Author is the git user already configured. Small verified commits on `main`.
- Never edit `mediator/executor.py` with source-specific names (grep invariant in CLAUDE.md §8 stays green).
- Mediator never writes to a source. Challan cases go in `mediator/meta.db` only.
- `app/app.py` imports tabs as `from tabs.x import …` after the `_APP_DIR` sys.path insert. Never `from app.tabs…` (double-run bug). Every widget has a unique `key=`.
- No new pip deps. No ML/LLM. Modules ≤ ~220 lines, type hints, one-line trade-off comments where a choice is not obvious.
- `REFERENCE_TODAY = 2026-09-04` (from `mediator/decide.py`) is "today" for all date logic.
- Full suite must stay green: `python -m pytest -q` (currently 546 passed). Run from repo root `C:\Users\siddh_ygv5bws\IIA-vehicle-data-federation\IIA_Project`; `tests/conftest.py` starts a local cluster itself.
- Design source of truth to copy from: `C:\Users\siddh_ygv5bws\AirSentinel\src\airsentinel\dashboard\common.py` (`_CSS`, `styled_table`, `readable_fill`, `_contrast`) and `rhythm.py`. Read them before Task A1. Ignore `docs/STYLE_GUIDE.md` / `DESIGN_TOKENS_V3.md` there (stale).

---

## Workstream A — Visibility UI (owner: Opus agent "ui")

Files A may touch: `app/theme.py`, `app/components.py`, `app/app.py`, `app/tabs/{investigate,plan_trace,matcher_tab,catalog_tab,reports,sql_console,source_editor,onboarding,ocr_upload,watchlist}.py`, `.streamlit/config.toml`, `tests/test_theme.py`, `tests/test_tabs_render.py`, `tests/test_investigate_tab.py`, `tests/test_sql_console.py`, `tests/test_source_editor.py`, `tests/test_onboarding.py`, `tests/test_watchlist.py`, `tests/test_plan_trace.py`. **Do NOT touch** `app/tabs/self_check.py` or anything under `mediator/` (Workstream B owns them; Task C1 restyles self_check afterwards).

### Task A1: Port the Visibility token system into `app/theme.py`

**Files:** Modify `app/theme.py`, `.streamlit/config.toml`; Test `tests/test_theme.py`.

**Interfaces:** Produces `theme.TOKENS: dict`, `theme.inject()`, `theme.masthead(title, sub, eyebrow=None)` (existing names kept so app.py keeps working), CSS class vocabulary `.vz-h`, `.vz-lead`, `.vz-card`, `.vz-kpi`, `.vz-chip`, `.vz-table`, `.vz-step`, `.vz-grouplabel` (Task A2 builds on these).

- [x] **Step 1: Failing tests** — append to `tests/test_theme.py`:

```python
def test_primary_button_label_is_forced_light():
    css = theme._css()
    assert '[data-testid="stBaseButton-primary"] p' in css
    assert "#FFF6F1" in css

def test_markdown_grey_rule_excludes_buttons():
    css = theme._css()
    # the rule that greys body <p> must not reach button labels
    assert ':not([data-testid="stBaseButton-primary"])' in css or 'button p' in css

def test_tokens_are_visibility_system():
    assert theme.TOKENS["ink"].upper() == "#14213D"
    assert theme.TOKENS["accent"].upper() == "#E4572E"
    assert "Unbounded" in theme.FONTS["display"]
    assert "Atkinson Hyperlegible" in theme.FONTS["body"]

def test_dark_mode_block_present():
    css = theme._css()
    assert "prefers-color-scheme: dark" in css.replace(":dark", ": dark")
```

- [x] **Step 2: Run** `python -m pytest tests/test_theme.py -q` → FAIL (tokens still indigo).

- [x] **Step 3: Implement.** Rewrite the token block in `app/theme.py`:

```python
TOKENS = {
    "ground": "#F3F4F6",          # page ground (cool, slight ink bias)
    "plate": "rgba(252,252,250,.92)",  # card surface (AirSentinel "plate")
    "plate_line": "rgba(20,33,61,.10)",
    "ink": "#14213D", "ink2": "#3B4660", "ink_muted": "#5B6785",
    "accent": "#E4572E",          # the ONE accent ("beacon")
    "accent_ink": "#B03D18",      # text-safe accent, 5.99:1 on plate
    "accent_soft": "#FCEDE7",
    # status/decision colours are DATA colours, never reused for chrome
    "ok": "#1F7A4D", "timeout": "#B7791F", "down": "#B42318", "error": "#C2410C",
    "clear": "#1F7A4D", "report": "#B42318", "alert": "#7F1D1D",
    "suspicious": "#B7791F", "undetermined": "#5B6785", "unknown": "#3B4660",
    "shadow": "0 18px 42px -26px rgba(20,33,61,.36)",
    "shadow_lift": "0 26px 54px -24px rgba(20,33,61,.44)",
    "r_shell": "22px", "r_inner": "14px",
}
FONTS = {
    "display": '"Unbounded", "Outfit", system-ui, sans-serif',
    "body": '"Atkinson Hyperlegible Next", "Atkinson Hyperlegible", "Public Sans", system-ui, sans-serif',
    "mono": '"JetBrains Mono", ui-monospace, monospace',
    "import_url": "https://fonts.googleapis.com/css2?family=Unbounded:wght@500;700;800&family=Atkinson+Hyperlegible+Next:wght@400;500;700&family=JetBrains+Mono:wght@400;600&display=swap",
}
```

CSS rules that MUST exist (copy the rest of the shipped AirSentinel `_CSS` structure, adapted to `--vz-*` var names):

```css
:root { --vz-ink:#14213D; ... all TOKENS ... }
@media (prefers-color-scheme: dark) { :root { --vz-ground:#0F1626; --vz-plate:rgba(22,30,48,.92); --vz-plate-line:rgba(255,255,255,.10); --vz-ink:#EEF1F8; --vz-ink2:#C4CBDC; --vz-ink-muted:#9AA5BF; --vz-accent-soft:#3A1F17; } }
html, body, .stApp { background:var(--vz-ground); color:var(--vz-ink); font-family:var(--vz-font-body); font-size:1.0625rem; line-height:1.6; }
/* body prose grey — scoped so it never reaches button labels or headings */
[data-testid="stMarkdownContainer"] > p:not(.vz-h):not(.vz-lead) { color:var(--vz-ink2); }
button [data-testid="stMarkdownContainer"] p { color:inherit !important; }
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] { background:var(--vz-accent) !important; border-color:var(--vz-accent) !important; }
[data-testid="stBaseButton-primary"] p, [data-testid="stBaseButton-primaryFormSubmit"] p { color:#FFF6F1 !important; font-weight:700; }
[data-testid="stBaseButton-secondary"] { background:var(--vz-plate); border:1px solid var(--vz-plate-line); color:var(--vz-ink); }
[data-testid="stBaseButton-secondary"] p { color:var(--vz-ink) !important; font-weight:600; }
button:active { transform:scale(.98); } *:focus-visible { outline:3px solid var(--vz-accent); outline-offset:2px; }
@media (prefers-reduced-motion: reduce) { * { transition:none !important; } }
/* real section heading with beacon bar (AirSentinel .as-h) — no tiny uppercase eyebrows as headings */
.vz-h { font-family:var(--vz-font-display); font-size:clamp(1.5rem,2.2vw,1.9rem); font-weight:700; line-height:1.15; letter-spacing:-0.01em; color:var(--vz-ink); border-left:5px solid var(--vz-accent); padding-left:16px; margin:40px 0 12px; }
.vz-lead { max-width:70ch; color:var(--vz-ink2); font-size:1.0625rem; margin:0 0 16px; }
.vz-grouplabel { font-family:var(--vz-font-mono); font-size:.8rem; letter-spacing:.08em; text-transform:uppercase; color:var(--vz-ink-muted); margin:18px 0 6px; }
.vz-card { background:var(--vz-plate); border:1px solid var(--vz-plate-line); border-radius:var(--vz-r-inner); box-shadow:var(--vz-shadow); padding:18px 20px; backdrop-filter:blur(8px); transition:transform .25s cubic-bezier(.32,.72,0,1), box-shadow .25s; }
.vz-card:hover { transform:translateY(-2px); box-shadow:var(--vz-shadow-lift); }
.vz-kpi { display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; }
.vz-kpi .n { font-family:var(--vz-font-mono); font-variant-numeric:tabular-nums; font-size:1.9rem; font-weight:600; color:var(--vz-ink); }
.vz-kpi .l { font-size:.85rem; color:var(--vz-ink-muted); }
.vz-chip { display:inline-block; padding:3px 10px; border-radius:999px; font-family:var(--vz-font-mono); font-size:.8rem; font-weight:600; }
.vz-table { width:100%; border-collapse:separate; border-spacing:0; font-variant-numeric:tabular-nums; }
.vz-table th { font-family:var(--vz-font-mono); font-size:.75rem; letter-spacing:.06em; text-transform:uppercase; color:var(--vz-ink-muted); text-align:left; padding:8px 10px; border-bottom:1px solid var(--vz-plate-line); position:sticky; top:0; background:var(--vz-plate); }
.vz-table td { padding:8px 10px; border-bottom:1px solid var(--vz-plate-line); color:var(--vz-ink); }
.vz-table tr:nth-child(even) td { background:rgba(20,33,61,.025); }
.vz-step { display:flex; gap:14px; align-items:flex-start; padding:10px 0; border-left:2px solid var(--vz-plate-line); padding-left:16px; position:relative; }
.vz-step::before { content:""; position:absolute; left:-7px; top:16px; width:12px; height:12px; border-radius:50%; background:var(--vz-plate-line); }
.vz-step.done::before { background:var(--vz-accent); }
.vz-step .t { font-weight:700; color:var(--vz-ink); } .vz-step .d { color:var(--vz-ink2); font-size:.95rem; }
[data-baseweb="tab-highlight"] { background:var(--vz-accent) !important; }
[data-baseweb="tab"] p { font-weight:600; font-size:1rem; }
[data-testid="stMetricValue"] { font-family:var(--vz-font-mono); font-variant-numeric:tabular-nums; }
[data-testid="stSidebarContent"] { background:color-mix(in srgb, var(--vz-ground) 88%, var(--vz-ink)); }
[data-testid="stWidgetLabel"] p { color:var(--vz-ink); font-weight:600; }
```

Masthead: `masthead()` renders a `.vz-hero` block (display title 2.4rem, lead ≤70ch, soft accent-tinted gradient `linear-gradient(135deg, var(--vz-accent-soft), transparent 60%)` on a plate card). Keep `_legacy_aliases()` mapping old `.fm-*` class names to the new ones so any tab not yet migrated still renders.

`.streamlit/config.toml`: `primaryColor="#E4572E"`, `backgroundColor="#F3F4F6"`, `secondaryBackgroundColor="#FCFCFA"`, `textColor="#14213D"`, `font="sans serif"`.

- [x] **Step 4:** `python -m pytest tests/test_theme.py tests/test_tabs_render.py -q` → PASS.
- [x] **Step 5:** Commit `style: port AirSentinel Visibility tokens; fix primary button label contrast`.

### Task A2: Shared primitives in `app/components.py`

**Files:** Modify `app/components.py`; Test `tests/test_components.py` (create).

**Interfaces (Produces):**
```python
def section(title: str, lead: str | None = None) -> None          # .vz-h + optional .vz-lead
def group_label(text: str) -> None                                 # .vz-grouplabel
def kpi_row(items: list[tuple[str, str | int | float]]) -> None    # [(label, value)]
def chip(text: str, colour_hex: str) -> str                        # returns HTML; ink chosen by luminance
def styled_table(rows: list[dict], columns: list[str] | None = None, max_rows: int = 60) -> None
def stepper(steps: list[dict]) -> None   # [{"title":..,"detail":..,"done":bool}]
def card(html_body: str) -> None
def readable_ink(bg_hex: str) -> str     # "#FFFFFF" or TOKENS["ink"], WCAG relative luminance
```
Keep existing `decision_banner`, `source_chips`, `profile_sections`, `conflict_panel`, `provenance_table`, `risk_gauge`, `alert_banner`, `risk_placeholder` signatures; re-implement their internals on the new classes.

- [x] **Step 1: Failing tests** (`tests/test_components.py`):
```python
from app import components as c
def test_readable_ink_picks_white_on_dark():
    assert c.readable_ink("#14213D") == "#FFFFFF"
def test_readable_ink_picks_ink_on_light():
    assert c.readable_ink("#FCEDE7").upper() == "#14213D"
def test_chip_html_contains_text_and_class():
    html = c.chip("OK", "#1F7A4D")
    assert "vz-chip" in html and ">OK<" in html
def test_styled_table_escapes_html(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.styled_table([{"a": "<b>x</b>"}])
    assert "&lt;b&gt;" in out[0] and "vz-table" in out[0]
```
- [x] **Step 2:** run → FAIL (functions missing).
- [x] **Step 3:** implement (use `html.escape` in `styled_table`; `readable_ink` = relative luminance per WCAG, threshold contrast ≥ 4.5 preferring white).
- [x] **Step 4:** `python -m pytest tests/test_components.py tests/test_investigate_tab.py -q` → PASS.
- [x] **Step 5:** Commit `feat(ui): shared Visibility primitives (section, kpi_row, styled_table, stepper, chip)`.

### Task A3: Re-compose every tab on the primitives

**Files:** Modify the ten tab files listed above + `app/app.py`. Tests: existing `tests/test_tabs_render.py` etc. must stay green; add one assertion per tab that `section` heading text appears (AppTest `at.markdown` contains the title).

Rules for each tab (apply, don't reinterpret):
1. Exactly one `section(title, lead)` per logical block. Replace every `st.subheader`, `st.markdown("###…")`, `st.markdown("#####…")` and every `st.caption` used as a heading. `st.caption` stays only for true metadata (timestamps, row counts).
2. Never two headings adjacent. Never a heading followed by a caption that repeats it.
3. Controls: group under one `group_label` + one `st.columns` row; not one label per control.
4. Tables ≤ 60 rows → `styled_table`; bigger → `st.dataframe(use_container_width=True)`.
5. Status/decision colours only via `chip()`/`decision_banner`; never accent for status.
6. Body text ≥ 1rem; no `font-size` below 0.8rem anywhere.
7. `plan_trace.py` and `watchlist.py`: replace local `_chip_row` / raw `<span>` header with `components.chip`/`styled_table` (keep the unique-key workaround by passing a `key_prefix`).
8. `ocr_upload.py`: the "OCR candidates" and "Did you mean" captions become `group_label`s.
9. `app/app.py`: tab labels become `Investigate · Challan Guard · Watchlist · Reports · Citizen Check · Plan Trace · Catalog · Matcher · SQL Console` in that order. **Add the Challan Guard tab wired to `tabs.challan_guard.render()` guarded by `try/except ImportError` that renders `st.info("Challan Guard not installed yet")`** so A can commit before B lands. Sidebar Source Editor stays.
10. Masthead copy: title "Uninsured Vehicle Identification", lead "Live federated check across registration, insurance, police, camera and PUC records. Nothing is copied; every answer is fetched now."

- [x] Step 1: add the per-tab heading assertions to `tests/test_tabs_render.py` (fail: titles not present).
- [x] Step 2: migrate tabs one at a time; run `python -m pytest tests/test_tabs_render.py tests/test_investigate_tab.py tests/test_sql_console.py tests/test_source_editor.py tests/test_onboarding.py tests/test_watchlist.py tests/test_plan_trace.py -q` after each.
- [x] Step 3: `python -m pytest -q` full → green.
- [x] Step 4: Commit per 2–3 tabs: `style(ui): recompose <tabs> on Visibility primitives`.
- [x] Step 5: Visual check: `python run_system.py`, open http://localhost:8501, screenshot each tab to `docs/screenshots/<tab>.png` is optional; at minimum confirm primary buttons show light text.

---

## Workstream B — Challan Guard (owner: Opus agent "guard")

**Pain point (sourced, for the viva):** ANPR-driven e-challans are wrong often enough that Delhi Traffic Police launched an Online Challan Dispute System (2025); guides estimate ~90% of wrongful challans come from plate misreads (O/0, 8/B, 1/I). Hyderabad police busted a cloned-plate e-challan racket in 2025 ("one number, two scooters"). UK patent GB2448780A and Met Police ANPR use *impossible travel* (same plate, two cameras, implausible speed) to flag clones. Vahan Samanvay lags on stolen-then-recovered updates. The Supreme Court's 2025–26 order to auto-challan uninsured vehicles multiplies all of this. **Challan Guard sits between the camera and the fine: no challan is issued until identity, clone signal, theft status and insurance-at-sighting-time are all confirmed live across the federation; if a source is down, it refuses. Citizens can dispute and the case is re-verified live.**

Files B may touch: `mediator/catalog.py` (append only), `mediator/plate_resolve.py` (new), `mediator/travel_check.py` (new), `mediator/challan_guard.py` (new), `app/tabs/challan_guard.py` (new), `app/tabs/self_check.py` (add dispute section only), `data/inject_demo_fixtures.py`, `data/challan_candidates.json` (new), `scripts/seed_challan_cases.py` (new), `scripts/seed_mappings.py` + `data/gold_mapping.json` (add two CAM mappings), `tests/test_plate_resolve.py`, `tests/test_travel_check.py`, `tests/test_challan_guard.py`, `tests/test_challan_tab.py`. **Do NOT touch `app/app.py`, `app/theme.py`, `app/components.py` or other tabs** (Workstream A owns them). Use `st.*` directly in the tab for now; Task C1 restyles it.

### Task B1: Case store in meta.db

**Files:** Modify `mediator/catalog.py` (append tables in `init_meta_db`, add CRUD at end); Test `tests/test_challan_guard.py::TestStore`.

**Interfaces (Produces):**
```python
def challan_insert(case: dict) -> int            # returns case_id
def challan_get(case_id: int) -> dict | None
def challan_list(status: str | None = None, limit: int = 200) -> list[dict]
def challan_update(case_id: int, **fields) -> None
def challan_event(case_id: int, event: str, actor: str, evidence: dict | None = None) -> None
def challan_events(case_id: int) -> list[dict]
def challan_prior_issued(plate: str, exclude_case_id: int | None = None) -> int
```
Tables:
```sql
CREATE TABLE IF NOT EXISTS CHALLAN_CASES (
  case_id INTEGER PRIMARY KEY AUTOINCREMENT,
  plate_read TEXT NOT NULL, plate_resolved TEXT, camera_id TEXT, location TEXT,
  lat REAL, lon REAL, captured_at TEXT NOT NULL,
  observed_make TEXT, observed_colour TEXT, ocr_confidence REAL,
  status TEXT NOT NULL,          -- CANDIDATE | HOLD | ISSUED | REJECTED | DISPUTED | UPHELD | CANCELLED
  verdict TEXT, reason TEXT, amount_inr INTEGER,
  evidence_json TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS CHALLAN_EVENTS (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT, case_id INTEGER NOT NULL,
  event TEXT NOT NULL, actor TEXT NOT NULL, at TEXT NOT NULL, evidence_json TEXT);
```
- [x] Step 1: tests (insert → get; list by status; update; events ordered; prior_issued counts only ISSUED/UPHELD for the plate).
- [x] Step 2: FAIL. Step 3: implement (follow `watchlist_*` style; JSON via `json.dumps`). Step 4: PASS. Step 5: commit `feat(guard): challan case store in meta.db`.

### Task B2: `mediator/plate_resolve.py` — confusable-character identity resolution

**Interfaces (Produces):**
```python
CONFUSABLE = {"0": "OD", "O": "0D", "D": "0O", "1": "I", "I": "1", "8": "B", "B": "8", "5": "S", "S": "5", "2": "Z", "Z": "2", "6": "G", "G": "6"}
def candidates(plate_read: str, max_edits: int = 1, limit: int = 12) -> list[str]   # read plate first, then 1-char substitutions, dedup, canonical uppercase alnum
@dataclass class Candidate: plate: str; registered: bool; make: str | None; colour: str | None; score: float; reasons: list[str]
def score_candidate(cand: str, plate_read: str, reg_profile: dict, observed_make: str | None, observed_colour: str | None) -> Candidate
def resolve(plate_read: str, observed_make: str | None, observed_colour: str | None, lookup: Callable[[str], dict]) -> tuple[Candidate | None, list[Candidate], str]
    # returns (best, ranked, outcome) ; outcome ∈ {"RESOLVED","AMBIGUOUS","NONE"}
```
Scoring (explicit, defensible): registered required; `+0.5` make match (case-insensitive, `startswith` either way), `+0.3` colour match, `+0.2` if `cand == plate_read`. RESOLVED if best.score ≥ 0.5 and (only one registered candidate or best − second ≥ 0.2). `lookup(cand)` returns the integrated profile from `mediator.core.run_global_query(cand, ["vehicle_make","vehicle_colour","registration_status"])["profile"]` — pass it in so tests use a dict stub; production wiring in B4. Trade-off comment: substitutions limited to one edit and a fixed confusable table so the candidate set is small, deterministic, and explainable in a dispute (no fuzzy matching).

- [x] Step 1 tests: `candidates("DL05CD9B76")` contains `DL05CD9876`, excludes duplicates, ≤12; `resolve` with stub lookups: (a) misread B→8 resolves to DL05CD9876 when make/colour match; (b) two registered candidates both matching → AMBIGUOUS; (c) none registered → NONE.
- [x] Steps 2–5 as usual. Commit `feat(guard): confusable-character plate resolution`.

### Task B3: `mediator/travel_check.py` — impossible-travel clone signal

**Interfaces (Produces):**
```python
PLAUSIBLE_KMH = 160.0   # above any legal Indian highway speed with margin; tunable
def haversine_km(lat1, lon1, lat2, lon2) -> float
@dataclass class Leg: from_loc: str; to_loc: str; from_at: str; to_at: str; km: float; minutes: float; kmh: float; impossible: bool
def legs(sightings: list[dict]) -> list[Leg]   # sightings: {"location","lat","lon","at"(ISO)}, sorted by at; skip legs with missing lat/lon or Δt ≤ 0
def impossible_legs(sightings: list[dict], plausible_kmh: float = PLAUSIBLE_KMH) -> list[Leg]
```
- [x] Step 1 tests: Gurgaon CAM004 (28.4601,77.0648) 11:00 → Agra (27.1767,78.0081) 11:30 ⇒ ~170 km, ~340 km/h ⇒ impossible; same pair 3 h apart ⇒ plausible; missing lat ⇒ leg skipped.
- [x] Steps 2–5. Commit `feat(guard): impossible-travel clone check`.

### Task B4: `mediator/challan_guard.py` — the workflow

**Interfaces (Produces):**
```python
ISSUE, HOLD, REJECT = "ISSUE", "HOLD", "REJECT"
FINE_FIRST_INR, FINE_REPEAT_INR = 2000, 4000        # MV Act §196 (no insurance)
def new_candidate(plate_read, camera_id, location, lat, lon, captured_at, observed_make=None, observed_colour=None, ocr_confidence=None, actor="anpr") -> int
def verify(case_id: int, actor: str = "operator") -> dict     # runs steps, persists verdict, returns updated case (with "steps": list[{"title","detail","done"}])
def issue(case_id, actor) / reject(case_id, actor, reason) / hold(case_id, actor, reason) -> dict
def dispute(case_id: int, reason: str, actor: str = "citizen") -> dict   # re-runs verify live; -> CANCELLED if new verdict != ISSUE else UPHELD; evidence records what changed
def sightings_for(plate: str) -> list[dict]    # CAM rows via execute_federated_plan(["CAM"], plate) mapped through catalog.get_mappings_for_source("CAM") to global attrs last_seen_location/last_seen_time/camera_lat/camera_lon
```
`verify` steps, in order, each appended to `steps` and to CHALLAN_EVENTS:
1. **Sources reachable** — `run_global_query(plate_read)`; if any of REG/INS/THEFT status ≠ OK → verdict HOLD, reason `"<SRC> unreachable — refusing to fine on partial evidence"`. Stop.
2. **Identity** — `plate_resolve.resolve(...)` with lookup = `run_global_query(c, ["vehicle_make","vehicle_colour","registration_status"])`. NONE → REJECT `"no registered vehicle matches this read; route to unknown-vehicle check"`. AMBIGUOUS → HOLD listing candidates. RESOLVED → set `plate_resolved`; note `"misread corrected: <read> → <resolved>"` when different.
3. **Clone signal** — `sightings_for(plate_resolved)` + this capture → `impossible_legs`; any → HOLD `"impossible travel: <A> → <B> in <m> min (<kmh> km/h) — cloned plate suspected"`. Also `watchlist.add(plate, "CLONE SUSPECT (Challan Guard)")` if not watched.
4. **Theft** — full profile `run_global_query(plate_resolved)`; `stolen_status` truthy and `last_incident_date` ≤ captured date → REJECT `"vehicle reported stolen before sighting — route to police, do not fine owner"`; also `catalog.log_alert(...)`.
5. **Registration** — `registration_status` SCRAPPED/INVALID → REJECT with reason.
6. **Insurance at sighting time** — `insurance_expiry` ≥ captured date (date part) → REJECT `"insured on <date> (policy valid to <expiry>) — no offence"`; else ISSUE, `amount_inr = FINE_REPEAT_INR if challan_prior_issued(plate) else FINE_FIRST_INR`, reason `"uninsured on <date>: policy expired <expiry>"` or `"no policy on record"`.
Verdict ISSUE → status ISSUED (auto), HOLD → HOLD, REJECT → REJECTED. `evidence_json` stores the profile snapshot, candidates, legs, and the per-source statuses + fetched_at (this is the "proof bundle").

`dispute()`: requires status ISSUED; sets DISPUTED, re-runs the same steps on a fresh live query; if new verdict ≠ ISSUE → CANCELLED with reason `"record changed since issue: <field> was <old> now <new>"` (diff insurance_expiry / stolen_status / registration_status between old evidence and new), else UPHELD `"re-verified live: still uninsured on <date>"`.

- [x] Step 1 tests (`tests/test_challan_guard.py`, use the session `local_cluster` fixture from conftest; plates: DL05CD9876 expired policy 12/07/2026, DL01AB1234 clean, HR26EF4455 stolen, DL09KL3321 no policy):
  - misread `DL05CD9B76` at CAM004 2026-09-04T11:00 observed make/colour = DL05CD9876's REG values → ISSUED, plate_resolved DL05CD9876, amount 2000, steps show "misread corrected".
  - `DLO1AB1234` → REJECTED "insured".
  - HR26EF4455 → REJECTED, reason contains "stolen".
  - DL09KL3321 → ISSUED; then `scripts/mutate_source.py`-style admin `add_policy` on INS (via httpx to the local INS wrapper `/admin/mutate`) with expiry 2027-01-01 → `dispute()` → CANCELLED, reason contains "insurance_expiry".
  - travel: insert (via CAM `/admin/mutate` `sight`) a second capture for UP16GH1122 at CAM006 30 min later → HOLD, reason contains "impossible travel". (Requires B5 fixtures for CAM006; write test to add the camera row via `/admin/sql` if missing.)
  - stop INS wrapper (fixture provides a way, or monkeypatch `execute_federated_plan` statuses) → HOLD "unreachable".
- [x] Steps 2–5. Commit `feat(guard): Challan Guard verify / issue / dispute workflow`.

### Task B5: Fixtures, mappings, seed script

**Files:** `data/gold_mapping.json` + `scripts/seed_mappings.py` (add CAM `CAMERAS.lat → camera_lat`, `CAMERAS.lon → camera_lon`, score 1.0); `data/inject_demo_fixtures.py` (add cameras `CAM006 "Yamuna Expressway Toll, Agra" 27.1767 78.0081`, `CAM007 "Jaipur Bypass" 26.9124 75.7873`; add captures: `DL05CD9B76` CAM004 2026-09-04T11:00 conf .71 make/colour per DL05CD9876; `DLO1AB1234` CAM002 2026-09-04T08:30; `UP16GH1122` CAM006 2026-09-04T11:30 Kia Blue .93); `data/challan_candidates.json` (the five demo candidates: the three above + `HR26EF4455` CAM003 2026-09-02T19:45 + `DL09KL3321` CAM001 2026-09-04T07:10); `scripts/seed_challan_cases.py` (idempotent: skips if a CANDIDATE with same plate_read+captured_at exists; `--reset` deletes all cases). Also add `camera_lat`/`camera_lon` to the global attribute list in `mediator/schema.py` if that list is enforced anywhere (check `tests/test_planner_metadata.py`).
- [x] Tests: `tests/test_challan_guard.py::test_seed_is_idempotent`; `tests/test_planner_metadata.py` still green; `python scripts\evaluate_ground_truth.py` still 621/621 (new captures must not change any story decision — `DLO1AB1234` is the existing CAM story format for DL01AB1234 so it integrates as the same vehicle; the extra UP16GH1122 capture keeps make/colour conflict).
- [x] Commit `feat(guard): demo cameras, misread/clone captures, candidate seed`.

### Task B6: `app/tabs/challan_guard.py`

**Interfaces:** `render() -> None`. Layout (plain `st.*` now; C1 restyles):
1. KPIs: candidates, held, issued, rejected, cancelled-after-dispute ("wrongful fines prevented" = REJECTED + CANCELLED).
2. Queue: table of cases (case_id, read, resolved, where, when, status, verdict, amount) with a selectbox `key="cg_case"`; buttons `Verify` (`key="cg_verify"`), `Issue` / `Reject` / `Hold` (operator overrides, `key="cg_issue"` …) and `Seed demo candidates` (`key="cg_seed"`), `Refresh` (`key="cg_refresh"`).
3. Case detail: the verification steps as a vertical stepper (title + detail, done flag), evidence expander (JSON), events timeline.
4. "New candidate from a sighting" form (`key="cg_new_*"`): plate as read, camera select (from live CAM `/query` on CAMERAS via `execute_federated_plan`? no plate key — instead read cameras from the evidence of `sightings_for` is wrong; simplest: a static list from `data/challan_candidates.json` cameras + free text), captured_at, observed make/colour, confidence.
- [x] Test `tests/test_challan_tab.py` with AppTest: render without exceptions; after clicking seed then verify on first case, a stepper title "Identity" appears.
- [x] Commit `feat(guard): Challan Guard tab`.

### Task B7: Citizen dispute in `app/tabs/self_check.py`

Add a block "Dispute a challan": inputs case id (`key="sc_case_id"`), reason (`key="sc_reason"`), button `key="sc_dispute"` → `challan_guard.dispute(...)` → show outcome UPHELD/CANCELLED with reason and the "what changed" diff. Data-minimised: show only plate (masked middle 4 chars), sighting time/location, outcome. Test in `tests/test_self_check.py`.
- [x] Commit `feat(guard): citizen dispute loop`.

---

## Workstream C — Integration, docs, cross-laptop confirmation (after A and B)

### Task C1: Restyle Challan Guard + Citizen Check on the primitives; wire tab
- Replace `st.subheader`/captions in `app/tabs/challan_guard.py` and `self_check.py` with `section`, `kpi_row`, `styled_table`, `stepper`, `chip`. Remove the ImportError guard in `app/app.py`. Full suite green. Commit `style(ui): Challan Guard + Citizen Check on Visibility primitives`.

### Task C2: Cross-laptop write confirmation
- `docs/LAPTOP_SETUP.md`: add section "Open the wrapper port" with `netsh advfirewall firewall add rule name="IIA wrapper 8002" dir=in action=allow protocol=TCP localport=8002` (one per laptop/port) and the check from laptop 1: `python scripts\configure_cluster.py --probe` (must show `admin: on` per source — extend `--probe` to also GET `/admin/actions` and print `admin on/off/unreachable`). Test: `tests/test_serve.py` or a new small test asserting `--probe` output includes `admin`.
- Commit `feat(ops): probe reports admin reachability; firewall notes`.

### Task C3: Docs + viva
- `docs/FIELD_RESEARCH.md`: add facts 12–15 with these URLs: Delhi online challan dispute 2025 (https://fatehlegacy.in/blogs/contest-wrong-traffic-challan-delhi-2025), ANPR misread ~90% of wrong challans (https://mparivahanguide.com/wrong-challan-complaint/), Hyderabad clone-plate e-challan racket (https://the420.in/hyderabad-clone-number-plate-racket-busted-anpr-cctv/), impossible-travel patent GB2448780A (https://patents.google.com/patent/GB2448780A/en), Met Police ANPR (https://www.met.police.uk/advice/advice-and-information/rs/road-safety/automatic-number-plate-recognition-anpr/), Vahan Samanvay recovery lag (https://sudhirrao.com/how-to-remove-a-vehicle-from-the-ncrb/). Add a "Challan Guard" row to the feature table and two viva lines.
- `docs/demo_script.md`: new 5-minute segment "Challan Guard" with the six demo cases (misread→correct vehicle fined; misread→wrongful fine prevented; clone HOLD; stolen→police; DL09KL3321 issued → teammate on laptop 2 runs `python scripts\mutate_source.py INS add_policy DL09KL3321 --expiry 2027-01-01` → citizen disputes → CANCELLED; kill INS → HOLD).
- `README.md` + `STUDY_GUIDE.md`: one paragraph each on Challan Guard and the Visibility UI.
- Commit `docs: Challan Guard workflow, field research, demo script`.

## Outcome
(fill after execution: commit hashes, test count, screenshots)
