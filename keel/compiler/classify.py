"""Safety classifiers for tools that carry no explicit side-effect hint.

LexicalClassifier (default): L2-regularised logistic regression over tokens
from the tool name, description and parameter names, trained on the gold set
in data/labels/gold.jsonl. Pure Python, deterministic, ~0.1 ms per tool (measured: phase 3 report),
no warm-up, no network. Each decision comes with the features that drove it.

PromptClassifier (optional): an LLM asked with a fixed prompt and few-shot
examples drawn from the same real tools; its output must parse as a strict
JSON schema or it counts as a failure (and the compiler falls back).
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterable, Literal, Optional, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from keel.kernel.ledger import SafetyClass
from keel.protocol.provisional import ToolSpec

MODEL_PATH = Path(__file__).resolve().parent / "model.json"

_STOP = frozenset("""
a an the and or of to for in on at by with from into this that these those is are be been it its as
if then than any all each can will may must should would your you user users their our we us not no
given specified specific provided provide based using use used via which who whom whose when where
""".split())
_CAMEL = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def split_identifier(name: str) -> list[str]:
    return [t.lower() for part in re.split(r"[_\-.\s/]+", name) for t in _CAMEL.findall(part)]


def stem(tok: str) -> str:
    for suf in ("ing", "ed", "es", "s"):
        if tok.endswith(suf) and len(tok) - len(suf) >= 3:
            return tok[: -len(suf)]
    return tok


def description_tokens(description: str) -> list[str]:
    return [stem(t) for t in re.findall(r"[a-z]+", description.lower()) if t not in _STOP and len(t) > 1][:80]


def idf_table(descriptions: Iterable[str]) -> dict[str, float]:
    """Inverse document frequency over tool descriptions. Words shared by most
    tools (boilerplate such as "this tool belongs to the ... system") get
    weight near zero, so they cannot masquerade as evidence."""
    docs = [set(description_tokens(d)) for d in descriptions]
    df: dict[str, int] = {}
    for d in docs:
        for t in d:
            df[t] = df.get(t, 0) + 1
    n = len(docs)
    return {t: math.log((n + 1) / (c + 1)) for t, c in df.items()}


def features(name: str, description: str, param_names: Iterable[str],
             idf: Optional[dict[str, float]] = None) -> dict[str, float]:
    f: dict[str, float] = {"bias": 1.0}
    ntoks = [stem(t) for t in split_identifier(name)]
    for t in ntoks:
        f[f"name:{t}"] = 1.0
    if ntoks:
        f[f"first:{ntoks[0]}"] = 1.0
    dtoks = description_tokens(description)
    # Unseen words get the maximum idf: rare, hence potentially informative.
    default_idf = max(idf.values()) if idf else 1.0
    weights = {t: (idf.get(t, default_idf) if idf is not None else 1.0) for t in set(dtoks)}
    norm = math.sqrt(sum(w * w for w in weights.values())) or 1.0
    for t, w in sorted(weights.items()):
        if w > 0:
            f[f"desc:{t}"] = w / norm
    for p in param_names:
        for t in split_identifier(p):
            f[f"param:{stem(t)}"] = 0.5
    return f


def spec_features(spec: ToolSpec, idf: Optional[dict[str, float]] = None) -> dict[str, float]:
    props = (spec.parameters or {}).get("properties") or {}
    return features(spec.name, spec.description, props.keys(), idf)


def _sigmoid(z: float) -> float:
    return 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))


@dataclass
class LexicalModel:
    weights: dict[str, float]
    meta: dict

    def p_write(self, feats: dict[str, float]) -> float:
        return _sigmoid(sum(self.weights.get(k, 0.0) * v for k, v in feats.items()))

    def explain(self, feats: dict[str, float], top: int = 4) -> list[str]:
        contrib = sorted(((self.weights.get(k, 0.0) * v, k) for k, v in feats.items() if k != "bias"),
                         key=lambda c: -abs(c[0]))
        out = []
        for c, k in contrib[:top]:
            if abs(c) < 1e-3:
                break
            kind, _, tok = k.partition(":")
            out.append(f"{kind} token '{tok}' -> {'write' if c > 0 else 'read'} ({c:+.2f})")
        return out

    @property
    def idf(self) -> Optional[dict[str, float]]:
        return self.meta.get("idf")

    def to_json(self) -> str:
        meta = dict(self.meta)
        if "idf" in meta:
            meta["idf"] = {k: round(v, 6) for k, v in sorted(meta["idf"].items())}
        return json.dumps({"meta": meta, "weights": {k: round(v, 6) for k, v in sorted(self.weights.items())}},
                          indent=1, sort_keys=True)

    @classmethod
    def load(cls, path: Path = MODEL_PATH) -> "LexicalModel":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(weights=data["weights"], meta=data["meta"])


def train(samples: Sequence[tuple[dict[str, float], int]], *, l2: float = 0.01, epochs: int = 600,
          lr: float = 0.5, meta: Optional[dict] = None) -> LexicalModel:
    """Full-batch gradient descent from zero weights: deterministic."""
    w: dict[str, float] = {}
    n = len(samples)
    for _ in range(epochs):
        grad: dict[str, float] = {}
        for feats, y in samples:
            err = _sigmoid(sum(w.get(k, 0.0) * v for k, v in feats.items())) - y
            for k, v in feats.items():
                grad[k] = grad.get(k, 0.0) + err * v
        for k, g in grad.items():
            reg = 0.0 if k == "bias" else l2 * w.get(k, 0.0)
            w[k] = w.get(k, 0.0) - lr * (g / n + reg)
    return LexicalModel(weights=w, meta=meta or {})


class LexicalClassifier:
    def __init__(self, model: Optional[LexicalModel] = None) -> None:
        self.model = model or LexicalModel.load()

    def __call__(self, spec: ToolSpec) -> tuple[SafetyClass, float, tuple[str, ...]]:
        feats = spec_features(spec, self.model.idf)
        p = self.model.p_write(feats)
        safety: SafetyClass = "state_changing" if p >= 0.5 else "read_only"
        conf = p if p >= 0.5 else 1.0 - p
        ev = (f"lexical model p(write)={p:.2f}",) + tuple(self.model.explain(feats))
        return safety, conf, ev


# --------------------------------------------------------------------------
# optional LLM classifier
# --------------------------------------------------------------------------

class _Verdict(BaseModel):
    model_config = ConfigDict(extra="forbid")
    safety: Literal["read_only", "state_changing"]
    confidence: float = Field(ge=0.0, le=1.0)
    reason: str = Field(max_length=300)


PROMPT_HEADER = (
    "You classify tools for a voice assistant. A tool is read_only if calling it can never change "
    "anything outside the conversation (no booking, sending, deleting, updating, logging in, or handing "
    "off). Otherwise it is state_changing. When unsure, answer state_changing.\n"
    'Reply with only JSON: {"safety": "read_only" | "state_changing", "confidence": 0..1, "reason": "..."}\n'
)


def _describe(name: str, description: str, params: Iterable[str]) -> str:
    return f"name: {name}\ndescription: {description.strip()[:400]}\nparameters: {', '.join(params) or '(none)'}"


class PromptClassifier:
    """Wraps any text-completion function. The model backend is chosen elsewhere."""

    def __init__(self, complete: Callable[[str], str], examples: Sequence[dict]) -> None:
        self.complete = complete
        shots = []
        for ex in examples:
            shots.append(_describe(ex["name"], ex["description"], [p["name"] for p in ex["params"]])
                         + f'\nanswer: {{"safety": "{ex["label"]}", "confidence": 1.0, "reason": "example"}}')
        self.prefix = PROMPT_HEADER + "\n\n".join(shots) + "\n\n"

    def prompt(self, spec: ToolSpec) -> str:
        props = (spec.parameters or {}).get("properties") or {}
        return self.prefix + _describe(spec.name, spec.description, props.keys()) + "\nanswer: "

    def __call__(self, spec: ToolSpec) -> tuple[SafetyClass, float, tuple[str, ...]]:
        raw = self.complete(self.prompt(spec))
        match = re.search(r"\{.*\}", raw, re.DOTALL)
        if not match:
            raise ValueError("no JSON object in model output")
        try:
            v = _Verdict.model_validate_json(match.group(0))
        except ValidationError as exc:
            raise ValueError(f"model output failed schema: {exc.error_count()} errors") from exc
        return v.safety, v.confidence, (f"LLM: {v.reason}",)
