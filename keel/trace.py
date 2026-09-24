"""Session trace logger.

One JSON object per line. The trace is the single record of what happened:
the local scorer reads it, and the console renders it. Nothing downstream is
allowed to invent data the trace does not contain.

Record kinds:
  in    - an input event, exactly as decoded
  out   - an output action, validated before it is written
  note  - kernel-internal facts (slot changes, call status, classifier
          evidence) that the console needs to explain a decision
  meta  - session_start / session_end
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import IO, Any, Iterator, Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, JsonValue

from keel.config import KeelConfig
from keel.kernel.clock import Clock
from keel.protocol.provisional import ACTION_ADAPTER, ActionT, EventT

Direction = Literal["in", "out", "note", "meta"]


class TraceRecord(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    seq: int
    ts_ms: int
    wall_ns: Optional[int]
    dir: Direction
    kind: str
    data: dict[str, JsonValue]


class TraceWriter:
    def __init__(
        self,
        sink: Union[IO[str], Path, str],
        *,
        clock: Clock,
        session_id: str,
        config: KeelConfig,
        synthetic: bool,
        scenario: Optional[str] = None,
    ) -> None:
        if isinstance(sink, (str, Path)):
            Path(sink).parent.mkdir(parents=True, exist_ok=True)
            # Line-buffered: every record is on disk as soon as it is written, so a
            # killed or crashed agent process never loses its trace.
            self._fh: IO[str] = open(sink, "w", encoding="utf-8", newline="\n", buffering=1)
            self._owns = True
        else:
            self._fh = sink
            self._owns = False
        self._clock = clock
        self._record_wall = config.trace.record_wall_time
        self._t0 = time.perf_counter_ns()
        self._seq = 0
        self._closed = False
        self._write(
            "meta",
            "session_start",
            {
                "session_id": session_id,
                "schema_version": config.protocol.schema_version,
                "config_digest": config.digest,
                # The console must show a visible "synthetic" label when set.
                "synthetic": synthetic,
                "scenario": scenario,
            },
        )

    def _write(self, direction: Direction, kind: str, data: dict[str, Any]) -> TraceRecord:
        if self._closed:
            raise RuntimeError("trace is closed")
        rec = TraceRecord(
            seq=self._seq,
            ts_ms=self._clock.now_ms(),
            wall_ns=(time.perf_counter_ns() - self._t0) if self._record_wall else None,
            dir=direction,
            kind=kind,
            data=data,
        )
        self._seq += 1
        self._fh.write(json.dumps(rec.model_dump(mode="json"), separators=(",", ":"), sort_keys=True))
        self._fh.write("\n")
        return rec

    def event_in(self, event: EventT) -> TraceRecord:
        return self._write("in", event.type, event.model_dump(mode="json"))

    def action_out(self, action: ActionT) -> TraceRecord:
        # Re-validate: never log (or emit) an action that bypassed validation.
        checked = ACTION_ADAPTER.validate_python(action.model_dump(mode="python"))
        return self._write("out", checked.type, checked.model_dump(mode="json"))

    def note(self, kind: str, **data: Any) -> TraceRecord:
        return self._write("note", kind, data)

    def close(self) -> None:
        if self._closed:
            return
        self._write("meta", "session_end", {"records": self._seq + 1})
        self._closed = True
        self._fh.flush()
        if self._owns:
            self._fh.close()

    def __enter__(self) -> "TraceWriter":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


def read_trace(path: Union[Path, str]) -> Iterator[TraceRecord]:
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                yield TraceRecord.model_validate_json(line)
