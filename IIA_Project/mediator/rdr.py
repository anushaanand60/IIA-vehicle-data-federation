"""Ripple-Down Rules engine (Week 8 deck, slides 24-32).

Single-classification RDR exactly as the deck draws it:

  * slide 25 -- each node is a rule with two children, an *except* (TRUE) branch and an *else*
    (FALSE) branch; the case that prompted a rule is stored with it (the cornerstone case).
  * slide 26 -- ``if condition then conclusion [because case] except ... else if ...``; the default
    rule is ``if true then normal``; a rule that fires wrongly gets an *except* child, a rule that
    fails to fire gets an *else* sibling.
  * slide 27 -- inference: if a node's rule is true, its TRUE child is tested and the parent's
    conclusion stands only if that child is not satisfied; if a node's rule is false, its FALSE
    child is tested.
  * slide 29 -- every node entered is traced in rule-execution order, so the operator can see which
    rule broke for a given case and where to add the new rule.

Conditions are small declarative JSON predicates over a case dict, so the tree can live in
``data/rdr_rules.json`` and be shown, diffed and grown during the demo without touching code.
"""
from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Mapping, Optional, Tuple

Case = Dict[str, Any]
Lexicons = Mapping[str, Any]


class RDRConflict(ValueError):
    """A new rule was refused because it would change another rule's cornerstone conclusion."""


@dataclass
class Rule:
    """One RDR node: ``if condition then conclusion [because cornerstone] except ... else ...``."""
    id: str
    condition: Dict[str, Any]
    conclusion: Dict[str, Any]
    because: str = ""
    cornerstone: Optional[Case] = None
    except_: Optional["Rule"] = None
    if_false: Optional["Rule"] = None
    created_at: str = ""
    created_by: str = "seed"

    def to_dict(self) -> Dict[str, Any]:
        return {"id": self.id, "condition": self.condition, "conclusion": self.conclusion,
                "because": self.because, "cornerstone": self.cornerstone,
                "created_at": self.created_at, "created_by": self.created_by,
                "except": self.except_.to_dict() if self.except_ else None,
                "else": self.if_false.to_dict() if self.if_false else None}

    @classmethod
    def from_dict(cls, d: Mapping[str, Any]) -> "Rule":
        return cls(id=str(d["id"]), condition=dict(d["condition"]), conclusion=dict(d["conclusion"]),
                   because=d.get("because", ""), cornerstone=d.get("cornerstone"),
                   created_at=d.get("created_at", ""), created_by=d.get("created_by", "seed"),
                   except_=cls.from_dict(d["except"]) if d.get("except") else None,
                   if_false=cls.from_dict(d["else"]) if d.get("else") else None)


# ------------------------------------------------------------------ condition language

def _resolve(value: Any, case: Case) -> Any:
    """A string "$name" refers to another field of the same case (e.g. a registered value)."""
    if isinstance(value, str) and value.startswith("$"):
        return case.get(value[1:])
    return value


def _key(token: Any) -> str:
    return str(token).strip().lower()


def normalise_lexicons(lexicons: Optional[Lexicons]) -> Dict[str, frozenset]:
    """Each lexicon as a frozenset of lower-cased entries, built once rather than per test."""
    return {name: frozenset(_key(e) for e in entries) for name, entries in (lexicons or {}).items()}


def _in_lexicon(token: Any, name: Any, lexicons: Lexicons) -> bool:
    entries = lexicons.get(str(name)) if lexicons else None
    if token is None or entries is None:
        return False
    if not isinstance(entries, frozenset):  # a caller passed raw lists: normalise on the fly
        entries = frozenset(_key(e) for e in entries)
    return _key(token) in entries


def holds(cond: Mapping[str, Any], case: Case, lexicons: Optional[Lexicons] = None) -> bool:
    """Evaluate one declarative condition. A missing field never satisfies a comparison."""
    lexicons = lexicons or {}
    if "true" in cond:
        return bool(cond["true"])
    if "all" in cond:
        return all(holds(c, case, lexicons) for c in cond["all"])
    if "any" in cond:
        return any(holds(c, case, lexicons) for c in cond["any"])
    if "not" in cond:
        return not holds(cond["not"], case, lexicons)
    if "field" not in cond:
        raise ValueError(f"malformed condition (needs true/all/any/not or field): {cond!r}")
    actual, op = case.get(cond["field"]), cond.get("op", "eq")
    target = _resolve(cond.get("value"), case)
    if op == "is_null":
        return actual is None
    if op == "not_null":
        return actual is not None
    if op == "is_true":
        return actual is True
    if op == "is_false":
        return actual is False
    if op == "in_lexicon":
        return _in_lexicon(actual, target, lexicons)
    if op == "suffix_in_lexicon":  # slide 32: "if last 4 characters belong to Area Type"
        n = int(cond.get("n", 4))
        return actual is not None and _in_lexicon(str(actual)[-n:], target, lexicons)
    if actual is None:
        return False
    if op == "eq":
        return actual == target
    if op == "ne":
        return actual != target
    if op == "in":
        return actual in (target or [])
    if op == "not_in":
        return actual not in (target or [])
    if op == "contains_digit":
        return any(ch.isdigit() for ch in str(actual))
    if op in ("lt", "le", "gt", "ge"):
        try:
            a, b = float(actual), float(target)
        except (TypeError, ValueError):
            return False
        return {"lt": a < b, "le": a <= b, "gt": a > b, "ge": a >= b}[op]
    raise ValueError(f"unknown condition operator {op!r}")


