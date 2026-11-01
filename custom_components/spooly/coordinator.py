"""Holt alle fünf Minuten Konto, Spulen, Slots, Wartung und Statistik."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    SpoolyApiDisabledError,
    SpoolyAuthError,
    SpoolyClient,
    SpoolyConnectionError,
    SpoolyError,
    SpoolyProExpiredError,
    SpoolyRateLimitError,
)
from .const import DOMAIN, UPDATE_INTERVAL

_LOGGER = logging.getLogger(__name__)

type SpoolyConfigEntry = ConfigEntry[SpoolyCoordinator]


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def printer_key(printer: str) -> str:
    """Spooly liefert keine Drucker-Id, nur den Namen. Gleiche Namen fallen deshalb zusammen."""
    return _short_hash(printer)


def slot_key(printer: str, slot: str) -> str:
    return _short_hash(f"{printer}\n{slot}")


@dataclass
class SpoolyData:
    me: dict[str, Any]
    spools: dict[str, dict[str, Any]] = field(default_factory=dict)
    slots: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Wartung: Aufgaben und Drucker je Id, dazu die Statistik des laufenden Monats
    tasks: dict[str, dict[str, Any]] = field(default_factory=dict)
    maint_printers: dict[str, dict[str, Any]] = field(default_factory=dict)
    statistics: dict[str, Any] = field(default_factory=dict)


class SpoolyCoordinator(DataUpdateCoordinator[SpoolyData]):
    config_entry: SpoolyConfigEntry

    def __init__(self, hass: HomeAssistant, entry: SpoolyConfigEntry, client: SpoolyClient) -> None:
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=UPDATE_INTERVAL,
        )
        self.client = client
        self.hub_device_id = ""
        self._duplicate_slots: set[str] = set()

    async def _async_update_data(self) -> SpoolyData:
        try:
            me = await self.client.get_me()
            spools = await self.client.get_spools()
            slots = await self.client.get_slots()
            maintenance = await self.client.get_maintenance()
            today = dt_util.now()
            statistics = await self.client.get_statistics(today.year, today.month)
        except SpoolyAuthError as err:
            raise ConfigEntryAuthFailed from err
        except SpoolyProExpiredError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="pro_expired") from err
        except SpoolyApiDisabledError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="api_disabled") from err
        except SpoolyRateLimitError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="rate_limited",
                retry_after=err.retry_after,
            ) from err
        except SpoolyConnectionError as err:
            raise UpdateFailed(translation_domain=DOMAIN, translation_key="cannot_connect") from err
        except SpoolyError as err:
            raise UpdateFailed(
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"error": str(err)},
            ) from err

        return SpoolyData(
            me=me,
            spools={s["id"]: s for s in spools},
            slots=self._index_slots(slots),
            tasks={t["id"]: t for t in maintenance["tasks"]},
            maint_printers={p["printer_id"]: p for p in maintenance["printers"]},
            statistics=statistics,
        )

    def _index_slots(self, slots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        indexed: dict[str, dict[str, Any]] = {}
        for item in slots:
            key = slot_key(item["printer"], item["slot"])
            if key in indexed:
                # zwei Drucker mit gleichem Namen lassen sich nicht trennen, der erste gewinnt
                if key not in self._duplicate_slots:
                    self._duplicate_slots.add(key)
                    _LOGGER.warning(
                        "Slot %s of printer %s appears more than once, only the first is shown. "
                        "Give the printers different names in Spooly",
                        item["slot"],
                        item["printer"],
                    )
                continue
            indexed[key] = item
        return indexed
