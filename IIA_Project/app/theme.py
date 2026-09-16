"""Design system for the mediator GUI — "Visibility", ported from the AirSentinel dashboard.

One place decides the whole product's look. The rules of the system, in the order they matter:

  * **One accent.** `accent` (#E4572E, the "beacon") means *interactive / this app* and nothing
    else. `accent_ink` is its text-safe tier (5.99:1 on the plate) for anything read small.
    Status and decision colours are DATA colours and are never reused for chrome.
  * **Real headings.** A section title is a 1.5–1.9rem display heading with a 5px beacon bar on
    its left (`.vz-h`), never a tiny uppercase eyebrow and never a caption.
  * **Frosted plates.** Every surface that carries text is a `.vz-card`: near-white, hairline
    border, soft shadow, 8px backdrop blur. Text never sits on raw ground.
  * **Numbers are numbers.** Metrics, KPIs and table cells are mono with tabular numerals.
  * **Nothing below 0.8rem** (pinned by `tests/test_theme.py`).

CSS variable names mirror the `TOKENS` keys (`ink_muted` → `--vz-ink-muted`), so a token added
here cannot be quietly missing from the stylesheet. `_legacy_aliases()` keeps the previous
`.fm-*` class/variable vocabulary alive for tabs that have not been recomposed yet.
"""
from __future__ import annotations

import streamlit as st

# ------------------------------------------------------------------ tokens

TOKENS: dict[str, str] = {
    "ground": "#F3F4F6",                # page ground (cool, slight ink bias)
    "plate": "rgba(252,252,250,.92)",   # card surface (AirSentinel "plate")
    "plate_line": "rgba(20,33,61,.10)",
    "ink": "#14213D", "ink2": "#3B4660", "ink_muted": "#5B6785",
    "accent": "#E4572E",                # the ONE accent ("beacon")
    "accent_ink": "#B03D18",            # text-safe accent, 5.99:1 on plate
    "accent_soft": "#FCEDE7",
    # status/decision colours are DATA colours, never reused for chrome
    "ok": "#1F7A4D", "timeout": "#B7791F", "down": "#B42318", "error": "#C2410C",
    "clear": "#1F7A4D", "report": "#B42318", "alert": "#7F1D1D",
    "suspicious": "#B7791F", "undetermined": "#5B6785", "unknown": "#3B4660",
    "shadow": "0 18px 42px -26px rgba(20,33,61,.36)",
    "shadow_lift": "0 26px 54px -24px rgba(20,33,61,.44)",
    "r_shell": "22px", "r_inner": "14px",
}

FONTS: dict[str, str] = {
    "display": '"Unbounded", "Outfit", system-ui, sans-serif',
    "body": ('"Atkinson Hyperlegible Next", "Atkinson Hyperlegible", "Public Sans", '
             "system-ui, sans-serif"),
    "mono": '"JetBrains Mono", ui-monospace, monospace',
    "import_url": ("https://fonts.googleapis.com/css2?family=Unbounded:wght@500;700;800"
                   "&family=Atkinson+Hyperlegible+Next:wght@400;500;700"
                   "&family=JetBrains+Mono:wght@400;600&display=swap"),
}

# Transport-layer outcome of a source call (mediator/executor.py statuses).
STATUS: tuple[str, ...] = ("ok", "timeout", "down", "error")
# Verdict of the decision engine (mediator/decide.py).
DECISION: tuple[str, ...] = ("clear", "report", "alert", "suspicious", "undetermined", "unknown")

# Solid fallbacks for browsers without color-mix(): the same hue at ~10% over the plate.
_TINT_FALLBACK = {
    "ok": "#ECF5F0", "timeout": "#FAF3E7", "down": "#FAEDEC", "error": "#FCF0E9",
    "clear": "#ECF5F0", "report": "#FAEDEC", "alert": "#F5EAEA",
    "suspicious": "#FAF3E7", "undetermined": "#F1F1F2", "unknown": "#EEF0F4",
}


# ------------------------------------------------------------- classifiers

