"""CONTEXT-ADDED (R2/R3): pure HTML / DOT builders for the Context Explorer page.

Nothing here contacts a source or touches session state: each function takes a phase-3 result
object (or its dict form) and returns markup, so every piece is unit-testable on hand-built data.
Colours: one fixed categorical order per source (validated colour-blind safe on adjacent pairs; three
slots sit below 3:1 on white, so every coloured mark also carries its source name as text).
"""
from __future__ import annotations

import html
import re
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple
from typing import Callable  # CONTEXT-ADDED (R2/R3: name functions passed in by the case file)

from components import chip, readable_ink
from theme import TOKENS

SOURCE_ORDER = ["REG", "INS", "THEFT", "CAM", "PUC", "DOCS"]
SOURCE_COLOURS = {"REG": "#2a78d6", "INS": "#eb6834", "THEFT": "#1baf7a", "CAM": "#eda100",
                  "PUC": "#e87ba4", "DOCS": "#008300"}
SOURCE_NAMES = {"REG": "Registration (RTO)", "INS": "Insurance", "THEFT": "Police", "CAM": "Road cameras",
                "PUC": "Pollution certificates", "DOCS": "Documents"}
SEVERITY = {"high": ("bad", "High"), "medium": ("warn", "Medium"), "low": ("info", "Low")}
# CONTEXT-ADDED (R2/R3: plain words for the case file)
PLAIN_SEVERITY = {"High": "Serious", "Medium": "Worth a look", "Low": "Minor"}
QUIET_CHIP = ("display:inline-block;margin-left:6px;padding:0 7px;border:1px solid var(--cv-line);border-radius:999px;"
              "font-size:0.78rem;line-height:1.5;color:var(--cv-ink-muted);white-space:nowrap;vertical-align:1px;")
# CONTEXT-END


