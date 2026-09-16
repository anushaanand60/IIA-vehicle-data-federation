"""Design system for the mediator GUI — one place where the product's look is decided.

Ported from AirSentinel's dashboard theme (warm-paper ground, a single indigo accent, one
Google-Fonts import, tokens as CSS custom properties). Streamlit's own chrome is themed in
`.streamlit/config.toml`; this module themes *our* surfaces and gives the app three semantic
classes it cannot get natively: a decision banner, a source status chip and a card.

Two palettes, deliberately separate:
  * status  — did the source answer? (OK / TIMEOUT / DOWN / ERROR)
  * decision — what did the mediator conclude? (CLEAR … ALERT POLICE)
Colour never carries meaning alone: every banner and chip also prints the word.

CSS variable names mirror the `TOKENS` keys exactly (`--fm-status_ok`), so a token that is
added here cannot be quietly missing from the stylesheet.
"""
from __future__ import annotations

from typing import Any

import streamlit as st

# ------------------------------------------------------------------ tokens

TOKENS: dict[str, Any] = {
    # Ground and ink — warm paper rather than pure white; less glare on a projector.
    "paper": "#FBFAF7",
    "paper2": "#F2EFE8",
    "ink": "#1F2937",
    "ink_muted": "#4B5563",
    "rule": "#E3DFD6",
    # The one accent. Anything indigo on screen means "interactive / this app", never a verdict.
    "accent": "#4338CA",
    "accent_soft": "#EEF0FD",
    # Transport-layer outcome of a source call (mediator/executor.py statuses).
    "status": {
        "ok": "#1F7A4D",
        "timeout": "#B7791F",
        "down": "#B42318",
        "error": "#C2410C",
    },
    # Verdict of the decision engine (mediator/decide.py).
    "decision": {
        "clear": "#1F7A4D",
        "report": "#B42318",
        "alert": "#7F1D1D",
        "suspicious": "#B7791F",
        "undetermined": "#4B5563",
        "unknown": "#4338CA",
    },
}

FONTS: dict[str, str] = {
    "import_url": (
        "https://fonts.googleapis.com/css2?family=Outfit:wght@500;700;800"
        "&family=Public+Sans:wght@400;500;600;700"
        "&family=JetBrains+Mono:wght@400;500;700&display=swap"
    ),
    "display": '"Outfit", system-ui, sans-serif',
    "body": '"Public Sans", system-ui, sans-serif',
    "mono": '"JetBrains Mono", ui-monospace, monospace',
}

# Solid fallbacks for browsers without color-mix(): the same hue at ~10% over paper, pre-mixed.
_TINT_FALLBACK = {
    "ok": "#ECF5F0", "timeout": "#FAF3E7", "down": "#FAEDEC", "error": "#FCF0E9",
    "clear": "#ECF5F0", "report": "#FAEDEC", "alert": "#F5EAEA", "suspicious": "#FAF3E7",
    "undetermined": "#F1F1F2", "unknown": "#EFEFFA",
}


# ------------------------------------------------------------- classifiers

def decision_class(decision: str) -> str:
    """Map a decision string from `mediator.decide` to a banner variant.

    Substring matching, not an enum: the decision engine owns its wording and adds new
    verdicts (Task 2.5's escalation ladder), so the GUI degrades to `undetermined` rather
    than raising on a string it has not seen.
    """
    text = (decision or "").upper()
    if text.startswith("CLEAR"):
        return "clear"
    if any(word in text for word in ("STOLEN", "SHREDDED", "SCRAPPED")):
        return "alert"
    if "REPORT" in text:
        return "report"
    if "SUSPICIOUS" in text:
        return "suspicious"
    if "UNKNOWN VEHICLE" in text:
        return "unknown"
    return "undetermined"


def status_class(status: str) -> str:
    """Map an executor `SourceResult.status` to a chip variant. Unknown ⇒ treated as down."""
    name = (status or "").strip().lower()
    return name if name in TOKENS["status"] else "down"


# ------------------------------------------------------------------- css

def _vars() -> str:
    lines = []
    for key, value in TOKENS.items():
        if isinstance(value, dict):
            lines.extend(f"  --fm-{key}_{sub}:{val};" for sub, val in value.items())
        else:
            lines.append(f"  --fm-{key}:{value};")
    lines.append(f'  --fm-display:{FONTS["display"]};')
    lines.append(f'  --fm-body:{FONTS["body"]};')
    lines.append(f'  --fm-mono:{FONTS["mono"]};')
    return "\n".join(lines)


