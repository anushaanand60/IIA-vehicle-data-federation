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


# CONTEXT-REPLACED (R3: the template sentences read like a log line ("Rto Notice D00547 ... is linked to PUC
# POLLUTION_CERT"; "the text gives recovery as recovered on ... and, the database has OPEN"). They now read as
# prose; "SID TABLE #pk" tokens stay so the page can swap them for plain nouns, and documents that matched only
# generic words (RetrievedDoc.relevant False) are not summarised):
# def template_sentences(context: ContextResult, docs: DocResult, fs: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
#     out: List[Dict[str, Any]] = []
#     by_source: Dict[str, Any] = {}
#     for r in sorted(context.rows, key=lambda r: -float(r.score or 0)):
#         by_source.setdefault(r.source_id, r)
#     if by_source:
#         best = list(by_source.values())[0]
#         out.append({"sentence": f"The context links {len(context.rows)} rows from {len(by_source)} sources; the "
#                                 f"strongest is {best.source_id} {best.table} #{best.pk}.",
#                     "citations": [row_cid(best.source_id, best.table, best.pk)]})
#     for d in docs.docs[:2]:
#         rows = ", ".join(f"{s} {t}" for s, t, _ in d.linked_rows[:3]) or "no row above the support threshold"
#         out.append({"sentence": f"{d.doc_type.replace('_', ' ').title()} {d.doc_id} ('{d.title}') is linked to {rows}.",
#                     "citations": [f"D:{d.doc_id}"] + [row_cid(*l) for l in d.linked_rows[:2]]})
#     for d in docs.docs:
#         for c in d.conflicts[:1]:
#             out.append({"sentence": f"{d.doc_id} contradicts {c.db_source}: the text gives {c.attr} as "
#                                     f"{c.text_value}, the database has {c.db_value}.", "citations": [f"D:{d.doc_id}"]})
#     known = {cid for cid, _ in fs}
#     return [s for s in out if all(c in known or c.startswith("D:") for c in s["citations"])][:MAX_SENTENCES]
DOC_NOUNS = {"FIR_NARRATIVE": "The police FIR narrative", "SURVEYOR_REPORT": "The surveyor's report",
             "RTO_NOTICE": "The RTO notice", "ACCIDENT_REPORT": "The accident report", "NEWS": "A news item",
             "WITNESS_STATEMENT": "A witness statement", "CLAIM_FORM": "The claim form"}
AGENCY_NOUNS = {"REG": "registration", "INS": "insurance", "THEFT": "police", "CAM": "camera", "PUC": "pollution"}
ATTR_WORDS = {"recovered_on": "the recovery date", "loss_date": "the date of loss", "policy_until": "the policy end date",
              "incident_date": "the theft date", "colour": "the colour", "make": "the make", "model": "the model"}
_TRAIL = re.compile(r"[\s,]+(and|or|but|then|from|with)?[\s,]*$", re.I)


def _doc_name(d: Any) -> str:
    return f"{DOC_NOUNS.get(str(d.doc_type), 'Document')} {d.doc_id}"


def _and(items: List[str]) -> str:
    return items[0] if len(items) == 1 else ", ".join(items[:-1]) + " and " + items[-1]


def _conflict_sentence(d: Any, c: Any) -> str:
    name = _doc_name(d)
    if c.attr == "recovery":
        clause = _TRAIL.sub("", str(c.text_value)).strip()
        return f"{name} contradicts the police record: it says the vehicle was {clause}, but the case is still open."
    if c.attr == "plate":
        return f"{name} contradicts the registration: it prints the chassis next to a different plate, {c.text_value}."
    what = ATTR_WORDS.get(c.attr, str(c.attr).replace("_", " "))
    agency = AGENCY_NOUNS.get(str(c.db_source), str(c.db_source))
    return f"{name} contradicts the {agency} record: it gives {what} as {c.text_value}; the record has {c.db_value}."


def template_sentences(context: ContextResult, docs: DocResult, fs: List[Tuple[str, str]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    by_source: Dict[str, Any] = {}
    for r in sorted(context.rows, key=lambda r: -float(r.score or 0)):
        by_source.setdefault(r.source_id, r)
    if by_source:
        best = list(by_source.values())[0]
        out.append({"sentence": f"{len(context.rows)} records from {len(by_source)} agencies relate to this vehicle; "
                                f"the closest is {best.source_id} {best.table} #{best.pk}.",
                    "citations": [row_cid(best.source_id, best.table, best.pk)]})
    relevant = [d for d in docs.docs if getattr(d, "relevant", True)]
    for d in [d for d in relevant if d.conflicts][:2]:   # a contradiction can change the verdict: it goes first
        out.append({"sentence": _conflict_sentence(d, d.conflicts[0]), "citations": [f"D:{d.doc_id}"]})
    for d in [d for d in relevant if not d.conflicts][:2]:
        about = _and([f"{s} {t} #{k}" for s, t, k in d.linked_rows[:3]]) if d.linked_rows else "no record closely enough to link"
        out.append({"sentence": f"{_doc_name(d)}, “{d.title}”, is about {about}.",
                    "citations": [f"D:{d.doc_id}"] + [row_cid(*l) for l in d.linked_rows[:2]]})
    known = {cid for cid, _ in fs}
    return [s for s in out if all(c in known or c.startswith("D:") for c in s["citations"])][:MAX_SENTENCES]
# CONTEXT-END


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
