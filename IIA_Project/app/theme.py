"""Design system for the mediator GUI — "Civic": a government service that a citizen can read.

One place decides the whole product's look. The rules of the system, in the order they matter:

  * **One light theme, on every machine.** The previous skin flipped its own tokens under
    `@media (prefers-color-scheme: dark)` while Streamlit's chrome stayed light, so an operator
    whose laptop was in dark mode got dark labels on dark ground and white dataframes glaring on
    navy. There is no dark branch here: `:root` pins `color-scheme: light`, `.streamlit/
    config.toml` pins `base = "light"`, and the page looks identical whatever the OS says.
  * **One neutral base, one action colour.** Near-black ink on a light grey ground, white content
    panels, and `action` (#00703C) for the single primary action in a block. Status and decision
    colours are DATA colours and are never reused for chrome.
  * **Bounded space.** Every function sits in a `.cv-panel`: white, hairline border, generous
    padding, 24px of air below it. Text never sits on raw ground.
  * **Readable by default.** 19px body text, labels above inputs, 44px targets, a 3px yellow
    focus ring, and nothing below 0.9rem (pinned by `tests/test_theme.py`).

Reference discipline (not branding): the GOV.UK Design System — black text on light grey, one
green action button, yellow focus ring, generous vertical rhythm.

CSS variable names mirror the `TOKENS` keys (`ink_muted` → `--cv-ink-muted`), so a token added
here cannot be quietly missing from the stylesheet. `_legacy_aliases()` keeps the earlier `.fm-*`
and `.vz-*` class vocabulary alive, re-pointed at these tokens, for modules that still speak it.
"""
from __future__ import annotations

import streamlit as st

# ------------------------------------------------------------------ tokens

TOKENS: dict[str, str] = {
    "ground": "#F3F4F6",        # page ground
    "panel": "#FFFFFF",         # content panels
    "line": "#D9DEE5",          # 1px borders
    "ink": "#0B0C0C",           # body text (near-black, never pure #000)
    "ink2": "#3C4650",          # secondary text (7.5:1 on white)
    "ink_muted": "#5F6B76",     # hints, captions (5.3:1 on white — never below 0.9rem)
    "brand": "#0F2B4C",         # navy: sidebar, page header band, headings
    "brand2": "#1A4373",        # navy gradient end
    "action": "#00703C",        # THE ONE accent: primary buttons, active nav bar
    "action_hover": "#005A30",
    "link": "#1D70B8",
    "focus": "#FFDD00",         # focus ring, 3px, with a black inner edge
    "ok": "#00703C", "warn": "#B45309", "bad": "#B42318", "info": "#1D70B8",
    "neutral": "#5F6B76",
    "shadow": "0 8px 24px -16px rgba(15,43,76,.28)",   # tinted toward the ground, diffused
    "radius": "10px",
    # The data vocabulary — four transport statuses and six decision verdicts — drawn only from
    # the five status colours above. `alert` is the one exception: the gravest verdict (stolen /
    # shredded) gets a darker red than `report` so the two are not one indistinguishable red.
    # Every chip prints its word as well as its colour, so nothing rests on hue alone.
    "timeout": "#B45309", "down": "#B42318", "error": "#B45309",
    "clear": "#00703C", "report": "#B42318", "alert": "#8A1B12",
    "suspicious": "#B45309", "undetermined": "#5F6B76", "unknown": "#1D70B8",
}

FONTS: dict[str, str] = {
    "display": '"Geist", system-ui, sans-serif',
    "body": '"Geist", system-ui, sans-serif',
    "mono": '"Geist Mono", ui-monospace, monospace',
    "import_url": ("https://fonts.googleapis.com/css2?family=Geist:wght@400;500;600;700"
                   "&family=Geist+Mono:wght@400;600&display=swap"),
}

# Transport-layer outcome of a source call (mediator/executor.py statuses).
STATUS: tuple[str, ...] = ("ok", "timeout", "down", "error")
# Verdict of the decision engine (mediator/decide.py).
DECISION: tuple[str, ...] = ("clear", "report", "alert", "suspicious", "undetermined", "unknown")