def get(obj: Any, name: str, default: Any = None) -> Any:
    """A field of a dataclass or a dict: results may be live objects or JSON round-trips."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def esc(value: Any) -> str:
    return html.escape("—" if value is None else str(value))


def source_colour(sid: str) -> str:
    return SOURCE_COLOURS.get(str(sid).upper(), TOKENS["neutral"])


def source_chip(sid: str, suffix: str = "") -> str:
    return chip(f"{sid}{suffix}", source_colour(sid))


def ref_text(ref: Sequence[Any]) -> str:
    """(source_id, table, pk) -> 'THEFT · CRIME_RECORDS #12'."""
    sid, table, pk = (list(ref) + [None, None, None])[:3]
    return f"{sid} · {table} #{pk}"


# ------------------------------------------------------------------ parsed query

def predicate_chips(parsed: Any) -> str:
    """One quiet pill per understood piece; 'rule' / 'LLM' says who produced it (LLM ones carry a confidence)."""
    pills: List[str] = []
    for plate in get(parsed, "plates") or []:
        pills.append(_pill(f"plate = {plate}", "rule"))
    for p in get(parsed, "predicates") or []:
        conf = float(get(p, "confidence", 1.0) or 0)
        origin = "rule" if conf >= 1.0 else f"LLM {conf:.0%}"
        value = get(p, "value")
        value = " – ".join(map(str, value)) if isinstance(value, (list, tuple)) else value
        pills.append(_pill(f"{get(p, 'attr')} {get(p, 'op')} {value}", origin))
    window = get(parsed, "time_window")
    if window:
        pills.append(_pill(f"between {window[0]} and {window[1]}", "rule"))
    for kw in get(parsed, "keywords") or []:
        pills.append(_pill(f"word: {kw}", "rule"))
    return f'<div class="cv-chipstrip">{"".join(pills)}</div>' if pills else ""


def _pill(text: str, origin: str) -> str:
    tint = "#E8F1FA" if origin == "rule" else "#F3EEFC"
    return (f'<span class="cv-chip" style="background:{tint};color:var(--cv-ink);border:1px solid var(--cv-line);">'
            f'{esc(text)}<span style="font-weight:400;color:var(--cv-ink2);">· {esc(origin)}</span></span>')


# ------------------------------------------------------------------ rows and paths

def node_label(element: Any) -> Tuple[Optional[str], str, str]:
    """'REG:OWNERS#4' / 'OWNERS#4' -> (source or None, table, pk)."""
    text = str(element)
    head, _, pk = text.partition("#")
    src = None
    for sep in (":", "."):
        if sep in head:
            src, head = head.split(sep, 1)
    return src, head, pk


BRIDGE_RE = re.compile(r"^(\w+)->(\w+)$")   # a path step like 'plate->INS': context crossed into INS by plate


def path_chain(path: Sequence[Any]) -> str:
    """The join path as a small chain: VEHICLE_REGISTRATION → OWNERS → … ; a bridge step reads 'plate ⇒ INS'."""
    steps = []
    for p in path or []:
        hop = BRIDGE_RE.match(str(p))
        steps.append(f"<b>{esc(hop.group(1))} ⇒ {esc(hop.group(2))}</b>" if hop else esc(node_label(p)[1]))
    if len(steps) <= 1:
        return '<span style="color:var(--cv-ink2);">found directly from the query</span>'
    return ('<span style="font-family:var(--cv-font-mono);font-size:0.9rem;color:var(--cv-ink2);">'
            + " → ".join(steps) + "</span>")


def row_values(row: Dict[str, Any], limit: int = 4) -> str:
    """The first few non-empty values of a raw source row, as 'column: value' (source names, unchanged)."""
    parts = [f"{esc(k)}: <b>{esc(v)}</b>" for k, v in (row or {}).items() if v not in (None, "")][:limit]
    return " · ".join(parts)


def score_bar(score: float) -> str:
    pct = max(0.0, min(1.0, float(score or 0))) * 100
    return (f'<div style="display:flex;align-items:center;gap:8px;"><div class="cv-bar" style="width:70px;margin:0;">'
            f'<i style="width:{pct:.0f}%;background:var(--cv-brand);"></i></div>'
            f'<span style="font-family:var(--cv-font-mono);font-size:0.9rem;">{float(score or 0):.2f}</span></div>')


def rows_table(rows: Iterable[Any]) -> str:
    """One source's context rows: what the record is, why it is here, how much it matters."""
    body = []
    for r in rows:
        bridge = get(r, "via_bridge")
        badge = (f' <span class="cv-chip" style="background:#F1F2F4;color:var(--cv-ink);border:1px dashed '
                 f'var(--cv-ink2);">via {esc(bridge)} · {float(get(r, "bridge_confidence", 1) or 0):.0%}</span>'
                 if bridge else "")
        body.append(f"<tr><td><b>{esc(get(r, 'table'))}</b> #{esc(get(r, 'pk'))}{badge}</td>"
                    f"<td>{path_chain(get(r, 'path') or [])}</td><td>{score_bar(get(r, 'score', 0))}</td>"
                    f"<td style='font-size:0.95rem;'>{row_values(get(r, 'row') or {})}</td></tr>")
    return ('<div class="cv-tablewrap"><table class="cv-table"><thead><tr><th>Record</th><th>How it was reached'
            '</th><th>Score</th><th>Key values</th></tr></thead><tbody>' + "".join(body) + "</tbody></table></div>")


# CONTEXT-REPLACED (R2: the graph showed each database's records as islands; a query node now links every
# database's starting record by the value that reached it (plate / chassis / person), and composite keys print once):
# def context_dot(rows: Sequence[Any], table_source: Dict[str, str], limit: int = 40) -> str:
def context_dot(rows: Sequence[Any], table_source: Dict[str, str], limit: int = 40,
                query: Optional[str] = None) -> str:
