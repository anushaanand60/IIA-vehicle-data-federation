"""Task 1.1 — the theme module (app/theme.py).

The GUI's look is defined in exactly one place: `TOKENS` (colours) + `FONTS` (type), turned into
CSS custom properties by `inject()`. These tests pin the three things that can silently rot:
the injection is a single `<style>` block, every token really reaches the page, and the two
classifier helpers map every decision/status string the mediator can actually produce.
"""
from __future__ import annotations

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import theme
from app.theme import FONTS, TOKENS, decision_class, inject, masthead, status_class

ROOT = Path(__file__).resolve().parents[1]


# AppTest.from_function re-executes the function's *source* in a fresh module, so the callable
# has to import everything it uses itself — module-level names from this file are not visible.
def _run_inject():
    from app.theme import inject
    inject()


def _run_masthead():
    from app.theme import masthead
    masthead("Federated Mediator", "sub text here")


def _css_var_names() -> list[str]:
    """`ink_muted` -> `--vz-ink-muted`: the exact spelling the stylesheet must carry."""
    return [f"--vz-{key.replace('_', '-')}" for key in TOKENS]


# ================================ inject() ================================

def test_inject_emits_exactly_one_style_block():
    at = AppTest.from_function(_run_inject).run()
    assert not at.exception
    blocks = [m.value for m in at.markdown if "<style>" in m.value]
    assert len(blocks) == 1, f"expected one <style> block, got {len(blocks)}"
    assert len(at.markdown) == 1, "inject() must emit nothing but the stylesheet"
    assert blocks[0].count("<style>") == 1 and blocks[0].count("</style>") == 1


def test_inject_carries_every_token_and_font():
    at = AppTest.from_function(_run_inject).run()
    css = at.markdown[0].value
    for name in _css_var_names():
        assert name in css, f"token {name} never reaches the stylesheet"
    for value in TOKENS.values():
        assert value.lower() in css.lower()
    assert FONTS["import_url"] in css
    for face in ("display", "body", "mono"):
        assert FONTS[face].split(",")[0].strip('"') in css


def test_inject_declares_the_classes_the_app_uses():
    at = AppTest.from_function(_run_inject).run()
    css = at.markdown[0].value
    for cls in (".fm-masthead", ".fm-eyebrow", ".fm-banner", ".fm-chip", ".fm-card"):
        assert cls in css  # app/tabs/self_check.py still speaks this vocabulary
    for variant in ("clear", "report", "alert", "suspicious", "undetermined", "unknown"):
        assert f".fm-banner--{variant}" in css
    for variant in ("ok", "timeout", "down", "error"):
        assert f".fm-chip--{variant}" in css
    # Legacy aliases keep app.py's current banner/status HTML alive until Task 1.2 replaces it.
    for legacy in (".decision-banner-clear", ".decision-banner-danger", ".decision-banner-warn",
                   ".decision-banner-unknown", ".status-card-ok", ".status-card-timeout",
                   ".status-card-down"):
        assert legacy in css


def test_masthead_renders_title_and_subtitle():
    at = AppTest.from_function(_run_masthead).run()
    assert not at.exception
    html = "".join(m.value for m in at.markdown)
    assert "Federated Mediator" in html
    assert "sub text here" in html
    assert "fm-masthead" in html


# ============================ decision_class() ============================

def test_decision_class_covers_every_decision_decide_can_return():
    cases = {
        "CLEAR": "clear",
        "STOLEN — ALERT POLICE": "alert",
        "SCRAPPED — ALERT POLICE": "alert",
        "SCRAPPED — REGISTRATION VOID": "alert",
        "SHREDDED — VEHICLE DESTROYED": "alert",
        "UNINSURED — REPORT": "report",
        "REGISTRATION INVALID — REPORT": "report",
        "UNREGISTERED / SUSPICIOUS": "suspicious",
        "SUSPICIOUS — POSSIBLE CLONED PLATE": "suspicious",
        "UNKNOWN VEHICLE — NOT REGISTERED": "unknown",
        "UNDETERMINED": "undetermined",
    }
    for decision, expected in cases.items():
        assert decision_class(decision) == expected, decision


def test_decision_class_is_total_and_case_insensitive():
    assert decision_class("") == "undetermined"
    assert decision_class(None) == "undetermined"  # type: ignore[arg-type]
    assert decision_class("clear") == "clear"
    assert decision_class("something the engine has never said") == "undetermined"


def test_every_decision_class_has_a_token():
    for name in set(decision_class(d) for d in
                    ("CLEAR", "STOLEN — ALERT POLICE", "UNINSURED — REPORT",
                     "SUSPICIOUS — POSSIBLE CLONED PLATE",
                     "UNKNOWN VEHICLE — NOT REGISTERED", "UNDETERMINED")):
        assert name in theme.DECISION


# ============================= status_class() =============================

def test_status_class_maps_the_four_executor_statuses():
    assert status_class("OK") == "ok"
    assert status_class("TIMEOUT") == "timeout"
    assert status_class("DOWN") == "down"
    assert status_class("ERROR") == "error"
    assert status_class("ok") == "ok"
    assert status_class("anything else") == "down"
    assert status_class(None) == "down"  # type: ignore[arg-type]
    for name in ("ok", "timeout", "down", "error"):
        assert name in theme.STATUS


# =============================== hygiene ================================

def test_no_use_container_width_left_in_the_gui():
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "app").rglob("*.py")
        if "use_container_width" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"use_container_width is deprecated; use width=: {offenders}"


def test_streamlit_config_pins_the_visibility_palette():
    text = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert re.search(r'primaryColor\s*=\s*"#E4572E"', text)
    assert re.search(r'backgroundColor\s*=\s*"#F3F4F6"', text)
    assert re.search(r'secondaryBackgroundColor\s*=\s*"#FCFCFA"', text)
    assert re.search(r'textColor\s*=\s*"#14213D"', text)
    assert "[server]" in text


# ======================= Visibility design system (Task A1) =======================
# Ported from AirSentinel's shipped `_CSS`: one accent ("beacon"), frosted plate cards,
# real section headings with a left accent bar, mono tabular numerals, a dark-mode token
# override. These four pin the things that silently rot: button label contrast, the scope
# of the body-prose grey rule, the token identity itself, and the dark block.

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


def test_form_submit_primary_is_themed_too():
    """`type="primary"` inside st.form renders as stBaseButton-primaryFormSubmit in 1.57."""
    css = theme._css()
    assert '[data-testid="stBaseButton-primaryFormSubmit"] p' in css


def test_class_vocabulary_is_declared():
    css = theme._css()
    for cls in (".vz-h", ".vz-lead", ".vz-card", ".vz-kpi", ".vz-chip", ".vz-table",
                ".vz-step", ".vz-grouplabel", ".vz-hero"):
        assert cls in css, cls


def test_nothing_is_smaller_than_point_eight_rem():
    """Readability floor: no rule in the stylesheet may set type below 0.8rem."""
    sizes = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)rem", theme._css())]
    assert sizes and min(sizes) >= 0.8, min(sizes)