# Tinted surfaces for banners and chips: the same hue at ~8% over white, stated as solid hex so
# a browser without color-mix() still gets the tint rather than a bare white box.
TINT = {
    "ok": "#E7F5EC", "clear": "#E7F5EC",
    "warn": "#FDF1E5", "timeout": "#FDF1E5", "error": "#FDF1E5", "suspicious": "#FDF1E5",
    "bad": "#FBE9E7", "down": "#FBE9E7", "report": "#FBE9E7", "alert": "#F8E4E1",
    "info": "#E8F1FA", "unknown": "#E8F1FA",
    "neutral": "#F1F2F4", "undetermined": "#F1F2F4",
}


# ------------------------------------------------------------- classifiers

def decision_class(decision: str) -> str:
    """Map a decision string from `mediator.decide` to a banner variant.

    Substring matching, not an enum: the decision engine owns its wording and adds new verdicts,
    so the GUI degrades to `undetermined` rather than raising on a string it has not seen.
    """
    text = (decision or "").upper()
    if text.startswith("CLEAR"):
        return "clear"
    if any(word in text for word in ("STOLEN", "SHREDDED", "SCRAPPED")):
        return "alert"
    if "ADVISORY" in text:
        return "suspicious"
    if "WARNING" in text or "REPORT" in text:
        return "report"
    if "SUSPICIOUS" in text:
        return "suspicious"
    if "UNKNOWN VEHICLE" in text:
        return "unknown"
    return "undetermined"


def status_class(status: str) -> str:
    """Map an executor `SourceResult.status` to a chip variant. Unknown ⇒ treated as down."""
    name = (status or "").strip().lower()
    return name if name in STATUS else "down"


# ------------------------------------------------------------------- css

def _vars() -> str:
    lines = [f"  --cv-{key.replace('_', '-')}:{value};" for key, value in TOKENS.items()]
    lines += [f'  --cv-font-{face}:{FONTS[face]};' for face in ("display", "body", "mono")]
    return "\n".join(lines)


def _base() -> str:
    """Streamlit's own chrome, repainted in our tokens — light, on every operating system.

    Trade-off: the 19px body size is set on `body`/`.stApp` and on the text elements themselves,
    never on `html`. Streamlit sizes its own internals in `rem`, so moving the root font size
    would rescale every control on the page instead of only the prose.
    """
    return """
:root {color-scheme: light;}
html, body, .stApp, [data-testid="stAppViewContainer"], [data-testid="stMain"],
[data-testid="stHeader"], [data-testid="stBottomBlockContainer"] {
  background:var(--cv-ground) !important;}
body, .stApp {color:var(--cv-ink); font-family:var(--cv-font-body); font-size:1.1875rem;
  line-height:1.5;}
html, body, [class*="css"], [data-testid="stMarkdownContainer"], label, input, select,
textarea, button {font-family:var(--cv-font-body);}
/* The reading column: wide enough for a table, narrow enough for a sentence. */
.block-container, [data-testid="stMainBlockContainer"] {
  max-width:1180px; padding:32px 40px 80px;}
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] > p,
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] > ul > li {
  color:var(--cv-ink); font-size:1.0625rem; line-height:1.55; max-width:70ch;}
button p, [role="button"] p, button [data-testid="stMarkdownContainer"] p {
  color:inherit !important; max-width:none;}
h1, h2, h3, h4, h5, [data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4 {
  font-family:var(--cv-font-display); font-weight:700; color:var(--cv-brand);
  letter-spacing:-0.01em;}
a, a:visited {color:var(--cv-link);}
a:hover {color:var(--cv-action-hover);}
code, pre, [data-testid="stCode"] *, [data-testid="stMetricValue"] {
  font-family:var(--cv-font-mono); font-variant-numeric:tabular-nums;}
[data-testid="stCode"], pre {background:var(--cv-ground) !important; color:var(--cv-ink);
  border:1px solid var(--cv-line); border-radius:6px;}
[data-testid="stMetricValue"] {color:var(--cv-brand); font-weight:600;}
[data-testid="stMetricLabel"] p {color:var(--cv-ink2); font-size:0.95rem;}
"""


