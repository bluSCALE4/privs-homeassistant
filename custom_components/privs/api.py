"""Client for the Privs Supabase backend.

Home Assistant signs in anonymously as its own Supabase user. Once someone
approves its pairing code in the Privs app, that user becomes a delegate of
theirs and the RPCs below return or change only what they delegated.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
import time
from typing import Any

import aiohttp

from .const import SUPABASE_KEY, SUPABASE_URL

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=20)
# Refresh this long before the access token expires.
TOKEN_LEEWAY = 60


class PrivsError(Exception):
    """Base error for the Privs backend."""


class PrivsConnectionError(PrivsError):
    """The backend could not be reached."""


class PrivsAuthError(PrivsError):
    """The session is gone, so Home Assistant has to pair again."""


class PrivsApiError(PrivsError):
    """The backend refused a request; the message is the database's."""


@dataclass(frozen=True)
class Person:
    """One person as Home Assistant sees them."""

    id: str
    name: str
    in_session: bool
    can_change: bool
    next_change_at: datetime | None
    privilege: int | None
    health: int | None
    max_privilege: int | None
    start_level: int | None
    status: str | None

    @classmethod
    def from_row(cls, row: dict[str, Any]) -> Person:
        """Build from an ha_people() row."""
        next_change = row.get("next_change_at")
        return cls(
            id=row["id"],
            name=row["name"],
            in_session=bool(row["in_session"]),
            can_change=bool(row["can_change"]),
            next_change_at=datetime.fromisoformat(next_change) if next_change else None,
            privilege=row.get("privilege"),
            health=row.get("health"),
            max_privilege=row.get("max_privilege"),
            start_level=row.get("start_level"),
            status=row.get("status"),
        )


@dataclass(frozen=True)
class Delegation:
    """What the person who approved the pairing granted."""

    id: str
    name: str
    scope: str
    active: bool


