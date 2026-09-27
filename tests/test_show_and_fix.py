"""Show & Fix extension: tools and the full voice flow through KeelGate, offline.

The vision model is replaced by a fake reader; everything else (manifest,
compiler, kernel, gate, manual, bookings) is the shipped code.
"""

import asyncio
import io
import json
from pathlib import Path

from extension.show_and_fix.backend import FrameStore, Manual, ShowAndFixBackend
from keel.compiler.manifest import default_compiler
from keel.compiler.probe import ManifestProbe
from keel.config import load_config, with_overrides
from keel.kernel.clock import MonotonicClock
from keel.kernel.ledger import CallEntry
from keel.livekit.gate import KeelGate
from keel.protocol.provisional import ToolSpec
from keel.sim.invariants import check_trace
from keel.trace import TraceRecord, TraceWriter

ROOT = Path(__file__).resolve().parents[1]
EXT = ROOT / "extension" / "show_and_fix"
MANUAL = Manual(EXT / "washer_codes.json")
SPECS = [ToolSpec(**t) for t in json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))["tools"]]


JPEG = b"\xff\xd8\xff\xe0fake-jpeg"
PNG = b"\x89PNG\r\n\x1a\nfake-png"


def reader_returning(code, confidence, seen=None):
    async def read(image, mime):
        if seen is not None:
            seen.append(mime)
        return {"code": code, "confidence": confidence, "what_i_see": "a display"}
    return read


def backend(reader=None, frames=None, **kw):
    return ShowAndFixBackend(manual=MANUAL, frames=frames or FrameStore(), reader=reader or reader_returning("4C", 0.9),
                             session_id="s1", clarify_below=0.6, frame_max_age_s=3.0, **kw)


# ---- manual ------------------------------------------------------------------

def test_manual_is_samsungs_table_with_its_source():
    data = json.loads((EXT / "washer_codes.json").read_text(encoding="utf-8"))
    assert data["source"].startswith("https://www.samsung.com/")
    assert all(e["codes"] and e["meaning"] for e in data["entries"])


def test_lookup_matches_any_listed_variant_case_insensitively():
    b = backend()
    out = asyncio.run(b._lookup_error_code("4c"))
    assert out["status"] == "success" and "4C" in out["codes"] and out["source"] == MANUAL.source
    assert asyncio.run(b._lookup_error_code("dc"))["codes"] == ["dC", "dE"]


def test_an_unknown_code_is_not_guessed():
    out = asyncio.run(backend()._lookup_error_code("Z9"))
    assert out["status"] == "not_found" and "meaning" not in out


# ---- reading the display -------------------------------------------------------

def test_no_frame_means_ask_for_the_camera():
    assert asyncio.run(backend()._read_error_display())["status"] == "no_frame"


def test_a_confident_reading_is_returned_with_samsungs_entry():
    frames = FrameStore()
    frames.put(JPEG, source="file:washer.jpg")
    out = asyncio.run(backend(frames=frames)._read_error_display())
    entry = MANUAL.find("4C")
    assert out == {"status": "success", "code": "4C", "confidence": 0.9, "meaning": entry["meaning"],
                   "steps": entry["steps"], "contact_service_if_persists": entry["contact_service_if_persists"],
                   "source": MANUAL.source}


def test_a_seven_segment_reading_is_matched_to_the_tables_code():
    # A seven-segment "5" and "S" are the same glyph: our Show & Fix live test's
    # display showed 5E and the vision model read "SE".
    frames = FrameStore()
    frames.put(JPEG, source="file:washer.jpg")
    out = asyncio.run(backend(reader=reader_returning("SE", 1.0), frames=frames)._read_error_display())
    assert out["code"] == "5E" and out["displayed_as"] == "SE" and out["meaning"] == "Water is not draining."
    assert asyncio.run(backend()._lookup_error_code("0E"))["code"] == "OE"      # letter O, digit 0
    assert asyncio.run(backend()._lookup_error_code("se"))["code"] == "5E"


def test_a_reading_that_is_not_in_the_table_is_not_explained():
    frames = FrameStore()
    frames.put(JPEG, source="file:washer.jpg")
    out = asyncio.run(backend(reader=reader_returning("H9", 0.95), frames=frames)._read_error_display())
    assert out["status"] == "success" and out["code"] == "H9" and out["manual"] == "not_found"
    assert "meaning" not in out


def test_seven_segment_equivalence_makes_no_two_table_codes_alike():
    from extension.show_and_fix.backend import code_key

    keys = [code_key(c) for e in MANUAL.entries for c in e["codes"]]
    per_entry = [{code_key(c) for c in e["codes"]} for e in MANUAL.entries]
    assert len(set(keys)) == sum(len(s) for s in per_entry)       # a key never spans two entries


