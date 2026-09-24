import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from keel.compiler.classify import LexicalClassifier, PromptClassifier, features, split_identifier
from keel.compiler.manifest import ack_template, compile_tool, default_compiler, explicit_hints
from keel.compiler.probe import ManifestProbe, object_words
from keel.compiler.schema import normalize, validate_arguments
from keel.kernel.ledger import CallEntry
from keel.protocol.provisional import ToolSpec

GOLD = [json.loads(line) for line in
        (Path(__file__).resolve().parents[1] / "data" / "labels" / "gold.jsonl").read_text(encoding="utf-8").splitlines()]


def spec(name, desc="", props=None, required=(), **extra):
    params = {"type": "object", "properties": props or {}, "required": list(required)}
    return ToolSpec(name=name, description=desc, parameters=params, **extra)


# ---- schema ------------------------------------------------------------------

def test_every_real_schema_normalises_to_valid_json_schema():
    checked = 0
    for row in GOLD:
        if row["parameters"]:
            Draft202012Validator.check_schema(normalize(row["parameters"]))
            checked += 1
    assert checked > 150


def test_bfcl_dialect_is_mapped():
    s = normalize({"type": "dict", "properties": {"x": {"type": "float"}, "y": {"type": "any"},
                                                  "z": {"type": "tuple", "items": {"type": "str"}}}})
    assert s == {"type": "object", "properties": {"x": {"type": "number"}, "y": {},
                                                  "z": {"type": "array", "items": {"type": "string"}}}}


def test_arguments_are_validated():
    schema = {"type": "dict", "properties": {"mode": {"type": "string", "enum": ["engage", "release"]}},
              "required": ["mode"]}
    assert validate_arguments(schema, {"mode": "engage"}) == []
    assert validate_arguments(schema, {"mode": "stop"})[0].startswith("mode:")
    assert validate_arguments(schema, {})[0].startswith("(arguments):")


# ---- hints ---------------------------------------------------------------------

@pytest.mark.parametrize("extra,expected", [
    ({"annotations": {"readOnlyHint": True}}, "read_only"),
    ({"annotations": {"readOnlyHint": False, "idempotentHint": True}}, "state_changing"),
    ({"read_only": False}, "state_changing"),
    ({"side_effects": False}, "read_only"),
    ({"tool_type": "READ"}, "read_only"),
    ({"method": "get"}, "read_only"),
    ({"http_method": "POST"}, "state_changing"),
])
def test_explicit_hints(extra, expected):
    safety, _, evidence = explicit_hints(spec("t", **extra))
    assert safety == expected and evidence


def test_hint_beats_classifier():
    pol = compile_tool(spec("get_everything", annotations={"readOnlyHint": False}),
                       LexicalClassifier(), 0.8)
    assert pol.safety == "state_changing" and pol.method == "hint"


def test_idempotent_hint_is_kept():
    assert compile_tool(spec("put", annotations={"readOnlyHint": False, "idempotentHint": True})).idempotent



def test_mcp_annotations_without_read_only_hint_use_the_spec_default():
    # MCP ToolAnnotations: readOnlyHint "Default: false". A read-looking name must not override it.
    pol = compile_tool(spec("get_order_status", annotations={"title": "Order status"}), LexicalClassifier(), 0.8)
    assert pol.safety == "state_changing" and pol.method == "hint"
    assert "MCP default: false" in pol.evidence[0]

# ---- classifier -----------------------------------------------------------------

def test_split_identifier_handles_snake_and_camel():
    assert split_identifier("activateParkingBrake") == ["activate", "parking", "brake"]
    assert split_identifier("get_user_details") == ["get", "user", "details"]


def test_features_are_deterministic():
    a = features("book_flight", "Book a flight", ["flight_id"])
    assert a == features("book_flight", "Book a flight", ["flight_id"])


def test_low_confidence_becomes_unknown_and_evidence_is_kept():
    pol = compile_tool(spec("frobnicate", "Frobnicates."), LexicalClassifier(), 0.99)
    assert pol.safety == "unknown" and pol.method == "fallback"
    assert any("p(write)" in e for e in pol.evidence)
    assert pol.evidence[-1] == "unknown is treated as state_changing"


def test_classifier_failure_falls_back():
    def broken(_):
        raise RuntimeError("model offline")

    pol = compile_tool(spec("x"), broken, 0.8)
    assert pol.safety == "unknown" and "model offline" in pol.evidence[0]