# ------------------------------------------------------------------ the tree

class RDRTree:
    """The rule tree plus the deck's two operations: inference and knowledge acquisition."""

    def __init__(self, root: Rule, lexicons: Optional[Lexicons] = None) -> None:
        self.root = root
        self.lexicons: Lexicons = normalise_lexicons(lexicons)

    # -- inference (slide 27) ---------------------------------------------------------------
    def evaluate(self, case: Case) -> Tuple[Optional[Dict[str, Any]], List[Dict[str, Any]]]:
        """(conclusion of the last satisfied rule, path of every rule tried in order)."""
        node, conclusion, path = self.root, None, []
        while node is not None:
            fired = holds(node.condition, case, self.lexicons)
            path.append({"rule": node.id, "fired": fired, "because": node.because})
            if fired:
                conclusion = {**node.conclusion, "rule": node.id}
                node = node.except_
            else:
                node = node.if_false
        return conclusion, path

    def rules(self) -> Iterator[Rule]:
        stack = [self.root]
        while stack:
            node = stack.pop()
            yield node
            stack.extend(n for n in (node.if_false, node.except_) if n is not None)

    def find(self, rule_id: str) -> Rule:
        for rule in self.rules():
            if rule.id == rule_id:
                return rule
        raise KeyError(f"no rule {rule_id!r} in the tree")

    # -- knowledge acquisition (slides 26, 28-32) -------------------------------------------
    def add_exception(self, case: Case, wrong_rule_id: str, new_condition: Dict[str, Any],
                      new_conclusion: Dict[str, Any], because: str, branch: str = "except",
                      created_by: str = "expert") -> Rule:
        """Add a rule for a misclassified case, never editing an existing rule.

        branch="except": the wrong rule fired and gave the wrong conclusion (slide 26, "if a rule
        fires but produces an incorrect conclusion, we add an except branch").
        branch="false": the rule failed to fire (slide 26, "we add an else branch").
        """
        if branch not in ("except", "false"):
            raise ValueError("branch must be 'except' or 'false'")
        wrong = self.find(wrong_rule_id)
        # The rule must sit where this case's own evaluation path goes (slide 29: the trace "guides
        # the person to add a new rule"); a rule hung anywhere else could never see the case.
        trace = {step["rule"]: step["fired"] for step in self.evaluate(case)[1]}
        if wrong_rule_id not in trace:
            raise RDRConflict(f"{wrong_rule_id} is not on this case's evaluation path "
                              f"({' -> '.join(trace)})")
        if branch == "except" and not trace[wrong_rule_id]:
            raise RDRConflict(f"{wrong_rule_id} did not fire for this case: add on its false branch")
        if branch == "false" and trace[wrong_rule_id]:
            raise RDRConflict(f"{wrong_rule_id} fired for this case: add an exception under it")
        if not holds(new_condition, case, self.lexicons):
            raise RDRConflict("the new condition is not satisfied by the case it is meant to fix")
        new = Rule(id=self._next_id(), condition=new_condition, conclusion=new_conclusion,
                   because=because, cornerstone=copy.deepcopy(case), created_by=created_by,
                   created_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
        before = {r.id: self.evaluate(r.cornerstone)[0] for r in self.rules() if r.cornerstone}
        if branch == "except":
            if wrong.except_ is None:
                wrong.except_ = new
            else:
                self._last_sibling(wrong.except_).if_false = new
        else:
            self._last_sibling(wrong).if_false = new
        try:  # validate, and on any failure leave the tree exactly as it was
            if (self.evaluate(case)[0] or {}).get("rule") != new.id:
                raise RDRConflict("the new rule would never fire for this case: an earlier rule on "
                                  "the same branch catches it first")
            broken = [rid for rid, old in before.items()
                      if _strip(self.evaluate(self.find(rid).cornerstone)[0]) != _strip(old)]
            if broken:
                raise RDRConflict(f"refused: the new rule would change the conclusion for the "
                                  f"cornerstone case(s) of {', '.join(broken)}")
        except Exception:
            self._detach(new)
            raise
        return new

    @staticmethod
    def _last_sibling(rule: Rule) -> Rule:
        while rule.if_false is not None:
            rule = rule.if_false
        return rule

    def _detach(self, target: Rule) -> None:
        for rule in self.rules():
            if rule.except_ is target:
                rule.except_ = None
            if rule.if_false is target:
                rule.if_false = None

    def _next_id(self) -> str:
        numbers = [int(r.id[1:]) for r in self.rules() if r.id[1:].isdigit()]
        return f"R{max(numbers, default=-1) + 1}"

    # -- persistence -------------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        return {"version": 1, "root": self.root.to_dict()}

    def save(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n",
                              encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path, lexicons: Optional[Lexicons] = None) -> "RDRTree":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return cls(Rule.from_dict(data["root"]), lexicons)

    def render(self) -> List[str]:
        """One line per rule, indented by depth; ``except`` children marked +, ``else`` marked -."""
        lines: List[str] = []

        def walk(rule: Optional[Rule], depth: int, mark: str) -> None:
            while rule is not None:
                lines.append(f"{'  ' * depth}{mark} {rule.id}: if {json.dumps(rule.condition)} "
                             f"then {json.dumps(rule.conclusion)}  because {rule.because}")
                walk(rule.except_, depth + 1, "+")
                rule, mark = rule.if_false, "-"

        walk(self.root, 0, " ")
        return lines


def _strip(conclusion: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return None if conclusion is None else {k: v for k, v in conclusion.items() if k != "rule"}
