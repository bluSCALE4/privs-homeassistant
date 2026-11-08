"""Buttons: gain, lose and straight to bottom.

Only added when the pairing was approved with "View and Change Points".
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Person
from .const import POINT_BOTTOM, POINT_GAIN, POINT_LOSE, SCOPE_READ_POINTS
from .coordinator import PrivsConfigEntry, PrivsCoordinator
from .entity import PrivsEntity, async_add_people

# Each press is a write; let the backend's rate limit see them in order.
PARALLEL_UPDATES = 1

BUTTONS: tuple[ButtonEntityDescription, ...] = (
    ButtonEntityDescription(key=POINT_GAIN, translation_key=POINT_GAIN),
    ButtonEntityDescription(key=POINT_LOSE, translation_key=POINT_LOSE),
    ButtonEntityDescription(key=POINT_BOTTOM, translation_key=POINT_BOTTOM),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrivsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add each person's point buttons when the pairing allows changes."""
    if entry.runtime_data.data.delegation.scope != SCOPE_READ_POINTS:
        return

    def build(coordinator: PrivsCoordinator, person: Person) -> list[PrivsPointButton]:
        return [PrivsPointButton(coordinator, person, description) for description in BUTTONS]

    async_add_people(entry, async_add_entities, build)


class PrivsPointButton(PrivsEntity, ButtonEntity):
    """Applies one point event."""

    @property
    def available(self) -> bool:
        """Unavailable outside the session or once locked for the day."""
        return super().available and self.person.can_change and self.person.health != 0

    async def async_press(self) -> None:
        """Apply the event."""
        await self.coordinator.async_change_points(self.person_id, self.entity_description.key)