# CONTEXT-END
    """Graphviz DOT: one node per row (coloured by its database), edges along each join path;
    a dashed, labelled edge where context crossed from one database to another (a bridge)."""
    nodes: Dict[str, str] = {}
    edges: Dict[Tuple[str, str], str] = {}

    def node(element: Any, sid_hint: Optional[str]) -> str:
        src, table, pk = node_label(element)
        sid = (src or table_source.get(table) or sid_hint or "").upper()
        pk = pk.replace('"', "'")   # composite keys print as lists; a double quote would end the DOT string
        # CONTEXT-ADDED (R2: one spelling per composite key, so "[5, 9, 'ACCUSED']" and "5|9|ACCUSED" are one node)
        if pk.startswith("[") and pk.endswith("]"):
            pk = "|".join(x.strip(" '") for x in pk[1:-1].split(","))
        # CONTEXT-END
        nid = f"{sid}:{table}#{pk}"
        if nid not in nodes:
            fill = source_colour(sid)
            nodes[nid] = (f'"{nid}" [label="{table}\\n#{pk}", fillcolor="{fill}", '
                          f'fontcolor="{readable_ink(fill)}"];')
        return nid

    for r in list(rows)[:limit]:
        sid = str(get(r, "source_id", ""))
        own = f"{get(r, 'table')}#{get(r, 'pk')}"
        path = [p for p in (get(r, "path") or []) if node_label(p)[1:] != node_label(own)[1:]] + [own]
        prev, bridge = None, None
        for i, p in enumerate(path):
            hop = BRIDGE_RE.match(str(p))
            if hop:  # not a record: the next edge is the step into another database
                bridge = hop.group(1)
                continue
            cur = node(p, sid if i == len(path) - 1 else None)
            # CONTEXT-ADDED (R2: the first record of a path hangs off the query, labelled by how it was reached)
            if prev is None and query:
                edges[("__q", cur)] = (f'"__q" -> "{cur}" [style=dashed, color="#1F3B57", '
                                       f'label="{esc(get(r, "via_bridge") or "plate")}"];')
            # CONTEXT-END
            if prev is not None and prev != cur:  # CONTEXT-REPLACED (R2: no self-loop when a key repeats): was `if prev is not None:`
                label = bridge or (get(r, "via_bridge") if prev.split(":")[0] != cur.split(":")[0] else None)
                edges[(prev, cur)] = (f'"{prev}" -> "{cur}" [style=dashed, label="{label}"];' if label
                                      else f'"{prev}" -> "{cur}";')
            prev, bridge = cur, None
    return ("digraph context {\n  rankdir=LR; bgcolor=\"transparent\";\n"
            '  node [shape=box, style="rounded,filled", fontname="Helvetica", fontsize=11, color="#D9DEE5"];\n'
            '  edge [color="#5F6B76", fontname="Helvetica", fontsize=10, fontcolor="#3C4650"];\n  '
            # CONTEXT-REPLACED (R2: query node first):
            # + "\n  ".join(list(nodes.values()) + list(edges.values())) + "\n}")
            + "\n  ".join(([f'"__q" [label="{esc(query)}", shape=ellipse, style=filled, fillcolor="#FFFFFF", '
                            'color="#1F3B57", penwidth=2, fontsize=13];'] if query else [])
                          + list(nodes.values()) + list(edges.values())) + "\n}")
            # CONTEXT-END


def source_legend(sources: Iterable[str]) -> str:
    present = [s for s in SOURCE_ORDER if s in set(sources)]
    return f'<div class="cv-chipstrip">{"".join(source_chip(s, " · " + SOURCE_NAMES[s]) for s in present)}</div>'


# ------------------------------------------------------------------ analytics and documents

