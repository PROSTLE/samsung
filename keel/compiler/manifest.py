"""Manifest compiler: tool definitions in, per-tool interruption policy out.

For each tool it produces
  * an argument validator (from the manifest's parameter schema),
  * a safety class, from, in order: explicit hints in the manifest; the
    lexical classifier (keel/compiler/classify.py) if it is at least
    `compiler.min_confidence` sure; otherwise `unknown`, which the kernel
    treats as state-changing,
  * a slot dependency map (argument -> session slot it reads by default),
  * an acknowledgment template that only ever describes what is being
    started, never claims it is done.

Every decision carries the evidence that produced it, so the console can show
*why* a tool was classified the way it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

from keel.compiler.schema import validate_arguments
from keel.kernel.ledger import SafetyClass
from keel.protocol.provisional import ToolSpec

# HTTP method semantics (RFC 9110 §9.2.1): GET, HEAD, OPTIONS and TRACE are
# "safe" (read-only). Everything else may change state.
_SAFE_HTTP = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    safety: SafetyClass
    # Repeating the call with the same arguments has no additional effect
    # (MCP idempotentHint semantics). Only then may an in-doubt write be retried.
    idempotent: bool
    confidence: float
    method: str  # "hint" | "classifier" | "fallback"
    evidence: tuple[str, ...]
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    # Spoken when this tool is dispatched, rendered from its arguments only
    # (keel.paths.fast.render).
    ack_template: Optional[str] = None
    required: tuple[str, ...] = ()
    # argument name -> session slot it reads unless a goal binds it otherwise.
    slot_map: dict[str, str] = field(default_factory=dict)

    def validate(self, arguments: dict[str, Any]) -> list[str]:
        return validate_arguments(self.parameters, arguments)


class Classifier(Protocol):
    def __call__(self, spec: ToolSpec) -> tuple[SafetyClass, float, tuple[str, ...]]: ...


def _bool(v: Any) -> Optional[bool]:
    return v if isinstance(v, bool) else None


def explicit_hints(spec: ToolSpec) -> Optional[tuple[SafetyClass, bool, tuple[str, ...]]]:
    """Read explicit side-effect hints if the manifest carries any.

    TODO(kit): [K17] Which hint fields real manifests carry. We accept MCP
    tool annotations, τ²-bench-style tool_type / mutates_state, a plain
    read_only / side_effects boolean, and an HTTP method.
    """
    extra = spec.model_extra or {}
    evidence: list[str] = []
    safety: Optional[SafetyClass] = None
    idempotent = False

    ann = extra.get("annotations")
    if isinstance(ann, dict):
        ro = _bool(ann.get("readOnlyHint"))
        if ro is not None:
            safety = "read_only" if ro else "state_changing"
            evidence.append(f"annotations.readOnlyHint={ro} (MCP)")
        if _bool(ann.get("idempotentHint")):
            idempotent = True
            evidence.append("annotations.idempotentHint=true (MCP)")

    if safety is None:
        for key, ro_when in (("read_only", True), ("readOnly", True), ("side_effects", False),
                             ("mutates_state", False)):
            b = _bool(extra.get(key))
            if b is not None:
                safety = "read_only" if b == ro_when else "state_changing"
                evidence.append(f"{key}={b}")
                break

    if safety is None:
        tt = extra.get("tool_type")
        if isinstance(tt, str) and tt.upper() in ("READ", "WRITE"):
            safety = "read_only" if tt.upper() == "READ" else "state_changing"
            evidence.append(f"tool_type={tt}")

    if safety is None:
        method = extra.get("method") or extra.get("http_method")
        if isinstance(method, str) and method.strip():
            m = method.strip().upper()
            safety = "read_only" if m in _SAFE_HTTP else "state_changing"
            evidence.append(f"HTTP {m} (RFC 9110 safe methods: {', '.join(sorted(_SAFE_HTTP))})")

    if safety is None:
        return None
    # A read-only call is idempotent by definition.
    return safety, idempotent or safety == "read_only", tuple(evidence)


_SCALAR_TYPES = frozenset({"string", "str", "integer", "int", "number", "float", "boolean", "bool"})


def ack_template(name: str, safety: SafetyClass, parameters: dict[str, Any]) -> str:
    """A progress phrase built from the tool's own name. Reads: "Let me check
    the reservation details for {reservation_id}." Writes (spoken only once the
    call is actually sent): "I'm sending the book reservation request now."
    Neither says the action is complete."""
    from keel.compiler.classify import split_identifier

    toks = split_identifier(name)
    head, rest = (toks[0], " ".join(toks[1:])) if len(toks) > 1 else (toks[0] if toks else "", "")
    if safety == "read_only":
        props = parameters.get("properties") or {}
        required = [a for a in (parameters.get("required") or []) if a in props]
        scalar = [a for a in required if str((props[a] or {}).get("type", "")).lower() in _SCALAR_TYPES]
        what = f"the {rest}" if rest else "that"
        suffix = f" for {{{scalar[0]}}}" if len(scalar) == 1 else ""
        return f"Let me check {what}{suffix}."
    return f"I'm sending the {head} {rest} request now." if rest else f"I'm sending that {head} request now."


def compile_tool(
    spec: ToolSpec,
    classifier: Optional[Classifier] = None,
    min_confidence: float = 1.0,
) -> ToolPolicy:
    policy = _classify(spec, classifier, min_confidence)
    props = (spec.parameters or {}).get("properties") or {}
    return ToolPolicy(
        **{**policy.__dict__,
           "ack_template": ack_template(spec.name, policy.safety, spec.parameters or {}),
           "required": tuple(a for a in (spec.parameters or {}).get("required") or [] if a in props),
           "slot_map": {a: a for a in props}},
    )


def _classify(spec: ToolSpec, classifier: Optional[Classifier], min_confidence: float) -> ToolPolicy:
    common = dict(name=spec.name, parameters=dict(spec.parameters), description=spec.description)
    hinted = explicit_hints(spec)
    if hinted is not None:
        safety, idem, ev = hinted
        return ToolPolicy(safety=safety, idempotent=idem, confidence=1.0, method="hint", evidence=ev, **common)
    if classifier is not None:
        try:
            safety, conf, ev = classifier(spec)
        except Exception as exc:  # noqa: BLE001 - any classifier failure falls back
            ev, conf, safety = (f"classifier error: {type(exc).__name__}: {exc}",), 0.0, "unknown"
        if safety != "unknown" and conf >= min_confidence:
            return ToolPolicy(safety=safety, idempotent=safety == "read_only", confidence=conf,
                              method="classifier", evidence=ev, **common)
        ev = ev + (f"confidence {conf:.2f} below threshold {min_confidence:.2f}",)
    else:
        ev = ("no explicit hint and no classifier configured",)
    return ToolPolicy(safety="unknown", idempotent=False, confidence=0.0, method="fallback",
                      evidence=ev + ("unknown is treated as state_changing",), **common)


ManifestCompiler = Callable[[list[ToolSpec]], dict[str, ToolPolicy]]


def hints_only_compiler(tools: list[ToolSpec]) -> dict[str, ToolPolicy]:
    return {t.name: compile_tool(t) for t in tools}


def default_compiler(min_confidence: float, classifier: Optional[Classifier] = None) -> ManifestCompiler:
    """Hints -> lexical classifier (or the one given) -> unknown."""
    if classifier is None:
        from keel.compiler.classify import LexicalClassifier

        classifier = LexicalClassifier()

    def compile_manifest(tools: list[ToolSpec]) -> dict[str, ToolPolicy]:
        return {t.name: compile_tool(t, classifier, min_confidence) for t in tools}

    return compile_manifest
