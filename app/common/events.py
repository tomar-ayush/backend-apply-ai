"""In-memory event bus for Server-Sent Events.

A simple async pub/sub implementation that lets the backend push real-time
updates to connected clients so the frontend can stop polling.
"""

import asyncio
import json
import uuid
from typing import Any, Callable, Dict, Set

from app.common.logging import get_logger

logger = get_logger(__name__)


class EventBus:
    """Async pub/sub event bus for domain events.

    Usage:
        bus = EventBus()
        # Subscribe
        queue = await bus.subscribe("user_123")
        # Publish
        await bus.publish("user_123", {"type": "job_updated", "job_id": ...})
        # Read
        event = await queue.get()
    """

    def __init__(self):
        self._channels: Dict[str, Set[asyncio.Queue]] = {}

    async def subscribe(self, channel: str) -> asyncio.Queue:
        """Create a queue and register it on *channel*."""
        queue: asyncio.Queue = asyncio.Queue(maxsize=256)
        self._channels.setdefault(channel, set()).add(queue)
        logger.debug("sse_subscribe channel=%s", channel)
        return queue

    def unsubscribe(self, channel: str, queue: asyncio.Queue) -> None:
        """Remove a queue from *channel*."""
        listeners = self._channels.get(channel)
        if listeners:
            listeners.discard(queue)
            if not listeners:
                self._channels.pop(channel, None)
        logger.debug("sse_unsubscribe channel=%s", channel)

    async def publish(
        self, channel: str, payload: dict[str, Any]
    ) -> None:
        """Broadcast *payload* to every queue on *channel*."""
        listeners = self._channels.get(channel)
        if not listeners:
            return

        message = json.dumps(payload, default=str)
        dead: Set[asyncio.Queue] = set()

        for queue in listeners:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                dead.add(queue)
                logger.warning(
                    "sse_queue_full channel=%s dropping_client", channel
                )

        for queue in dead:
            self.unsubscribe(channel, queue)


# Global singleton – imported by services and routers.
event_bus = EventBus()