def _sidebar() -> str:
    """Navy sidebar: the navigation menu and the cluster's health, nothing else."""
    return """
[data-testid="stSidebar"], [data-testid="stSidebarContent"] {
  background:linear-gradient(180deg, var(--cv-brand) 0%, var(--cv-brand2) 100%) !important;
  border-right:1px solid var(--cv-brand);}
[data-testid="stSidebar"] * {color:#FFFFFF;}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {color:#FFFFFF;
  font-size:1rem; max-width:none;}
[data-testid="stSidebar"] [data-testid="stWidgetLabel"] p,
[data-testid="stSidebar"] [data-testid="stCaptionContainer"] p {color:#DCE6F2;
  font-size:0.95rem;}
[data-testid="stSidebar"] [data-testid="stIconMaterial"] {color:#DCE6F2;}
/* The brand block at the top: a plate mark drawn in CSS, never an emoji. */
.cv-brand {display:flex; align-items:center; gap:12px; padding:4px 4px 18px;
  border-bottom:1px solid rgba(255,255,255,.18); margin-bottom:8px;}
.cv-brand .mark {flex:0 0 42px; height:30px; border-radius:5px; background:#FFFFFF;
  border:2px solid #0B0C0C; color:#0B0C0C; font-family:var(--cv-font-mono); font-weight:600;
  font-size:0.9rem; display:flex; align-items:center; justify-content:center;
  letter-spacing:.02em;}
.cv-brand .t {color:#FFFFFF; font-family:var(--cv-font-display); font-weight:700;
  font-size:1.05rem; line-height:1.25;}
.cv-brand .s {color:#DCE6F2; font-size:0.9rem; line-height:1.3; margin-top:2px;}
/* Navigation: one radio, drawn as a menu. The radio dot is visually hidden rather than
   removed, so the menu is still reachable and operable from the keyboard. */
[data-testid="stSidebar"] [role="radiogroup"] {gap:2px;}
[data-testid="stSidebar"] [role="radiogroup"] > label {position:relative; min-height:44px;
  display:flex; align-items:center; padding:0 14px; margin:0; border-radius:8px;
  transition:background 150ms ease;}
[data-testid="stSidebar"] [role="radiogroup"] > label > div:first-child {
  position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0 0 0 0); margin:0;
  padding:0; border:0;}
[data-testid="stSidebar"] [role="radiogroup"] > label p {color:#FFFFFF; font-size:1.05rem;
  font-weight:500;}
[data-testid="stSidebar"] [role="radiogroup"] > label:hover {background:rgba(255,255,255,.08);}
[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) {
  background:rgba(255,255,255,.12); box-shadow:inset 4px 0 0 var(--cv-action);}
[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:checked) p {font-weight:700;}
[data-testid="stSidebar"] [role="radiogroup"] > label:has(input:focus-visible) {
  outline:3px solid var(--cv-focus); outline-offset:1px;}
/* Group headings inside the single nav radio: Enforcement (1), Citizens (5), Mediator (6). */
.cv-navgroup, [data-testid="stSidebar"] [role="radiogroup"] > label::before {
  color:#A9C0DC; font-size:0.9rem; font-weight:600;}
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(1),
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(5),
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(6) {margin-top:38px;}
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(1) {margin-top:18px;}
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(1)::before {
  content:"Enforcement"; position:absolute; top:-24px; left:14px;}
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(5)::before {
  content:"Citizens"; position:absolute; top:-24px; left:14px;}
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(6)::before {
  content:"Mediator"; position:absolute; top:-24px; left:14px;}
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(5)::after,
[data-testid="stSidebar"] [role="radiogroup"] > label:nth-of-type(6)::after {
  content:""; position:absolute; top:-36px; left:0; right:0; height:1px;
  background:rgba(255,255,255,.18);}
"""


