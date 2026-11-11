"""CONTEXT-ADDED (R3: integrate the documents with the rows in one short, cited summary).

summarise(context, docs) returns 3-5 sentences, each with the ids of the facts it rests on:
  [D:D00036]                       a retrieved document
  [REG:VEHICLE_REGISTRATION#601]   a context row
With the local model (qwen2.5:3b via mediator.llm, temperature 0, cached) the sentences are written from a numbered
fact list and then POST-VALIDATED: a sentence is dropped unless every citation names a fact we gave it AND every
identifier/number it states appears in the facts it cites (a cheap guard against invented plates or dates).
Without the model, or if nothing survives validation, template sentences are built from the same facts.
Never raises: the summary is decoration, the rows and documents are the evidence.
"""
from __future__ import annotations

import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from mediator.context_types import ContextResult, DocResult
from mediator.doc_conflicts import text_dates

CITE = re.compile(r"\[([A-Z]+):([^\]\s]+)\]")
MAX_ROWS, MAX_DOCS, MAX_SENTENCES = 8, 4, 5
SKIP_COLS = re.compile(r"(^|_)(id|lat|lon|mobile|pincode|premium_inr|ocr_confidence|co_pct|hc_ppm)$", re.I)


def row_cid(source_id: str, table: str, pk: Any) -> str:
    return f"{source_id}:{table}#{pk}"


def facts(context: ContextResult, docs: DocResult) -> List[Tuple[str, str]]:
    """(citation id, one-line fact) for the strongest rows, the top documents and every flagged conflict."""
    out: List[Tuple[str, str]] = []
    for r in sorted(context.rows, key=lambda r: -float(r.score or 0))[:MAX_ROWS]:
        cols = [f"{k}={v}" for k, v in r.row.items() if v not in (None, "") and not SKIP_COLS.search(str(k))]
        out.append((row_cid(r.source_id, r.table, r.pk), f"{r.source_id} {r.table}: " + ", ".join(cols)[:220]))
    for d in docs.docs[:MAX_DOCS]:
        out.append((f"D:{d.doc_id}", f"{d.doc_type} '{d.title}': {d.snippet[:260]}"))
        for c in d.conflicts:
            out.append((f"D:{d.doc_id}", f"CONFLICT in {d.doc_id}: text says {c.attr} = {c.text_value}, "
                                          f"{c.db_source} says {c.db_value}"))
    return out


def _numbers(text: str) -> List[str]:
    return [re.sub(r"[^A-Z0-9]", "", t.upper()) for t in re.findall(r"[A-Za-z0-9/-]*\d[A-Za-z0-9/-]*", text)]


def validate(sentence: str, known: Dict[str, str]) -> Optional[Dict[str, Any]]:
    """Keep a sentence only if it cites known facts and states no identifier/number missing from them."""
    cites = [f"{a}:{b}" for a, b in CITE.findall(sentence)]
    valid = [c for c in dict.fromkeys(cites) if c in known]
    if not valid or len(valid) < len(set(cites)):
        return None
    text = CITE.sub("", sentence).strip(" -*•\t")
    text = re.sub(r"\s+([.,;])", r"\1", re.sub(r"\s{2,}", " ", text))
    cited = " ".join(known[c] for c in valid)
    # dates may be re-worded ("28th February 2027" for 2027-02-28): compared as dates, then removed
    allowed = {d for d, _, _ in text_dates(cited)}
    said = text_dates(text)
    if any(d not in allowed for d, _, _ in said):
        return None
    rest = text
    for _, s, e in sorted(said, key=lambda x: -x[1]):
        rest = rest[:s] + " " + rest[e:]
    support = re.sub(r"[^A-Z0-9]", "", cited.upper())
    if any(n and n not in support for n in _numbers(rest)):
        return None
    return {"sentence": text, "citations": valid} if len(text) > 12 else None


