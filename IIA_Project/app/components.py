"""Presentation components for the mediator GUI — one vocabulary, reused by every page.

Every function here is *pure render*: it takes data the mediator already produced (a
`VEHICLE_PROFILE`, a `PLAN_TRACE["sources_detail"]`, a provenance dict) and draws it. Nothing in
this module queries a source, decides anything, or mutates session state — that separation is what
lets the same banner appear in Investigate, Reports and the citizen self-check without three
slightly different truths on screen.

Look and colour come from `app/theme.py` — the "Civic" system: `.cv-pagehead` bands, `.cv-h`
headings, `.cv-panel` panels, `.cv-chip` pills, `.cv-table`, `.cv-step`, and the CSS custom
properties `--cv-*`. The lower half of this module is the domain vocabulary (decision banner,
source chips, profile cards, risk gauge); the upper half is the generic vocabulary every page
composes from. Semantic markup still carries its old `.fm-*` class alongside the new one, because
`app/tabs/self_check.py` and the filed reports still speak it.

Layout rule of the re-skin: every function on a page sits inside `panel()`, which opens a keyed
Streamlit container that `theme.py` paints white with a hairline border and 24px of air beneath.
Keys are how the stylesheet finds it — `[class*="st-key-cvp_"]` — because the Streamlit test id
for a layout block is shared by every column, expander and container on the page.

Text colour is always stated explicitly through the tokens; inheriting it is what produced the
old light-on-light banners. A colour that carries meaning is paired with `readable_ink` /
`readable_fill` so the label on it clears WCAG AA, and the word is always printed too.

Two display conventions, applied everywhere:
  * a value the source did not carry renders as an em dash, never the string "None";
  * a *derived* status that is `None` renders "not asked" — the integrator leaves
    `insurance_status` / `stolen_status` as `None` when that source was never in the plan, and a
    question we never asked must not read as an answer.
"""
from __future__ import annotations

import html
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator

_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:  # `streamlit run` puts only app/ on sys.path
    sys.path.insert(0, str(_ROOT))
_APP_DIR = Path(__file__).resolve().parent
if str(_APP_DIR) not in sys.path:
    sys.path.insert(0, str(_APP_DIR))

import pandas as pd  # noqa: E402
import streamlit as st  # noqa: E402

from theme import TOKENS, decision_class, status_class  # noqa: E402

DASH = "—"
NOT_ASKED = "not asked"

# The two inks a coloured chip can carry. Nothing else: two is what makes a page feel like one
# system rather than a colour picker.
_INKS = ("#FFFFFF", TOKENS["ink"])

# A verdict is named in words, never signalled by colour alone (WCAG 1.4.1). No emoji: this is a
# government service, and an emoji renders differently on every machine in the room.
_DECISION_WORD = {
    "clear": "Cleared",
    "report": "Report",
    "alert": "Alert",
    "suspicious": "Advisory",
    "unknown": "Not on record",
    "undetermined": "Undetermined",
}


# ------------------------------------------------------------------ formatting

def fmt(value: Any) -> str:
    """A source value as text. Missing renders as an em dash — "None" is Python, not evidence."""
    if value is None:
        return DASH
    text = str(value).strip()
    return text if text else DASH


def fmt_derived(value: Any) -> str:
    """A derived status. `None` means the source was never asked, which is not the same as absent."""
    return NOT_ASKED if value is None else fmt(value)


def _esc(value: Any) -> str:
    return html.escape(fmt(value))


# ------------------------------------------------------------ colour arithmetic

def _rgb(hex_colour: str) -> tuple[int, int, int]:
    h = hex_colour.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]


def _rel_lum(hex_colour: str) -> float:
    """WCAG relative luminance of an sRGB hex colour."""
    channels = [v / 255 for v in _rgb(hex_colour)]
    channels = [x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4 for x in channels]
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def _contrast(bg_hex: str, ink_hex: str) -> float:
    a, b = _rel_lum(bg_hex), _rel_lum(ink_hex)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def readable_ink(bg_hex: str) -> str:
    """Whichever of the two inks contrasts more with this fill — white wins ties."""
    return max(_INKS, key=lambda ink: _contrast(bg_hex, ink))


