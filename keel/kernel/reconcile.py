"""'Did it go through?' reconciliation for writes whose outcome is in doubt.

A write is in doubt when it timed out / returned an ambiguous result
(status `unknown`), or was cancelled after dispatch (the world may have run it
anyway). For such a write Keel:

  * never claims success without a matching success result,
  * never re-sends it blindly: a retry is allowed only if the manifest says
    the tool is idempotent (MCP idempotentHint semantics) and config permits,
  * otherwise asks a StatusProbe for a read-only call that can check, runs it,
    and lets the probe's verdict settle the entry,
  * and if nothing can check, says so truthfully and blocks that step.

StatusProbes are built by the manifest compiler (phase 3). The kernel only
knows this interface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Mapping, Optional, Protocol, Union

from pydantic import JsonValue

from keel.compiler.manifest import ToolPolicy
from keel.kernel.ledger import CallEntry

Verdict = Literal["executed", "not_executed", "unknown"]


@dataclass(frozen=True)
class ProbeCall:
    tool: str
    arguments: dict[str, JsonValue]


class StatusProbe(Protocol):
    def plan(self, entry: CallEntry, policies: Mapping[str, ToolPolicy]) -> Optional[ProbeCall]: ...

    def verdict(self, entry: CallEntry, probe_result: JsonValue) -> Verdict: ...


@dataclass(frozen=True)
class Retry:
    reason: str


@dataclass(frozen=True)
class Probe:
    call: ProbeCall


@dataclass(frozen=True)
class CannotConfirm:
    reason: str


Plan = Union[Retry, Probe, CannotConfirm]


def plan_reconciliation(
    entry: CallEntry,
    policies: Mapping[str, ToolPolicy],
    probe: Optional[StatusProbe],
    *,
    retry_if_idempotent: bool,
) -> Plan:
    policy = policies.get(entry.tool)
    if retry_if_idempotent and policy is not None and policy.idempotent and entry.status == "unknown":
        return Retry(f"{entry.tool} is declared idempotent ({'; '.join(policy.evidence)})")
    if probe is not None:
        call = probe.plan(entry, policies)
        if call is not None:
            checked = policies.get(call.tool)
            if checked is None or checked.safety != "read_only":
                return CannotConfirm(f"probe tool {call.tool} is not known to be read-only")
            return Probe(call)
    return CannotConfirm("no read-only tool in the manifest can check this call")
