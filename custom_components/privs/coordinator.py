"""Coordinator holding everyone delegated to this install."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.debounce import Debouncer
from homeassistant.helpers.event import async_track_point_in_utc_time
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Delegation, Person, PrivsApiError, PrivsAuthError, PrivsClient, PrivsError
from .const import CONF_DELEGATION_ID, DOMAIN, POLL_INTERVAL

_LOGGER = logging.getLogger(__name__)

# apply_point_event's exception messages, mapped to translation keys.
POINT_ERRORS = {
    "locked": "locked",
    "not delegated": "not_delegated",
    "read only": "read_only",
    "outside session": "outside_session",
    "rate limited": "rate_limited",
}

type PrivsConfigEntry = ConfigEntry[PrivsCoordinator]


@dataclass(frozen=True)
class PrivsData:
    """One refresh's worth of state."""

    delegation: Delegation
    people: dict[str, Person]


class PrivsCoordinator(DataUpdateCoordinator[PrivsData]):
    """Fetches ha_people() on Realtime changes, schedule boundaries and a slow poll."""

    config_entry: PrivsConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PrivsConfigEntry, client: PrivsClient) -> None:
        """Set up the coordinator for one paired install."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=POLL_INTERVAL,
            # Realtime can fire several changes for one tap in the app.
            request_refresh_debouncer=Debouncer(hass, _LOGGER, cooldown=1, immediate=False),
        )
        self.client = client
        self._unsub_boundary: CALLBACK_TYPE | None = None
        self._shut_down = False

    async def _async_update_data(self) -> PrivsData:
        try:
            delegation = await self.client.delegation(self.config_entry.data[CONF_DELEGATION_ID])
            if delegation is None or not delegation.active:
                raise ConfigEntryAuthFailed(
                    translation_domain=DOMAIN, translation_key="disconnected"
                )
            people = await self.client.people()
        except PrivsAuthError as err:
            raise ConfigEntryAuthFailed(
                translation_domain=DOMAIN, translation_key="session_expired"
            ) from err
        except PrivsError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="update_failed",
                translation_placeholders={"error": str(err)},
            ) from err

        if self.data is not None and self.data.delegation.scope != delegation.scope:
            # The app changed what this install may do; buttons come and go.
            self.hass.config_entries.async_schedule_reload(self.config_entry.entry_id)

        self._schedule_boundary(people)
        return PrivsData(delegation=delegation, people={person.id: person for person in people})

    async def async_change_points(self, person_id: str, kind: str, note: str | None = None) -> None:
        """Apply a point event, turning the backend's refusals into readable errors."""
        try:
            await self.client.apply_point_event(person_id, kind, note)
        except PrivsAuthError as err:
            self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="session_expired"
            ) from err
        except PrivsApiError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=POINT_ERRORS.get(str(err), "point_failed"),
                translation_placeholders={"error": str(err)},
            ) from err
        except PrivsError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="point_failed",
                translation_placeholders={"error": str(err)},
            ) from err
        # Show the result right away rather than after the debounce.
        await self.async_refresh()

    def _schedule_boundary(self, people: list[Person]) -> None:
        """Refresh right when someone's session starts or ends."""
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        boundaries = [p.next_change_at for p in people if p.next_change_at is not None]
        if boundaries and not self._shut_down:
            self._unsub_boundary = async_track_point_in_utc_time(
                self.hass, self._boundary_reached, min(boundaries)
            )

    @callback
    def _boundary_reached(self, _now: datetime) -> None:
        self._unsub_boundary = None
        self.hass.async_create_task(self.async_request_refresh())

    async def async_shutdown(self) -> None:
        """Cancel the boundary timer with the coordinator."""
        self._shut_down = True
        if self._unsub_boundary is not None:
            self._unsub_boundary()
            self._unsub_boundary = None
        await super().async_shutdown()
