"""The only place that knows the evaluation kit's wire format.

The kernel speaks in provisional models (keel.protocol.provisional). A
KitAdapter moves those models across the harness's two asynchronous queues
(original guide v1.0.0, §3: "two asynchronous queues (timestamped events input, actions
output)"). When the real kit ships, write a new Codec and, if its transport
differs, a new KitAdapter. Nothing in keel.kernel should change.
"""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from typing import Any, Mapping, Optional

from pydantic import ValidationError

from keel.protocol.provisional import ACTION_ADAPTER, EVENT_ADAPTER, ActionT, EventT


class KitProtocolError(ValueError):
    """An inbound message could not be decoded into a provisional Event."""

    def __init__(self, raw: Any, cause: Exception):
        super().__init__(f"undecodable inbound message: {cause}")
        self.raw = raw
        self.cause = cause


class Codec(ABC):
    """Translate between kit wire messages and provisional models."""

    @abstractmethod
    def decode_event(self, raw: Any) -> EventT: ...

    @abstractmethod
    def encode_action(self, action: ActionT) -> Any: ...


class ProvisionalCodec(Codec):
    """Identity codec: the wire format *is* the provisional JSON.

    ASSUMPTION [K14] Wire messages are JSON objects (or JSON text) matching
    provisional.py field-for-field. Replace this class when the kit's real
    field names are known.
    """

    def decode_event(self, raw: Any) -> EventT:
        try:
            if isinstance(raw, (str, bytes)):
                return EVENT_ADAPTER.validate_json(raw)
            return EVENT_ADAPTER.validate_python(raw)
        except (ValidationError, ValueError) as exc:
            raise KitProtocolError(raw, exc) from exc

    def encode_action(self, action: ActionT) -> Mapping[str, Any]:
        # Re-validate on the way out: a model built with model_construct()
        # skips validation, and a malformed action must never leave Keel.
        validated = ACTION_ADAPTER.validate_python(action.model_dump(mode="python"))
        return validated.model_dump(mode="json")


class KitAdapter(ABC):
    """Transport between the kernel and the harness."""

    async def warmup(self) -> None:
        """Called once inside the harness's setup hook.

        Original guide v1.0.0, section 6: "300s setup/warm-up hook". Model loading happens here.
        ASSUMPTION [K15] How the harness invokes the warm-up hook.
        """

    @abstractmethod
    async def receive(self) -> Optional[EventT]:
        """Next inbound event, or None when the scenario stream has ended."""

    @abstractmethod
    async def send(self, action: ActionT) -> None: ...

    async def close(self) -> None:
        return None


# ASSUMPTION [K16] End-of-stream is signalled by a None on the input queue.
END_OF_STREAM = None


class QueueAdapter(KitAdapter):
    """Two asyncio queues carrying wire messages, decoded through a Codec."""

    def __init__(
        self,
        inbox: asyncio.Queue,
        outbox: asyncio.Queue,
        codec: Optional[Codec] = None,
    ) -> None:
        self.inbox = inbox
        self.outbox = outbox
        self.codec = codec or ProvisionalCodec()

    async def receive(self) -> Optional[EventT]:
        raw = await self.inbox.get()
        if raw is END_OF_STREAM:
            return None
        return self.codec.decode_event(raw)

    async def send(self, action: ActionT) -> None:
        await self.outbox.put(self.codec.encode_action(action))


def dumps_action(action: ActionT, codec: Optional[Codec] = None) -> str:
    """Serialise an action to compact JSON text through a codec."""
    encoded = (codec or ProvisionalCodec()).encode_action(action)
    return json.dumps(encoded, separators=(",", ":"), sort_keys=True)
