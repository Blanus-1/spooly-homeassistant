"""Sensoren: Restgewicht je Spule, Belegung je Slot, Wartung je Aufgabe und Drucker, Monat und Lager."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity, SensorStateClass
from homeassistant.const import EntityCategory, UnitOfMass, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN
from .coordinator import SpoolyConfigEntry, SpoolyCoordinator
from .entity import (
    SpoolEntity,
    SpoolyEntity,
    hub_device,
    maint_printer_device,
    printer_device_id,
    track_new_entities,
)

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: SpoolyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    coordinator = entry.runtime_data
    async_add_entities([AccountCallsSensor(coordinator), MonthGramsSensor(coordinator), StockValueSensor(coordinator)])
    track_new_entities(entry, async_add_entities, lambda d: d.spools, SpoolWeightSensor)
    track_new_entities(entry, async_add_entities, lambda d: d.slots, SlotSensor)
    track_new_entities(entry, async_add_entities, lambda d: d.tasks, MaintenanceTaskSensor)
    track_new_entities(entry, async_add_entities, lambda d: d.maint_printers, MaintenanceDueSensor)
    track_new_entities(entry, async_add_entities, lambda d: d.maint_printers, PrintHoursSensor)


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
            # eindeutig auch bei gleichnamigen Orten, passt direkt in spooly.move
            "location_id": spool.get("location_id"),
            "place": spool.get("place"),
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


TASK_STATUS = ["due", "soon", "ok"]


class MaintenanceTaskSensor(SpoolyEntity, SensorEntity):
    """Status einer Wartungsaufgabe. Die Dienste complete_maintenance und snooze_maintenance zielen darauf."""

    _attr_translation_key = "maintenance_task"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = TASK_STATUS

    def __init__(self, coordinator: SpoolyCoordinator, task_id: str) -> None:
        super().__init__(coordinator)
        self._task_id = task_id
        task = coordinator.data.tasks[task_id]
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_task_{task_id}"
        self._attr_name = task["name"]
        self._attr_device_info = maint_printer_device(coordinator, task["printer_id"], task.get("printer"))

    @property
    def _task(self) -> dict[str, Any] | None:
        return self.coordinator.data.tasks.get(self._task_id)

    @property
    def available(self) -> bool:
        # erledigte oder gelöschte Aufgaben fehlen in der Antwort
        return super().available and self._task is not None

    @property
    def native_value(self) -> str | None:
        task = self._task
        # ein unbekannter Status würde den ENUM-Sensor zum Fehler machen
        return task["status"] if task and task.get("status") in TASK_STATUS else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        task = self._task
        if not task:
            return {}
        keys = ("metric", "interval", "used", "remaining", "eta_days", "last_done_at", "snoozed_until", "default_action")
        return {k: task[k] for k in keys if task.get(k) is not None}


class MaintenancePrinterEntity(SpoolyEntity):
    """Entität am Drucker-Gerät der Wartung. Fällt der Drucker weg, wird sie nicht verfügbar."""

    def __init__(self, coordinator: SpoolyCoordinator, printer_id: str) -> None:
        super().__init__(coordinator)
        self._printer_id = printer_id
        printer = coordinator.data.maint_printers[printer_id]
        self._attr_device_info = maint_printer_device(coordinator, printer_id, printer.get("printer"))

    @property
    def printer(self) -> dict[str, Any] | None:
        return self.coordinator.data.maint_printers.get(self._printer_id)

    @property
    def available(self) -> bool:
        return super().available and self.printer is not None


class MaintenanceDueSensor(MaintenancePrinterEntity, SensorEntity):
    _attr_translation_key = "maintenance_due"
    _attr_state_class = SensorStateClass.MEASUREMENT

    def __init__(self, coordinator: SpoolyCoordinator, printer_id: str) -> None:
        super().__init__(coordinator, printer_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{printer_id}_maintenance_due"

    @property
    def native_value(self) -> int | None:
        printer = self.printer
        return printer["due_count"] if printer else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        printer = self.printer
        return {"soon_count": printer["soon_count"]} if printer else {}


class PrintHoursSensor(MaintenancePrinterEntity, SensorEntity):
    _attr_translation_key = "print_hours"
    _attr_device_class = SensorDeviceClass.DURATION
    _attr_native_unit_of_measurement = UnitOfTime.HOURS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 1

    def __init__(self, coordinator: SpoolyCoordinator, printer_id: str) -> None:
        super().__init__(coordinator, printer_id)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{printer_id}_print_hours"

    @property
    def native_value(self) -> float | None:
        printer = self.printer
        return printer["hours"] if printer else None


class MonthGramsSensor(SpoolyEntity, SensorEntity):
    """Verbrauchtes Filament im laufenden Monat (Zeit der HA-Instanz), beginnt mit jedem Monat bei 0."""

    _attr_translation_key = "filament_month"
    _attr_device_class = SensorDeviceClass.WEIGHT
    _attr_native_unit_of_measurement = UnitOfMass.GRAMS
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_suggested_display_precision = 0

    def __init__(self, coordinator: SpoolyCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_filament_month"
        self._attr_device_info = hub_device(entry)

    @property
    def native_value(self) -> float | None:
        return self.coordinator.data.statistics.get("grams")


class StockValueSensor(SpoolyEntity, SensorEntity):
    _attr_translation_key = "stock_value"
    _attr_device_class = SensorDeviceClass.MONETARY
    _attr_suggested_display_precision = 2

    def __init__(self, coordinator: SpoolyCoordinator) -> None:
        super().__init__(coordinator)
        entry = coordinator.config_entry
        self._attr_unique_id = f"{entry.entry_id}_stock_value"
        self._attr_device_info = hub_device(entry)

    @property
    def native_value(self) -> float | None:
        return (self.coordinator.data.statistics.get("stock") or {}).get("stock_value")

    @property
    def native_unit_of_measurement(self) -> str | None:
        return self.coordinator.data.statistics.get("currency")
