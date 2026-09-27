"""Benchmark runs collected by scripts/reproduce_fdb_v3.sh, read for the web app.

Everything comes from the run folder: run_info.txt and FDB-v3's own reports
(<provider>_evaluation_report.json, _pass_rate_report.json, _latency_report.json).
A value a report does not contain is None, never a guess.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional


_CACHE: dict[Path, tuple[int, int, Optional[dict[str, Any]]]] = {}
RUNNING_WINDOW_S = 600


def _json(path: Path) -> Optional[dict[str, Any]]:
    """A report or result file, parsed once per change (the app re-reads them on every request)."""
    try:
        st = path.stat()
    except OSError:
        return None
    hit = _CACHE.get(path)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = None
    _CACHE[path] = (st.st_mtime_ns, st.st_size, data)
    return data


def _get(d: Optional[dict[str, Any]], *keys: str) -> Any:
    for k in keys:
        if not isinstance(d, dict):
            return None
        d = d.get(k)
    return d


def run_info(run: Path) -> dict[str, str]:
    info: dict[str, str] = {}
    try:
        for line in (run / "run_info.txt").read_text(encoding="utf-8").splitlines():
            if ":" in line:
                k, v = line.split(":", 1)
                info[k.strip()] = v.strip()
    except OSError:
        pass
    return info


def _report(run: Path, suffix: str) -> Optional[dict[str, Any]]:
    found = sorted(run.glob(f"*_{suffix}.json"))
    return _json(found[0]) if found else None


def summarize_run(run: Path) -> dict[str, Any]:
    info = run_info(run)
    ev, pr, lat = _report(run, "evaluation_report"), _report(run, "pass_rate_report"), _report(run, "latency_report")
    per_scenario = sorted((run / "per_scenario").glob("*.json")) if (run / "per_scenario").is_dir() else []
    complete = ev is not None or pr is not None
    # No reports: still running if its logs changed in the last 10 minutes, else it stopped
    # before FDB-v3's evaluation (a crash or an interrupted run; its logs say which).
    newest = max((p.stat().st_mtime for p in run.glob("*.log")), default=0.0)   # the run's own logs only
    status = "complete" if complete else ("running" if time.time() - newest < RUNNING_WINDOW_S else "stopped")
    return {
        "id": run.name,
        "status": status,
        "provider": info.get("provider_label"),
        "judge": info.get("judge"),
        "command": info.get("command"),
        "keel_commit": info.get("keel_commit"),
        "fdb_v3_commit": info.get("fdb_v3_commit"),
        "scenarios": _get(ev, "total_scenarios") or _get(pr, "total_scenarios") or len(per_scenario) or None,
        "complete": complete,
        "pass_rate": _get(pr, "overall_pass_rate"),
        "passed": _get(pr, "passed"),
        "failed": _get(pr, "failed"),
        "turn_take_rate": _get(ev, "turn_taking", "turn_take_rate"),
        "tool_selection": _get(ev, "by_metric", "tool_selection_acc"),
        "argument_accuracy": _get(ev, "by_metric", "argument_acc"),
        "response_quality": _get(ev, "by_metric", "response_qual"),
        "latency_median_s": _get(lat, "aggregate", "first_response_latency", "median"),
        "tool_latency_median_s": _get(lat, "aggregate", "tool_call_latency", "median"),
    }


def run_detail(run: Path) -> dict[str, Any]:
    out = summarize_run(run)
    pr, ev = _report(run, "pass_rate_report"), _report(run, "evaluation_report")
    out["by_disfluency"] = _get(pr, "by_disfluency_feature") or {}
    out["by_domain"] = _get(pr, "by_domain") or {}
    out["failure_breakdown"] = _get(pr, "failure_breakdown") or {}
    evaluated = {s.get("scenario_id"): s for s in (_get(ev, "scenario_results") or [])}
    rows = []
    for s in _get(pr, "scenario_results") or []:
        sid = s.get("scenario_id")
        e = evaluated.get(sid, {})
        rows.append({
            "scenario": sid, "title": s.get("title"), "domain": s.get("domain"),
            "passed": s.get("passed"), "failure": plain_failure(s.get("failure_reason")),
            "turn_taken": e.get("turn_take_success"),
            "latency_s": _get(e, "latency", "agent_response_latency_s"),
        })
    rooms = {}
    for f in sorted((run / "per_scenario").glob("*.json")) if (run / "per_scenario").is_dir() else []:
        d = _json(f) or {}
        if d.get("room_name"):
            rooms[d.get("example_id") or f.stem] = d["room_name"]
    for r in rows:
        r["session"] = rooms.get(r["scenario"])
    out["scenarios_detail"] = rows
    return out


def list_runs(results_dir: Path) -> list[dict[str, Any]]:
    if not results_dir.is_dir():
        return []
    runs = [p for p in results_dir.iterdir() if p.is_dir()]
    return [summarize_run(p) for p in sorted(runs, key=lambda p: p.name, reverse=True)]


def scenario_titles(results_dir: Path, fdb_data: Optional[Path] = None) -> dict[str, dict[str, Any]]:
    """room name -> {"scenario", "title"[, "run"]} (a room name is unique per run).

    From the collected runs, and from FDB-v3's own per-scenario result files
    (fdb_data: v3/fdb_v3_data_released), which the runner writes as it goes, so
    sessions of a run still in progress have their scenario's title too."""
    out: dict[str, dict[str, Any]] = {}
    if fdb_data is not None and fdb_data.is_dir():
        for f in fdb_data.glob("*/result_*.json"):
            d = _json(f) or {}
            if d.get("room_name"):
                out[d["room_name"]] = {"scenario": d.get("example_id"), "title": d.get("title")}
    if results_dir.is_dir():
        for f in results_dir.glob("*/per_scenario/*.json"):
            d = _json(f) or {}
            if d.get("room_name"):
                out[d["room_name"]] = {"scenario": d.get("example_id"), "title": d.get("title"),
                                       "run": f.parent.parent.name}
    return out