# CONTEXT-REPLACED (R2/R3: plain severity words and plain evidence names in the case file):
# def indicator_card(ind: Any) -> str:
#     variant, word = SEVERITY.get(str(get(ind, "severity", "low")).lower(), ("info", "Low"))
#     evidence = "".join(chip(ref_text(e), source_colour(list(e)[0] if e else "")) for e in get(ind, "evidence") or [])
def indicator_card(ind: Any, name: Optional[Callable[[Sequence[Any]], str]] = None) -> str:
    variant, word = SEVERITY.get(str(get(ind, "severity", "low")).lower(), ("info", "Low"))
    word = PLAIN_SEVERITY.get(word, word)
    names: Dict[Tuple[str, str], int] = {}
    for e in get(ind, "evidence") or []:   # two unnumbered camera sightings read as one chip "… ×2"
        key = ((name or ref_text)(e), str(list(e)[0]) if e else "")
        names[key] = names.get(key, 0) + 1
    evidence = "".join(chip(n + (f" ×{k}" if k > 1 else ""), source_colour(sid)) for (n, sid), k in names.items())
# CONTEXT-END
    return (f'<div class="cv-banner" style="border-left:5px solid var(--cv-{variant});">'
            f'<p class="cv-banner-title">{chip(word, TOKENS[variant])}&nbsp; {esc(get(ind, "title"))}</p>'
            f'<p style="margin:8px 0 6px;color:var(--cv-ink);font-size:1rem;">{esc(get(ind, "explanation"))}</p>'
            f'<div class="cv-chipstrip">{evidence}</div></div>')


def highlight(snippet: str, mentions: Iterable[Any]) -> str:
    """Escape the snippet, then mark each mention: anchors (plate, chassis, FIR no.) solid,
    descriptors (names, places, make, colour) dotted-underlined. Found by text, so offsets into the
    full body do not have to line up with the snippet."""
    spans: List[Tuple[int, int, str]] = []
    for m in sorted(mentions or [], key=lambda m: -len(str(get(m, "text", "")))):
        text = str(get(m, "text", "") or "")
        if not text:
            continue
        for hit in re.finditer(re.escape(text), snippet or "", flags=re.I):
            if all(hit.end() <= s or hit.start() >= e for s, e, _ in spans):
                spans.append((hit.start(), hit.end(), str(get(m, "kind", "descriptor"))))
    out, pos = [], 0
    for start, end, kind in sorted(spans):
        out.append(esc(snippet[pos:start]))
        style = ("background:#E8F1FA;border-bottom:2px solid var(--cv-info);font-weight:600;" if kind == "anchor"
                 else "border-bottom:2px dotted var(--cv-ink2);")
        out.append(f'<mark style="{style}color:var(--cv-ink);padding:0 2px;" title="{esc(kind)}">'
                   f"{esc(snippet[start:end])}</mark>")
        pos = end
    out.append(esc((snippet or "")[pos:]))
    return "".join(out)


def conflict_callout(c: Any) -> str:
    return (f'<div class="cv-banner cv-banner--suspicious" style="padding:12px 16px;margin:8px 0;">'
            f'<b>Text and database disagree on {esc(get(c, "attr"))}.</b> The document says '
            f'<b>{esc(get(c, "text_value"))}</b>; {esc(get(c, "db_source"))} says <b>{esc(get(c, "db_value"))}</b>. '
            f'<span style="color:var(--cv-ink2);">{esc(get(c, "note"))}</span></div>')


# CONTEXT-REPLACED (R2/R3: quiet citation chips with plain names; table jargon in the sentence read as nouns):
# def summary_html(sentences: Iterable[Dict[str, Any]]) -> str:
#     items = []
#     for s in sentences or []:
#         cites = "".join(f'<span class="cv-conf">{esc(_cite(c))}</span>' for c in s.get("citations") or [])
#         items.append(f'<li>{esc(s.get("sentence"))}{cites}</li>')
#     return f'<ul style="margin:0 0 0 18px;">{"".join(items)}</ul>' if items else ""
def summary_html(sentences: Iterable[Dict[str, Any]], name: Optional[Callable[[Any], str]] = None,
                 ident: Optional[Callable[[str, str, Any], Optional[str]]] = None) -> str:
    items = []
    for s in sentences or []:
        cites = "".join(f'<span style="{QUIET_CHIP}">{esc((name or _cite)(c))}</span>' for c in s.get("citations") or [])
        text = plain_text(str(s.get("sentence") or ""), ident) if name else str(s.get("sentence") or "")
        items.append(f'<li style="margin:0 0 10px;">{esc(text)}{cites}</li>')
    return (f'<ul style="margin:0 0 0 18px;line-height:1.6;font-size:1.05rem;">{"".join(items)}</ul>'
            if items else "")
