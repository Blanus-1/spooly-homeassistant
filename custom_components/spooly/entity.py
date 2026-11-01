"""Gemeinsame Basis der Entitäten."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import SpoolyConfigEntry, SpoolyCoordinator, SpoolyData, printer_key


def hub_device(entry: SpoolyConfigEntry) -> DeviceInfo:
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        name="Spooly",
        manufacturer="Spooly",
        entry_type=DeviceEntryType.SERVICE,
    )


def spool_device_name(spool: dict[str, Any]) -> str:
    title = spool.get("title") or f"Spool {spool['id'][:8]}"
    if spool.get("number") is not None:
        return f"#{spool['number']} {title}"
    return title


def spool_device_id(entry: SpoolyConfigEntry, spool_id: str) -> str:
    return f"{entry.entry_id}_spool_{spool_id}"


def printer_device_id(entry: SpoolyConfigEntry, printer: str) -> str:
    return f"{entry.entry_id}_printer_{printer_key(printer)}"


def maint_printer_device_id(entry: SpoolyConfigEntry, printer_id: str, name: str | None) -> str:
    """Mit Namen dasselbe Gerät wie seine Slots, ohne Namen über die Kennung aus der Wartung."""
    return printer_device_id(entry, name) if name else f"{entry.entry_id}_maintprinter_{printer_id}"


def device_ids(entry: SpoolyConfigEntry, data: SpoolyData) -> set[str]:
    """Alle Geräte, die zu den aktuellen Daten gehören (für das Entfernen alter Geräte)."""
    ids = {entry.entry_id}
    ids.update(spool_device_id(entry, spool_id) for spool_id in data.spools)
    ids.update(printer_device_id(entry, item["printer"]) for item in data.slots.values())
    ids.update(maint_printer_device_id(entry, pid, p.get("printer")) for pid, p in data.maint_printers.items())
    return ids


class SpoolyEntity(CoordinatorEntity[SpoolyCoordinator]):
    _attr_has_entity_name = True


class SpoolEntity(SpoolyEntity):
    """Entität an einem Spulen-Gerät. Die Id der Spule bleibt, die Nummer kann sich ändern."""

    def __init__(self, coordinator: SpoolyCoordinator, spool_id: str) -> None:
        super().__init__(coordinator)
        self._spool_id = spool_id
        entry = coordinator.config_entry
        spool = coordinator.data.spools[spool_id]
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, spool_device_id(entry, spool_id))},
            name=spool_device_name(spool),
            manufacturer=spool.get("manufacturer"),
            model=spool.get("material"),
            via_device_id=coordinator.hub_device_id,
        )

    @property
    def spool(self) -> dict[str, Any] | None:
        return self.coordinator.data.spools.get(self._spool_id)

    @property
    def available(self) -> bool:
        # archivierte oder gelöschte Spulen fehlen in der Antwort
        return super().available and self.spool is not None


def maint_printer_device(coordinator: SpoolyCoordinator, printer_id: str, name: str | None) -> DeviceInfo:
    """Das Gerät eines Druckers aus der Wartung. Ohne Namen gilt der übersetzte Fallback."""
    info = DeviceInfo(
        identifiers={(DOMAIN, maint_printer_device_id(coordinator.config_entry, printer_id, name))},
        via_device_id=coordinator.hub_device_id,
    )
    if name:
        info["name"] = name
    else:
        info["translation_key"] = "printer"
    return info


@callback
def track_new_entities(
    entry: SpoolyConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
    get_ids: Callable[[SpoolyData], Iterable[str]],
    make: Callable[[SpoolyCoordinator, str], Entity],
) -> None:
    """Legt Entitäten für neue Spulen oder Slots an, ohne dass HA neu starten muss."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def add_new() -> None:
        new = [i for i in get_ids(coordinator.data) if i not in known]
        if new:
            known.update(new)
            async_add_entities([make(coordinator, i) for i in new])

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))
