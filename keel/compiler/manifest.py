"""Manifest compiler: tool definitions in, per-tool interruption policy out.

Order of evidence (build prompt §2):
  1. explicit hints in the manifest,
  2. a classifier (phase 3) with a confidence score,
  3. otherwise `unknown`, which the kernel treats as state-changing.

Every decision carries the evidence that produced it, so the console can show
*why* a tool was classified the way it was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Protocol

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
    # (keel.paths.fast.render). Built by the compiler in phase 3.
    ack_template: Optional[str] = None


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


def compile_tool(
    spec: ToolSpec,
    classifier: Optional[Classifier] = None,
    min_confidence: float = 1.0,
) -> ToolPolicy:
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
