"""Config flow: pairing, expiry, errors and reauth."""

import asyncio
from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.privs.api import PrivsConnectionError
from custom_components.privs.const import (
    CONF_DELEGATION_ID,
    CONF_REFRESH_TOKEN,
    CONF_USER_ID,
    DOMAIN,
)

from .conftest import DELEGATION_ID, USER_ID

pytestmark = pytest.mark.usefixtures("mock_setup_entry")


async def _start(hass: HomeAssistant) -> dict:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Kitchen"})


async def _finish_progress(hass: HomeAssistant, result: dict) -> dict:
    """Let the approval poll finish, then advance past the progress step."""
    if result["type"] is not FlowResultType.SHOW_PROGRESS:
        return result
    await hass.async_block_till_done()
    return await hass.config_entries.flow.async_configure(result["flow_id"])


async def test_pairs_and_creates_entry(hass: HomeAssistant, client: AsyncMock) -> None:
    """The app approves the code; the entry takes the name given in the app."""
    approved = asyncio.Event()

    async def wait_for_app(code: str) -> tuple[bool, str]:
        await approved.wait()
        return True, DELEGATION_ID

    client.pairing_delegation_id.side_effect = wait_for_app
    result = await _start(hass)
    assert result["type"] is FlowResultType.SHOW_PROGRESS
    assert result["step_id"] == "pair"
    assert result["progress_action"] == "wait_for_approval"
    assert result["description_placeholders"] == {"code": "ABCD2345"}
    client.create_pairing.assert_awaited_once_with("Kitchen")
    client.pairing_delegation_id.assert_awaited_once_with("ABCD2345")

    approved.set()
    result = await _finish_progress(hass, result)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Home"
    assert result["data"] == {
        CONF_REFRESH_TOKEN: "refresh-1",
        CONF_USER_ID: USER_ID,
        CONF_DELEGATION_ID: DELEGATION_ID,
    }
    assert result["result"].unique_id == DELEGATION_ID


async def test_waits_until_approved(hass: HomeAssistant, client: AsyncMock) -> None:
    """Pending polls keep the progress step up."""
    client.pairing_delegation_id.side_effect = [(True, None), (True, DELEGATION_ID)]
    with patch("custom_components.privs.config_flow.PAIRING_POLL_INTERVAL", 0):
        result = await _start(hass)
        result = await _finish_progress(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert client.pairing_delegation_id.await_count == 2


async def test_expired_code_offers_a_new_one(hass: HomeAssistant, client: AsyncMock) -> None:
    """An expired code leads to a retry step that makes a new code."""
    client.pairing_delegation_id.return_value = (False, None)
    result = await _start(hass)
    result = await _finish_progress(hass, result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "expired"

    client.create_pairing.return_value = "WXYZ6789"
    client.pairing_delegation_id.return_value = (True, DELEGATION_ID)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    result = await _finish_progress(hass, result)
    assert client.create_pairing.await_count == 2
    client.pairing_delegation_id.assert_awaited_with("WXYZ6789")
    # The same anonymous user asks for the new code.
    client.sign_in_anonymously.assert_awaited_once()
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_lost_connection_while_waiting(hass: HomeAssistant, client: AsyncMock) -> None:
    """A network error while waiting leads to the failed step."""
    client.pairing_delegation_id.side_effect = PrivsConnectionError("down")
    result = await _start(hass)
    result = await _finish_progress(hass, result)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "failed"


async def test_cannot_connect(hass: HomeAssistant, client: AsyncMock) -> None:
    """Sign-in failure keeps the form up with an error, then recovers."""
    client.sign_in_anonymously.side_effect = PrivsConnectionError("down")
    result = await _start(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "cannot_connect"}

    client.sign_in_anonymously.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"name": "Kitchen"})
    result = await _finish_progress(hass, result)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_already_configured(
    hass: HomeAssistant, client: AsyncMock, entry: MockConfigEntry
) -> None:
    """The same delegation can't be added twice."""
    entry.add_to_hass(hass)
    result = await _start(hass)
    result = await _finish_progress(hass, result)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_pairs_again(
    hass: HomeAssistant, client: AsyncMock, entry: MockConfigEntry
) -> None:
    """Reauth makes a new code and moves the entry to the new delegation."""
    entry.add_to_hass(hass)
    new_delegation = "55555555-5555-5555-5555-555555555555"
    client.pairing_delegation_id.return_value = (True, new_delegation)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    client.create_pairing.assert_awaited_once_with("Home")
    result = await _finish_progress(hass, result)

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.unique_id == new_delegation
    assert entry.data[CONF_DELEGATION_ID] == new_delegation
    assert entry.data[CONF_REFRESH_TOKEN] == "refresh-1"