# CONTEXT-END


def _cite(c: Any) -> str:
    if isinstance(c, (list, tuple)):
        return ref_text(c)
    if isinstance(c, dict):
        return c.get("doc_id") or ref_text([c.get("source_id"), c.get("table"), c.get("pk")])
    return str(c)


def rows_stats_table(stats: Iterable[Any]) -> str:
    """Per-source transfer figures, the table view behind the bar chart."""
    body = "".join(
        f"<tr><td>{source_chip(get(s, 'source_id'))}</td><td>{int(get(s, 'rows_shipped', 0)):,}</td>"
        f"<td>{float(get(s, 'bytes_shipped', 0)) / 1024:,.1f}</td><td>{get(s, 'round_trips', 0)}</td>"
        f"<td>{float(get(s, 'latency_ms', 0)):,.0f}</td><td>{esc(get(s, 'status'))}"
        f"{' — ' + esc(get(s, 'error')) if get(s, 'error') else ''}</td></tr>" for s in stats)
    return ('<div class="cv-tablewrap"><table class="cv-table"><thead><tr><th>Database</th><th>Rows</th><th>KB</th>'
            '<th>Round trips</th><th>ms</th><th>Status</th></tr></thead><tbody>' + body + "</tbody></table></div>")


def data_moved_sentence(rows: int, baseline: Optional[int], n: int) -> str:
    """'Each database ranked its own rows and sent only its best 10 — 312 rows instead of 4,870 (94% less).'"""
    if baseline and baseline > 0:
        saved = max(0.0, 1 - rows / baseline)
        return (f"Each database ranked its own rows and sent only its best {n} — {rows:,} rows instead of "
                f"{baseline:,} ({saved:.0%} less).")
    return f"Each database ranked its own rows and sent only its best {n} — {rows:,} rows in total."


# ------------------------------------------------------------------ case file (plain language)
# CONTEXT-ADDED (R2/R3: the Explorer as a single-scroll case file; every label a person reads is a noun, not a table)

# table -> (what the record is, what its date means); one dict so the wording is reviewed in one place
TABLE_NOUNS: Dict[str, Tuple[str, str]] = {
    "VEHICLE_REGISTRATION": ("Registration", "registered"), "OWNERS": ("Owner", ""),
    "VEHICLE_IDENTITY": ("Chassis and engine record", ""), "OWNERSHIP_HISTORY": ("Ownership transfer", "on"),
    "OWNER_CONTACTS": ("Owner contact", ""), "POLICY_RECORDS": ("Insurance policy", "valid till"),
    "INSURERS": ("Insurer", ""), "POLICY_HOLDERS": ("Policy holder", ""), "POLICY_PARTIES": ("Policy parties", ""),
    "CLAIMS": ("Insurance claim", "loss on"), "CRIME_RECORDS": ("Police case", "reported"),
    "STATIONS": ("Police station", ""), "STOLEN_PROPERTY": ("Stolen-property record", "recovered"),
    "PERSONS": ("Person in police records", ""), "INCIDENT_PERSONS": ("Person named in a case", ""),
    "PLATE_CAPTURES": ("Camera sighting", "seen"), "CAMERAS": ("Camera", ""), "ZONES": ("Camera zone", ""),
    "CAMERA_ZONES": ("Camera road", ""), "CHALLANS": ("Traffic challan", "issued"),
    "POLLUTION_CERT": ("Pollution certificate", "valid till"), "TEST_CENTRES": ("Pollution test centre", ""),
    "ANALYSERS": ("Emission analyser", "calibrated"), "CERT_TESTS": ("Emission test", "tested"),
}
DOC_TYPES = {"FIR_NARRATIVE": "Police FIR", "SURVEYOR_REPORT": "Surveyor report", "RTO_NOTICE": "RTO notice",
             "ACCIDENT_REPORT": "Accident report", "CLAIM_FORM": "Claim form", "PUC_NOTICE": "Pollution notice"}