def readable_fill(bg_hex: str) -> tuple[str, str]:
    """(fill, ink) for a chip carrying small text on a data colour.

    Some published status colours clear 4.5:1 against neither ink, so the fill is lifted toward
    white in 5% steps until one of them does. It stays unmistakably the same hue; it just becomes
    legible. Returned rather than baked in, so a caller that only needs the ink can ask for that.
    """
    base = _rgb(bg_hex)
    for step in range(0, 51, 5):
        candidate = "#%02X%02X%02X" % tuple(round(c + (255 - c) * step / 100) for c in base)
        ink = readable_ink(candidate)
        if _contrast(candidate, ink) >= 4.5:
            return candidate, ink
    return bg_hex, readable_ink(bg_hex)


# ------------------------------------------------------- the Civic primitives

def page_head(title: str, lead: str) -> None:
    """The gradient band at the top of a page: its name, and one sentence saying what it is for.

    Every page renders its own, so an operator who lands on a page from the menu is never asked
    to infer what it does from its controls.
    """
    st.markdown(
        f'<div class="cv-pagehead"><p class="t">{html.escape(title)}</p>'
        f'<p class="s">{html.escape(lead)}</p></div>',
        unsafe_allow_html=True,
    )


@contextmanager
def panel(key: str) -> Iterator[None]:
    """One function, one bounded white panel. `with panel("queue"): ...`

    A keyed container, because `theme.py` styles it through the `st-key-` class Streamlit puts on
    it; the test id for a layout block is shared with every column and expander on the page and
    cannot be targeted on its own. Keys are namespaced `cvp_` and must be unique per page.
    """
    with st.container(key=f"cvp_{key}"):
        yield


def section(title: str, lead: str | None = None) -> None:
    """One logical block = one real heading (+ at most one lead line).

    Emitted as a single markdown call so a section can never become "heading, then a caption
    repeating it" — the pattern this re-skin exists to remove.
    """
    lead_html = f'<p class="cv-lead">{html.escape(lead)}</p>' if lead else ""
    st.markdown(f'<p class="cv-h">{html.escape(title)}</p>{lead_html}', unsafe_allow_html=True)


def group_label(text: str) -> None:
    """A quiet label over a *group* of controls or chips. Never a section heading."""
    st.markdown(f'<p class="cv-grouplabel">{html.escape(text)}</p>', unsafe_allow_html=True)


def nav_group_label(text: str) -> None:
    """A heading over one group of pages in the sidebar menu."""
    st.sidebar.markdown(f'<div class="cv-navgroup">{html.escape(text)}</div>',
                        unsafe_allow_html=True)


def kpi_row(items: list[tuple[str, Any]]) -> None:
    """Headline figures as one responsive grid of plates; numbers in tabular mono."""
    if not items:
        return
    cells = "".join(
        f'<div><div class="n">{html.escape(str(value))}</div>'
        f'<div class="l">{html.escape(str(label))}</div></div>'
        for label, value in items)
    st.markdown(f'<div class="cv-kpi vz-kpi">{cells}</div>', unsafe_allow_html=True)


def chip(text: str, colour_hex: str) -> str:
    """A filled pill in a data colour, inked for legibility. Returns HTML; the caller places it.

    Carries the legacy `fm-chip` class too, so pages this workstream has not recomposed keep
    their look.
    """
    fill, ink = readable_fill(colour_hex)
    return (f'<span class="cv-chip fm-chip" style="background:{fill};color:{ink};'
            f'border:1px solid rgba(11,12,12,.18);">{html.escape(str(text))}</span>')


def chip_strip(chips: list[str]) -> None:
    """A wrapping row of `chip()` HTML — one markdown call, so no container key is needed."""
    if not chips:
        return
    st.markdown(f'<div class="cv-chipstrip">{"".join(chips)}</div>', unsafe_allow_html=True)


def card(html_body: str) -> None:
    """A white plate around caller-built HTML. The caller owns escaping inside it."""
    st.markdown(f'<div class="cv-panel">{html_body}</div>', unsafe_allow_html=True)


