"""CONTEXT-ADDED (R3: documents in the case file) relevant documents as readable cards, a reader, and the claim matrix.

render_documents(res): only documents about this vehicle (DocResult.relevant), contradictions first; each card says
what the document is, who issued it, when, and why it was retrieved, and opens in a reader that numbers every
highlighted passage against the database record it supports or contradicts. render_claims(res): who states what.
Both take the Explorer's `res`, never raise, and render nothing harmful without a context or documents.
Derived data is kept on `res` under `_docs_values` / `_claims` (per query, not a cache of source data). Keys: `cxd_`.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

for _p in (Path(__file__).resolve().parents[2], Path(__file__).resolve().parents[1]):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import streamlit as st  # noqa: E402

from components import chip  # noqa: E402
from mediator import claim_matrix as cm  # noqa: E402
from mediator.doc_conflicts import to_date  # noqa: E402
from mediator.doc_retrieval import (EXTRA_ANCHORS, _fmt, column_kind, context_values, load_graph,  # noqa: E402
                                    lookup_table)
from tabs.context_widgets import SOURCE_NAMES, esc, highlight, source_colour  # noqa: E402
from theme import TOKENS  # noqa: E402

# Plain nouns for records, and the word for each table's time column ("valid till 10 Jun 2026").
RECORD_WORDS = {"VEHICLE_REGISTRATION": ("Registration", "registered"), "OWNERS": ("Owner", None),
                "OWNERSHIP_HISTORY": ("Ownership transfer", "on"), "VEHICLE_IDENTITY": ("Chassis record", None),
                "POLICY_RECORDS": ("Insurance policy", "valid till"), "POLICY_HOLDERS": ("Policy holder", None),
                "INSURERS": ("Insurer", None), "CLAIMS": ("Insurance claim", "loss on"),
                "POLICY_PARTIES": ("Insured chassis", None),
                "CRIME_RECORDS": ("Police FIR", "reported"), "STOLEN_PROPERTY": ("Stolen-property record", "recovered"),
                "PERSONS": ("Person in the police case", None), "STATIONS": ("Police station", None),
                "PLATE_CAPTURES": ("Camera sighting", "seen"), "CAMERAS": ("Camera", None),
                "CHALLANS": ("Traffic challan", "issued"),
                "POLLUTION_CERT": ("Pollution certificate", "valid till"), "CERT_TESTS": ("Pollution test", "tested")}
PLAIN_CHIP = "background:#F1F2F4;color:var(--cv-ink);border:1px solid var(--cv-line);font-weight:400;"


def _date(value: Any, fmt: str = "iso_date") -> str:
    d = to_date(value, fmt)
    return d.strftime("%d %b %Y").lstrip("0") if d else str(value)


def doc_kind(d: Any) -> str:
    return cm.DOC_TYPES.get(d.doc_type, str(d.doc_type).replace("_", " ").capitalize())


def describe(key: Tuple[str, str, Any], res: Dict[str, Any]) -> str:
    """'Insurance policy POL-000562 — TrustLine Insurers, valid till 10 Jun 2026' from the row and the graph."""
    sid, table, pk = key
    graph, rows = _graph(res), getattr(res.get("context"), "rows", []) or []
    row = next((r for r in rows if (r.source_id, r.table, str(r.pk)) == (sid, table, str(pk))), None)
    noun, when = RECORD_WORDS.get(table, (table.replace("_", " ").capitalize(), None))
    if row is None:
        return f"{noun} {pk}"
    spec = graph.get("sources", {}).get(sid, {}).get("tables", {}).get(table, {})
    ident = next((_fmt(row.row.get(c), pat) for (s, t, c), (_, pat) in EXTRA_ANCHORS.items() if (s, t) == (sid, table)),
                 row.row.get(spec.get("plate")) if spec.get("plate") else None)
    details = [str(row.row[c]) for c in spec.get("text", []) if row.row.get(c) and column_kind(c, spec)
               not in (None, "address") and str(row.row[c]) != str(ident)]
    for other in rows:   # a lookup row reached through this one: the insurer of a policy, the camera of a sighting
        if f"{table}#{pk}" in (other.path or []) and lookup_table(graph, other.source_id, other.table):
            ospec = graph["sources"][other.source_id]["tables"][other.table]
            details += [str(other.row[c]) for c in ospec.get("text", []) if other.row.get(c) and column_kind(c, ospec)]
    time_spec = spec.get("time") or {}
    if when and row.row.get(time_spec.get("col")) is not None:
        details.append(f"{when} {_date(row.row[time_spec['col']], time_spec.get('fmt', 'iso_date'))}")
    head = f"{noun} {ident}" if ident else noun
    return head + (" — " + ", ".join(details) if details else "")


def conflict_line(c: Any) -> str:
    agency = SOURCE_NAMES.get(c.db_source, c.db_source).split(" (")[0].lower()
    if c.attr == "recovery":
        return "Says the car was recovered, but the police case is still open."
    if c.attr in ("colour", "make", "model"):
        return f"Describes the car's {c.attr} as {c.text_value}; the registration says {c.db_value}."
    if c.attr in cm.DATE_FACTS:
        return (f"Gives the {cm.DATE_FACTS[c.attr].lower()} as {_date(c.text_value)}; "
                f"the {agency} record says {_date(c.db_value)}.")
    if c.attr == "plate":
        return f"Prints this car's chassis number next to a different plate ({c.text_value})."
    return str(c.note)


def _graph(res: Dict[str, Any]) -> Dict[str, Any]:
    if "_graph" not in res:
        res["_graph"] = load_graph()
    return res["_graph"]


def _value_rows(res: Dict[str, Any]) -> Dict[Tuple[str, str], List[Any]]:
    if "_docs_values" not in res:
        ctx = res.get("context")
        res["_docs_values"] = {(v.attr, v.value): v.rows for v in context_values(ctx, _graph(res))} if ctx else {}
    return res["_docs_values"]


def reader_html(d: Any, res: Dict[str, Any]) -> Tuple[str, List[Tuple[int, Any, Optional[str]]]]:
    """The body with numbered highlights, and the numbered records: (number, row key, contradiction or None)."""
    body = d.body or d.snippet or ""
    linked = [(s, t, str(p)) for s, t, p in d.linked_rows]
    number = {k: i + 1 for i, k in enumerate(linked)}
    spans: List[Tuple[int, int, str]] = []

    def free(s: int, e: int) -> bool:
        return 0 <= s < e <= len(body) and all(e <= a or s >= b for a, b, _ in spans)

    notes: Dict[Any, str] = {}
    for c in d.conflicts:   # red, with the database value, before any ordinary highlight
        key = next((k for k in linked if k[0] == c.db_source), None)
        s, e = c.span
        if key:
            notes[key] = conflict_line(c)
        if free(s, e):
            tag = f"[{number[key]}] " if key else ""
            spans.append((s, e, f'<mark style="background:#FDE2E1;border-bottom:2px solid {TOKENS["bad"]};color:'
                                f'var(--cv-ink);">{esc(body[s:e])}</mark><sup style="color:{TOKENS["bad"]};font-weight:'
                                f'600;">{tag}records say {esc(c.db_value)}</sup>'))
    vrows = _value_rows(res)
    for m in d.mentions:
        if not free(m.start, m.end):
            continue
        key = next((k for k in [(s, t, str(p)) for s, t, p in vrows.get((m.attr, m.matched_value), [])]
                    if k in number), None)
        colour = source_colour(key[0]) if key else TOKENS["neutral"]
        sup = f'<sup style="color:{colour};font-weight:600;">[{number[key]}]</sup>' if key else ""
        spans.append((m.start, m.end, f'<mark style="background:{colour}22;border-bottom:2px solid {colour};'
                                      f'color:var(--cv-ink);">{esc(body[m.start:m.end])}</mark>{sup}'))
    out, pos = [], 0
    for s, e, markup in sorted(spans):
        out += [esc(body[pos:s]), markup]
        pos = e
    out.append(esc(body[pos:]))
    text = f'<div style="white-space:pre-wrap;line-height:1.75;font-size:1rem;">{"".join(out)}</div>'
    return text, [(number[k], k, notes.get(k)) for k in linked]


def _reader(d: Any, res: Dict[str, Any]) -> None:
    text, records = reader_html(d, res)
    left, right = st.columns([3, 2], gap="large")
    left.markdown(text, unsafe_allow_html=True)
    with right:
        st.markdown("**Database records it speaks about**")
        items = "".join(
            f'<li style="margin-bottom:8px;"><b style="color:{source_colour(k[0])};">[{n}]</b> {esc(describe(k, res))}'
            f' <span style="color:var(--cv-ink2);">({esc(SOURCE_NAMES.get(k[0], k[0]))})</span>'
            + (f'<br><span style="color:{TOKENS["bad"]};">Contradiction: {esc(note)}</span>' if note else "") + "</li>"
            for n, k, note in records) or "<li>None: it only shares descriptive words with this case.</li>"
        st.markdown(f'<ul style="list-style:none;padding:0;margin:0;">{items}</ul>', unsafe_allow_html=True)
        st.markdown("**About the document**")
        st.markdown(f"{esc(doc_kind(d))} · {esc(d.doc_id)}  \nIssued by {esc(d.issuer or 'unknown')}"
                    f"{' on ' + esc(_date(d.issued_on)) if d.issued_on else ''}", unsafe_allow_html=True)


def _open_button(d: Any, res: Dict[str, Any], where: str) -> None:
    if st.button("Open", key=f"cxd_open_{where}_{d.doc_id}", help="Read the whole document"):
        st.dialog(f"{doc_kind(d)}: {d.title}"[:90], width="large")(_reader)(d, res)


def _card(d: Any, res: Dict[str, Any]) -> None:
    with st.container(border=True):
        head, button = st.columns([6, 1])
        meta = " · ".join(x for x in [doc_kind(d), d.issuer, _date(d.issued_on) if d.issued_on else None] if x)
        head.markdown(f'<p style="margin:0;color:var(--cv-ink2);font-size:.9rem;">{esc(meta)}</p>'
                      f'<p style="margin:2px 0 6px;font-weight:600;font-size:1.05rem;">{esc(d.title)}</p>',
                      unsafe_allow_html=True)
        with button:
            _open_button(d, res, "card")
        for c in d.conflicts:
            st.markdown(f'{chip("Contradiction", TOKENS["bad"])} {esc(conflict_line(c))}', unsafe_allow_html=True)
        reasons = "".join(f'<span class="cv-chip" style="{PLAIN_CHIP}">{esc(w)}</span>' for w in d.why[:4])
        st.markdown(f'<p style="margin:6px 0;line-height:1.6;display:-webkit-box;-webkit-line-clamp:2;'
                    f'-webkit-box-orient:vertical;overflow:hidden;">{highlight(d.snippet, d.mentions)}</p>'
                    f'<div class="cv-chipstrip">{reasons}</div>', unsafe_allow_html=True)


def render_documents(res: Dict[str, Any]) -> None:
    try:
        docs = getattr(res.get("docs"), "docs", None)
        if res.get("docs") is None:
            st.caption("Documents are not available for this question.")
            return
        shown = sorted([d for d in docs if d.relevant], key=lambda d: (not d.conflicts, -d.score))
        hidden = [d for d in docs if not d.relevant]
        if not shown:
            st.caption("No document names this vehicle or one of its records.")
        for d in shown:
            _card(d, res)
        if hidden:
            st.caption(f"{len(hidden)} other document{'s' if len(hidden) > 1 else ''} matched only common words "
                       "(a make, a colour, an insurer's name) and name nothing of this vehicle, so they are hidden.")
            with st.expander(f"Show the {len(hidden)} hidden document{'s' if len(hidden) > 1 else ''}"):
                for d in hidden:
                    shares = esc("; ".join(d.why) or "—")
                    st.markdown(f"**{esc(d.title)}** · {esc(doc_kind(d))}  \nOnly shares: {shares}",
                                unsafe_allow_html=True)
                    _open_button(d, res, "hidden")
    except Exception as exc:  # the case file keeps rendering whatever this block hits
        st.caption(f"Documents could not be shown ({type(exc).__name__}).")


def claims_html(matrix: cm.ClaimMatrix) -> str:
    marks = {"agree": ("✓", TOKENS["ok"]), "conflict": ("✗", TOKENS["bad"]), "silent": ("–", TOKENS["neutral"])}
    head = "".join(f'<th style="border-top:3px solid {source_colour(c.key)};">{esc(SOURCE_NAMES.get(c.key))}</th>'
                   if c.kind == "agency" else
                   f'<th>{esc(c.label)}<br><span style="font-weight:400;">{esc(c.key)}</span></th>'
                   for c in matrix.columns)
    body = []
    for r in matrix.rows:
        cells = []
        for c in matrix.columns:
            cell = r.cells[c.key]
            mark, colour = marks[cell.status]
            value = esc(cm.shown(r.fact, cell)) if cell.norm else ""
            cells.append(f'<td title="as stated: {esc(cell.value)}"><b style="color:{colour};">{mark}</b> {value}</td>')
        best = next((cm.shown(r.fact, c) for c in r.cells.values() if c.status == "agree"), "")
        body.append(f"<tr><td><b>{esc(r.label)}</b></td><td><b>{esc(best)}</b></td>{''.join(cells)}</tr>")
    return ('<div class="cv-tablewrap"><table class="cv-table" style="font-size:.9rem;"><thead><tr><th>Fact</th>'
            f'<th>Best supported</th>{head}</tr></thead><tbody>{"".join(body)}</tbody></table></div>')


def render_claims(res: Dict[str, Any]) -> None:
    try:
        if res.get("context") is None:
            return
        if "_claims" not in res:
            res["_claims"] = cm.build(res["context"], res.get("docs"), _graph(res))
        matrix = res["_claims"]
        if not matrix.rows:
            st.caption("No agency or document states a comparable fact about this vehicle.")
            return
        st.markdown(f"**{esc(cm.takeaway(matrix))}**", unsafe_allow_html=True)
        st.markdown(claims_html(matrix), unsafe_allow_html=True)
        with st.expander("How the best-supported value is chosen"):
            weights = ", ".join(f"{SOURCE_NAMES.get(s, s)} {cm.TRUST[s]:.2f}" for s in cm.AGENCY_ORDER if s in cm.TRUST)
            st.markdown(f"Each statement votes with its source's trust: {weights}, any document {cm.DOC_TRUST:.2f}. "
                        "Dates a day or so apart (three for a theft report) count as the same. "
                        "✓ agrees with the winner, ✗ disagrees, – says nothing.")
    except Exception as exc:  # never take the case file down
        st.caption(f"The comparison of facts could not be shown ({type(exc).__name__}).")
