"""The Supabase client: sessions, token rotation and errors."""

import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import pytest
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.privs.api import (
    PrivsApiError,
    PrivsAuthError,
    PrivsClient,
    PrivsConnectionError,
)

URL = "https://example.supabase.co"


def session_body(access: str, refresh: str, expires_in: int = 3600) -> dict:
    """A GoTrue session response."""
    return {
        "access_token": access,
        "refresh_token": refresh,
        "expires_in": expires_in,
        "expires_at": int(time.time()) + expires_in,
        "user": {"id": "user-1"},
    }


def make_client(hass: HomeAssistant, refresh: str | None = "r0", saved: list | None = None):
    """A client against the fake URL."""
    return PrivsClient(
        async_get_clientsession(hass),
        refresh,
        saved.append if saved is not None else None,
        url=URL,
        key="pk",
    )


async def test_anonymous_sign_in(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Sign-in stores the session and sends the publishable key."""
    aioclient_mock.post(f"{URL}/auth/v1/signup", json=session_body("a1", "r1"))
    client = make_client(hass, None)
    assert await client.sign_in_anonymously() == "user-1"
    assert client.refresh_token == "r1"
    assert await client.access_token() == "a1"
    assert aioclient_mock.mock_calls[0][3]["apikey"] == "pk"


async def test_refresh_rotates_and_saves(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """The first request refreshes, and the new refresh token is handed back."""
    saved: list[str] = []
    aioclient_mock.post(
        f"{URL}/auth/v1/token?grant_type=refresh_token", json=session_body("a1", "r1")
    )
    aioclient_mock.post(f"{URL}/rest/v1/rpc/ha_people", json=[])
    client = make_client(hass, saved=saved)

    assert await client.people() == []
    assert saved == ["r1"]
    assert aioclient_mock.mock_calls[0][2] == {"refresh_token": "r0"}
    assert aioclient_mock.mock_calls[1][3]["Authorization"] == "Bearer a1"


async def test_dead_refresh_token(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """A refresh token the server no longer knows means pairing again."""
    aioclient_mock.post(
        f"{URL}/auth/v1/token?grant_type=refresh_token",
        status=400,
        json={"error_code": "refresh_token_not_found", "msg": "Invalid Refresh Token"},
    )
    with pytest.raises(PrivsAuthError, match="Invalid Refresh Token"):
        await make_client(hass).people()


async def test_database_error_message(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """A raised exception's message comes through as PrivsApiError."""
    aioclient_mock.post(
        f"{URL}/auth/v1/token?grant_type=refresh_token", json=session_body("a1", "r1")
    )
    aioclient_mock.post(
        f"{URL}/rest/v1/rpc/apply_point_event",
        status=400,
        json={"code": "P0001", "message": "rate limited"},
    )
    with pytest.raises(PrivsApiError, match=r"^rate limited$"):
        await make_client(hass).apply_point_event("p1", "gain")


async def test_server_error_is_connection_error(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    """5xx is treated as temporary."""
    aioclient_mock.post(
        f"{URL}/auth/v1/token?grant_type=refresh_token", json=session_body("a1", "r1")
    )
    aioclient_mock.post(f"{URL}/rest/v1/rpc/ha_people", status=503, json={})
    with pytest.raises(PrivsConnectionError):
        await make_client(hass).people()


async def test_pairing_states(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    """Pending, approved, expired and gone pairings."""
    client = make_client(hass)
    future = "2999-01-01T00:00:00+00:00"
    past = "2000-01-01T00:00:00+00:00"
    cases = [
        ([{"status": "pending", "delegation_id": None, "expires_at": future}], (True, None)),
        ([{"status": "approved", "delegation_id": "d1", "expires_at": past}], (True, "d1")),
        ([{"status": "pending", "delegation_id": None, "expires_at": past}], (False, None)),
        ([], (False, None)),
    ]
    for rows, expected in cases:
        aioclient_mock.clear_requests()
        aioclient_mock.post(
            f"{URL}/auth/v1/token?grant_type=refresh_token", json=session_body("a1", "r1")
        )
        aioclient_mock.get(f"{URL}/rest/v1/ha_pairings", json=rows)
        assert await client.pairing_delegation_id("ABCD2345") == expected
