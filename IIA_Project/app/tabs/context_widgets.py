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

from components import chip, readable_ink
from theme import TOKENS

SOURCE_ORDER = ["REG", "INS", "THEFT", "CAM", "PUC", "DOCS"]
SOURCE_COLOURS = {"REG": "#2a78d6", "INS": "#eb6834", "THEFT": "#1baf7a", "CAM": "#eda100",
                  "PUC": "#e87ba4", "DOCS": "#008300"}
SOURCE_NAMES = {"REG": "Registration (RTO)", "INS": "Insurance", "THEFT": "Police", "CAM": "Road cameras",
                "PUC": "Pollution certificates", "DOCS": "Documents"}
SEVERITY = {"high": ("bad", "High"), "medium": ("warn", "Medium"), "low": ("info", "Low")}


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

def indicator_card(ind: Any) -> str:
    variant, word = SEVERITY.get(str(get(ind, "severity", "low")).lower(), ("info", "Low"))
    evidence = "".join(chip(ref_text(e), source_colour(list(e)[0] if e else "")) for e in get(ind, "evidence") or [])
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


def summary_html(sentences: Iterable[Dict[str, Any]]) -> str:
    items = []
    for s in sentences or []:
        cites = "".join(f'<span class="cv-conf">{esc(_cite(c))}</span>' for c in s.get("citations") or [])
        items.append(f'<li>{esc(s.get("sentence"))}{cites}</li>')
    return f'<ul style="margin:0 0 0 18px;">{"".join(items)}</ul>' if items else ""


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