def _tinted(group: str, variant: str) -> str:
    """Background + left rule for a semantic surface, with a pre-mixed solid fallback first."""
    token = f"var(--fm-{group}_{variant})"
    return (
        f"  background:{_TINT_FALLBACK[variant]};\n"
        f"  background:color-mix(in srgb, {token} 10%, var(--fm-paper));\n"
        f"  border-left:6px solid {token};"
    )


def _banner_variants() -> str:
    out = []
    for variant in TOKENS["decision"]:
        out.append(f".fm-banner--{variant} {{\n{_tinted('decision', variant)}\n}}")
        out.append(f".fm-banner--{variant} .fm-banner-title {{color:var(--fm-decision_{variant});}}")
    return "\n".join(out)


def _chip_variants() -> str:
    out = []
    for variant in TOKENS["status"]:
        out.append(
            f".fm-chip--{variant} {{\n{_tinted('status', variant)}\n"
            f"  border:1px solid color-mix(in srgb, var(--fm-status_{variant}) 30%,"
            f" var(--fm-paper));\n"
            f"  border-left:6px solid var(--fm-status_{variant});\n"
            f"  color:var(--fm-status_{variant});\n}}"
        )
        out.append(f".fm-chip--{variant} .fm-dot {{background:var(--fm-status_{variant});}}")
    return "\n".join(out)


def _legacy_aliases() -> str:
    """Task 1.2 replaces app.py's hand-rolled banner/status HTML; until then it keeps working."""
    pairs = [
        (".decision-banner-clear", "decision", "clear"),
        (".decision-banner-danger", "decision", "report"),
        (".decision-banner-warn", "decision", "suspicious"),
        (".decision-banner-unknown", "decision", "undetermined"),
        (".status-card-ok", "status", "ok"),
        (".status-card-timeout", "status", "timeout"),
        (".status-card-down", "status", "down"),
    ]
    out = []
    for selector, group, variant in pairs:
        extra = "  text-align:center;\n" if selector.startswith(".status-card") else ""
        out.append(
            f"{selector} {{\n{_tinted(group, variant)}\n{extra}"
            f"  color:var(--fm-ink); padding:14px 18px; border-radius:10px;\n"
            f"  margin-bottom:14px; font-family:var(--fm-body);\n}}"
        )
        out.append(f"{selector} *, {selector} h3, {selector} b {{color:var(--fm-ink);}}")
    return "\n".join(out)


