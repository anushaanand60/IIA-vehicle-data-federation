<!-- The shared brief given to the four case-file coding agents (2026-11-14), verbatim except redacted paths. -->

# Case-file redesign — shared brief for all four workstreams

Repo: `<home>\IIA-vehicle-data-federation\IIA_Project` (Windows; use Git Bash or PowerShell).
Python: `.venv/Scripts/python.exe`. Streamlit 1.63, plotly 7.0.

## Why
A federated mediator over six autonomous agency databases (REG, INS, THEFT, CAM, PUC, DOCS). Read
`docs/phase3/WHAT_WAS_BUILT.md` and `docs/phase3/CONTRACT.md` first. The user, a student, opened the Context
Explorer, typed a plate, and saw a jumble: documents with no explanation of what they were, documents that could not
be opened, five tabs of database jargon. The Context Evaluation page was unclear. We are turning the Explorer into a
single-scroll **case file** and the Evaluation page into a short **Proof** page, and adding novel, explainable
features. The work is graded (Information Integration course). Every mechanism must be explainable out loud to a
professor.

## Page order of the new Explorer (WS4 owns the shell; others supply render functions)
1. Search box (unchanged behaviour).
2. Header: plate + identity card (make/model/colour/owner from the registration row) + verdict + robustness line.
3. "What happened": the grounded summary (cited sentences).
4. Timeline (WS2).
5. Warning signs.
6. Documents (WS1) + claim matrix (WS1).
7. "How it was found": why-is-this-here, leads not followed, graph (WS3 + existing graph).
8. Receipt: the cost of this query (WS3).
Free-text queries ("stolen white Creta Gurgaon") still show ranked vehicles with an "Open" button, as now.

## The seam (do not change these names)
`app/tabs/context_flow.run_query(text)` returns a dict `res` with keys:
`query, parsed, plate, context (ContextResult | None), indicators (list[Indicator] | None), docs (DocResult | None),
summary, topk, missing, problems, elapsed_ms`. Types are in `mediator/context_types.py`.

Render functions the Explorer calls. Each takes `res`, must **never raise** (catch, then show one quiet caption), and
must render nothing harmful when `res["context"]` or `res["docs"]` is None:
- `app/tabs/context_docs.py`: `render_documents(res)`, `render_claims(res)` (WS1)
- `app/tabs/context_timeline.py`: `render_timeline(res)` (WS2)
- `app/tabs/context_receipt.py`: `render_robustness(res)` (one compact line or chip row for the header),
  `render_why(res)`, `render_receipt(res)` (WS3)

Expensive derived data may be cached in `res` itself under a key starting with `_` (e.g. `res["_timeline"]`).
`res` is per query; that is not a cache of source data.

New optional fields already exist in `mediator/context_types.py` (do not edit that file; ask me if you need more):
- `ContextRow.affinity`, `ContextRow.edge_weights`
- `ContextResult.pruned_leads` (list of dicts: target, attr, value, bound, from, round)
- `RetrievedDoc.body`, `issuer`, `issued_on`, `why`, `relevant`

## Rules (all workstreams)
- **Touch only the files your workstream owns.** If you need a change elsewhere, say so in your final report. Do not
  commit; I commit. Do not stop or restart running servers (wrappers on 8001–8006, Streamlit on 8501).
- **Change markers.** In an existing file, every changed line sits between `# CONTEXT-ADDED (Rn: reason)` and
  `# CONTEXT-END`. A replaced line goes under `# CONTEXT-REPLACED (Rn: reason):` with the old lines commented out,
  then `# CONTEXT-END`. Comment old code out; never delete it. New files say `CONTEXT-ADDED (Rn: ...)` in their
  module docstring. Rn = R2 (context discovery), R3 (documents) or R2/R3.
- **Metadata-driven.** Table, column and time-format knowledge comes from `data/context_graph.json` (see
  `mediator/doc_retrieval.load_graph`, `column_kind`; `mediator/doc_conflicts.to_date`). Do not hard-code source
  column names in new mediator modules unless an existing module already does the same thing.
- **No caching of source data, no writes to sources, no new central store.** The local LLM (`mediator/llm.py`,
  Ollama) is optional, never writes SQL, and every feature must work with it off.
- Modules under ~200 lines, type hints, and a one-line comment wherever a trade-off is not obvious.
- **UI language is plain.** Never show `pk`, `B0`, `θ`, `τ`, `nDCG`, raw enum codes like `SURVEYOR_REPORT`, or table
  names as the main label. Use "Police FIR", "Surveyor report", "Insurance policy POL-000562", "Camera sighting".
  Technical detail may sit inside an expander or popover. No clutter: one idea per block, generous spacing.
- **Styling.** Use the existing design system: `app/theme.py` (`TOKENS`, `FONTS`) and `app/components.py`
  (`page_head`, `panel`, `section`, `kpi_row`, `styled_table`). Look at `app/tabs/context_widgets.py` for the
  source colours and names (`SOURCE_NAMES`, `source_colour`, `SOURCE_ORDER`, `chip`, `esc`, `highlight`). Widget keys
  carry your module's prefix. Charts use plotly with the theme tokens and `config={"displayModeBar": False}`.
- **Tests.** Write focused pytest tests for the new mediator logic (pure functions, no network) and a smoke test of
  your render functions with `streamlit.testing.v1.AppTest` where practical. Run only your own tests plus
  `tests/test_context_pages.py` (the full suite takes 9 minutes; I run it). Live sources are up on 127.0.0.1:8001–8006
  if you want to try real data: `from mediator.query_understanding import parse`,
  `from app.tabs.context_flow import run_query` (add `app` to `sys.path`).
- **Redaction.** No email addresses or passwords anywhere.

## Demo plates (use them in tests and examples)
| Plate | Story |
|---|---|
| DL05CD9876 | expired policy; PUC lapsed; accident report; surveyor pre-inspection |
| MH12EF0248 | ALIBI: PUC test while reported stolen; FIR narrative D00260 says "recovered" while the FIR is OPEN |
| HR26EF4455 | reported stolen, case open |
| UP16GH1122 | clone: REG says Hyundai Creta white, camera saw Venue silver |
| UP16CD0391 | quick claim (theft claim days after policy start) |
| DL01GH0599 | transfer shortly before a loss |

## Final report (≤ 300 words)
Files changed, what each new feature does, test results (the command and pass count), anything you could not do,
and any change you need in a file you do not own.
