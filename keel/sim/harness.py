"""Run a kernel against a mock environment on the virtual clock."""

from __future__ import annotations

import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Optional, Union

from keel.compiler.manifest import ManifestCompiler
from keel.config import KeelConfig
from keel.kernel.clock import VirtualClock
from keel.kernel.internal import Interpretation
from keel.kernel.loop import Kernel
from keel.kernel.reconcile import StatusProbe
from keel.protocol.provisional import EVENT_ADAPTER, ActionT, EventT
from keel.sim.mock_env import MockEnv, ToolBehaviour
from keel.sim.scripted import ScriptedInterpreter
from keel.trace import TraceRecord, TraceWriter

# Original guide (v1.0.0) §6: "120s wall-clock cap per scenario". We apply the same bound to
# virtual time so a runaway timer loop cannot spin forever.
SCENARIO_CAP_MS = 120_000


@dataclass
class SimResult:
    actions: list[ActionT]
    trace_text: str
    env: MockEnv
    kernel: Kernel

    @property
    def records(self) -> list[TraceRecord]:
        return [TraceRecord.model_validate_json(line) for line in self.trace_text.splitlines() if line.strip()]

    def of(self, kind: str) -> list[Any]:
        return [a for a in self.actions if a.type == kind]


@dataclass
class Simulation:
    config: KeelConfig
    behaviours: dict[str, ToolBehaviour] = field(default_factory=dict)
    script: Mapping[str, list[tuple[int, Interpretation]]] = field(default_factory=dict)
    session_id: str = "sim"
    probe: Optional[StatusProbe] = None
    compiler: Optional[ManifestCompiler] = None
    synthetic: bool = True
    scenario: Optional[str] = None
    trace_path: Optional[Union[str, Path]] = None

    def run(self, events: list[Union[EventT, dict]], until_ms: int = SCENARIO_CAP_MS) -> SimResult:
        clock = VirtualClock()
        buf = io.StringIO()
        trace = TraceWriter(buf, clock=clock, session_id=self.session_id, config=self.config,
                            synthetic=self.synthetic, scenario=self.scenario)
        actions: list[ActionT] = []
        holder: dict[str, Kernel] = {}

        env = MockEnv(clock, lambda ev: holder["k"].post(ev), self.behaviours, self.session_id)

        def sink(a: ActionT) -> None:
            actions.append(a)
            env.receive(a)

        kernel = Kernel(session_id=self.session_id, clock=clock, config=self.config, trace=trace, sink=sink,
                        interpreter=ScriptedInterpreter(clock, self.script), compiler=self.compiler,
                        probe=self.probe)
        holder["k"] = kernel

        for raw in events:
            ev = EVENT_ADAPTER.validate_python(raw) if isinstance(raw, dict) else raw
            clock.call_at(ev.ts_ms, lambda ev=ev: kernel.handle(ev))
        while (nxt := clock.next_deadline()) is not None and nxt <= until_ms:
            clock.run_next()
        trace.close()
        text = buf.getvalue()
        if self.trace_path:
            Path(self.trace_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.trace_path).write_text(text, encoding="utf-8", newline="\n")
        return SimResult(actions, text, env, kernel)
