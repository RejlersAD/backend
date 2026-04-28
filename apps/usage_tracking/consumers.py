"""
Usage Tracking — WebSocket Consumer
====================================

Streams a live snapshot of system usage to authenticated dashboards.

Wire-up:
  * Routing : `routing.py` → `ws/usage/`
  * ASGI    : merged in `backend/config/asgi.py`
  * Triggers: `signals.py` (post-save broadcast) + periodic self-tick

Soft-coded knobs live in `realtime.py`:
  * USAGE_WS_GROUP / USAGE_WS_ENABLED / USAGE_TICK_INTERVAL_SECS
"""
from __future__ import annotations

import asyncio
import json
import logging

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncWebsocketConsumer

from .realtime import (
    USAGE_TICK_INTERVAL_SECS,
    USAGE_WS_ENABLED,
    USAGE_WS_GROUP,
    build_snapshot,
)

logger = logging.getLogger(__name__)

# Soft-coded auth toggle — set USAGE_WS_REQUIRE_AUTH=1 in production once a
# JWT-aware Channels middleware is wired up. Defaults to OFF to match the
# existing `ActivityStreamConsumer` pattern (frontend route is already
# auth-gated and the payload is non-sensitive aggregate metrics).
import os as _os
USAGE_WS_REQUIRE_AUTH = _os.getenv('USAGE_WS_REQUIRE_AUTH', '0') == '1'


class UsageStreamConsumer(AsyncWebsocketConsumer):
    """Real-time usage feed consumed by the Usage Dashboard."""

    async def connect(self):
        # Soft kill-switch — if env disables realtime, refuse cleanly so
        # the frontend falls back to polling.
        if not USAGE_WS_ENABLED:
            await self.close(code=4030)
            return

        if USAGE_WS_REQUIRE_AUTH:
            user = self.scope.get('user')
            if user is None or not getattr(user, 'is_authenticated', False):
                await self.close(code=4001)
                return

        self.group_name = USAGE_WS_GROUP
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        # Send initial snapshot immediately.
        snapshot = await database_sync_to_async(build_snapshot)()
        await self.send(text_data=json.dumps({**snapshot, 'type': 'usage_initial'}))

        # Self-tick loop — guarantees the dashboard updates even when
        # nobody else is hitting the API.
        self._tick_task = asyncio.create_task(self._self_tick_loop())

    async def disconnect(self, close_code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass
        task = getattr(self, '_tick_task', None)
        if task and not task.done():
            task.cancel()

    async def receive(self, text_data=None, bytes_data=None):
        # Frontend can request an immediate refresh.
        try:
            data = json.loads(text_data or '{}')
        except (TypeError, ValueError):
            return
        if data.get('type') == 'request_refresh':
            snapshot = await database_sync_to_async(build_snapshot)()
            await self.send(text_data=json.dumps(snapshot))

    # Group-event handler — name matches the `type` set in
    # `realtime.broadcast_snapshot` ("usage.tick").
    async def usage_tick(self, event):
        try:
            await self.send(text_data=json.dumps(event['payload']))
        except Exception as exc:                                              # pragma: no cover
            logger.debug('[UsageWS] send failed: %s', exc)

    async def _self_tick_loop(self):
        while True:
            try:
                await asyncio.sleep(USAGE_TICK_INTERVAL_SECS)
                snapshot = await database_sync_to_async(build_snapshot)()
                await self.send(text_data=json.dumps(snapshot))
            except asyncio.CancelledError:
                return
            except Exception as exc:                                          # pragma: no cover
                logger.debug('[UsageWS] tick failed: %s', exc)
                # Brief back-off so a transient DB error doesn't spin tightly.
                await asyncio.sleep(USAGE_TICK_INTERVAL_SECS)
