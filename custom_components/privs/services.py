"""The change_points service: a point event with an optional note."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv, device_registry as dr
import voluptuous as vol

from .const import (
    ATTR_KIND,
    ATTR_NOTE,
    DOMAIN,
    POINT_KINDS,
    SCOPE_READ_POINTS,
    SERVICE_CHANGE_POINTS,
)
from .coordinator import PrivsConfigEntry

CHANGE_POINTS_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_DEVICE_ID): vol.All(cv.ensure_list, [cv.string]),
        vol.Required(ATTR_KIND): vol.In(POINT_KINDS),
        vol.Optional(ATTR_NOTE): cv.string,
    }
)


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services once for all entries."""

    async def change_points(call: ServiceCall) -> None:
        registry = dr.async_get(hass)
        targets: list[tuple[PrivsConfigEntry, str]] = []
        for device_id in call.data[ATTR_DEVICE_ID]:
            device = registry.async_get(device_id)
            person_id = (
                next((value for domain, value in device.identifiers if domain == DOMAIN), None)
                if device
                else None
            )
            entry = next(
                (
                    e
                    for e in hass.config_entries.async_loaded_entries(DOMAIN)
                    if device and e.entry_id in device.config_entries
                ),
                None,
            )
            if person_id is None or entry is None or entry.state is not ConfigEntryState.LOADED:
                raise ServiceValidationError(
                    translation_domain=DOMAIN,
                    translation_key="not_a_person",
                    translation_placeholders={"device_id": device_id},
                )
            if entry.runtime_data.data.delegation.scope != SCOPE_READ_POINTS:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="read_only")
            targets.append((entry, person_id))

        for entry, person_id in targets:
            await entry.runtime_data.async_change_points(
                person_id, call.data[ATTR_KIND], call.data.get(ATTR_NOTE)
            )

    hass.services.async_register(
        DOMAIN, SERVICE_CHANGE_POINTS, change_points, schema=CHANGE_POINTS_SCHEMA
    )
