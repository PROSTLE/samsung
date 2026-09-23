"""Derive read-only / state-changing labels from a tool's reference
implementation, by static analysis of whether it mutates its environment.

This gives ground truth for tools whose authors published an implementation
but no read/write label (τ-bench v1, BFCL). The code is parsed, never run.

Two-kind taint analysis (flow-insensitive, conservative):
  E  an object that *is* part of the environment (`data` / `self`, anything
     reached from it by subscript, attribute, iteration or method return);
  C  a *fresh* container holding environment objects (a list/dict literal or
     comprehension over E, a shallow .copy(), list()/sorted()/...).
Writing into an E object mutates state. Writing into a C object does not
(it's a new container), but its elements are E. deepcopy() and scalar
conversions produce untainted values.

A function mutates state if it assigns/deletes through an E subscript or
attribute, or calls a mutating method on an E object: a built-in container
mutator (append, update, ...) or any method, of any class in the same
module, that itself mutates `self` (computed to a fixed point).
"""

from __future__ import annotations

import ast
from dataclasses import dataclass, field
from typing import Iterable, Optional

BUILTIN_MUTATORS = frozenset({
    "append", "extend", "insert", "pop", "popitem", "remove", "clear", "update",
    "setdefault", "add", "discard", "sort", "reverse", "__setitem__", "__delitem__",
})
# Calls whose result shares nothing mutable with their arguments.
FRESH_CALLS = frozenset({
    "deepcopy", "str", "int", "float", "bool", "len", "round", "abs", "sum", "isinstance",
    "hash", "repr", "dumps", "format", "any", "all", "lower", "upper", "strip", "split",
    "join", "startswith", "endswith", "replace", "strftime", "strptime", "isoformat",
})
# Calls that build a new container around the argument's elements.
CONTAINER_CALLS = frozenset({"list", "dict", "set", "tuple", "sorted", "reversed", "filter", "map",
                             "zip", "enumerate", "copy"})

Kind = Optional[str]  # "E", "C" or None


def _join(*kinds: Kind) -> Kind:
    return "E" if "E" in kinds else "C" if "C" in kinds else None


@dataclass
class Effects:
    mutates: bool = False
    evidence: list[str] = field(default_factory=list)
    calls_self: set[str] = field(default_factory=set)


def _call_name(func: ast.expr) -> Optional[str]:
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