def styled_table(rows: list[dict], columns: list[str] | None = None,
                 max_rows: int = 60) -> None:
    """THE table renderer for small result sets: one look, hairline rows, tabular numerals.

    HTML rather than `st.dataframe` because a canvas dataframe is a slab of its own that cannot
    carry the page's header row, zebra striping or type scale. Every value is escaped: these are
    live source rows, not our strings.
    """
    if not rows:
        return
    cols = columns or list(dict.fromkeys(k for row in rows for k in row))
    shown = rows[:max_rows]
    head = "".join(f"<th>{html.escape(str(c))}</th>" for c in cols)
    body = "".join(
        "<tr>" + "".join(f"<td>{html.escape(fmt(row.get(c)))}</td>" for c in cols) + "</tr>"
        for row in shown)
    st.markdown(
        f'<div class="cv-tablewrap"><table class="cv-table"><thead><tr>{head}</tr></thead>'
        f"<tbody>{body}</tbody></table></div>",
        unsafe_allow_html=True)
    if len(rows) > max_rows:
        st.caption(f"Showing the first {max_rows} of {len(rows)} rows.")


def stepper(steps: list[dict]) -> None:
    """A vertical run of steps, each a title + detail, filled when done."""
    if not steps:
        return
    body = "".join(
        f'<div class="cv-step{" done" if step.get("done") else ""}">'
        f'<span class="t">{html.escape(str(step.get("title", "")))}</span>'
        f'<span class="d">{html.escape(str(step.get("detail", "")))}</span></div>'
        for step in steps)
    st.markdown(body, unsafe_allow_html=True)


# ------------------------------------------------------------- decision banner

def decision_banner(profile: dict) -> None:
    """The verdict: the word, the decision string, confidence, and the reasons it rests on."""
    profile = profile or {}
    decision = str(profile.get("decision") or "UNDETERMINED")
    confidence = str(profile.get("confidence") or "LOW")
    variant = decision_class(decision)
    reasons = [r for r in (profile.get("reasons") or []) if str(r).strip()]
    items = "".join(f"<li>{html.escape(str(r))}</li>" for r in reasons)
    st.markdown(
        f'<div class="cv-banner fm-banner cv-banner--{variant} fm-banner--{variant}">'
        f'<p class="cv-banner-title fm-banner-title">{_DECISION_WORD.get(variant, "Undetermined")}'
        f' — Decision: {html.escape(decision)}'
        f'<span class="cv-conf fm-conf">Confidence: {html.escape(confidence)}</span></p>'
        f"{f'<ul>{items}</ul>' if items else ''}"
        f"</div>",
        unsafe_allow_html=True,
    )


# ---------------------------------------------------------------- source chips

def _chip(source_id: str, detail: dict) -> str:
    variant = status_class(str(detail.get("status") or ""))
    status_word = str(detail.get("status") or "DOWN").upper()
    elapsed = detail.get("elapsed_ms")
    rows = detail.get("row_count")
    elapsed_txt = DASH if elapsed is None else f"{elapsed} ms"
    rows_txt = DASH if rows is None else f"{rows} rows"
    return (
        f'<span class="cv-chip fm-chip cv-chip--{variant} fm-chip--{variant}">'
        f'<span class="fm-dot"></span>'
        f"{html.escape(source_id)} · {html.escape(status_word)}"
        f'<span class="fm-meta">{html.escape(elapsed_txt)} · {html.escape(rows_txt)}</span></span>'
    )


def _not_asked_chip(source_id: str) -> str:
    """A catalogued source the planner left out — shown, muted, so the plan is legible."""
    muted = ("border:1px dashed var(--cv-line);background:var(--cv-ground);"
             "color:var(--cv-ink2);")
    return (
        f'<span class="cv-chip fm-chip" style="{muted}">'
        f'<span class="fm-dot" style="background:var(--cv-neutral);"></span>'
        f"{html.escape(source_id)} · NOT ASKED"
        f'<span class="fm-meta">{NOT_ASKED}</span></span>'
    )


def _catalogued_ids() -> list[str]:
    try:
        from mediator.catalog import get_source_catalog

        return list(get_source_catalog().keys())
    except Exception:  # the chips row must never be the thing that breaks the page
        return []