def _controls() -> str:
    """Buttons, inputs, tabs, alerts — the things an operator actually presses."""
    return """
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background:var(--cv-action) !important; border:2px solid transparent !important;
  box-shadow:0 2px 0 #002D18; border-radius:6px; min-height:44px; padding:8px 18px;}
[data-testid="stBaseButton-primary"] p,
[data-testid="stBaseButton-primaryFormSubmit"] p {color:#FFFFFF !important; font-weight:600;
  font-size:1rem;}
[data-testid="stBaseButton-primary"]:hover,
[data-testid="stBaseButton-primaryFormSubmit"]:hover {
  background:var(--cv-action-hover) !important;}
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"],
[data-testid="stDownloadButton"] button {
  background:var(--cv-panel) !important; border:2px solid var(--cv-ink) !important;
  border-radius:6px; min-height:44px; padding:8px 18px; color:var(--cv-ink) !important;}
[data-testid="stBaseButton-secondary"] p, [data-testid="stBaseButton-secondaryFormSubmit"] p,
[data-testid="stDownloadButton"] button p {color:var(--cv-ink) !important; font-weight:600;
  font-size:1rem;}
[data-testid="stBaseButton-secondary"]:hover, [data-testid="stDownloadButton"] button:hover {
  background:#EEF2F6 !important;}
button:active, [role="button"]:active {transform:translateY(1px);}
*:focus-visible {outline:3px solid var(--cv-focus) !important; outline-offset:0;
  box-shadow:0 0 0 5px var(--cv-ink) inset;}
input, textarea, [data-baseweb="select"] > div, [data-baseweb="input"] {
  background:var(--cv-panel) !important; color:var(--cv-ink) !important;
  border-color:var(--cv-ink) !important; border-radius:4px;}
[data-baseweb="input"], [data-baseweb="select"] > div, [data-baseweb="textarea"] {
  border-width:2px !important; min-height:44px;}
[data-testid="stWidgetLabel"] p {color:var(--cv-ink); font-weight:600; font-size:1rem;}
[data-testid="stCaptionContainer"] p {color:var(--cv-ink-muted); font-size:0.95rem;
  max-width:70ch;}
[data-baseweb="popover"], [data-baseweb="menu"], [data-baseweb="popover"] *,
[data-baseweb="menu"] *, [data-testid="stTooltipContent"], [data-testid="stTooltipContent"] * {
  color:var(--cv-ink);}
[data-baseweb="popover"] ul, [data-baseweb="menu"] {background:var(--cv-panel);}
[data-testid="stExpander"] {background:var(--cv-panel); border:1px solid var(--cv-line);
  border-radius:var(--cv-radius);}
[data-testid="stExpander"] summary p, [data-testid="stExpander"] summary {
  color:var(--cv-ink); font-weight:600; font-size:1rem;}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border:1px solid var(--cv-line); border-radius:var(--cv-radius); overflow:hidden;
  background:var(--cv-panel);}
[data-testid="stTable"] td, [data-testid="stTable"] th {color:var(--cv-ink);
  font-size:1rem; background:var(--cv-panel);}
[data-testid="stAlert"], [data-testid="stAlert"] * {color:var(--cv-ink);}
[data-testid="stAlertContentInfo"] {background:#E8F1FA; border-left:5px solid var(--cv-info);}
[data-testid="stAlertContentSuccess"] {background:#E7F5EC; border-left:5px solid var(--cv-ok);}
[data-testid="stAlertContentWarning"] {background:#FDF1E5; border-left:5px solid var(--cv-warn);}
[data-testid="stAlertContentError"] {background:#FBE9E7; border-left:5px solid var(--cv-bad);}
[data-baseweb="tab-highlight"] {background:var(--cv-action) !important;}
[data-baseweb="tab"] p {color:var(--cv-ink); font-weight:600; font-size:1rem;}
@media (prefers-reduced-motion: reduce) {
  * {transition:none !important; animation:none !important;}}
"""