_TABLE_IN_TEXT = re.compile(r"\b(REG|INS|THEFT|CAM|PUC) ([A-Z][A-Z_]+)(?: #([\w\-]+))?")
_ENUM = re.compile(r"^[A-Z]{3,}(?:_[A-Z]+)*$")
IdentFn = Optional[Callable[[str, str, Any], Optional[str]]]


def noun(table: Any) -> str:
    t = str(table or "")
    return TABLE_NOUNS.get(t, (t.replace("_", " ").capitalize(), ""))[0]


def doc_noun(doc_type: Any) -> str:
    t = str(doc_type or "Document")
    return DOC_TYPES.get(t, t.replace("_", " ").capitalize())


def plain_value(value: Any) -> str:
    """THIRD_PARTY -> 'third party'; codes with digits or dashes (BS-IV, DL05) stay as written."""
    text = str(value)
    return text.replace("_", " ").lower() if _ENUM.match(text) else text


def plain_day(d: Any) -> str:
    return f"{d.day} {d:%b %Y}" if hasattr(d, "day") else str(d)


def plain_ref(ref: Sequence[Any], ident: IdentFn = None) -> str:
    """('INS', 'POLICY_RECORDS', 562) -> 'Insurance policy POL-000562'."""
    sid, table, pk = (list(ref) + [None, None, None])[:3]
    shown = ident(str(sid), str(table), pk) if ident else pk   # with an ident function, None hides a bare id
    return f"{noun(table)} {shown}".strip() if shown not in (None, "") else noun(table)


def plain_text(sentence: str, ident: IdentFn = None) -> str:
    """'... the strongest is INS POLICY_RECORDS #562.' -> '... the strongest is Insurance policy POL-000562.'"""
    def sub(m: "re.Match[str]") -> str:
        if m.group(2) not in TABLE_NOUNS:
            return m.group(0)
        return plain_ref([m.group(1), m.group(2), m.group(3)], ident) if m.group(3) else noun(m.group(2))
    return _TABLE_IN_TEXT.sub(sub, sentence)


def cite_namer(docs: Any, ident: IdentFn = None) -> Callable[[Any], str]:
    """A citation ('INS:POLICY_RECORDS#562', ['INS', ...], {'doc_id': 'D00312'}, 'D:D00312') -> a plain name."""
    types = {str(get(d, "doc_id")): get(d, "doc_type") for d in (get(docs, "docs") or [])}

    def name(c: Any) -> str:
        if isinstance(c, (list, tuple)):
            return plain_ref(c, ident)
        if isinstance(c, dict):
            if c.get("doc_id"):
                return f"{doc_noun(types.get(str(c['doc_id'])))} {c['doc_id']}"
            return plain_ref([c.get("source_id"), c.get("table"), c.get("pk")], ident)
        src, table, pk = node_label(c)
        if not pk:   # 'D:D00312' -- a document id, not a row
            return f"{doc_noun(types.get(table))} {table}"
        return plain_ref([src, table, pk], ident)
    return name


def heading(title: str, anchor: str, lead: Optional[str] = None) -> str:
    lead_html = f'<p class="cv-lead">{esc(lead)}</p>' if lead else ""
    return f'<p class="cv-h" id="{esc(anchor)}" style="margin-top:28px;">{esc(title)}</p>{lead_html}'


