"""Read an FDB-v3 agent template's tools and instructions, without importing it.

The benchmark defines its 12 tools as ``@function_tool`` methods in its own
agent templates (FDB-v3 ``v3/cascaded_agent.py`` and ``v3/lk_agent_tool.py``).
Keel reads those definitions from the file itself, so the names, parameters
and descriptions the LLM sees are exactly the benchmark's: nothing is copied
into this repository, and nothing needs updating by hand if the benchmark
changes a tool.

The file is parsed with ``ast``, not imported. Importing a template runs its
module-level code (it creates a mock registry and edits ``sys.argv``).

What is read:
  * every ``async def`` whose decorator is a call named ``function_tool``,
    ``ai_callable`` or ``ai_callable_decorator`` (the templates alias
    ``llm.function_tool`` to ``ai_callable_decorator``);
  * its ``description=`` keyword, parameters, type annotations, literal
    defaults and the ``Args:`` section of its docstring;
  * the ``instructions=`` string an ``Agent`` subclass passes to
    ``super().__init__``.
"""

from __future__ import annotations

import ast
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from keel.protocol.provisional import ToolSpec

_DECORATORS = frozenset({"function_tool", "ai_callable", "ai_callable_decorator"})
# Python annotation -> JSON Schema type. Anything else is left unconstrained.
_TYPES = {"str": "string", "int": "integer", "float": "number", "bool": "boolean",
          "dict": "object", "list": "array"}
_ARG_LINE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?:\([^)]*\))?\s*:\s*(.+)$")


class TemplateError(ValueError):
    """The template does not have the shape this reader expects."""


@dataclass(frozen=True)
class TemplateTool:
    spec: ToolSpec
    # Literal defaults from the signature. The templates pass them to the mock
    # API and write them in the tool log, so Keel does the same.
    defaults: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Template:
    path: Path
    tools: tuple[TemplateTool, ...]
    instructions: Optional[str]

    def specs(self) -> list[ToolSpec]:
        return [t.spec for t in self.tools]

    def tool(self, name: str) -> TemplateTool:
        for t in self.tools:
            if t.spec.name == name:
                return t
        raise KeyError(name)


def _decorator_name(node: ast.expr) -> Optional[str]:
    target = node.func if isinstance(node, ast.Call) else node
    if isinstance(target, ast.Name):
        return target.id
    if isinstance(target, ast.Attribute):
        return target.attr
    return None


def _annotation_type(node: Optional[ast.expr]) -> Optional[str]:
    if node is None:
        return None
    if isinstance(node, ast.Name):
        return _TYPES.get(node.id)
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return _TYPES.get(node.value)
    # Optional[X] / X | None: the non-None member decides the type.
    if isinstance(node, ast.Subscript) and _decorator_name(node.value) == "Optional":
        return _annotation_type(node.slice)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr):
        for side in (node.left, node.right):
            if not (isinstance(side, ast.Constant) and side.value is None):
                return _annotation_type(side)
    return None


def _arg_docs(docstring: Optional[str]) -> dict[str, str]:
    """The ``Args:`` section of a Google-style docstring, one line per argument."""
    out: dict[str, str] = {}
    if not docstring:
        return out
    in_args = False
    for line in docstring.splitlines():
        stripped = line.strip()
        if stripped.lower() in ("args:", "arguments:"):
            in_args = True
            continue
        if in_args:
            if not stripped:
                continue
            if stripped.endswith(":") and " " not in stripped:
                break  # next section, e.g. "Returns:"
            m = _ARG_LINE.match(stripped)
            if m:
                out[m.group(1)] = m.group(2).strip()
    return out


def _tool_from(fn: ast.AsyncFunctionDef, deco: ast.Call) -> TemplateTool:
    if fn.args.kwonlyargs or fn.args.posonlyargs or fn.args.vararg or fn.args.kwarg:
        # Only plain positional parameters are read; anything else would be
        # silently missing from the schema the LLM sees.
        raise TemplateError(f"{fn.name}: only plain parameters are supported (no *, **, / or keyword-only)")
    description = ""
    for kw in deco.keywords:
        if kw.arg == "description":
            description = ast.literal_eval(kw.value)
    docs = _arg_docs(ast.get_docstring(fn))
    if not description:
        # function_tool falls back to the docstring's description.
        doc = ast.get_docstring(fn) or ""
        description = doc.split("\n\n", 1)[0].strip() if doc and not doc.lstrip().lower().startswith("args") else ""

    params = [a for a in fn.args.args if a.arg not in ("self", "cls")]
    defaults_nodes = fn.args.defaults
    first_default = len(fn.args.args) - len(defaults_nodes)
    properties: dict[str, Any] = {}
    required: list[str] = []
    defaults: dict[str, Any] = {}
    for i, a in enumerate(fn.args.args):
        if a.arg in ("self", "cls"):
            continue
        prop: dict[str, Any] = {}
        t = _annotation_type(a.annotation)
        if t:
            prop["type"] = t
        if a.arg in docs:
            prop["description"] = docs[a.arg]
        if i >= first_default:
            value = ast.literal_eval(defaults_nodes[i - first_default])
            defaults[a.arg] = value
            if value is None:
                # `x: float = None` accepts an explicit null as well.
                if "type" in prop:
                    prop["type"] = [prop["type"], "null"]
            else:
                prop["default"] = value
        else:
            required.append(a.arg)
        properties[a.arg] = prop
    if not params:
        properties = {}
    spec = ToolSpec(name=fn.name, description=description,
                    parameters={"type": "object", "properties": properties, "required": required})
    return TemplateTool(spec=spec, defaults=defaults)


def _instructions(tree: ast.Module) -> Optional[str]:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        if not any(_decorator_name(b) == "Agent" for b in node.bases):
            continue
        for call in ast.walk(node):
            if (isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute)
                    and call.func.attr == "__init__"):
                for kw in call.keywords:
                    if kw.arg == "instructions":
                        return ast.literal_eval(kw.value)
    return None


def read_template(path: Path | str) -> Template:
    path = Path(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    tools: list[TemplateTool] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.AsyncFunctionDef):
            continue
        for deco in node.decorator_list:
            if isinstance(deco, ast.Call) and _decorator_name(deco) in _DECORATORS:
                tools.append(_tool_from(node, deco))
                break
    if not tools:
        raise TemplateError(f"no @function_tool methods found in {path}")
    names = [t.spec.name for t in tools]
    if len(set(names)) != len(names):
        raise TemplateError(f"duplicate tool names in {path}: {names}")
    return Template(path=path, tools=tuple(tools), instructions=_instructions(tree))