def _primitives() -> str:
    """The class vocabulary `app/components.py` composes every page from."""
    return """
/* The page header band: every page states its own name and its one-sentence purpose. */
.cv-pagehead {background:linear-gradient(120deg, var(--cv-brand) 0%, var(--cv-brand2) 70%);
  color:#FFFFFF; border-radius:14px; padding:28px 32px; margin:0 0 28px;
  box-shadow:var(--cv-shadow);}
.cv-pagehead .t {font-family:var(--cv-font-display); font-size:2rem; font-weight:700;
  line-height:1.15; color:#FFFFFF; margin:0;}
.cv-pagehead .s {color:#DCE6F2; font-size:1.125rem; line-height:1.5; max-width:70ch;
  margin:10px 0 0;}
/* A real section heading. No left bar, no uppercase eyebrow, no caption pretending to be one. */
.cv-h {font-family:var(--cv-font-display); font-size:1.5rem; font-weight:700; line-height:1.2;
  color:var(--cv-brand); margin:40px 0 8px; text-wrap:balance;}
.cv-lead {max-width:70ch; color:var(--cv-ink2); font-size:1.0625rem; line-height:1.55;
  margin:0 0 20px;}
.cv-grouplabel {font-size:0.95rem; font-weight:600; color:var(--cv-ink); margin:20px 0 6px;}
.cv-navgroup {margin:18px 0 6px;}
/* Every function gets bounded space: one panel, 24px of air under it. `components.panel()`
   opens a keyed container, so the rule is keyed too rather than hung on a Streamlit test id
   that every layout block in the page shares. */
[class*="st-key-cvp_"] {background:var(--cv-panel); border:1px solid var(--cv-line);
  border-radius:var(--cv-radius); box-shadow:var(--cv-shadow); padding:24px 28px;
  margin-bottom:24px;}
[class*="st-key-cvp_"] .cv-h:first-child, [class*="st-key-cvp_"] [data-testid="stVerticalBlock"]
  > [data-testid="stElementContainer"]:first-child .cv-h {margin-top:0;}
.cv-panel {background:var(--cv-panel); border:1px solid var(--cv-line);
  border-radius:var(--cv-radius); box-shadow:var(--cv-shadow); padding:24px 28px;
  margin:0 0 24px; color:var(--cv-ink);}
.cv-panel .cv-panel-title {font-family:var(--cv-font-display); font-size:1.0625rem;
  font-weight:700; color:var(--cv-brand); margin:0 0 10px;}
.cv-row {display:flex; gap:12px; justify-content:space-between; padding:6px 0;
  border-bottom:1px solid var(--cv-line); font-size:1rem;}
.cv-row:last-child {border-bottom:none;}
.cv-row .k {color:var(--cv-ink2);}
.cv-row .v {color:var(--cv-ink); font-weight:600; text-align:right;}
.cv-kpi {display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:16px;
  margin:4px 0 8px;}
.cv-kpi > div {background:var(--cv-panel); border:1px solid var(--cv-line);
  border-radius:var(--cv-radius); padding:18px 20px;}
.cv-kpi .n {font-family:var(--cv-font-mono); font-variant-numeric:tabular-nums; font-size:2rem;
  font-weight:700; color:var(--cv-brand); line-height:1.15;}
.cv-kpi .l {font-size:0.95rem; color:var(--cv-ink2); margin-top:6px;}
.cv-chip {display:inline-flex; align-items:center; gap:8px; min-height:26px; padding:2px 12px;
  border-radius:999px; font-size:0.9rem; font-weight:600; line-height:1.4; white-space:nowrap;}
.cv-chipstrip {display:flex; flex-wrap:wrap; gap:8px; margin:4px 0 8px;}
.cv-tablewrap {overflow-x:auto; margin:4px 0 8px; border:1px solid var(--cv-line);
  border-radius:var(--cv-radius); background:var(--cv-panel);}
.cv-table {width:100%; border-collapse:separate; border-spacing:0;
  font-variant-numeric:tabular-nums;}
.cv-table th {background:var(--cv-ground); color:var(--cv-ink); font-weight:600;
  font-size:0.95rem; text-align:left; padding:12px 14px; border-bottom:1px solid var(--cv-line);
  position:sticky; top:0; white-space:nowrap;}
.cv-table td {padding:12px 14px; border-bottom:1px solid var(--cv-line); color:var(--cv-ink);
  font-size:1rem; vertical-align:middle;}
.cv-table tbody tr:nth-child(even) td {background:#FAFBFC;}
.cv-table tbody tr:last-child td {border-bottom:none;}
.cv-step {display:flex; flex-direction:column; gap:2px; padding:10px 0 10px 20px;
  border-left:2px solid var(--cv-line); position:relative;}
.cv-step::before {content:""; position:absolute; left:-7px; top:16px; width:12px; height:12px;
  border-radius:50%; background:var(--cv-line);}
.cv-step.done::before {background:var(--cv-action);}
.cv-step .t {font-size:1.05rem; font-weight:600; color:var(--cv-ink);}
.cv-step .d {font-size:1rem; color:var(--cv-ink2);}
.cv-bar {background:#E6E9EE; border-radius:999px; height:12px; overflow:hidden; margin:6px 0 12px;}
.cv-bar i {display:block; height:100%; border-radius:999px;}
.cv-banner {padding:18px 22px; border-radius:var(--cv-radius); margin:0 0 16px;
  color:var(--cv-ink); background:var(--cv-panel); border:1px solid var(--cv-line);}
.cv-banner .cv-banner-title {font-family:var(--cv-font-display); font-size:1.25rem;
  font-weight:700; color:var(--cv-ink); margin:0;}
.cv-banner ul {margin:10px 0 0 18px; padding:0;}
.cv-banner li {color:var(--cv-ink); font-size:1rem; line-height:1.55;}
.cv-conf {font-family:var(--cv-font-mono); font-size:0.9rem; color:var(--cv-ink);
  background:var(--cv-ground); border:1px solid var(--cv-line); border-radius:999px;
  padding:2px 10px; margin-left:10px;}
"""


