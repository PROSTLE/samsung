"""Offline tests for the data/fetch parsers (no network)."""

from data.fetch.bfcl import parse_func_doc
from data.fetch.tau_bench import extract_get_info, extract_is_tool_methods

TAU1_SNIPPET = '''
class CancelReservation(Tool):
    @staticmethod
    def invoke(data, reservation_id: str) -> str:
        return "x"

    @staticmethod
    def get_info() -> Dict[str, Any]:
        return {"type": "function", "function": {"name": "cancel_reservation",
                "parameters": {"type": "object", "properties": {}, "required": []}}}
'''

TAU2_SNIPPET = '''
class Tools(ToolKitBase):
    @is_tool(ToolType.WRITE)
    def book(self, user_id: str, nights: int = 1) -> str:
        """Book a stay."""

    @is_tool()
    def lookup(self, user_id: str) -> str:
        pass

    @is_tool(ToolType.GENERIC, mutates_state=True)
    def unlock(self, *, name: str, force: bool = False) -> str:
        pass

    # @is_tool(ToolType.THINK)
    # def think(self, thought: str) -> str: ...

    def helper(self):
        pass
'''


def test_extract_get_info_reads_literal_without_executing():
    info = extract_get_info(TAU1_SNIPPET)
    assert info["function"]["name"] == "cancel_reservation"


def test_extract_is_tool_labels_and_signature():
    got = {m["name"]: m for m in extract_is_tool_methods(TAU2_SNIPPET)}
    assert set(got) == {"book", "lookup", "unlock"}
    assert got["book"]["tool_type"] == "WRITE"
    assert got["book"]["docstring"] == "Book a stay."
    assert got["book"]["params"] == [
        {"name": "user_id", "annotation": "str", "required": True},
        {"name": "nights", "annotation": "int", "required": False},
    ]
    assert got["lookup"]["tool_type"] == "READ"  # upstream default
    assert got["unlock"]["mutates_state_explicit"] is True
    assert [p["required"] for p in got["unlock"]["params"]] == [True, False]


def test_parse_bfcl_jsonl():
    text = '{"name": "a", "parameters": {"type": "dict"}}\n\n{"name": "b"}\n'
    assert [f["name"] for f in parse_func_doc(text)] == ["a", "b"]