def sentences(raw: str) -> List[str]:
    """One sentence per line; a line holding only citations belongs to the line after it (models do both)."""
    out: List[str] = []
    pending = ""
    for line in [l.strip() for l in raw.splitlines() if l.strip()]:
        if not CITE.sub("", line).strip(" -*.•"):
            pending += " " + line
            continue
        out.append((line + pending).strip())
        pending = ""
    return out


def _prompt(fs: List[Tuple[str, str]]) -> str:
    lines = "\n".join(f"[{cid}] {fact}" for cid, fact in fs)
    return ("You summarise evidence about one vehicle for a police analyst. Use ONLY the facts below. Write 3 to 5 "
            "short sentences, one per line. End every sentence with the id(s) in square brackets of the fact(s) it "
            "uses, exactly as written, e.g. [D:D00036] or [REG:VEHICLE_REGISTRATION#601]. Mention any CONFLICT. "
            "Do not invent names, numbers or dates.\n\nFACTS:\n" + lines + "\n\nSUMMARY:")


def template_sentences(context: ContextResult, docs: DocResult, fs: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    by_source: Dict[str, Any] = {}
    for r in sorted(context.rows, key=lambda r: -float(r.score or 0)):
        by_source.setdefault(r.source_id, r)
    if by_source:
        best = list(by_source.values())[0]
        out.append({"sentence": f"The context links {len(context.rows)} rows from {len(by_source)} sources; the "
                                f"strongest is {best.source_id} {best.table} #{best.pk}.",
                    "citations": [row_cid(best.source_id, best.table, best.pk)]})
    for d in docs.docs[:2]:
        rows = ", ".join(f"{s} {t}" for s, t, _ in d.linked_rows[:3]) or "no row above the support threshold"
        out.append({"sentence": f"{d.doc_type.replace('_', ' ').title()} {d.doc_id} ('{d.title}') is linked to {rows}.",
                    "citations": [f"D:{d.doc_id}"] + [row_cid(*l) for l in d.linked_rows[:2]]})
    for d in docs.docs:
        for c in d.conflicts[:1]:
            out.append({"sentence": f"{d.doc_id} contradicts {c.db_source}: the text gives {c.attr} as "
                                    f"{c.text_value}, the database has {c.db_value}.", "citations": [f"D:{d.doc_id}"]})
    known = {cid for cid, _ in fs}
    return [s for s in out if all(c in known or c.startswith("D:") for c in s["citations"])][:MAX_SENTENCES]


def summarise(context: ContextResult, docs: DocResult, use_llm: bool = True,
              llm: Optional[Callable[[str], Optional[str]]] = None) -> List[Dict[str, Any]]:
    try:
        fs = facts(context, docs)
        if not fs:
            return []
        if use_llm:
            if llm is None:
                from mediator.llm import generate_text
                llm = lambda p: generate_text(p, timeout_s=90)  # noqa: E731   (a 3B model on a laptop CPU: ~30 s)
            try:
                raw = llm(_prompt(fs)) or ""
            except Exception:  # model crashed / unreachable: same as "no model"
                raw = ""
            known: Dict[str, str] = {}
            for cid, fact in fs:
                known[cid] = (known.get(cid, "") + " " + fact).strip()
            kept = [v for line in sentences(raw) if (v := validate(line, known))]
            if kept:
                # a flagged conflict must reach the reader even if the model left it out or it fell past the cut
                conflicted = [f"D:{d.doc_id}" for d in docs.docs if d.conflicts]
                if conflicted and not any(set(s["citations"]) & set(conflicted) for s in kept[:MAX_SENTENCES - 1]):
                    extra = [s for s in template_sentences(context, docs, fs) if "contradicts" in s["sentence"]][:1]
                    kept = kept[:MAX_SENTENCES - len(extra)] + extra
                return kept[:MAX_SENTENCES]
        return template_sentences(context, docs, fs)
    except Exception:  # a summary failure must never take the page down
        return []