def fdb_recordings(fdb_data: Optional[Path]) -> dict[str, dict[str, Any]]:
    """room name -> the audio FDB-v3 recorded for that scenario, and how to line it up.

    FDB-v3 keeps, per scenario folder, the user's recording (input_mono.wav, made
    by its runner, or input.wav) and the agent's reply recorded from the room
    (output_<provider>.wav), both starting at the moment it began streaming
    (stream_start_time, Unix seconds), and the executed tool calls with their
    times relative to that moment. A later run with the same provider overwrites
    them, so only the latest room per scenario and provider has audio."""
    out: dict[str, dict[str, Any]] = {}
    if fdb_data is None or not fdb_data.is_dir():
        return out
    for f in fdb_data.glob("*/result_*.json"):
        d = _json(f) or {}
        room, provider = d.get("room_name"), f.stem.removeprefix("result_")
        if not room:
            continue
        user = next((p for p in (f.parent / "input_mono.wav", f.parent / "input.wav") if p.is_file()), None)
        agent = f.parent / f"output_{provider}.wav"
        out[room] = {
            "scenario": d.get("example_id"), "provider": provider,
            "user": user, "agent": agent if agent.is_file() else None,
            "stream_start_time": d.get("stream_start_time"),
            "tool_calls": d.get("actual_tool_calls") or [],
        }
    return out


def audio_offset_ms(recording: dict[str, Any], unix_ms_at_zero: Optional[int],
                    sent: list[tuple[str, int]]) -> tuple[Optional[int], Optional[str]]:
    """Trace time (ms) at which the recording starts, and how it was found.

    "clock": the trace recorded the Unix time of its t = 0 (session_start.unix_ms),
    so the offset is FDB-v3's stream start minus that.
    "tool_call": older traces have no clock anchor. FDB-v3 times each executed call
    from the tool log Keel writes as it dispatches (timestamp_start, relative to
    the stream start, 10 ms resolution), and the trace records that dispatch
    (`sent`: (tool, t) in order), so the first call of the same tool anchors both.
    None when neither is available: the replay then plays without audio."""
    start = recording.get("stream_start_time")
    if unix_ms_at_zero is not None and isinstance(start, (int, float)):
        return round(start * 1000) - unix_ms_at_zero, "clock"
    for call in recording.get("tool_calls") or []:
        ts = call.get("timestamp_start")
        if not isinstance(ts, (int, float)):
            continue
        t = next((t for tool, t in sent if tool == call.get("function")), None)
        if t is not None:
            return t - round(ts * 1000), "tool_call"
    return None, None


def scenario_verdict(run: Path, scenario: str) -> Optional[dict[str, Any]]:
    """FDB-v3's own verdict on one scenario of a run: expected and actual calls."""
    pr = _report(run, "pass_rate_report")
    for s in _get(pr, "scenario_results") or []:
        if s.get("scenario_id") != scenario:
            continue
        checks = s.get("checks") or {}
        sel = checks.get("tool_selection") or {}
        args = (checks.get("argument_accuracy") or {}).get("details") or []
        return {
            "passed": s.get("passed"), "failure": plain_failure(s.get("failure_reason")),
            "expected_tools": sel.get("expected") or [], "actual_tools": sel.get("actual") or [],
            "missing": sel.get("missing") or [], "unexpected": sel.get("unexpected") or [],
            "arguments": [{"tool": a.get("function"), "passed": a.get("passed"),
                           "expected": a.get("expected_args"), "actual": a.get("actual_args"),
                           "explanation": a.get("explanation")} for a in args],
            "disfluency": s.get("disfluency") or [], "difficulty": s.get("difficulty"),
        }
    return None


def plain_failure(reason: Optional[str]) -> Optional[str]:
    """FDB-v3's failure line in plain words: "Wrong arguments for: ['search_flights']"
    -> "Wrong arguments: Search flights". Unknown shapes are returned as they are."""
    if not reason or ":" not in reason:
        return reason
    head, _, tail = reason.partition(":")
    names = [n.strip(" '\"") for n in tail.strip().strip("[]").split(",") if n.strip(" '\"")]
    if not names or not all(n.replace("_", "").isalnum() for n in names):
        return reason
    head = head.strip().removesuffix(" for")
    return f"{head}: " + ", ".join(n.replace("_", " ").capitalize() for n in names)
