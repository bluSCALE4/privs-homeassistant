"""Base entity: one device per person."""

from __future__ import annotations

from collections.abc import Callable, Iterable

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity, EntityDescription
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import Person
from .const import DOMAIN
from .coordinator import PrivsConfigEntry, PrivsCoordinator


class PrivsEntity(CoordinatorEntity[PrivsCoordinator]):
    """An entity on a person's device."""

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: PrivsCoordinator, person: Person, description: EntityDescription
    ) -> None:
        """Attach to the person's device."""
        super().__init__(coordinator)
        self.person_id = person.id
        self.entity_description = description
        self._attr_unique_id = f"{person.id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, person.id)},
            name=person.name,
            manufacturer="Privs",
            model="Person",
        )

    @property
    def person(self) -> Person:
        """The person's latest state."""
        return self.coordinator.data.people[self.person_id]

    @property
    def available(self) -> bool:
        """Unavailable once the person is no longer delegated."""
        return super().available and self.person_id in self.coordinator.data.people


def async_add_people(
    entry: PrivsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    build: Callable[[PrivsCoordinator, Person], Iterable[Entity]],
) -> None:
    """Add entities for everyone now, and for people delegated later."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        # Someone removed and later added back gets fresh entities.
        known.intersection_update(coordinator.data.people)
        new = [p for pid, p in coordinator.data.people.items() if pid not in known]
        if not new:
            return
        known.update(p.id for p in new)
        async_add_entities(entity for person in new for entity in build(coordinator, person))

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
