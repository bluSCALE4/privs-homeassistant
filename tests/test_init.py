"""Setup, entities, buttons, the service and reauth."""

from collections.abc import AsyncGenerator
from unittest.mock import AsyncMock

from freezegun.api import FrozenDateTimeFactory
from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, SERVICE_PRESS
from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed

import custom_components.privs as privs_module
from custom_components.privs import async_remove_config_entry_device
from custom_components.privs.api import Delegation, PrivsApiError, PrivsAuthError
from custom_components.privs.const import CONF_REFRESH_TOKEN, DOMAIN, POLL_INTERVAL

from .conftest import AVA_ID, BEN_ID, DELEGATION_ID, make_person


@pytest.fixture
async def loaded(
    hass: HomeAssistant, client: AsyncMock, entry: MockConfigEntry
) -> AsyncGenerator[MockConfigEntry]:
    """Set the entry up, and unload it so no timers linger."""
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    yield entry
    if entry.state is ConfigEntryState.LOADED:
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_entities(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """Visible people show their state; hidden people show only their schedule."""
    assert loaded.state is ConfigEntryState.LOADED
    assert hass.states.get("sensor.ava_status").state == "green"
    assert hass.states.get("sensor.ava_level").state == "4"
    assert hass.states.get("sensor.ava_hearts").state == "3"
    assert hass.states.get("binary_sensor.ava_in_session").state == "on"
    assert hass.states.get("sensor.ava_next_session_change").state == STATE_UNKNOWN
    assert hass.states.get("button.ava_gain_point").state == STATE_UNKNOWN

    assert hass.states.get("sensor.ben_status").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.ben_level").state == STATE_UNAVAILABLE
    assert hass.states.get("binary_sensor.ben_in_session").state == "off"
    assert hass.states.get("sensor.ben_next_session_change").state != STATE_UNAVAILABLE
    assert hass.states.get("button.ben_lose_point").state == STATE_UNAVAILABLE

    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, AVA_ID), loaded.entry_id)
    assert device is not None
    assert device.name == "Ava"


@pytest.mark.parametrize("scope", ["read"])
async def test_read_only_has_no_buttons(hass: HomeAssistant, loaded: MockConfigEntry) -> None:
    """View Only pairings get sensors but no buttons."""
    assert hass.states.get("sensor.ava_status") is not None
    assert hass.states.async_entity_ids(BUTTON_DOMAIN) == []


async def test_press_button(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry
) -> None:
    """A press applies the event and refreshes."""
    client.people.return_value = [make_person(privilege=5)]
    await hass.services.async_call(
        BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.ava_gain_point"}, blocking=True
    )
    client.apply_point_event.assert_awaited_once_with(AVA_ID, "gain", None)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.ava_level").state == "5"


@pytest.mark.parametrize(
    ("message", "key"),
    [
        ("rate limited", "rate_limited"),
        ("outside session", "outside_session"),
        ("locked", "locked"),
        ("something new", "point_failed"),
    ],
)
async def test_press_refused(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry, message: str, key: str
) -> None:
    """The backend's refusals become readable errors."""
    client.apply_point_event.side_effect = PrivsApiError(message)
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            BUTTON_DOMAIN, SERVICE_PRESS, {ATTR_ENTITY_ID: "button.ava_lose_point"}, blocking=True
        )
    assert err.value.translation_key == key