def source_chips(sources_detail: dict) -> None:
    """One row: every source that answered, plus the catalogued ones this query did not need."""
    sources_detail = sources_detail or {}
    asked = list(sources_detail.keys())
    skipped = [s for s in _catalogued_ids() if s not in sources_detail]
    if not asked and not skipped:
        return
    with st.container(horizontal=True, key="fm_chip_row"):
        for source_id in asked:
            st.markdown(_chip(source_id, sources_detail.get(source_id) or {}),
                        unsafe_allow_html=True)
        for source_id in skipped:
            st.markdown(_not_asked_chip(source_id), unsafe_allow_html=True)


# -------------------------------------------------------------- profile cards

def _card(title: str, rows: list[tuple[str, str]]) -> str:
    body = "".join(
        f'<div class="cv-row vz-row"><span class="k">{html.escape(label)}</span>'
        f'<span class="v">{value}</span></div>'
        for label, value in rows
    )
    return (f'<div class="cv-panel fm-card"><p class="cv-panel-title fm-card-title">'
            f"{html.escape(title)}</p>{body}</div>")


def profile_sections(profile: dict) -> None:
    """The integrated `VEHICLE_PROFILE`, grouped by the authority that contributed each block."""
    profile = profile or {}
    make_model = " ".join(p for p in (fmt(profile.get("vehicle_make")),
                                      str(profile.get("vehicle_model") or "").strip()) if p)
    observed = " ".join(p for p in (fmt(profile.get("observed_make")),
                                    str(profile.get("observed_model") or "").strip()) if p)
    seen_time = str(profile.get("last_seen_time") or "")[:19] or None

    c1, c2, c3 = st.columns(3)
    c1.markdown(_card("Registration (REG)", [
        ("Plate", _esc(profile.get("plate_number"))),
        ("Owner", _esc(profile.get("owner_name"))),
        ("Make / model", html.escape(make_model or DASH)),
        ("Colour", _esc(profile.get("vehicle_colour"))),
        ("Registered on", _esc(profile.get("registration_date"))),
        ("Status", _esc(profile.get("registration_status"))),
    ]), unsafe_allow_html=True)

    c2.markdown(_card("Insurance (INS)", [
        ("Insurer", _esc(profile.get("insurer_name"))),
        ("Policy type", _esc(profile.get("policy_type"))),
        ("Start date", _esc(profile.get("insurance_start"))),
        ("Expiry date", _esc(profile.get("insurance_expiry"))),
        ("Derived status", _derived_html(profile.get("insurance_status"), good=("VALID",))),
    ]), unsafe_allow_html=True)

    c3.markdown(_card("Sighting and crime (CAM / THEFT)", [
        ("Theft status", _derived_html(profile.get("stolen_status"),
                                       good=("NOT_REPORTED", "RECOVERED"))),
        ("Case status", _esc(profile.get("case_status"))),
        ("Last seen location", _esc(profile.get("last_seen_location"))),
        ("Last seen time", _esc(seen_time)),
        ("Observed vehicle", html.escape(observed or DASH)),
        ("Observed colour", _esc(profile.get("observed_colour"))),
    ]), unsafe_allow_html=True)

    if profile.get("puc_expiry"):  # PUC joined the cluster at demo time (UC6); only show it then
        st.markdown(_card("Pollution certificate (PUC)", [
            ("Valid upto", _esc(profile.get("puc_expiry"))),
        ]), unsafe_allow_html=True)


def _derived_html(value: Any, good: tuple[str, ...]) -> str:
    """Derived statuses carry a verdict, so they get the decision palette — with the word."""
    if value is None:
        return (f'<span style="color:var(--cv-ink-muted);font-style:italic;">'
                f"{NOT_ASKED}</span>")
    text = str(value).upper()
    if text == "UNKNOWN":
        token = "var(--cv-down)"
    elif text in good:
        token = "var(--cv-clear)"
    else:
        token = "var(--cv-report)"
    return f'<span style="color:{token};font-weight:600;">{html.escape(text)}</span>'


# ------------------------------------------------------------- conflicts

