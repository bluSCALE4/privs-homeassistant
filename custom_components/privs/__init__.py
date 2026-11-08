"""The Privileges integration: Privs people and points in Home Assistant."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv, device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import PrivsClient
from .const import CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import PrivsConfigEntry, PrivsCoordinator
from .realtime import PrivsRealtime
from .services import async_setup_services

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration's services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: PrivsConfigEntry) -> bool:
    """Connect a paired install."""
    session = async_get_clientsession(hass)

    @callback
    def save_refresh_token(token: str) -> None:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
        )

    client = PrivsClient(session, entry.data[CONF_REFRESH_TOKEN], save_refresh_token)
    coordinator = PrivsCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    @callback
    def changed() -> None:
        hass.async_create_task(coordinator.async_request_refresh())

    entry.async_create_background_task(
        hass, PrivsRealtime(client, session, changed).run(), "privs realtime"
    )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: PrivsConfigEntry) -> bool:
    """Disconnect; the background Realtime task is cancelled with the entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: PrivsConfigEntry, device: dr.DeviceEntry
) -> bool:
    """Allow removing a person's device once they're no longer delegated."""
    people = entry.runtime_data.data.people
    return not any(
        identifier[0] == DOMAIN and identifier[1] in people for identifier in device.identifiers
    )
