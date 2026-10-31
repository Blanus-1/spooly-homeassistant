"""Spooly: Spulen und Slots in Home Assistant anzeigen (nur lesen)."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import SpoolyClient
from .const import CONF_API_KEY, CONF_URL, DEFAULT_URL, DOMAIN
from .coordinator import SpoolyConfigEntry, SpoolyCoordinator
from .entity import device_ids, hub_device

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: SpoolyConfigEntry) -> bool:
    client = SpoolyClient(
        async_get_clientsession(hass),
        entry.data[CONF_API_KEY],
        entry.data.get(CONF_URL, DEFAULT_URL),
    )
    coordinator = SpoolyCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    # das Hub-Gerät muss vor den Spulen- und Druckergeräten da sein (via_device_id)
    hub = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **hub_device(entry))
    coordinator.hub_device_id = hub.id

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: SpoolyConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: SpoolyConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Geräte von archivierten oder gelöschten Spulen darf man von Hand entfernen."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or coordinator.data is None:
        return False
    current = device_ids(entry, coordinator.data)
    return not any(
        domain == DOMAIN and ident in current for domain, ident in device_entry.identifiers
    )
