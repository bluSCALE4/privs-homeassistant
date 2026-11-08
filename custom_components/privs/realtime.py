"""Supabase Realtime listener that tells the coordinator when data changed.

Speaks the Phoenix channel protocol over aiohttp's websocket. Row level
security filters the change feed, so this install only hears about people
it was delegated and its own delegation.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import itertools
import logging
from typing import Any

import aiohttp

from .api import PrivsAuthError, PrivsClient, PrivsError

_LOGGER = logging.getLogger(__name__)

TOPIC = "realtime:privs-ha"
TABLES = ("persons", "delegations")
HEARTBEAT_INTERVAL = 25
MAX_BACKOFF = 300


class PrivsRealtime:
    """Keeps one Realtime channel open, reconnecting with backoff."""

    def __init__(
        self,
        client: PrivsClient,
        session: aiohttp.ClientSession,
        on_change: Callable[[], None],
    ) -> None:
        """Call on_change for every change, and once after each (re)connect."""
        self._client = client
        self._session = session
        self._on_change = on_change
        self._refs = itertools.count(1)

    async def run(self) -> None:
        """Listen until cancelled or the session is gone."""
        backoff = 1
        while True:
            try:
                await self._listen()
                backoff = 1
            except PrivsAuthError:
                # The coordinator's next poll surfaces this as a reauth.
                _LOGGER.debug("Realtime stopped: session is gone")
                return
            except (PrivsError, aiohttp.ClientError, TimeoutError) as err:
                _LOGGER.debug("Realtime disconnected: %s", err)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _listen(self) -> None:
        url = self._client.url.replace("https://", "wss://", 1).replace("http://", "ws://", 1)
        token = await self._client.access_token()
        async with self._session.ws_connect(
            f"{url}/realtime/v1/websocket",
            params={"apikey": self._client.key, "vsn": "1.0.0"},
            heartbeat=None,
        ) as ws:
            join_ref = str(next(self._refs))
            await ws.send_json(
                {
                    "topic": TOPIC,
                    "event": "phx_join",
                    "payload": {
                        "config": {
                            "postgres_changes": [
                                {"event": "*", "schema": "public", "table": table}
                                for table in TABLES
                            ]
                        },
                        "access_token": token,
                    },
                    "ref": join_ref,
                    "join_ref": join_ref,
                }
            )
            heartbeat = asyncio.create_task(self._heartbeat(ws, join_ref, token))
            try:
                async for message in ws:
                    if message.type is aiohttp.WSMsgType.TEXT:
                        self._handle(message.json(), join_ref)
                    elif message.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                        break
            finally:
                heartbeat.cancel()
            if heartbeat.done() and not heartbeat.cancelled() and heartbeat.exception():
                raise heartbeat.exception()  # type: ignore[misc]

    def _handle(self, message: dict[str, Any], join_ref: str) -> None:
        event = message.get("event")
        if event == "postgres_changes":
            self._on_change()
        elif event == "phx_reply" and message.get("ref") == join_ref:
            if message.get("payload", {}).get("status") == "ok":
                # Anything that changed while disconnected was missed.
                self._on_change()
            else:
                raise PrivsError(f"join refused: {message.get('payload')}")
        elif event in ("phx_error", "phx_close"):
            raise PrivsError(f"channel {event}")

    async def _heartbeat(
        self, ws: aiohttp.ClientWebSocketResponse, join_ref: str, token: str
    ) -> None:
        try:
            await self._beat(ws, join_ref, token)
        except asyncio.CancelledError:
            raise
        except Exception:
            # Unblock the receive loop so _listen can surface the error.
            await ws.close()
            raise

    async def _beat(self, ws: aiohttp.ClientWebSocketResponse, join_ref: str, token: str) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await ws.send_json(
                {
                    "topic": "phoenix",
                    "event": "heartbeat",
                    "payload": {},
                    "ref": str(next(self._refs)),
                }
            )
            # Access tokens last an hour; the channel needs each new one.
            new_token = await self._client.access_token()
            if new_token != token:
                token = new_token
                await ws.send_json(
                    {
                        "topic": TOPIC,
                        "event": "access_token",
                        "payload": {"access_token": token},
                        "ref": str(next(self._refs)),
                        "join_ref": join_ref,
                    }
                )