async def test_locked_disables_buttons(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """No hearts left means no buttons until tomorrow's reset."""
    client.people.return_value = [make_person(health=0, privilege=1, status="red")]
    freezer.tick(POLL_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.ava_status").state == "red"
    assert hass.states.get("button.ava_gain_point").state == STATE_UNAVAILABLE


async def test_change_points_service(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry
) -> None:
    """The service applies the event to each person with the note."""
    registry = dr.async_get(hass)
    ava = registry.async_get_device_by_identifier((DOMAIN, AVA_ID), loaded.entry_id)
    ben = registry.async_get_device_by_identifier((DOMAIN, BEN_ID), loaded.entry_id)
    await hass.services.async_call(
        DOMAIN,
        "change_points",
        {"device_id": [ava.id, ben.id], "kind": "lose", "note": "Hit his sister"},
        blocking=True,
    )
    assert client.apply_point_event.await_args_list[0].args == (AVA_ID, "lose", "Hit his sister")
    assert client.apply_point_event.await_args_list[1].args == (BEN_ID, "lose", "Hit his sister")


async def test_change_points_rejects_other_devices(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry
) -> None:
    """A device that isn't a Privs person is a validation error."""
    other = MockConfigEntry(domain="demo")
    other.add_to_hass(hass)
    device = dr.async_get(hass).async_get_or_create(
        config_entry_id=other.entry_id, identifiers={("demo", "x")}
    )
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "change_points", {"device_id": device.id, "kind": "gain"}, blocking=True
        )
    client.apply_point_event.assert_not_awaited()


@pytest.mark.parametrize("scope", ["read"])
async def test_change_points_read_only(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry
) -> None:
    """The service refuses View Only pairings before calling the backend."""
    ava = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, AVA_ID), loaded.entry_id)
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN, "change_points", {"device_id": ava.id, "kind": "gain"}, blocking=True
        )


async def test_disconnected_in_app_starts_reauth(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Disconnecting in the app asks the user to pair again."""
    client.delegation.return_value = None
    freezer.tick(POLL_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    flows = hass.config_entries.flow.async_progress()
    assert [f["context"]["source"] for f in flows] == ["reauth"]


async def test_lost_session_fails_setup_into_reauth(
    hass: HomeAssistant, client: AsyncMock, entry: MockConfigEntry
) -> None:
    """A dead refresh token at startup goes straight to reauth."""
    client.delegation.side_effect = PrivsAuthError("refresh_token_not_found")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert any(
        f["context"]["source"] == "reauth" for f in hass.config_entries.flow.async_progress()
    )


async def test_refresh_token_is_saved(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry
) -> None:
    """Rotated refresh tokens are written back to the entry."""
    on_refresh = privs_module.PrivsClient.call_args.args[2]
    on_refresh("refresh-2")
    assert loaded.data[CONF_REFRESH_TOKEN] == "refresh-2"


async def test_new_person_gets_entities(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """Someone added to the pairing later shows up without a reload."""
    client.people.return_value = [
        *client.people.return_value,
        make_person(id="66666666-6666-6666-6666-666666666666", name="Cy"),
    ]
    freezer.tick(POLL_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.cy_status").state == "green"


async def test_removed_person_device_can_be_deleted(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """A person no longer delegated goes unavailable and can be removed."""
    client.people.return_value = [make_person()]
    freezer.tick(POLL_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get("sensor.ben_next_session_change").state == STATE_UNAVAILABLE

    registry = dr.async_get(hass)
    ben = registry.async_get_device_by_identifier((DOMAIN, BEN_ID), loaded.entry_id)
    ava = registry.async_get_device_by_identifier((DOMAIN, AVA_ID), loaded.entry_id)
    assert await async_remove_config_entry_device(hass, loaded, ben)
    assert not await async_remove_config_entry_device(hass, loaded, ava)


async def test_scope_change_reloads(
    hass: HomeAssistant, client: AsyncMock, loaded: MockConfigEntry, freezer: FrozenDateTimeFactory
) -> None:
    """When the app narrows access, the buttons go away."""
    client.delegation.return_value = Delegation(
        id=DELEGATION_ID, name="Home", scope="read", active=True
    )
    freezer.tick(POLL_INTERVAL)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert loaded.state is ConfigEntryState.LOADED
    assert hass.states.get("button.ava_gain_point").state == STATE_UNAVAILABLE
    assert hass.states.get("sensor.ava_status").state == "green"
