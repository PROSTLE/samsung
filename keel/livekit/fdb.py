"""FDB-v3 tool backend: the benchmark's own mock APIs, and the log it scores.

Execution and logging match FDB-v3's agent templates (``v3/cascaded_agent.py``,
``AssistantFnc``):
  * the call goes to ``mock_apis.MockAPIRegistry(latency_profile).call(name,
    **args)`` with the signature's defaults filled in, as the templates do;
  * one JSON line per *executed* call is appended to the tool log,
    ``{"room": ..., "call": {"function", "args", "timestamp_start",
    "timestamp_end"}}``. That line is what the FDB-v3 runner turns into
    ``actual_tool_calls`` (run_tool_benchmark.py, step 6), so a call Keel never
    executes is never scored.

Two deliberate differences:
  * The registry is created per session, not once per process, so the
    injector's per-API call counters (progressive latency) never carry over
    from one scenario to the next (guide §6: "Don't cache anything across
    scenarios").
  * The registry call, which sleeps to simulate latency, runs in a worker
    thread, so the agent's audio and event loop keep running while a tool is
    "loading" (the templates call it inline in an async function).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import random
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable, Optional

from keel.livekit.template import Template


# Parallel tool calls run in worker threads; appends to one file from several
# threads can interleave, and the runner parses the log line by line. Every
# line is written whole, under this lock, in a single write.
_LOG_LOCK = threading.Lock()


def load_mock_registry(fdb_dir: Path, latency_profile: str) -> Any:
    """Import FDB-v3's mock_apis.py from its checkout and build a registry."""
    fdb_dir = Path(fdb_dir)
    path = fdb_dir / "mock_apis.py"
    if not path.exists():
        raise FileNotFoundError(f"FDB-v3 mock_apis.py not found at {path} (set KEEL_FDB_DIR)")
    # mock_apis does `from latency_injector import ...`, so its directory must be importable.
    if str(fdb_dir) not in sys.path:
        sys.path.insert(0, str(fdb_dir))
    spec = importlib.util.spec_from_file_location("fdb_mock_apis", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.MockAPIRegistry(latency_profile=latency_profile)


class FdbBackend:
    def __init__(self, *, template: Template, registry: Any, room: str, tool_log: Path | str,
                 seed: int, clock: Callable[[], float] = time.time,
                 on_executed: Optional[Callable[[str, float, float], None]] = None) -> None:
        self.template = template
        self.registry = registry
        self.room = room
        self.tool_log = Path(tool_log)
        self.tool_log.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock
        self._on_executed = on_executed
        # latency_injector draws jitter from the module-level `random`; seed it
        # once per session so a re-run reproduces the same delays.
        random.seed(seed)

    def arguments(self, tool: str, args: dict[str, Any]) -> dict[str, Any]:
        """The LLM's arguments with the signature's defaults filled in and
        scalars in the schema's type: 1 and 1.0 are one float argument, and a
        number or boolean given for a string parameter is its string form (FDB-v3
        data has update_search_filter(value=1800) and (value=True) against `value: str`)."""
        t = self.template.tool(tool)
        out = {**t.defaults, **args}
        props = t.spec.parameters.get("properties") or {}
        for k, v in out.items():
            types = props.get(k, {}).get("type")
            types = types if isinstance(types, list) else [types]
            if isinstance(v, bool):
                if "string" in types and "boolean" not in types:
                    out[k] = "true" if v else "false"
                continue
            if "number" in types and isinstance(v, int):
                out[k] = float(v)
            elif "integer" in types and isinstance(v, float) and v.is_integer():
                out[k] = int(v)
            elif "string" in types and not ({"number", "integer"} & set(types)) and isinstance(v, (int, float)):
                out[k] = str(int(v)) if isinstance(v, float) and v.is_integer() else str(v)
        return out

    def _run(self, tool: str, args: dict[str, Any]) -> tuple[dict[str, Any], float, float]:
        t_start = self._clock()
        result = self.registry.call(tool, **args)
        t_end = self._clock()
        line = json.dumps({"room": self.room, "call": {
            "function": tool, "args": args, "timestamp_start": t_start, "timestamp_end": t_end}}) + "\n"
        with _LOG_LOCK, open(self.tool_log, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
        return result, t_start, t_end

    async def execute(self, tool: str, args: dict[str, Any]) -> tuple[str, Any, Optional[str]]:
        """Run one call. Returns (status, result, error) in Keel's ToolResult terms."""
        full = self.arguments(tool, args)
        try:
            result, t_start, t_end = await asyncio.to_thread(self._run, tool, full)
        except Exception as exc:  # noqa: BLE001 - a crashing mock is a failed call, not a crashed session
            return "error", None, f"{type(exc).__name__}: {exc}"
        if self._on_executed is not None:
            self._on_executed(tool, t_start, t_end)
        if isinstance(result, dict) and result.get("status") == "error":
            return "error", result, str(result.get("message") or "tool reported an error")
        return "ok", result, None
