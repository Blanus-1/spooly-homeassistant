"""Spooly: Spulen und Slots in Home Assistant anzeigen und ändern."""

from __future__ import annotations

from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    ImplementationUnavailableError,
    OAuth2Session,
    async_get_config_entry_implementation,
    async_register_implementation,
)
from homeassistant.helpers.typing import ConfigType

from .api import SpoolyClient
from .const import CONF_API_KEY, CONF_URL, DEFAULT_URL, DOMAIN
from .coordinator import SpoolyConfigEntry, SpoolyCoordinator
from .entity import device_ids, hub_device
from .oauth import SpoolyImplementation, origin, token_getter
from .services import async_register_services

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

PLATFORMS = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_register_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: SpoolyConfigEntry) -> bool:
    url = entry.data.get(CONF_URL, DEFAULT_URL)
    if CONF_API_KEY in entry.data:
        token = entry.data[CONF_API_KEY]
    else:
        # Anmeldung per OAuth: die Implementierung gehört zur Adresse des Eintrags
        async_register_implementation(hass, DOMAIN, SpoolyImplementation(hass, origin(url)))
        try:
            implementation = await async_get_config_entry_implementation(hass, entry)
        except ImplementationUnavailableError as err:
            raise ConfigEntryNotReady from err
        token = token_getter(OAuth2Session(hass, entry, implementation))

    client = SpoolyClient(async_get_clientsession(hass), token, url)
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