def test_prompt_classifier_parses_strict_json_and_rejects_the_rest():
    shots = [r for r in GOLD if r["source"] == "tau2_bench"][:2]
    good = PromptClassifier(lambda p: 'x {"safety": "read_only", "confidence": 0.9, "reason": "lookup"}', shots)
    assert good(spec("lookup"))[0] == "read_only"
    assert "name: lookup" in good.prompt(spec("lookup"))
    bad = PromptClassifier(lambda p: '{"safety": "maybe", "confidence": 2}', shots)
    with pytest.raises(ValueError):
        bad(spec("lookup"))
    pol = compile_tool(spec("lookup"), bad, 0.8)
    assert pol.safety == "unknown"


def test_shipped_model_is_trained_on_the_current_gold_set():
    import hashlib

    from keel.compiler.classify import MODEL_PATH, LexicalModel

    gold = Path(__file__).resolve().parents[1] / "data" / "labels" / "gold.jsonl"
    assert LexicalModel.load(MODEL_PATH).meta["gold_sha256"] == hashlib.sha256(gold.read_bytes()).hexdigest()


# ---- ack templates ------------------------------------------------------------------

def test_ack_templates_never_claim_completion():
    params = {"properties": {"reservation_id": {"type": "string"}}, "required": ["reservation_id"]}
    assert ack_template("get_reservation_details", "read_only", params) == \
        "Let me check the reservation details for {reservation_id}."
    assert ack_template("book_reservation", "state_changing", params) == "I'm sending the book reservation request now."
    for row in GOLD:
        for safety in ("read_only", "state_changing"):
            text = ack_template(row["name"], safety, row["parameters"] or {}).lower()
            assert not any(w in text for w in ("done", "booked", "completed", "confirmed", "success"))


def test_slot_map_and_required_come_from_the_schema():
    pol = compile_tool(spec("t", props={"a": {"type": "string"}, "b": {"type": "integer"}}, required=["a"]))
    assert pol.slot_map == {"a": "a", "b": "b"} and pol.required == ("a",)


# ---- probe ---------------------------------------------------------------------------

def entry(tool, args, call_id="c1"):
    return CallEntry(call_id=call_id, seq=1, step_id="s", goal_gen=1, tool=tool, arguments=args, reads={},
                     safety="state_changing", idem_key="k", created_at=0)


def test_probe_picks_a_related_read_tool_it_can_call():
    policies = default_compiler(0.8)([
        spec("book_reservation", annotations={"readOnlyHint": False}),
        spec("get_reservation_details", props={"reservation_id": {"type": "string"}}, required=["reservation_id"],
             annotations={"readOnlyHint": True}),
        spec("get_user_reservations", props={"user_id": {"type": "string"}}, required=["user_id"],
             annotations={"readOnlyHint": True}),
        spec("get_weather", props={"user_id": {"type": "string"}}, annotations={"readOnlyHint": True}),
    ])
    probe = ManifestProbe()
    call = probe.plan(entry("book_reservation", {"user_id": "u1", "flight": "F1"}), policies)
    assert call is not None and call.tool == "get_user_reservations" and call.arguments == {"user_id": "u1"}
    e = entry("book_reservation", {"user_id": "u1", "flight": "F1"})
    assert probe.verdict(e, {"reservations": [{"flight": "F2"}, {"flight": "F1", "status": "ok"}]}) == "executed"
    assert probe.verdict(e, {"reservations": [{"flight": "F2"}]}) == "not_executed"
    assert probe.verdict(e, {"reservations": []}) == "not_executed"
    assert probe.verdict(e, "some text") == "unknown"
    assert probe.verdict(e, {"exists": False}) == "not_executed"


def test_probe_needs_shared_object_words():
    assert object_words("book_reservation") == {"reservation"}
    policies = default_compiler(0.8)([spec("get_weather", annotations={"readOnlyHint": True})])
    assert ManifestProbe().plan(entry("book_reservation", {}), policies) is None


def test_probe_reads_an_empty_result_inside_a_status_envelope():
    e = entry("book_reservation", {"day": "fri"})
    probe = ManifestProbe()
    assert probe.verdict(e, {"status": "success", "reservations": []}) == "not_executed"
    assert probe.verdict(e, {"status": "success"}) == "unknown"   # no collection at all: says nothing
