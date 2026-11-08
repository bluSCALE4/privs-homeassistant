"""Sensors: status, level, hearts and the next session change."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import Person
from .coordinator import PrivsConfigEntry, PrivsCoordinator
from .entity import PrivsEntity, async_add_people

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class PrivsSensorEntityDescription(SensorEntityDescription):
    """A person sensor."""

    value_fn: Callable[[Person], str | int | datetime | None]
    # Outside a shared person's session the backend hides their state.
    needs_session: bool = True


SENSORS: tuple[PrivsSensorEntityDescription, ...] = (
    PrivsSensorEntityDescription(
        key="status",
        translation_key="status",
        device_class=SensorDeviceClass.ENUM,
        options=["green", "yellow", "red"],
        value_fn=lambda p: p.status,
    ),
    PrivsSensorEntityDescription(
        key="level",
        translation_key="level",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.privilege,
    ),
    PrivsSensorEntityDescription(
        key="hearts",
        translation_key="hearts",
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda p: p.health,
    ),
    PrivsSensorEntityDescription(
        key="max_level",
        translation_key="max_level",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.max_privilege,
    ),
    PrivsSensorEntityDescription(
        key="start_level",
        translation_key="start_level",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda p: p.start_level,
    ),
    PrivsSensorEntityDescription(
        key="next_change",
        translation_key="next_change",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda p: p.next_change_at,
        needs_session=False,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PrivsConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add each person's sensors."""

    def build(coordinator: PrivsCoordinator, person: Person) -> list[PrivsSensor]:
        return [PrivsSensor(coordinator, person, description) for description in SENSORS]

    async_add_people(entry, async_add_entities, build)


class PrivsSensor(PrivsEntity, SensorEntity):
    """One value of a person's state."""

    entity_description: PrivsSensorEntityDescription

    @property
    def available(self) -> bool:
        """Unavailable while the person's state is hidden."""
        return super().available and (
            self.person.in_session or not self.entity_description.needs_session
        )

    @property
    def native_value(self) -> str | int | datetime | None:
        """The current value."""
        return self.entity_description.value_fn(self.person)