class PrivsClient:
    """Authenticated access to the Privs backend as one delegate."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        refresh_token: str | None = None,
        on_refresh_token: Callable[[str], None] | None = None,
        url: str = SUPABASE_URL,
        key: str = SUPABASE_KEY,
    ) -> None:
        """Start from a saved refresh token, or none before sign-in."""
        self._session = session
        self._url = url.rstrip("/")
        self._key = key
        self._refresh_token = refresh_token
        self._on_refresh_token = on_refresh_token
        self._access_token: str | None = None
        self._expires_at = 0.0
        self.user_id: str | None = None
        self._lock = asyncio.Lock()

    @property
    def url(self) -> str:
        """The backend's base URL."""
        return self._url

    @property
    def key(self) -> str:
        """The publishable API key."""
        return self._key

    @property
    def refresh_token(self) -> str | None:
        """The current refresh token; it changes on every refresh."""
        return self._refresh_token

    # Auth -------------------------------------------------------------------

    async def sign_in_anonymously(self) -> str:
        """Create a new anonymous user and return its id."""
        data = await self._auth_request("signup", {})
        self._store_session(data)
        assert self.user_id is not None
        return self.user_id

    async def access_token(self) -> str:
        """A valid access token, refreshing it first when it's close to expiry."""
        async with self._lock:
            if self._access_token is None or time.time() > self._expires_at - TOKEN_LEEWAY:
                await self._refresh()
            assert self._access_token is not None
            return self._access_token

    async def _refresh(self) -> None:
        if self._refresh_token is None:
            raise PrivsAuthError("not signed in")
        data = await self._auth_request(
            "token?grant_type=refresh_token", {"refresh_token": self._refresh_token}
        )
        self._store_session(data)

    def _store_session(self, data: dict[str, Any]) -> None:
        self._access_token = data["access_token"]
        self._expires_at = float(data.get("expires_at") or time.time() + data["expires_in"])
        self.user_id = data["user"]["id"]
        if data["refresh_token"] != self._refresh_token:
            self._refresh_token = data["refresh_token"]
            # Refresh tokens are single use, so the new one must be saved
            # before the next restart or the session is lost.
            if self._on_refresh_token is not None:
                self._on_refresh_token(self._refresh_token)

    async def _auth_request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        try:
            async with self._session.post(
                f"{self._url}/auth/v1/{path}",
                json=body,
                headers={"apikey": self._key},
                timeout=REQUEST_TIMEOUT,
            ) as response:
                data = await response.json(content_type=None)
                if response.status >= 500:
                    raise PrivsConnectionError(f"auth server error {response.status}")
                if response.status >= 400:
                    message = _error_message(data) or f"auth error {response.status}"
                    raise PrivsAuthError(message)
                return data
        except (aiohttp.ClientError, TimeoutError) as err:
            raise PrivsConnectionError(str(err)) from err

    # Data -------------------------------------------------------------------

    async def _rest(self, method: str, path: str, **kwargs: Any) -> Any:
        for attempt in range(2):
            token = await self.access_token()
            try:
                async with self._session.request(
                    method,
                    f"{self._url}/rest/v1/{path}",
                    headers={"apikey": self._key, "Authorization": f"Bearer {token}"},
                    timeout=REQUEST_TIMEOUT,
                    **kwargs,
                ) as response:
                    data = await response.json(content_type=None)
                    if response.status == 401 and attempt == 0:
                        # Expired early (clock skew, revoked session): refresh once.
                        self._access_token = None
                        continue
                    if response.status == 401:
                        raise PrivsAuthError(_error_message(data) or "unauthorized")
                    if response.status >= 500:
                        raise PrivsConnectionError(
                            _error_message(data) or f"server error {response.status}"
                        )
                    if response.status >= 400:
                        raise PrivsApiError(_error_message(data) or f"error {response.status}")
                    return data
            except (aiohttp.ClientError, TimeoutError) as err:
                raise PrivsConnectionError(str(err)) from err
        raise PrivsAuthError("unauthorized")

    async def _rpc(self, name: str, params: dict[str, Any] | None = None) -> Any:
        return await self._rest("POST", f"rpc/{name}", json=params or {})

    async def create_pairing(self, install_name: str) -> str:
        """Start a pairing and return the code to approve in the app."""
        return await self._rpc("create_ha_pairing", {"install_name": install_name})

    async def pairing_delegation_id(self, code: str) -> tuple[bool, str | None]:
        """Whether the pairing still exists, and its delegation once approved."""
        rows = await self._rest(
            "GET",
            "ha_pairings",
            params={"code": f"eq.{code}", "select": "status,delegation_id,expires_at"},
        )
        if not rows:
            return False, None
        row = rows[0]
        if row["status"] == "approved":
            return True, row["delegation_id"]
        expired = datetime.fromisoformat(row["expires_at"]).timestamp() < time.time()
        return not expired, None

    async def delegation(self, delegation_id: str) -> Delegation | None:
        """The delegation, or None once the app disconnected it."""
        rows = await self._rest(
            "GET",
            "delegations",
            params={"id": f"eq.{delegation_id}", "select": "id,name,scope,status"},
        )
        if not rows:
            return None
        row = rows[0]
        return Delegation(
            id=row["id"], name=row["name"], scope=row["scope"], active=row["status"] == "active"
        )

    async def people(self) -> list[Person]:
        """Everyone delegated to this install, with what it may see of them now."""
        return [Person.from_row(row) for row in await self._rpc("ha_people")]

    async def apply_point_event(self, person_id: str, kind: str, note: str | None = None) -> None:
        """Gain, lose or straight to bottom for one person."""
        await self._rpc("apply_point_event", {"person_id": person_id, "kind": kind, "note": note})


def _error_message(data: Any) -> str | None:
    if isinstance(data, dict):
        for key in ("message", "msg", "error_description", "error"):
            if isinstance(data.get(key), str):
                return data[key]
    return None