def _css() -> str:
    return f"""<style>
@import url('{FONTS["import_url"]}');

:root, .stApp {{
{_vars()}
}}

html, body, .stApp, [data-testid="stAppViewContainer"] {{background:var(--fm-paper);}}
html, body, [class*="css"], [data-testid="stMarkdownContainer"], label, button, input,
select, textarea, [data-baseweb="tab"] p {{font-family:var(--fm-body);}}
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li,
[data-testid="stMarkdownContainer"] td {{color:var(--fm-ink_muted);}}
h1, h2, h3, h4, h5, [data-testid="stMarkdownContainer"] h1,
[data-testid="stMarkdownContainer"] h2, [data-testid="stMarkdownContainer"] h3,
[data-testid="stMarkdownContainer"] h4, [data-testid="stMarkdownContainer"] h5 {{
  font-family:var(--fm-display); font-weight:700; letter-spacing:-0.02em;
  color:var(--fm-ink);}}
/* Numbers are read as numbers: metrics, code and our own .fm-mono are tabular mono. */
[data-testid="stMetricValue"], .fm-mono, code, pre, [data-testid="stCode"] * {{
  font-family:var(--fm-mono); font-variant-numeric:tabular-nums;}}
[data-testid="stMetricValue"] {{color:var(--fm-ink); font-weight:700;}}
[data-testid="stMetricLabel"] p {{color:var(--fm-ink_muted);}}
[data-testid="stSidebarContent"] {{background:var(--fm-paper2); color:var(--fm-ink);}}
[data-testid="stSidebar"] [data-testid="stMarkdownContainer"] p {{color:var(--fm-ink_muted);}}
/* One accent, product-wide: the tab underline is the only indigo that moves. */
[data-baseweb="tab-highlight"] {{background-color:var(--fm-accent) !important;}}
[data-baseweb="tab-list"] {{border-bottom:1px solid var(--fm-rule); gap:4px;}}
[data-baseweb="tab"] p {{font-weight:600; color:var(--fm-ink_muted);}}
[data-baseweb="tab"][aria-selected="true"] p {{color:var(--fm-ink);}}
button, [role="button"] {{font-family:var(--fm-body); font-weight:600;}}
[data-testid="stBaseButton-primary"] {{background:var(--fm-accent); border-color:var(--fm-accent);
  color:#FFFFFF;}}
[data-testid="stBaseButton-secondary"] {{background:var(--fm-paper); color:var(--fm-ink);
  border:1px solid var(--fm-rule);}}
input, textarea, [data-baseweb="select"] > div {{font-family:var(--fm-body) !important;
  color:var(--fm-ink) !important; background:var(--fm-paper) !important;}}
[data-testid="stWidgetLabel"] p {{color:var(--fm-ink_muted); font-weight:600;}}
:focus-visible {{outline:3px solid var(--fm-accent); outline-offset:2px;}}

/* ------------------------------------------------------------------ masthead */
.fm-masthead {{padding:6px 0 18px; border-bottom:1px solid var(--fm-rule); margin-bottom:18px;}}
.fm-eyebrow {{font-family:var(--fm-mono); font-size:0.75rem; letter-spacing:.14em;
  text-transform:uppercase; color:var(--fm-accent); margin-bottom:6px;}}
.fm-masthead .fm-title {{font-family:var(--fm-display); font-size:2rem; font-weight:800;
  letter-spacing:-0.03em; line-height:1.1; color:var(--fm-ink); margin:0;}}
.fm-masthead .fm-sub {{font-family:var(--fm-body); font-size:1rem; line-height:1.5;
  color:var(--fm-ink_muted); margin:6px 0 0; max-width:74ch;}}

/* -------------------------------------------------------- decision banners */
.fm-banner {{padding:16px 20px; border-radius:10px; margin:6px 0 16px; color:var(--fm-ink);}}
.fm-banner .fm-banner-title {{font-family:var(--fm-display); font-size:1.25rem; font-weight:700;
  letter-spacing:-0.02em; margin:0;}}
.fm-banner .fm-conf {{font-family:var(--fm-mono); font-size:0.8125rem; color:var(--fm-ink);
  background:var(--fm-paper); border:1px solid var(--fm-rule); border-radius:999px;
  padding:2px 10px; margin-left:10px;}}
.fm-banner ul {{margin:10px 0 0 18px; padding:0;}}
.fm-banner li {{color:var(--fm-ink); font-size:0.9375rem; line-height:1.55;}}
{_banner_variants()}

/* ------------------------------------------------------------ status chips */
.fm-chip {{display:inline-flex; align-items:center; gap:8px; padding:6px 12px;
  border-radius:8px; font-family:var(--fm-body); font-weight:600; font-size:0.875rem;
  line-height:1.2;}}
.fm-chip .fm-dot {{width:9px; height:9px; border-radius:50%; flex:0 0 9px;}}
.fm-chip .fm-meta {{font-family:var(--fm-mono); font-weight:400; color:var(--fm-ink_muted);}}
{_chip_variants()}

/* ------------------------------------------------------------------- cards */
.fm-card {{background:var(--fm-paper); border:1px solid var(--fm-rule); border-radius:12px;
  padding:18px 20px; margin:6px 0 16px; color:var(--fm-ink);
  box-shadow:0 12px 30px -26px rgba(31,41,55,.55);}}
.fm-card h4, .fm-card .fm-card-title {{font-family:var(--fm-display); font-size:1rem;
  font-weight:700; color:var(--fm-ink); margin:0 0 10px;}}
[data-testid="stDataFrame"] {{border:1px solid var(--fm-rule); border-radius:10px;
  overflow:hidden;}}

/* --------------------------------------------- legacy class aliases (Task 1.2 removes) */
{_legacy_aliases()}
</style>"""


def inject() -> None:
    """Emit the whole stylesheet once. Safe to call on every rerun; Streamlit re-renders it."""
    st.markdown(_css(), unsafe_allow_html=True)


def masthead(title: str, subtitle: str) -> None:
    """The page header: eyebrow, display title, one-line explanation of what the app does."""
    st.markdown(
        f'<div class="fm-masthead">'
        f'<div class="fm-eyebrow">GAV virtual integration</div>'
        f'<p class="fm-title">{title}</p>'
        f'<p class="fm-sub">{subtitle}</p>'
        f"</div>",
        unsafe_allow_html=True,
    )