class _Analyser:
    def __init__(self, fn: ast.FunctionDef, roots: Iterable[str], mutators: frozenset[str]) -> None:
        self.fn = fn
        self.kinds: dict[str, str] = {r: "E" for r in roots}
        self.mutators = BUILTIN_MUTATORS | mutators
        self.fx = Effects()

    def kind(self, e: Optional[ast.expr]) -> Kind:
        if e is None:
            return None
        if isinstance(e, ast.Name):
            return self.kinds.get(e.id)
        if isinstance(e, (ast.Subscript, ast.Attribute)):
            return "E" if self.kind(e.value) else None
        if isinstance(e, ast.Starred):
            return self.kind(e.value)
        if isinstance(e, ast.Call):
            name = _call_name(e.func)
            owner = self.kind(e.func.value) if isinstance(e.func, ast.Attribute) else None
            args = _join(*(self.kind(a) for a in e.args), *(self.kind(k.value) for k in e.keywords))
            if name in FRESH_CALLS:
                return None
            if name in CONTAINER_CALLS and (owner or args):
                return "C"
            if owner:
                return "E"  # a method on an environment object may return part of it
            return "E" if args else None
        if isinstance(e, ast.IfExp):
            return _join(self.kind(e.body), self.kind(e.orelse))
        if isinstance(e, ast.BoolOp):
            return _join(*(self.kind(v) for v in e.values))
        if isinstance(e, (ast.List, ast.Tuple, ast.Set)):
            return "C" if any(self.kind(v) for v in e.elts) else None
        if isinstance(e, ast.Dict):
            return "C" if any(self.kind(v) for v in e.values) else None
        if isinstance(e, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            return "C" if any(self.kind(g.iter) for g in e.generators) else None
        if isinstance(e, ast.NamedExpr):
            return self.kind(e.value)
        return None

    def bind(self, target: ast.expr, kind: Kind) -> None:
        if kind is None:
            return
        if isinstance(target, ast.Name):
            self.kinds[target.id] = _join(self.kinds.get(target.id), kind) or kind
        elif isinstance(target, (ast.Tuple, ast.List)):
            for t in target.elts:
                self.bind(t, kind)
        elif isinstance(target, ast.Starred):
            self.bind(target.value, kind)

    def note(self, node: ast.AST, what: str) -> None:
        self.fx.mutates = True
        self.fx.evidence.append(f"line {getattr(node, 'lineno', '?')}: {what}")

    def check_target(self, t: ast.expr, node: ast.AST) -> None:
        if isinstance(t, (ast.Subscript, ast.Attribute)) and self.kind(t.value) == "E":
            self.note(node, f"writes {ast.unparse(t)}")
        elif isinstance(t, (ast.Tuple, ast.List)):
            for x in t.elts:
                self.check_target(x, node)

    def run(self) -> Effects:
        # Repeat so a binding made late in a loop body reaches earlier lines.
        for _ in range(3):
            for node in ast.walk(self.fn):
                if isinstance(node, ast.Assign):
                    if (len(node.targets) == 1 and isinstance(node.targets[0], ast.Tuple)
                            and isinstance(node.value, ast.Tuple)
                            and len(node.targets[0].elts) == len(node.value.elts)):
                        for t, v in zip(node.targets[0].elts, node.value.elts):
                            self.bind(t, self.kind(v))
                    else:
                        for t in node.targets:
                            self.bind(t, self.kind(node.value))
                elif isinstance(node, ast.AnnAssign) and node.value is not None:
                    self.bind(node.target, self.kind(node.value))
                elif isinstance(node, (ast.For, ast.AsyncFor)):
                    self.bind(node.target, "E" if self.kind(node.iter) else None)
                elif isinstance(node, ast.comprehension):
                    self.bind(node.target, "E" if self.kind(node.iter) else None)
                elif isinstance(node, ast.With):
                    for item in node.items:
                        if item.optional_vars is not None:
                            self.bind(item.optional_vars, self.kind(item.context_expr))
                elif isinstance(node, ast.NamedExpr):
                    self.bind(node.target, self.kind(node.value))
        for node in ast.walk(self.fn):
            if isinstance(node, ast.Assign):
                for t in node.targets:
                    self.check_target(t, node)
            elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                self.check_target(node.target, node)
            elif isinstance(node, ast.Delete):
                for t in node.targets:
                    self.check_target(t, node)
            elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                owner = node.func.value
                if isinstance(owner, ast.Name) and owner.id == "self":
                    self.fx.calls_self.add(node.func.attr)
                elif node.func.attr in self.mutators and self.kind(owner) == "E":
                    self.note(node, f"calls {ast.unparse(node.func)}()")
        self.fx.evidence = sorted(set(self.fx.evidence), key=_line_of)
        return self.fx


def _line_of(s: str) -> int:
    token = s.split()[1].rstrip(":") if len(s.split()) > 1 else ""
    return int(token) if token.isdigit() else 0


def function_effects(fn: ast.FunctionDef, roots: Iterable[str],
                     mutators: frozenset[str] = frozenset()) -> Effects:
    return _Analyser(fn, roots, mutators).run()


def tau1_invoke_effects(source: str) -> Optional[Effects]:
    """τ-bench v1: does the `invoke(data, ...)` static method mutate `data`?"""
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.FunctionDef) and node.name == "invoke":
            root = node.args.args[0].arg if node.args.args else "data"
            return function_effects(node, [root])
    return None


def class_method_effects(source: str) -> dict[str, Effects]:
    """BFCL: for every public method of every class, does it mutate state,
    directly or through methods (of any class in the module) it calls?"""
    tree = ast.parse(source)
    methods: list[tuple[str, ast.FunctionDef]] = []
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        methods += [(cls.name, f) for f in cls.body if isinstance(f, ast.FunctionDef)]

    mutators: frozenset[str] = frozenset()
    while True:  # fixed point: a method mutates if it mutates self or calls one that does
        fx = {(c, f.name): function_effects(f, ["self"], mutators) for c, f in methods}
        for (cls, name), e in fx.items():
            for callee in sorted(e.calls_self):
                if (cls, callee) in fx and callee in mutators and not e.mutates:
                    e.mutates = True
                    e.evidence.append(f"calls self.{callee}(), which mutates state")
        new = frozenset(name for (_, name), e in fx.items() if e.mutates and name != "__init__")
        if new == mutators:
            break
        mutators = new
    return {name: e for (_, name), e in fx.items() if not name.startswith("_")}