def test_a_low_confidence_reading_asserts_nothing():
    frames = FrameStore()
    frames.put(JPEG, source="file:washer.jpg")
    out = asyncio.run(backend(reader=reader_returning("4C", 0.4), frames=frames)._read_error_display())
    assert out["status"] == "unclear" and "code" not in out and out["best_guess"] == "4C"


def test_a_stale_camera_frame_is_not_trusted():
    now = [100.0]
    frames = FrameStore(clock=lambda: now[0])
    frames.put(JPEG, source="camera")
    now[0] += 10.0
    assert asyncio.run(backend(frames=frames)._read_error_display())["status"] == "no_frame"


def test_camera_frames_are_encoded_only_when_read():
    encoded = []
    frames = FrameStore()
    frames.put("raw-frame", source="camera", encode=lambda f: encoded.append(f) or JPEG)
    assert encoded == []
    asyncio.run(backend(frames=frames)._read_error_display())
    assert encoded == ["raw-frame"]


# ---- the compiled manifest -------------------------------------------------------

def test_the_booking_has_a_status_probe_in_the_same_manifest():
    policies = default_compiler(0.8)(SPECS)
    assert policies["book_technician"].safety == "state_changing"
    entry = CallEntry(call_id="c", seq=1, step_id="s", goal_gen=1, tool="book_technician",
                      arguments={"code": "4C", "day": "Saturday", "time_window": "morning"}, reads={},
                      safety="state_changing", idem_key="k", created_at=0)
    probe = ManifestProbe()
    call = probe.plan(entry, policies)
    assert call is not None and call.tool == "list_technician_bookings"
    booked = {"status": "success", "bookings": [{"booking_id": "SVC-1", **entry.arguments}]}
    assert probe.verdict(entry, booked) == "executed"
    assert probe.verdict(entry, {"status": "success", "bookings": []}) == "not_executed"


# ---- the whole conversation, through the gate -------------------------------------

def test_show_and_fix_conversation_books_once_with_the_corrected_day():
    cfg = with_overrides(load_config(None, ROOT / "config" / "show_and_fix.toml"),
                         trace={"record_wall_time": False}, fence={"quiet_ms": 60, "stale_turn_ms": 120})
    frames = FrameStore()
    frames.put(JPEG, source="file:washer.jpg")
    b = backend(frames=frames)
    buf = io.StringIO()

    async def main():
        loop = asyncio.get_running_loop()
        clock = MonotonicClock(loop)
        trace = TraceWriter(buf, clock=clock, session_id="s1", config=cfg, synthetic=True)
        gate = KeelGate(session_id="s1", config=cfg, tools=SPECS, execute=b.execute, trace=trace, clock=clock,
                        loop=loop, drop_on_new_speech=True)

        def say(text):
            gate.user_speaking(); gate.user_stopped()
            gate.user_transcript(text, final=True); gate.user_turn_committed(text)

        say("my washer shows an error, can you look?")
        seen = await gate.call("read_error_display", {})
        meaning = await gate.call("lookup_error_code", {"code": seen["result"]["code"]})
        say("book a technician for Friday morning")
        friday = asyncio.ensure_future(gate.call("book_technician", {"code": "4C", "day": "Friday", "time_window": "morning"}))
        await asyncio.sleep(0.01)                       # held by the fence
        say("no wait, Saturday morning")                # self-correction
        dropped = await friday
        saturday = await gate.call("book_technician", {"code": "4C", "day": "Saturday", "time_window": "morning"})
        again = await gate.call("book_technician", {"code": "4C", "day": "Saturday", "time_window": "morning"})
        listed = await gate.call("list_technician_bookings", {})
        await gate.aclose()
        return seen, meaning, dropped, saturday, again, listed

    seen, meaning, dropped, saturday, again, listed = asyncio.run(main())
    assert seen["result"]["code"] == "4C"
    assert meaning["result"]["meaning"] == "Water is not supplied."
    assert dropped["status"] == "superseded"
    assert saturday["result"]["day"] == "Saturday"
    assert again["note"] == "identical call already made in this session"
    assert [x["day"] for x in listed["result"]["bookings"]] == ["Saturday"]
    assert [x["day"] for x in b.bookings] == ["Saturday"]          # the world: exactly one visit
    records = [TraceRecord.model_validate_json(line) for line in buf.getvalue().splitlines()]
    assert check_trace(records) == []


def test_a_still_photo_is_sent_with_its_own_image_type():
    seen = []
    frames = FrameStore()
    frames.put(PNG, source="file:washer.png")
    out = asyncio.run(backend(reader=reader_returning("4C", 0.9, seen), frames=frames)._read_error_display())
    assert out["status"] == "success" and seen == ["image/png"]


def test_a_file_that_is_not_an_image_is_an_error_not_a_guess():
    frames = FrameStore()
    frames.put(b"not an image", source="file:notes.txt")
    status, _, error = asyncio.run(backend(frames=frames).execute("read_error_display", {}))
    assert status == "error" and "not a PNG" in error
