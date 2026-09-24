"""Reading tools and instructions from an agent template, and FDB-v3's own files.

The fixture tests always run. The FDB-v3 tests run when KEEL_FDB_DIR points at
a checkout's v3/ directory (CI clones it at the commit pinned in
scripts/reproduce_fdb_v3.sh); otherwise they are skipped.
"""

import asyncio
import json
import os
from pathlib import Path

import pytest

from keel.compiler.manifest import default_compiler
from keel.livekit.fdb import FdbBackend, load_mock_registry
from keel.livekit.template import read_template

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "fdb_style_agent.py"
FDB = os.getenv("KEEL_FDB_DIR")
needs_fdb = pytest.mark.skipif(not FDB, reason="set KEEL_FDB_DIR to an FDB-v3 checkout's v3/ directory")


def test_fixture_tools_signatures_and_docs_become_json_schema():
    t = read_template(FIXTURE)
    assert [x.spec.name for x in t.tools] == ["search_trains", "reserve_seat", "convert"]
    reserve = t.tool("reserve_seat")
    assert reserve.spec.parameters == {
        "type": "object",
        "properties": {"train_id": {"type": "string", "description": "Train identifier"},
                       "seats": {"type": "integer", "description": "Number of seats", "default": 1}},
        "required": ["train_id"]}
    assert reserve.defaults == {"seats": 1}
    note = t.tool("convert").spec.parameters["properties"]["note"]
    assert note["type"] == ["string", "null"]          # `note: str = None` accepts null
    assert t.instructions == "Be brief. Use the tools."


@needs_fdb
def test_fdb_templates_define_the_same_twelve_tools_and_instructions():
    a = read_template(Path(FDB) / "cascaded_agent.py")
    b = read_template(Path(FDB) / "lk_agent_tool.py")
    assert len(a.tools) == 12
    assert [x.spec.model_dump() for x in a.tools] == [x.spec.model_dump() for x in b.tools]
    assert a.instructions and a.instructions == b.instructions
    # Every tool the benchmark expects is one the agent offers.
    bench = json.loads((Path(FDB) / "benchmark_data_v2.json").read_text(encoding="utf-8"))
    expected = {c["function"] for s in bench["scenarios"] for c in s["expected_tool_calls"]}
    assert expected <= {x.spec.name for x in a.tools}


@needs_fdb
def test_fdb_tool_schemas_compile_and_accept_the_benchmarks_own_argument_shapes(tmp_path):
    # In the gate's order: complete (defaults, canonical types), then validate.
    t = read_template(Path(FDB) / "cascaded_agent.py")
    policies = default_compiler(0.8)(t.specs())
    backend = FdbBackend(template=t, registry=None, room="r", tool_log=tmp_path / "x.log", seed=5)
    bench = json.loads((Path(FDB) / "benchmark_data_v2.json").read_text(encoding="utf-8"))
    for s in bench["scenarios"]:
        for c in s["expected_tool_calls"]:
            args = {k: v for k, v in c["args"].items() if not (isinstance(v, str) and v.startswith("$"))}
            errors = [e for e in policies[c["function"]].validate(backend.arguments(c["function"], args))
                      if "required" not in e]
            assert errors == [], (s["id"], c, errors)


@needs_fdb
def test_the_real_mock_registry_runs_and_is_logged_in_the_runners_format(tmp_path):
    t = read_template(Path(FDB) / "cascaded_agent.py")
    backend = FdbBackend(template=t, registry=load_mock_registry(Path(FDB), "instant"), room="eval-test",
                         tool_log=tmp_path / "agent_tool_calls.log", seed=5)
    status, result, _ = asyncio.run(backend.execute("track_order", {"order_id": "ABC123"}))
    assert status == "ok" and result["order_id"] == "ABC123"
    line = json.loads((tmp_path / "agent_tool_calls.log").read_text(encoding="utf-8"))
    assert line["room"] == "eval-test" and line["call"]["function"] == "track_order"
    assert set(line["call"]) == {"function", "args", "timestamp_start", "timestamp_end"}


def test_a_parameter_the_reader_cannot_represent_is_an_error_not_a_silent_drop(tmp_path):
    from keel.livekit.template import TemplateError

    src = tmp_path / "agent.py"
    src.write_text("class T:\n    @function_tool(description='d')\n    async def f(self, a: str, *, b: int = 1):\n        pass\n",
                   encoding="utf-8")
    with pytest.raises(TemplateError):
        read_template(src)
