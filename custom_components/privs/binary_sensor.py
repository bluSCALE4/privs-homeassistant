"""Binary sensor: whether this install may see a person right now."""

from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Person
from .coordinator import PrivsConfigEntry, PrivsCoordinator
from .entity import PrivsEntity, async_add_people

PARALLEL_UPDATES = 0

IN_SESSION = BinarySensorEntityDescription(key="in_session", translation_key="in_session")


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrivsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add each person's in-session sensor."""

    def build(coordinator: PrivsCoordinator, person: Person) -> list[PrivsInSession]:
        return [PrivsInSession(coordinator, person, IN_SESSION)]

    async_add_people(entry, async_add_entities, build)


class PrivsInSession(PrivsEntity, BinarySensorEntity):
    """On during the access window of whoever approved the pairing.

    Always on for people they own; for people shared with them, it follows
    their collaborator schedule.
    """

    @property
    def is_on(self) -> bool:
        """Whether the person's state is visible now."""
        return self.person.in_session
