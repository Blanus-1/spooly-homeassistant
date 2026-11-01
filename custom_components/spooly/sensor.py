"""Sensoren: Restgewicht je Spule, Belegung je Slot, Aufrufzähler des Kontos."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfMass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import SpoolyConfigEntry, SpoolyCoordinator
from .entity import (
    SpoolEntity,
    SpoolyEntity,
    hub_device,
    printer_device_id,
    track_new_entities,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpoolyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    async_add_entities([AccountCallsSensor(entry.runtime_data)])
    track_new_entities(entry, async_add_entities, lambda d: d.spools, SpoolWeightSensor)
    track_new_entities(entry, async_add_entities, lambda d: d.slots, SlotSensor)


class SpoolWeightSensor(SpoolEntity, SensorEntity):
    _attr_translation_key = "remaining_weight"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: SpoolyCoordinator, spool_id: str) -> None:
        super().__init__(coordinator, spool_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{spool_id}_remaining_weight"

    @property
    def native_value(self) -> float | None:
        spool = self.spool
        return spool.get("remaining_grams") if spool else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        spool = self.spool
        if not spool:
            return {}
        size = spool.get("spool_size_grams")
        remaining = spool.get("remaining_grams")
        attrs = {
            "manufacturer": spool.get("manufacturer"),
            "material": spool.get("material"),
            "finish": spool.get("finish"),
            "color": spool.get("color_hex"),
            "location": spool.get("location"),
            "spool_size_grams": size,
            "fill_percent": round(remaining / size * 100, 1) if remaining is not None and size else None,
        }
        return {k: v for k, v in attrs.items() if v is not None}


class SlotSensor(SpoolyEntity, SensorEntity):
    """Welche Spule steckt im Slot. Leer, wenn keine zugeordnet ist."""

    _attr_translation_key = "slot"

    def __init__(self, coordinator: SpoolyCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        entry = coordinator.config_entry
        item = coordinator.data.slots[key]
        self._attr_unique_id = f"{entry.entry_id}_slot_{key}"
        self._attr_translation_placeholders = {"slot": item["slot"]}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, printer_device_id(entry, item["printer"]))},
            name=item["printer"],
            via_device_id=coordinator.hub_device_id,
        )

    @property
    def _item(self) -> dict[str, Any] | None:
        return self.coordinator.data.slots.get(self._key)

    @property
    def available(self) -> bool:
        return super().available and self._item is not None

    @property
    def native_value(self) -> str | None:
        item = self._item
        return item["spool"]["title"] if item and item.get("spool") else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        item = self._item
        if not item:
            return {}
        spool = item["spool"] or {}
        attrs = {
            # kommt nur mit aktivem Slot-Autopilot, die Dienste assign_slot und clear_slot brauchen ihn
            "slot_id": item.get("slot_id"),
            "spool_id": spool.get("id"),
            "number": spool.get("number"),
            "material": spool.get("material"),
            "color": spool.get("color_hex"),
        }
        return {k: v for k, v in attrs.items() if v is not None}


class AccountCallsSensor(SpoolyEntity, SensorEntity):
    """Wie viele Aufrufe heute verbraucht sind. Nützlich nur zur Fehlersuche."""

    _attr_translation_key = "calls_today"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_entity_registry_enabled_default = False
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SpoolyCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_calls_today"
        self._attr_device_info = hub_device(entry)

    @property
    def native_value(self) -> int:
        return self.coordinator.data.me["calls_today"]

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        me = self.coordinator.data.me
        return {"daily_limit": me["daily_limit"], "account": me["account"]}