def conflict_panel(conflicts: list[dict] | None) -> None:
    """Disagreements between an authoritative and an observational source, and how they were settled."""
    conflicts = conflicts or []
    if not conflicts:
        return
    st.error(f"{len(conflicts)} attribute conflict(s) detected across sources")
    for conflict in conflicts:
        attribute = str(conflict.get("attribute", "")).upper()
        by_source = conflict.get("values_by_source") or {}
        stated = " vs ".join(f"`{sid}` = `{fmt(val)}`" for sid, val in by_source.items())
        st.markdown(
            f"- **{attribute}**: {stated} — resolved by *{fmt(conflict.get('resolution'))}* "
            f"→ selected `{fmt(conflict.get('chosen'))}`"
        )


# ------------------------------------------------------------- provenance

def provenance_table(provenance: dict | None) -> None:
    """Which source each attribute came from, with its authority, trust and fetch time."""
    provenance = provenance or {}
    with st.expander("Per-attribute provenance and trust metadata"):
        if not provenance:
            st.caption("No attribute was contributed by any source in this query.")
            return
        st.dataframe(
            pd.DataFrame([
                {
                    "Attribute": attribute,
                    "Source": info.get("source"),
                    "Authority": info.get("authority"),
                    "Trust score": info.get("trust"),
                    "Fetched at": info.get("fetched_at"),
                }
                for attribute, info in provenance.items()
            ]),
            width="stretch",
            hide_index=True,
        )


# ------------------------------------------------------------- risk

_RISK_TOKEN = {
    "LOW": "var(--cv-clear)",
    "MEDIUM": "var(--cv-suspicious)",
    "HIGH": "var(--cv-report)",
    "CRITICAL": "var(--cv-alert)",
}


def risk_gauge(risk: dict | None) -> None:
    """The 0–100 risk score as a bar, with the factors that add up to it.

    The factor list is not decoration: `mediator/risk.py` guarantees the points sum exactly to the
    value, so the bar can be read as arithmetic rather than as an opaque index. An empty gauge
    would be a claim, so nothing renders when there is no score.
    """
    if not risk:
        return
    value = int(risk.get("value") or 0)
    level = str(risk.get("level") or "LOW").upper()
    token = _RISK_TOKEN.get(level, "var(--cv-neutral)")
    factors = risk.get("factors") or []
    rows = "".join(
        f'<div class="cv-row vz-row"><span class="k">{html.escape(str(f.get("name", "")))}'
        f'<span style="display:block;font-size:0.95rem;color:var(--cv-ink-muted);">'
        f'{html.escape(str(f.get("note", "")))}</span></span>'
        f'<span class="v" style="font-family:var(--cv-font-mono);">'
        f'{int(f.get("points", 0)):+d}</span></div>'
        for f in factors
    )
    st.markdown(
        f'<div class="cv-panel fm-card">'
        f'<p class="cv-panel-title fm-card-title">Risk score {value}/100 · '
        f"{html.escape(level)}</p>"
        f'<div class="cv-bar fm-risk-bar">'
        f'<i style="width:{max(0, min(100, value))}%;background:{token};"></i>'
        f"</div>{rows}</div>",
        unsafe_allow_html=True,
    )


def alert_banner(alerts: list[dict] | None) -> None:
    """Watchlist / hotlist hits raised by this query, with the evidence that came back with them."""
    alerts = alerts or []
    if not alerts:
        return
    items = "".join(
        "<li>"
        f'<strong>{html.escape(fmt(a.get("plate")))}</strong> — {html.escape(fmt(a.get("reason")))}'
        f' · seen at {html.escape(fmt(a.get("location")))} ({html.escape(fmt(a.get("seen_at")))})'
        f' · raised {html.escape(str(a.get("ts") or "")[:19])}'
        "</li>"
        for a in alerts
    )
    st.markdown(
        f'<div class="cv-banner fm-banner cv-banner--alert fm-banner--alert">'
        f'<p class="cv-banner-title fm-banner-title">Watchlist hit ({len(alerts)})</p>'
        f"<ul>{items}</ul></div>",
        unsafe_allow_html=True,
    )


def risk_placeholder(profile: dict | None = None) -> None:
    """The risk slot on the Investigate page.

    Takes the profile when the caller has it; otherwise it reads the last query out of
    `latest_result`, the session key the Investigate page already publishes for Plan Trace.
    """
    if profile is None:
        profile = (st.session_state.get("latest_result") or {}).get("profile") or {}
    risk_gauge(profile.get("risk"))
