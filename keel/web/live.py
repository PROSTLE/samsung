"""Publish a session's story to the web app while it happens.

The agent calls `attach(trace, room)` once per room. Every trace record is
turned into story events (keel/web/story.py) and sent as one text-stream
message on STORY_TOPIC, only to participants who joined from the web app, so
the FDB-v3 runner's client never receives anything and benchmark runs are
unaffected.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from keel.trace import TraceRecord, TraceWriter
from keel.web.story import STORY_TOPIC, WEB_IDENTITY_PREFIX, Story

log = logging.getLogger("keel.web.live")


def web_participants(room: Any) -> list[str]:
    return [p.identity for p in room.remote_participants.values() if p.identity.startswith(WEB_IDENTITY_PREFIX)]


def attach(trace: TraceWriter, room: Any, loop: asyncio.AbstractEventLoop) -> Story:
    story = Story()
    tasks: set[asyncio.Task[Any]] = set()

    async def send(payload: str, to: list[str]) -> None:
        try:
            await room.local_participant.send_text(payload, topic=STORY_TOPIC, destination_identities=to)
        except Exception as e:  # noqa: BLE001 - the web view is best effort; the session goes on
            log.debug("story not sent: %s", e)

    def schedule(payload: str, to: list[str]) -> None:
        t = loop.create_task(send(payload, to))
        tasks.add(t)
        t.add_done_callback(tasks.discard)

    def on_record(rec: TraceRecord) -> None:
        events = story.feed(rec)
        if not events or not room.isconnected():
            return
        to = web_participants(room)
        if to:
            loop.call_soon_threadsafe(schedule, json.dumps(events, ensure_ascii=False), to)

    trace.listeners.append(on_record)
    return story
