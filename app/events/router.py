"""Server-Sent Events router.

Provides a single ``GET /events`` endpoint that streams real-time domain
updates to the frontend.  This eliminates the need for polling:

    const es = new EventSource('/events', { headers: { Authorization: 'Bearer ...' }});
    es.addEventListener('job_status_updated', e => { queryClient.invalidateQueries({ queryKey: ['jobs'] }); });

The connection stays open for up to ``MAX_SSE_AGE_SECONDS`` (5 minutes) and
automatically re-establishes on the client side.
"""

import asyncio
from typing import AsyncGenerator

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.session import get_db
from app.common.dependencies import get_current_user
from app.users.models import User
from app.common.events import event_bus
from app.common.logging import get_logger

logger = get_logger(__name__)

router = APIRouter()

# Keep connections alive for 5 minutes; clients reconnect automatically.
MAX_SSE_AGE_SECONDS = 300
HEARTBEAT_INTERVAL_SECONDS = 15


async def _event_stream(
    user: User,
) -> AsyncGenerator[str, None]:
    """Yield SSE formatted lines for every event on the user's channel."""
    channel = f"user:{user.id}"
    queue = await event_bus.subscribe(channel)
    start = asyncio.get_event_loop().time()

    try:
        # Send an initial comment so the client knows the connection is live.
        yield ":ok\n\n"

        while True:
            elapsed = asyncio.get_event_loop().time() - start
            if elapsed >= MAX_SSE_AGE_SECONDS:
                # Graceful end-of-stream so the client reconnects.
                yield "event: close\ndata: reconnect\n\n"
                break

            try:
                message = await asyncio.wait_for(
                    queue.get(), timeout=HEARTBEAT_INTERVAL_SECONDS
                )
            except asyncio.TimeoutError:
                # Send a heartbeat comment to keep proxies happy.
                yield ":hb\n\n"
                continue

            # SSE format:  data: <json>\n\n
            yield f"data: {message}\n\n"
    finally:
        event_bus.unsubscribe(channel, queue)


@router.get("/events")
async def sse_events(
    request: Request,
    current_user: User = Depends(get_current_user),
):
    """Stream real-time updates for the authenticated user."""
    return StreamingResponse(
        _event_stream(current_user),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
