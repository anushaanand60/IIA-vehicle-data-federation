"""Task D2 — the shared Civic primitives in `app/components.py`.

Every tab is composed from this vocabulary, so these tests pin the parts that carry a claim:
the ink chosen for a coloured chip really is the readable one (WCAG relative luminance, not a
guess), a chip prints its word as well as its colour, and the HTML table escapes source values
instead of letting a database row inject markup into the page.
"""
from __future__ import annotations

import pytest

from app import components as c


# --------------------------------------------------------------- readable_ink

def test_readable_ink_picks_white_on_dark():
    assert c.readable_ink("#0F2B4C") == "#FFFFFF"


def test_readable_ink_picks_ink_on_light():
    assert c.readable_ink("#E7F5EC").upper() == "#0B0C0C"


@pytest.mark.parametrize("bg", ["#1F7A4D", "#B42318", "#B7791F", "#C2410C", "#7F1D1D",
                                "#5B6785", "#3B4660", "#E4572E"])
def test_every_status_colour_gets_a_readable_chip(bg):
    """The (fill, ink) pair a chip actually uses must clear WCAG AA (4.5:1).

    Neither ink clears 4.5:1 on the amber #B7791F as published (best is 4.30), so — exactly
    as AirSentinel's `readable_fill` does — the fill is lifted toward white in 5% steps until
    it does. It stays unmistakably the same amber; it just becomes legible.
    """
    fill, ink = c.readable_fill(bg)
    assert c._contrast(fill, ink) >= 4.5, (bg, fill, ink)


# ---------------------------------------------------------------------- chip

def test_chip_html_contains_text_and_class():
    html = c.chip("OK", "#1F7A4D")
    assert "cv-chip" in html and ">OK<" in html


def test_chip_keeps_the_legacy_class_for_untouched_tabs():
    assert "fm-chip" in c.chip("OK", "#1F7A4D")


def test_chip_escapes_its_label():
    assert "&lt;b&gt;" in c.chip("<b>x</b>", "#1F7A4D")


# -------------------------------------------------------------- styled_table

def test_styled_table_escapes_html(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.styled_table([{"a": "<b>x</b>"}])
    assert "&lt;b&gt;" in out[0] and "cv-table" in out[0]


def test_styled_table_honours_an_explicit_column_order(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.styled_table([{"a": 1, "b": 2}], columns=["b", "a"])
    assert out[0].index(">b<") < out[0].index(">a<")


def test_styled_table_truncates_and_says_so(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    monkeypatch.setattr(c.st, "caption", lambda s, **k: out.append(s))
    c.styled_table([{"n": i} for i in range(80)], max_rows=60)
    assert out[0].count("<tr>") == 61          # 60 body rows + the header row
    assert any("80" in str(o) for o in out[1:])


def test_styled_table_renders_nothing_for_no_rows(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.styled_table([])
    assert out == []


# --------------------------------------------------------- section / stepper

def test_section_emits_one_heading_and_an_optional_lead(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.section("Source selection", "who was asked, and why")
    assert len(out) == 1, "a section is one markdown block, never a heading plus a caption"
    assert 'class="cv-h"' in out[0] and "Source selection" in out[0]
    assert 'class="cv-lead"' in out[0] and "who was asked" in out[0]


def test_section_without_a_lead_has_no_lead_element(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.section("Latency per source")
    assert "cv-lead" not in out[0]


def test_stepper_marks_done_steps(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.stepper([{"title": "Identity", "detail": "resolved", "done": True},
               {"title": "Theft", "detail": "pending", "done": False}])
    assert "cv-step done" in out[0] and "Identity" in out[0]
    assert out[0].count("cv-step") == 2


def test_kpi_row_prints_label_and_value(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.kpi_row([("Sources asked", 4), ("Total time", "412 ms")])
    assert "cv-kpi" in out[0] and "Sources asked" in out[0] and "412 ms" in out[0]


def test_group_label_is_not_a_heading(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.group_label("Filters")
    assert "cv-grouplabel" in out[0] and "cv-h" not in out[0]


# ------------------------------------------------- page head / panel / nav labels (Task D2)

def test_page_head_prints_the_title_and_its_purpose(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.page_head("Investigate a vehicle", "One plate in, one defensible decision out.")
    assert len(out) == 1
    assert "cv-pagehead" in out[0]
    assert "Investigate a vehicle" in out[0] and "defensible decision" in out[0]


def test_page_head_escapes_what_it_is_given(monkeypatch):
    out = []
    monkeypatch.setattr(c.st, "markdown", lambda s, **k: out.append(s))
    c.page_head("<b>x</b>", "<i>y</i>")
    assert "&lt;b&gt;" in out[0] and "&lt;i&gt;" in out[0]


def test_panel_opens_a_keyed_container_the_stylesheet_can_find(monkeypatch):
    """`theme.py` paints `[class*="st-key-cvp_"]`; the namespace is the contract between them."""
    seen = {}

    class _Ctx:
        def __enter__(self):
            return None

        def __exit__(self, *exc):
            return False

    def fake_container(*args, **kwargs):
        seen["key"] = kwargs.get("key")
        return _Ctx()

    monkeypatch.setattr(c.st, "container", fake_container)
    with c.panel("queue"):
        pass
    assert seen["key"] == "cvp_queue"


def test_nav_group_label_writes_into_the_sidebar(monkeypatch):
    out = []
    monkeypatch.setattr(c.st.sidebar, "markdown", lambda s, **k: out.append(s))
    c.nav_group_label("Enforcement")
    assert out and "cv-navgroup" in out[0] and "Enforcement" in out[0]