def decision_class(decision: str) -> str:
    """Map a decision string from `mediator.decide` to a banner variant.

    Substring matching, not an enum: the decision engine owns its wording and adds new
    verdicts, so the GUI degrades to `undetermined` rather than raising on a string it has
    not seen.
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
    lines = [f"  --vz-{key.replace('_', '-')}:{value};" for key, value in TOKENS.items()]
    lines += [f'  --vz-font-{face}:{FONTS[face]};' for face in ("display", "body", "mono")]
    return "\n".join(lines)


def _dark() -> str:
    """Night tokens. Only the ground/plate/ink flip; the beacon and every data colour stay put,
    so a DOWN chip is the same red at 2 a.m. as at noon."""
    return """@media (prefers-color-scheme: dark) {
  :root, .stApp {
    --vz-ground:#0F1626; --vz-plate:rgba(22,30,48,.92);
    --vz-plate-line:rgba(255,255,255,.10); --vz-ink:#EEF1F8; --vz-ink2:#C4CBDC;
    --vz-ink-muted:#9AA5BF; --vz-accent-soft:#3A1F17; --vz-accent-ink:#FF8A5C;
    --vz-shadow:0 18px 42px -26px rgba(0,0,0,.60);
    --vz-shadow-lift:0 26px 54px -24px rgba(0,0,0,.70);
  }
  /* Surfaces Streamlit still paints LIGHT (config.toml pins its base theme to "light"):
     keep their text dark, or it turns light-on-light. */
  [data-testid="stAlert"], [data-testid="stAlert"] *, [data-baseweb="popover"],
  [data-baseweb="popover"] *, [data-baseweb="menu"] *, [data-testid="stTooltipContent"],
  [data-testid="stTooltipContent"] * {color:#1F2937;}
}"""


def _base() -> str:
    """Streamlit's own chrome, repainted in our tokens."""
    return """
html, body, .stApp, [data-testid="stAppViewContainer"] {background:var(--vz-ground);}
html, body, .stApp {color:var(--vz-ink); font-family:var(--vz-font-body);
  font-size:1.0625rem; line-height:1.6;}
html, body, [class*="css"], [data-testid="stMarkdownContainer"], label, input, select,
textarea, button, [data-baseweb="tab"] p {font-family:var(--vz-font-body);}
/* Body prose reads a touch quieter than a heading — scoped to direct children so it never
   reaches a button label, a chip, or our own heading classes. */
[data-testid="stMarkdownContainer"] > p:not(.vz-h):not(.vz-lead),
[data-testid="stMarkdownContainer"] > ul > li {color:var(--vz-ink2); font-size:1.0625rem;}
[data-testid="stMainBlockContainer"] [data-testid="stMarkdownContainer"] > p {max-width:78ch;}
button p, [role="button"] p, button [data-testid="stMarkdownContainer"] p
  {color:inherit !important;}
h1, h2, h3, h4, h5, [data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4, [data-testid="stMarkdownContainer"] h5 {
  font-family:var(--vz-font-display); font-weight:700; letter-spacing:-0.02em;
  color:var(--vz-ink);}
[data-testid="stMetricValue"], .vz-mono, .fm-mono, code, pre, [data-testid="stCode"] * {
  font-family:var(--vz-font-mono); font-variant-numeric:tabular-nums;}
[data-testid="stMetricValue"] {color:var(--vz-ink); font-weight:600;}
[data-testid="stMetricLabel"] p {color:var(--vz-ink-muted);}
[data-testid="stSidebarContent"] {
  background:color-mix(in srgb, var(--vz-ground) 88%, var(--vz-ink)); color:var(--vz-ink);}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {color:var(--vz-ink2);}
[data-baseweb="tab-highlight"] {background:var(--vz-accent) !important;}
[data-baseweb="tab-list"] {border-bottom:1px solid var(--vz-plate-line); gap:4px;}
[data-baseweb="tab"] p {font-weight:600; font-size:1rem; color:var(--vz-ink2);}
[data-baseweb="tab"][aria-selected="true"] p {color:var(--vz-ink);}
/* One primary action per section, in the beacon. `type="primary"` inside st.form renders as
   stBaseButton-primaryFormSubmit in Streamlit 1.57, so both ids carry the same rule. */
[data-testid="stBaseButton-primary"], [data-testid="stBaseButton-primaryFormSubmit"] {
  background:var(--vz-accent) !important; border-color:var(--vz-accent) !important;}
[data-testid="stBaseButton-primary"] p,
[data-testid="stBaseButton-primaryFormSubmit"] p {color:#FFF6F1 !important; font-weight:700;}
[data-testid="stBaseButton-secondary"], [data-testid="stBaseButton-secondaryFormSubmit"] {
  background:var(--vz-plate); border:1px solid var(--vz-plate-line); color:var(--vz-ink);}
[data-testid="stBaseButton-secondary"] p,
[data-testid="stBaseButton-secondaryFormSubmit"] p {color:var(--vz-ink) !important;
  font-weight:600;}
[data-testid="stDownloadButton"] button p {color:var(--vz-ink) !important; font-weight:600;}
input, textarea, [data-baseweb="select"] > div {font-family:var(--vz-font-body) !important;
  color:var(--vz-ink) !important; background:var(--vz-plate) !important;}
[data-testid="stWidgetLabel"] p {color:var(--vz-ink); font-weight:600; font-size:1rem;}
[data-testid="stCaptionContainer"] p {color:var(--vz-ink-muted); font-size:0.9375rem;}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border:1px solid var(--vz-plate-line); border-radius:var(--vz-r-inner); overflow:hidden;
  background:var(--vz-plate);}
button:active, [role="button"]:active {transform:scale(.98);}
*:focus-visible {outline:3px solid var(--vz-accent); outline-offset:2px;}
@media (prefers-reduced-motion: reduce) {* {transition:none !important; animation:none !important;}}
"""


def _primitives() -> str:
    """The class vocabulary `app/components.py` composes every tab from."""
    return """
/* A real section heading with the beacon bar — never a tiny uppercase eyebrow. */
.vz-h {font-family:var(--vz-font-display); font-size:clamp(1.5rem,2.2vw,1.9rem);
  font-weight:700; line-height:1.15; letter-spacing:-0.01em; color:var(--vz-ink);
  border-left:5px solid var(--vz-accent); padding-left:16px; margin:40px 0 12px;
  text-wrap:balance;}
.vz-lead {max-width:70ch; color:var(--vz-ink2); font-size:1.0625rem; margin:0 0 16px;}
.vz-grouplabel {font-family:var(--vz-font-mono); font-size:0.8rem; letter-spacing:.08em;
  text-transform:uppercase; color:var(--vz-ink-muted); margin:18px 0 6px;}
.vz-hero {background:var(--vz-plate); border:1px solid var(--vz-plate-line);
  border-radius:var(--vz-r-shell); box-shadow:var(--vz-shadow); backdrop-filter:blur(8px);
  padding:28px 32px 26px; margin:6px 0 26px;
  background-image:linear-gradient(135deg, var(--vz-accent-soft), transparent 60%);}
.vz-hero .eyebrow {font-family:var(--vz-font-mono); font-size:0.8rem; letter-spacing:.12em;
  text-transform:uppercase; color:var(--vz-accent-ink); margin:0 0 8px;}
.vz-hero .t {font-family:var(--vz-font-display); font-size:clamp(1.9rem,3.2vw,2.4rem);
  font-weight:800; letter-spacing:-0.03em; line-height:1.08; color:var(--vz-ink); margin:0;}
.vz-hero .s {color:var(--vz-ink2); font-size:1.0625rem; line-height:1.5; max-width:70ch;
  margin:10px 0 0;}
.vz-card {background:var(--vz-plate); border:1px solid var(--vz-plate-line);
  border-radius:var(--vz-r-inner); box-shadow:var(--vz-shadow); padding:18px 20px;
  margin:6px 0 16px; color:var(--vz-ink); backdrop-filter:blur(8px);
  transition:transform .25s cubic-bezier(.32,.72,0,1), box-shadow .25s;}
.vz-card:hover {transform:translateY(-2px); box-shadow:var(--vz-shadow-lift);}
.vz-card .vz-card-title {font-family:var(--vz-font-display); font-size:1.0625rem;
  font-weight:700; color:var(--vz-ink); margin:0 0 10px;}
.vz-row {display:flex; gap:10px; justify-content:space-between; padding:4px 0;
  font-size:0.9375rem;}
.vz-row .k {color:var(--vz-ink-muted);} .vz-row .v {color:var(--vz-ink); font-weight:600;
  text-align:right;}
.vz-kpi {display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px;
  margin:6px 0 18px;}
.vz-kpi .n {font-family:var(--vz-font-mono); font-variant-numeric:tabular-nums;
  font-size:1.9rem; font-weight:600; color:var(--vz-ink); line-height:1.1;}
.vz-kpi .l {font-size:0.85rem; color:var(--vz-ink-muted); margin-top:6px;}
.vz-chip {display:inline-block; padding:3px 10px; border-radius:999px;
  font-family:var(--vz-font-mono); font-size:0.8rem; font-weight:600; line-height:1.5;
  white-space:nowrap;}
.vz-chipstrip {display:flex; flex-wrap:wrap; gap:8px; margin:4px 0 14px;}
.vz-tablewrap {overflow-x:auto; margin:8px 0 16px; border-radius:var(--vz-r-inner);
  border:1px solid var(--vz-plate-line); background:var(--vz-plate);
  box-shadow:var(--vz-shadow);}
.vz-table {width:100%; border-collapse:separate; border-spacing:0;
  font-variant-numeric:tabular-nums; font-size:1rem;}
.vz-table th {font-family:var(--vz-font-mono); font-size:0.8rem; letter-spacing:.06em;
  text-transform:uppercase; color:var(--vz-ink-muted); text-align:left; padding:10px 14px;
  border-bottom:1px solid var(--vz-plate-line); position:sticky; top:0;
  background:var(--vz-plate); white-space:nowrap;}
.vz-table td {padding:10px 14px; border-bottom:1px solid var(--vz-plate-line);
  color:var(--vz-ink); vertical-align:middle;}
.vz-table tr:nth-child(even) td {background:rgba(20,33,61,.025);}
.vz-table tr:last-child td {border-bottom:none;}
.vz-step {display:flex; flex-direction:column; gap:2px; padding:10px 0;
  border-left:2px solid var(--vz-plate-line); padding-left:16px; position:relative;}
.vz-step::before {content:""; position:absolute; left:-7px; top:16px; width:12px; height:12px;
  border-radius:50%; background:var(--vz-plate-line);}
.vz-step.done::before {background:var(--vz-accent);}
.vz-step .t {font-weight:700; color:var(--vz-ink);}
.vz-step .d {color:var(--vz-ink2); font-size:0.95rem;}
.vz-bar {background:color-mix(in srgb, var(--vz-ink) 12%, transparent); border-radius:999px;
  height:12px; overflow:hidden; margin:6px 0 10px;}
.vz-bar i {display:block; height:100%; border-radius:999px;}
"""


def _fm_variants() -> str:
    """Tinted surface + left rule per semantic variant, for the legacy `.fm-*` vocabulary."""
    out = []
    for variant in (*DECISION, *STATUS):
        token = f"var(--vz-{variant})"
        tint = (f"  background:{_TINT_FALLBACK[variant]};\n"
                f"  background:color-mix(in srgb, {token} 10%, var(--vz-plate));\n"
                f"  border-left:6px solid {token};")
        if variant in DECISION:
            out.append(f".fm-banner--{variant} {{\n{tint}\n}}")
            out.append(f".fm-banner--{variant} .fm-banner-title {{color:{token};}}")
        if variant in STATUS:
            out.append(f".fm-chip--{variant} {{\n{tint}\n"
                       f"  border:1px solid color-mix(in srgb, {token} 30%, var(--vz-plate));\n"
                       f"  border-left:6px solid {token}; color:{token};\n}}")
            out.append(f".fm-chip--{variant} .fm-dot {{background:{token};}}")
    return "\n".join(out)


def _legacy_aliases() -> str:
    """The previous `.fm-*` / `--fm-*` vocabulary, re-pointed at the Visibility tokens.

    Kept rather than deleted because `app/tabs/self_check.py` (another workstream's file) and
    the Ministry-report HTML still speak it; deleting it would be an edit to someone else's
    module by other means.
    """
    return """
:root, .stApp {
  --fm-paper:var(--vz-plate); --fm-paper2:var(--vz-ground); --fm-ink:var(--vz-ink);
  --fm-ink_muted:var(--vz-ink-muted); --fm-rule:var(--vz-plate-line);
  --fm-accent:var(--vz-accent); --fm-accent_soft:var(--vz-accent-soft);
  --fm-display:var(--vz-font-display); --fm-body:var(--vz-font-body);
  --fm-mono:var(--vz-font-mono);
  --fm-status_ok:var(--vz-ok); --fm-status_timeout:var(--vz-timeout);
  --fm-status_down:var(--vz-down); --fm-status_error:var(--vz-error);
  --fm-decision_clear:var(--vz-clear); --fm-decision_report:var(--vz-report);
  --fm-decision_alert:var(--vz-alert); --fm-decision_suspicious:var(--vz-suspicious);
  --fm-decision_undetermined:var(--vz-undetermined); --fm-decision_unknown:var(--vz-unknown);
}
.fm-masthead {padding:0;} .fm-eyebrow {font-size:0.8rem;}
.fm-banner {padding:16px 20px; border-radius:var(--vz-r-inner); margin:6px 0 16px;
  color:var(--vz-ink); background:var(--vz-plate); border:1px solid var(--vz-plate-line);
  box-shadow:var(--vz-shadow);}
.fm-banner .fm-banner-title {font-family:var(--vz-font-display); font-size:1.25rem;
  font-weight:700; letter-spacing:-0.02em; margin:0;}
.fm-banner .fm-conf {font-family:var(--vz-font-mono); font-size:0.8125rem;
  color:var(--vz-ink); background:var(--vz-plate); border:1px solid var(--vz-plate-line);
  border-radius:999px; padding:2px 10px; margin-left:10px;}
.fm-banner ul {margin:10px 0 0 18px; padding:0;}
.fm-banner li {color:var(--vz-ink); font-size:0.9375rem; line-height:1.55;}
.fm-chip {display:inline-flex; align-items:center; gap:8px; padding:6px 12px;
  border-radius:8px; font-family:var(--vz-font-body); font-weight:600; font-size:0.875rem;
  line-height:1.2;}
.fm-chip .fm-dot {width:9px; height:9px; border-radius:50%; flex:0 0 9px;}
.fm-chip .fm-meta {font-family:var(--vz-font-mono); font-weight:400;
  color:var(--vz-ink-muted); font-size:0.8rem;}
.fm-card {background:var(--vz-plate); border:1px solid var(--vz-plate-line);
  border-radius:var(--vz-r-inner); padding:18px 20px; margin:6px 0 16px; color:var(--vz-ink);
  box-shadow:var(--vz-shadow);}
.fm-card h4, .fm-card .fm-card-title {font-family:var(--vz-font-display); font-size:1.0625rem;
  font-weight:700; color:var(--vz-ink); margin:0 0 10px;}
.fm-risk-bar {background:color-mix(in srgb, var(--vz-ink) 12%, transparent);}
.decision-banner-clear, .decision-banner-danger, .decision-banner-warn,
.decision-banner-unknown, .status-card-ok, .status-card-timeout, .status-card-down {
  padding:14px 18px; border-radius:var(--vz-r-inner); margin-bottom:14px;
  font-family:var(--vz-font-body); color:var(--vz-ink); background:var(--vz-plate);
  border:1px solid var(--vz-plate-line);}
.decision-banner-clear {border-left:6px solid var(--vz-clear);}
.decision-banner-danger {border-left:6px solid var(--vz-report);}
.decision-banner-warn {border-left:6px solid var(--vz-suspicious);}
.decision-banner-unknown {border-left:6px solid var(--vz-undetermined);}
.status-card-ok, .status-card-timeout, .status-card-down {text-align:center;}
.status-card-ok {border-left:6px solid var(--vz-ok);}
.status-card-timeout {border-left:6px solid var(--vz-timeout);}
.status-card-down {border-left:6px solid var(--vz-down);}
"""


def _css() -> str:
    return f"""<style>
@import url('{FONTS["import_url"]}');

:root, .stApp {{
{_vars()}
}}
{_dark()}
{_base()}
{_primitives()}
{_fm_variants()}
{_legacy_aliases()}
</style>"""


def inject() -> None:
    """Emit the whole stylesheet once. Safe to call on every rerun; Streamlit re-renders it."""
    st.markdown(_css(), unsafe_allow_html=True)


def masthead(title: str, subtitle: str, eyebrow: str | None = None) -> None:
    """The page header: a plate card washed with the accent, a display title, one lead line."""
    eyebrow_html = f'<p class="eyebrow">{eyebrow}</p>' if eyebrow else ""
    st.markdown(
        f'<div class="vz-hero fm-masthead">{eyebrow_html}'
        f'<p class="t fm-title">{title}</p>'
        f'<p class="s fm-sub">{subtitle}</p></div>',
        unsafe_allow_html=True,
    )