def _variants() -> str:
    """Tinted surface + left bar per semantic variant, for banners and status chips."""
    out = []
    for variant in (*DECISION, *STATUS):
        token = f"var(--cv-{variant})"
        tint = TINT[variant]
        if variant in DECISION:
            out.append(f".cv-banner--{variant}, .fm-banner--{variant} {{background:{tint};"
                       f" border-left:5px solid {token};}}")
        if variant in STATUS:
            out.append(f".cv-chip--{variant}, .fm-chip--{variant} {{background:{tint};"
                       f" border:1px solid {token}; color:var(--cv-ink);}}")
            out.append(f".fm-chip--{variant} .fm-dot {{background:{token};}}")
    return "\n".join(out)


def _legacy_aliases() -> str:
    """The earlier `.fm-*` / `.vz-*` vocabulary, re-pointed at the civic tokens.

    `app/tabs/self_check.py` and the filed Ministry-report HTML still speak it; aliasing keeps
    them looking like the rest of the page instead of rewriting another workstream's markup.
    """
    return """
.vz-card, .fm-card {background:var(--cv-panel); border:1px solid var(--cv-line);
  border-radius:var(--cv-radius); padding:20px 22px; margin:0 0 16px; color:var(--cv-ink);}
.vz-card .vz-card-title, .fm-card h4, .fm-card .fm-card-title {
  font-family:var(--cv-font-display); font-size:1.0625rem; font-weight:700;
  color:var(--cv-brand); margin:0 0 10px;}
.vz-row {display:flex; gap:12px; justify-content:space-between; padding:6px 0; font-size:1rem;}
.vz-row .k {color:var(--cv-ink2);} .vz-row .v {color:var(--cv-ink); font-weight:600;
  text-align:right;}
.vz-kpi {display:grid; grid-template-columns:repeat(auto-fit,minmax(190px,1fr)); gap:16px;}
.vz-kpi .n {font-family:var(--cv-font-mono); font-size:2rem; font-weight:700;
  color:var(--cv-brand);}
.vz-kpi .l {font-size:0.95rem; color:var(--cv-ink2); margin-top:6px;}
.vz-h {font-family:var(--cv-font-display); font-size:1.5rem; font-weight:700;
  color:var(--cv-brand); margin:40px 0 8px;}
.vz-lead {max-width:70ch; color:var(--cv-ink2); font-size:1.0625rem; margin:0 0 20px;}
.vz-table, .fm-table {width:100%; border-collapse:separate; border-spacing:0;}
.vz-tablewrap {overflow-x:auto; border:1px solid var(--cv-line);
  border-radius:var(--cv-radius); background:var(--cv-panel);}
.vz-table th {background:var(--cv-ground); color:var(--cv-ink); font-weight:600;
  font-size:0.95rem; text-align:left; padding:12px 14px;
  border-bottom:1px solid var(--cv-line);}
.vz-table td {padding:12px 14px; border-bottom:1px solid var(--cv-line); color:var(--cv-ink);
  font-size:1rem;}
.vz-step {display:flex; flex-direction:column; padding:10px 0 10px 20px;
  border-left:2px solid var(--cv-line);}
.vz-step .t {font-weight:600; color:var(--cv-ink);} .vz-step .d {color:var(--cv-ink2);}
.vz-bar, .fm-risk-bar {background:#E6E9EE; border-radius:999px; height:12px; overflow:hidden;}
.vz-bar i {display:block; height:100%; border-radius:999px;}
.vz-chip, .fm-chip {display:inline-flex; align-items:center; gap:8px; min-height:26px;
  padding:2px 12px; border-radius:999px; font-size:0.9rem; font-weight:600; line-height:1.4;}
.fm-chip .fm-dot {width:9px; height:9px; border-radius:50%; flex:0 0 9px;}
.fm-chip .fm-meta {font-family:var(--cv-font-mono); font-weight:400; font-size:0.9rem;
  color:var(--cv-ink2);}
.fm-banner {padding:18px 22px; border-radius:var(--cv-radius); margin:0 0 16px;
  color:var(--cv-ink); background:var(--cv-panel); border:1px solid var(--cv-line);}
.fm-banner .fm-banner-title {font-family:var(--cv-font-display); font-size:1.25rem;
  font-weight:700; color:var(--cv-ink); margin:0;}
.fm-banner .fm-conf {font-family:var(--cv-font-mono); font-size:0.9rem; color:var(--cv-ink);
  background:var(--cv-ground); border:1px solid var(--cv-line); border-radius:999px;
  padding:2px 10px; margin-left:10px;}
.fm-banner ul {margin:10px 0 0 18px; padding:0;}
.fm-banner li {color:var(--cv-ink); font-size:1rem; line-height:1.55;}
.decision-banner-clear, .decision-banner-danger, .decision-banner-warn,
.decision-banner-unknown, .status-card-ok, .status-card-timeout, .status-card-down {
  padding:16px 20px; border-radius:var(--cv-radius); margin-bottom:16px; color:var(--cv-ink);
  background:var(--cv-panel); border:1px solid var(--cv-line);}
.decision-banner-clear {border-left:5px solid var(--cv-clear);}
.decision-banner-danger {border-left:5px solid var(--cv-report);}
.decision-banner-warn {border-left:5px solid var(--cv-suspicious);}
.decision-banner-unknown {border-left:5px solid var(--cv-undetermined);}
.status-card-ok {border-left:5px solid var(--cv-ok);}
.status-card-timeout {border-left:5px solid var(--cv-timeout);}
.status-card-down {border-left:5px solid var(--cv-down);}
"""


def _css() -> str:
    return f"""<style>
@import url('{FONTS["import_url"]}');

:root, .stApp {{
{_vars()}
}}
{_base()}
{_sidebar()}
{_controls()}
{_primitives()}
{_variants()}
{_legacy_aliases()}
</style>"""


def inject() -> None:
    """Emit the whole stylesheet once. Safe to call on every rerun; Streamlit re-renders it."""
    st.markdown(_css(), unsafe_allow_html=True)


def masthead(title: str, subtitle: str, eyebrow: str | None = None) -> None:
    """Deprecated: the single page-wide masthead. Task D2 replaces it with
    `components.page_head`, which every page renders with its own title and purpose."""
    st.markdown(
        f'<div class="cv-pagehead"><p class="t">{title}</p>'
        f'<p class="s">{subtitle}</p></div>',
        unsafe_allow_html=True,
    )