def toc(items: Sequence[Tuple[str, str]]) -> str:
    links = " · ".join(f'<a href="#{esc(a)}" style="color:var(--cv-ink2);text-decoration:none;">{esc(t)}</a>'
                       for t, a in items)
    return f'<p style="font-size:0.9rem;color:var(--cv-ink-muted);margin:6px 0 0;">{links}</p>' if items else ""


def case_header(plate: str, ident: Dict[str, str]) -> str:
    car = " ".join(ident[k] for k in ("make", "model") if ident.get(k))
    parts = [p for p in (car, ident.get("colour", "").lower(), f"owner {ident['owner']}" if ident.get("owner") else "")
             if p]
    line = (f'<p style="margin:2px 0 0;font-size:1.15rem;color:var(--cv-ink2);">{esc(" · ".join(parts))}</p>'
            if parts else '<p style="margin:2px 0 0;font-size:1rem;color:var(--cv-ink-muted);">'
                          'No registration record was found for this plate.</p>')
    return (f'<div style="margin:8px 0 14px;"><p style="margin:0;font-family:var(--cv-font-mono);font-size:2.4rem;'
            f'font-weight:700;letter-spacing:.04em;color:var(--cv-brand);line-height:1.1;">{esc(plate)}</p>{line}</div>')


def facts_line(elapsed_ms: Optional[float], agencies: int, rows: Optional[int], baseline: Optional[int],
               baseline_measured_earlier: bool = False) -> str:
    """'Answered in 0.6 s from 6 agencies · 115 rows moved instead of 17,157' -- one sentence, not a KPI wall."""
    bits = []
    if elapsed_ms is not None:
        bits.append(f"Answered in {elapsed_ms / 1000:.1f} s from {agencies} agenc{'y' if agencies == 1 else 'ies'}")
    if rows is not None:
        bits.append(f"{rows:,} rows moved" + (f" instead of {baseline:,}" if baseline else "")
                    + (" (that figure from the last evaluation run)" if baseline and baseline_measured_earlier else ""))
    return f'<p style="margin:8px 0 0;font-size:0.95rem;color:var(--cv-ink-muted);">{esc(" · ".join(bits))}</p>'


def found_via(path: Sequence[Any], via_bridge: Optional[str] = None) -> str:
    """'found via Registration → Owner → same person → Policy holder' (plain nouns; muted)."""
    steps = []
    for p in path or []:
        hop = BRIDGE_RE.match(str(p))
        steps.append(f"same {hop.group(1)}" if hop else noun(node_label(p)[1]))
    if len(steps) <= 1:
        return "found directly from the plate" if not via_bridge else f"found by matching the {via_bridge}"
    return "found via " + " → ".join(steps)


def record_line(r: Any, facts: Dict[str, Any], limit: int = 4) -> str:
    """'Insurance policy POL-000562 · TrustLine Insurers · third party · valid till 10 Jun 2026', with a muted
    'found via …' line underneath. At most `limit` values after the noun; internal ids are never shown."""
    table = get(r, "table")
    head = noun(table) + (f" {facts['id']}" if facts.get("id") else "")
    when = TABLE_NOUNS.get(str(table), ("", ""))[1]
    vals = list(facts.get("linked") or [])
    if facts.get("date") is not None:
        vals.append(f"{when} {plain_day(facts['date'])}".strip())
    vals += [plain_value(v) for v in facts.get("values") or [] if v not in vals]
    vals = list(dict.fromkeys(vals))[:limit]
    via = found_via(get(r, "path") or [], get(r, "via_bridge"))
    return (f'<div style="padding:10px 0;border-bottom:1px solid var(--cv-line);">'
            f'<p style="margin:0;font-size:1.02rem;"><b>{esc(head)}</b>'
            + "".join(f' <span style="color:var(--cv-ink-muted);">·</span> {esc(v)}' for v in vals) + "</p>"
            f'<p style="margin:2px 0 0;font-size:0.85rem;color:var(--cv-ink-muted);">{esc(via)}</p></div>')
# CONTEXT-END
