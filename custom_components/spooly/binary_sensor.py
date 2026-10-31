"""Binärsensor „Niedrig“ je Spule, gesteuert nur über fill_level von Spooly."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import SpoolyConfigEntry, SpoolyCoordinator
from .entity import SpoolEntity, track_new_entities

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpoolyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    track_new_entities(entry, async_add_entities, lambda d: d.spools, SpoolLowSensor)


class SpoolLowSensor(SpoolEntity, BinarySensorEntity):
    _attr_translation_key = "low"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM

    def __init__(self, coordinator: SpoolyCoordinator, spool_id: str) -> None:
        super().__init__(coordinator, spool_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{spool_id}_low"

    @property
    def is_on(self) -> bool | None:
        spool = self.spool
        return spool.get("fill_level") in ("low", "crit") if spool else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        spool = self.spool
        if spool and spool.get("fill_level"):
            return {"level": spool["fill_level"]}
        return {}
