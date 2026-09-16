"""Task D1 — the civic theme module (app/theme.py).

The GUI's look is defined in exactly one place: `TOKENS` (colours) + `FONTS` (type), turned into
CSS custom properties by `inject()`. These tests pin the things that can silently rot:

  * the injection is a single `<style>` block and every token really reaches the page;
  * the theme is **light only** — the previous skin flipped its own tokens dark under
    `@media (prefers-color-scheme: dark)` while Streamlit's own chrome stayed light, which is
    exactly how the shipped page ended up with dark labels on dark ground and white dataframes
    glaring on navy. There is no dark branch any more, in the stylesheet or in config.toml;
  * the two classifier helpers map every decision/status string the mediator can produce.
"""
from __future__ import annotations

import re
from pathlib import Path

from streamlit.testing.v1 import AppTest

from app import theme
from app.theme import FONTS, TOKENS, decision_class, inject, status_class  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


# AppTest.from_function re-executes the function's *source* in a fresh module, so the callable
# has to import everything it uses itself — module-level names from this file are not visible.
def _run_inject():
    from app.theme import inject
    inject()


def _css_var_names() -> list[str]:
    """`ink_muted` -> `--cv-ink-muted`: the exact spelling the stylesheet must carry."""
    return [f"--cv-{key.replace('_', '-')}" for key in TOKENS]


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
    css = theme._css()
    for cls in (".cv-pagehead", ".cv-h", ".cv-lead", ".cv-panel", ".cv-kpi", ".cv-chip",
                ".cv-table", ".cv-step", ".cv-grouplabel", ".cv-navgroup", ".cv-brand"):
        assert cls in css, cls
    # `app/tabs/self_check.py` and the filed Ministry-report HTML still speak the `.fm-*`
    # vocabulary; the aliases keep them looking like the rest of the page.
    for cls in (".fm-banner", ".fm-chip", ".fm-card", ".vz-card", ".vz-table"):
        assert cls in css, cls
    for variant in ("clear", "report", "alert", "suspicious", "undetermined", "unknown"):
        assert f".fm-banner--{variant}" in css
    for variant in ("ok", "timeout", "down", "error"):
        assert f".fm-chip--{variant}" in css


# ============================ light-only (the bug this task fixes) ============================

def test_the_stylesheet_has_no_dark_branch_at_all():
    css = theme._css()
    assert "prefers-color-scheme" not in css, (
        "an OS-dark viewer must get the same page as everyone else")


def test_the_stylesheet_pins_the_colour_scheme_to_light():
    css = theme._css().replace(" ", "")
    assert "color-scheme:light" in css


def test_no_token_from_the_old_airsentinel_skin_survives():
    css = theme._css()
    assert "--vz-" not in css, "the `--vz-*` variable vocabulary is replaced by `--cv-*`"
    assert "#E4572E" not in css.upper(), "the AirSentinel orange accent is gone"
    assert "Unbounded" not in css, "the AirSentinel display face is gone"


def test_streamlit_config_forces_the_light_base_theme():
    text = (ROOT / ".streamlit" / "config.toml").read_text(encoding="utf-8")
    assert re.search(r'base\s*=\s*"light"', text), "config.toml must pin base = \"light\""
    assert re.search(r'primaryColor\s*=\s*"#00703C"', text)
    assert re.search(r'backgroundColor\s*=\s*"#F3F4F6"', text)
    assert re.search(r'secondaryBackgroundColor\s*=\s*"#FFFFFF"', text)
    assert re.search(r'textColor\s*=\s*"#0B0C0C"', text)
    assert "[server]" in text


# ================================== tokens and type ==================================

def test_tokens_are_the_civic_palette():
    assert TOKENS["action"].upper() == "#00703C"
    assert TOKENS["ink"].upper() == "#0B0C0C"
    assert TOKENS["ground"].upper() == "#F3F4F6"
    assert TOKENS["panel"].upper() == "#FFFFFF"
    assert TOKENS["brand"].upper() == "#0F2B4C"
    assert TOKENS["focus"].upper() == "#FFDD00"
    assert "Geist" in FONTS["display"] and "Geist" in FONTS["body"]


def test_every_status_and_decision_name_has_a_token():
    for name in (*theme.STATUS, *theme.DECISION):
        assert name in TOKENS, name


def test_primary_button_label_is_forced_white_on_the_action_green():
    css = theme._css()
    assert '[data-testid="stBaseButton-primary"]' in css
    assert '[data-testid="stBaseButton-primaryFormSubmit"]' in css
    rule = css[css.index('[data-testid="stBaseButton-primary"] p'):][:400]
    assert "#FFFFFF" in rule.upper()


def test_focus_ring_is_the_yellow_one():
    css = theme._css()
    assert "focus-visible" in css and "var(--cv-focus)" in css


def test_nothing_is_smaller_than_point_nine_rem():
    """Readability floor: no rule in the stylesheet may set type below 0.9rem."""
    sizes = [float(m) for m in re.findall(r"font-size:\s*([0-9.]+)rem", theme._css())]
    assert sizes and min(sizes) >= 0.9, min(sizes)


# ============================= classifiers =============================

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


def test_status_class_maps_the_four_executor_statuses():
    assert status_class("OK") == "ok"
    assert status_class("TIMEOUT") == "timeout"
    assert status_class("DOWN") == "down"
    assert status_class("ERROR") == "error"
    assert status_class("ok") == "ok"
    assert status_class("anything else") == "down"
    assert status_class(None) == "down"  # type: ignore[arg-type]


# =============================== hygiene ================================

def test_no_use_container_width_left_in_the_gui():
    offenders = [
        str(p.relative_to(ROOT))
        for p in (ROOT / "app").rglob("*.py")
        if "use_container_width" in p.read_text(encoding="utf-8")
    ]
    assert offenders == [], f"use_container_width is deprecated; use width=: {offenders}"
