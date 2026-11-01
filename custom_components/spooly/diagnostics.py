"""Diagnosedaten ohne Schlüssel und ohne Namen der Nutzer."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import CONF_API_KEY
from .coordinator import SpoolyConfigEntry

TO_REDACT = {
    CONF_API_KEY,
    "token",
    "access_token",
    "refresh_token",
    "account_id",
    "slot_id",
    "key_name",
    "title",
    "location",
    "printer",
    "slot",
}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: SpoolyConfigEntry) -> dict[str, Any]:
    data = entry.runtime_data.data
    return {
        "entry": async_redact_data(entry.data, TO_REDACT),
        "me": async_redact_data(data.me, TO_REDACT),
        "spools": async_redact_data(list(data.spools.values()), TO_REDACT),
        "slots": async_redact_data(list(data.slots.values()), TO_REDACT),
    }
