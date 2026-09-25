"""Show & Fix tools: read the appliance display from the camera, look the code
up in Samsung's published table, book a technician.

Everything here is session-scoped (a new conversation starts empty). The
vision model is injected (`Reader`), so the tools are tested offline with a
fake reader; the agent passes the OpenAI one below.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

# (image bytes, MIME type) -> {"code": str | None, "confidence": float, "what_i_see": str}
Reader = Callable[[bytes, str], Awaitable[dict[str, Any]]]


def image_mime(data: bytes) -> str:
    """MIME type from an image's leading bytes (a still photo may be any of these)."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    raise ValueError("not a PNG, JPEG, WebP or GIF image")


VISION_PROMPT = (
    "You read the display panel of a home appliance in a photo. Report only characters you can "
    "actually see on the display; do not infer a code from the appliance type or from what codes "
    "usually exist. Reply with JSON only: {\"code\": \"<the characters on the display, or null if "
    "none are legible>\", \"confidence\": <0 to 1, how sure you are that every character is right>, "
    "\"what_i_see\": \"<one short sentence>\"}"
)


@dataclass
class Frame:
    raw: Any           # JPEG bytes, or a camera frame that `encode` turns into JPEG
    at: float          # time.monotonic() when it was captured
    source: str        # "camera" or "file:<path>"
    encode: Optional[Callable[[Any], bytes]] = None

    def image(self) -> tuple[bytes, str]:
        """(bytes, MIME type). Camera frames are encoded to JPEG here, on demand."""
        data = self.encode(self.raw) if self.encode else self.raw
        return data, image_mime(data)


class FrameStore:
    """The latest camera frame. Staleness is checked against a monotonic clock.
    Camera frames are kept raw and encoded only when a tool reads one."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self.latest: Optional[Frame] = None

    def put(self, raw: Any, source: str = "camera", encode: Optional[Callable[[Any], bytes]] = None) -> None:
        self.latest = Frame(raw=raw, at=self._clock(), source=source, encode=encode)

    def fresh(self, max_age_s: float) -> Optional[Frame]:
        f = self.latest
        if f is None:
            return None
        if f.source.startswith("file:"):
            return f   # a still image stands in for the camera; it does not age
        return f if self._clock() - f.at <= max_age_s else None


class Manual:
    def __init__(self, path: Path | str) -> None:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        self.source = data["source"]
        self.entries = data["entries"]

    def find(self, code: str) -> Optional[dict[str, Any]]:
        key = code.strip().replace(" ", "").replace("-", "").upper()
        for e in self.entries:
            if key in {c.upper() for c in e["codes"]}:
                return e
        return None


@dataclass
class ShowAndFixBackend:
    manual: Manual
    frames: FrameStore
    reader: Reader
    session_id: str
    clarify_below: float
    frame_max_age_s: float
    bookings: list[dict[str, Any]] = field(default_factory=list)
    on_perception: Optional[Callable[[dict[str, Any]], None]] = None

    async def execute(self, tool: str, args: dict[str, Any]) -> tuple[str, Any, Optional[str]]:
        fn = getattr(self, f"_{tool}", None)
        if fn is None:
            return "error", None, f"unknown tool {tool}"
        try:
            return "ok", await fn(**args), None
        except Exception as exc:  # noqa: BLE001 - a failing tool is reported, not fatal
            return "error", None, f"{type(exc).__name__}: {exc}"

    async def _read_error_display(self) -> dict[str, Any]:
        frame = self.frames.fresh(self.frame_max_age_s)
        if frame is None:
            return {"status": "no_frame",
                    "message": "No recent camera frame. Ask the user to turn on the camera and point it at the display."}
        # JPEG-encoding a camera frame takes tens of ms: keep it off the event loop.
        data, mime = await asyncio.to_thread(frame.image)
        seen = await self.reader(data, mime)
        code = seen.get("code")
        conf = float(seen.get("confidence") or 0.0)
        record = {"code": code, "confidence": conf, "what_i_see": seen.get("what_i_see", ""), "frame": frame.source}
        if self.on_perception:
            self.on_perception(record)
        if code and conf >= self.clarify_below:
            return {"status": "success", "code": code, "confidence": round(conf, 2)}
        # Below the threshold nothing is asserted (config perception.clarify_below).
        return {"status": "unclear", "best_guess": code, "confidence": round(conf, 2),
                "message": "The display is not readable with confidence. Ask the user to read the code "
                           "aloud or hold the camera closer; do not state a code."}

    async def _lookup_error_code(self, code: str) -> dict[str, Any]:
        e = self.manual.find(code)
        if e is None:
            return {"status": "not_found", "code": code,
                    "message": "This code is not in the manual table Keel has. Do not guess what it means."}
        return {"status": "success", "codes": e["codes"], "meaning": e["meaning"], "steps": e["steps"],
                "contact_service_if_persists": e["contact_service_if_persists"], "source": self.manual.source}

    async def _book_technician(self, code: str, day: str, time_window: str) -> dict[str, Any]:
        digest = hashlib.sha256(json.dumps([self.session_id, code, day, time_window]).encode()).hexdigest()
        booking = {"booking_id": f"SVC-{digest[:6].upper()}", "code": code, "day": day, "time_window": time_window}
        self.bookings.append(booking)
        return {"status": "success", **booking}

    async def _list_technician_bookings(self) -> dict[str, Any]:
        return {"status": "success", "bookings": list(self.bookings)}


def openai_reader(model: str, *, seed: int, client: Any = None) -> Reader:
    """Read a display with an OpenAI vision model (JSON mode, temperature 0)."""
    from openai import AsyncOpenAI

    api = client or AsyncOpenAI()

    async def read(image: bytes, mime: str) -> dict[str, Any]:
        url = f"data:{mime};base64," + base64.b64encode(image).decode("ascii")
        resp = await api.chat.completions.create(
            model=model, temperature=0, seed=seed, response_format={"type": "json_object"},
            messages=[{"role": "system", "content": VISION_PROMPT},
                      {"role": "user", "content": [{"type": "text", "text": "Read the display."},
                                                   {"type": "image_url", "image_url": {"url": url}}]}])
        try:
            data = json.loads(resp.choices[0].message.content or "{}")
        except json.JSONDecodeError:
            return {"code": None, "confidence": 0.0, "what_i_see": "unparseable model output"}
        code = data.get("code")
        return {"code": str(code).strip() if code else None,
                "confidence": max(0.0, min(1.0, float(data.get("confidence") or 0.0))),
                "what_i_see": str(data.get("what_i_see") or "")[:200]}

    return read
