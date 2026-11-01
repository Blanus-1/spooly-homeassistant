"""Dienste zum Ändern: Gewicht, Nachfüllen, Slots, Lagerort, Archiv. Endgültiges Löschen gibt es nicht."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from .api import (
    SpoolyApiDisabledError,
    SpoolyAuthError,
    SpoolyConnectionError,
    SpoolyError,
    SpoolyHttpError,
    SpoolyProExpiredError,
    SpoolyRateLimitError,
    SpoolyScopeError,
)
from .const import DOMAIN
from .coordinator import SpoolyConfigEntry, SpoolyCoordinator

ATTR_SLOT = "slot"
ATTR_GRAMS = "grams"
ATTR_GROSS_GRAMS = "gross_grams"
ATTR_CONFIRM_COLOR = "confirm_color"
ATTR_LOCATION = "location"

GRAMS = vol.All(vol.Coerce(float), vol.Range(min=0, max=40000))

SPOOL_SCHEMA = vol.Schema({vol.Required(ATTR_DEVICE_ID): cv.string})
SET_WEIGHT_SCHEMA = SPOOL_SCHEMA.extend({vol.Optional(ATTR_GRAMS): GRAMS, vol.Optional(ATTR_GROSS_GRAMS): GRAMS})
REFILL_SCHEMA = SPOOL_SCHEMA.extend({vol.Required(ATTR_GRAMS): GRAMS})
MOVE_SCHEMA = SPOOL_SCHEMA.extend({vol.Required(ATTR_LOCATION): vol.All(cv.string, vol.Length(min=1))})
ASSIGN_SCHEMA = SPOOL_SCHEMA.extend(
    {vol.Required(ATTR_SLOT): cv.entity_id, vol.Optional(ATTR_CONFIRM_COLOR, default=False): cv.boolean}
)
CLEAR_SCHEMA = vol.Schema({vol.Required(ATTR_SLOT): cv.entity_id})

# Fehlercode von Spooly -> Schlüssel der Übersetzung
HTTP_ERRORS = {
    "tare_missing": "tare_missing",
    "slot_autopilot_off": "slot_autopilot_off",
    "andere_farbe": "other_color",
    "archiviert": "spool_archived",
    "gleichzeitig": "busy",
    "above_spool_size": "above_spool_size",
    "gross_not_above_tare": "gross_not_above_tare",
    # Spooly meldet eine Spule im laufenden Druck als allgemeinen 409
    "conflict": "spool_in_print",
    "spool_in_print": "spool_in_print",
}


def _loaded_coordinator(hass: HomeAssistant, entry_id: str) -> SpoolyCoordinator:
    entry: SpoolyConfigEntry | None = hass.config_entries.async_get_entry(entry_id)
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_not_loaded")
    return entry.runtime_data


def _spool_target(hass: HomeAssistant, device_id: str) -> tuple[SpoolyCoordinator, str]:
    """Die Spule zum Gerät, sonst Fehler (fremdes, unbekanntes oder Drucker-Gerät)."""
    device = dr.async_get(hass).async_get(device_id)
    if device:
        prefix = f"{device.config_entry_id}_spool_"
        for domain, ident in device.identifiers:
            if domain == DOMAIN and ident.startswith(prefix):
                return _loaded_coordinator(hass, device.config_entry_id), ident.removeprefix(prefix)
    raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_a_spool")


def _slot_target(hass: HomeAssistant, entity_id: str) -> tuple[SpoolyCoordinator, str]:
    """Die `slot_id` zum Slot-Sensor. Ohne aktiven Slot-Autopilot gibt Spooly keine aus."""
    reg_entry = er.async_get(hass).async_get(entity_id)
    prefix = f"{reg_entry.config_entry_id}_slot_" if reg_entry else ""
    if reg_entry is None or reg_entry.platform != DOMAIN or not (reg_entry.unique_id or "").startswith(prefix):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_a_slot")
    coordinator = _loaded_coordinator(hass, reg_entry.config_entry_id)
    item = coordinator.data.slots.get(reg_entry.unique_id.removeprefix(prefix))
    if not item or not item.get("slot_id"):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="slot_autopilot_off")
    return coordinator, item["slot_id"]


def _error(coordinator: SpoolyCoordinator, err: SpoolyError) -> HomeAssistantError:
    """Übersetzt einen Fehler von Spooly in eine Meldung ohne interne Texte."""
    entry = coordinator.config_entry

    def invalid(key: str, **placeholders: str) -> ServiceValidationError:
        return ServiceValidationError(
            translation_domain=DOMAIN, translation_key=key, translation_placeholders=placeholders or None
        )

    if isinstance(err, SpoolyScopeError):
        # die Meldung nennt den Weg, die Anfrage nach neuer Anmeldung macht ihn sichtbar
        entry.async_start_reauth(coordinator.hass)
        return invalid("no_write_permission")
    if isinstance(err, SpoolyAuthError):
        entry.async_start_reauth(coordinator.hass)
        return HomeAssistantError(translation_domain=DOMAIN, translation_key="auth_failed")
    if isinstance(err, SpoolyProExpiredError):
        return invalid("pro_expired")
    if isinstance(err, SpoolyApiDisabledError):
        return invalid("api_disabled")
    if isinstance(err, SpoolyRateLimitError):
        return invalid("change_limit", seconds=str(err.retry_after))
    if isinstance(err, SpoolyConnectionError):
        return HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect")
    if isinstance(err, SpoolyHttpError):
        if key := HTTP_ERRORS.get(err.code):
            return invalid(key)
        if err.status == 404:
            return invalid("not_found")
        if err.status == 422:
            return invalid("invalid_input")
        return HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="rejected",
            translation_placeholders={"status": str(err.status), "code": err.code or "-"},
        )
    return HomeAssistantError(translation_domain=DOMAIN, translation_key="cannot_connect")


async def _perform(coordinator: SpoolyCoordinator, action: Awaitable[None]) -> None:
    """Führt die Änderung aus und holt danach sofort neue Daten."""
    try:
        await action
    except SpoolyError as err:
        raise _error(coordinator, err) from err
    await coordinator.async_request_refresh()


async def _find_location(coordinator: SpoolyCoordinator, wanted: str) -> str:
    """Lagerort nach Name (wie in Spooly angezeigt, mit Pfad) oder Kennung."""
    locations = await coordinator.client.get_locations()
    wanted = wanted.strip().casefold()
    found = [
        loc for loc in locations if loc["id"].casefold() == wanted or loc["name"].casefold() == wanted
    ]
    if not found:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="location_not_found")
    if len(found) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="location_ambiguous")
    return found[0]["id"]


async def _set_weight(call: ServiceCall) -> None:
    grams, gross = call.data.get(ATTR_GRAMS), call.data.get(ATTR_GROSS_GRAMS)
    if (grams is None) == (gross is None):
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="weight_exactly_one")
    coordinator, spool_id = _spool_target(call.hass, call.data[ATTR_DEVICE_ID])
    await _perform(coordinator, coordinator.client.set_weight(spool_id, grams, gross))


async def _refill(call: ServiceCall) -> None:
    coordinator, spool_id = _spool_target(call.hass, call.data[ATTR_DEVICE_ID])
    await _perform(coordinator, coordinator.client.refill(spool_id, call.data[ATTR_GRAMS]))


async def _move(call: ServiceCall) -> None:
    coordinator, spool_id = _spool_target(call.hass, call.data[ATTR_DEVICE_ID])

    async def move() -> None:
        location_id = await _find_location(coordinator, call.data[ATTR_LOCATION])
        await coordinator.client.move(spool_id, location_id)

    await _perform(coordinator, move())


async def _archive(call: ServiceCall) -> None:
    coordinator, spool_id = _spool_target(call.hass, call.data[ATTR_DEVICE_ID])
    await _perform(coordinator, coordinator.client.archive(spool_id))


async def _restore(call: ServiceCall) -> None:
    coordinator, spool_id = _spool_target(call.hass, call.data[ATTR_DEVICE_ID])
    await _perform(coordinator, coordinator.client.restore(spool_id))


async def _assign_slot(call: ServiceCall) -> None:
    coordinator, spool_id = _spool_target(call.hass, call.data[ATTR_DEVICE_ID])
    slot_coordinator, slot_id = _slot_target(call.hass, call.data[ATTR_SLOT])
    if slot_coordinator is not coordinator:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="different_accounts")
    await _perform(coordinator, coordinator.client.assign_slot(slot_id, spool_id, call.data[ATTR_CONFIRM_COLOR]))


async def _clear_slot(call: ServiceCall) -> None:
    coordinator, slot_id = _slot_target(call.hass, call.data[ATTR_SLOT])
    await _perform(coordinator, coordinator.client.clear_slot(slot_id))


SERVICES: dict[str, tuple[Any, Any]] = {
    "set_weight": (_set_weight, SET_WEIGHT_SCHEMA),
    "refill": (_refill, REFILL_SCHEMA),
    "assign_slot": (_assign_slot, ASSIGN_SCHEMA),
    "clear_slot": (_clear_slot, CLEAR_SCHEMA),
    "move": (_move, MOVE_SCHEMA),
    "archive": (_archive, SPOOL_SCHEMA),
    "restore": (_restore, SPOOL_SCHEMA),
}


@callback
def async_register_services(hass: HomeAssistant) -> None:
    for name, (handler, schema) in SERVICES.items():
        hass.services.async_register(DOMAIN, name, handler, schema)
