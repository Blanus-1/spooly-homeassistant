from datetime import timedelta
import json
import logging

import pytest

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_OFF, STATE_ON, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.spooly import async_remove_config_entry_device
from custom_components.spooly.api import SpoolyClient
from custom_components.spooly.config_flow import key_id
from custom_components.spooly.const import DEFAULT_URL, DOMAIN, UPDATE_INTERVAL
from custom_components.spooly.coordinator import slot_key
from custom_components.spooly.entity import printer_device_id

from .common import KEY, FakeSpooly, entry_data, make_slot, make_spool


async def setup_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, **fake_attrs):
    fake = FakeSpooly(aioclient_mock)
    for name, value in fake_attrs.items():
        setattr(fake, name, value)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id=key_id(KEY))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, fake


async def refresh(hass: HomeAssistant, seconds: float = UPDATE_INTERVAL.total_seconds() + 1) -> None:
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=seconds))
    await hass.async_block_till_done()


def entity_id(hass: HomeAssistant, domain: str, entry, suffix: str) -> str | None:
    return er.async_get(hass).async_get_entity_id(domain, DOMAIN, f"{entry.entry_id}_{suffix}")


def weight(hass, entry, spool_id: str = "spool-1"):
    return hass.states.get(entity_id(hass, "sensor", entry, f"{spool_id}_remaining_weight"))


def low(hass, entry, spool_id: str = "spool-1"):
    return hass.states.get(entity_id(hass, "binary_sensor", entry, f"{spool_id}_low"))


def slot_state(hass, entry, printer: str, slot: str):
    return hass.states.get(entity_id(hass, "sensor", entry, f"slot_{slot_key(printer, slot)}"))


async def test_spool_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_entry(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED

    state = weight(hass, entry)
    assert state.state == "800.0"
    assert state.attributes["unit_of_measurement"] == "g"
    assert state.attributes["device_class"] == "weight"
    assert state.attributes["state_class"] == "measurement"
    assert state.attributes["manufacturer"] == "Bambu Lab"
    assert state.attributes["material"] == "PLA"
    assert state.attributes["finish"] == "Matt"
    assert state.attributes["color"] == "#111111"
    assert state.attributes["location"] == "Regal 1 · Platz 4"
    assert (state.attributes["location_id"], state.attributes["place"]) == ("loc-2", 4)
    assert state.attributes["spool_size_grams"] == 1000.0
    assert state.attributes["fill_percent"] == 80.0
    assert state.name.endswith("Restgewicht") or state.name.endswith("Remaining weight")

    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_spool_spool-1"), entry.entry_id)
    assert device.name == "#1 PLA Basic Schwarz 1"
    assert device.via_device_id is not None

    assert low(hass, entry).state == STATE_OFF
    assert low(hass, entry).attributes["device_class"] == "problem"


@pytest.mark.parametrize(
    ("fill_level", "expected"),
    [(None, STATE_OFF), ("low", STATE_ON), ("crit", STATE_ON)],
)
async def test_fill_level_drives_low(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, fill_level, expected
) -> None:
    # 5 % Rest ohne fill_level bleibt aus: es gibt keine eigene Schwelle
    entry, _ = await setup_entry(
        hass, aioclient_mock, spools=[make_spool(1, fill_level=fill_level, remaining_grams=50.0)]
    )
    state = low(hass, entry)
    assert state.state == expected
    assert state.attributes.get("level") == fill_level


async def test_spool_without_number_or_title(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    spools = [
        make_spool(1, number=None, title="Nur Name"),
        make_spool(2, number=None, title="", id="abcdef123456"),
    ]
    entry, _ = await setup_entry(hass, aioclient_mock, spools=spools)
    devices = dr.async_get(hass)
    assert devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_spool_spool-1"), entry.entry_id).name == "Nur Name"
    assert (
        devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_spool_abcdef123456"), entry.entry_id).name
        == "Spool abcdef12"
    )


async def test_spool_without_weight(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_entry(
        hass, aioclient_mock, spools=[make_spool(1, remaining_grams=None, spool_size_grams=None)]
    )
    state = weight(hass, entry)
    assert state.state == STATE_UNKNOWN
    assert "fill_percent" not in state.attributes


async def test_all_pages_are_fetched(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    fake.spools = [make_spool(n) for n in range(1, 1204)]
    client = SpoolyClient(async_get_clientsession(hass), KEY, DEFAULT_URL)
    spools = await client.get_spools()
    assert len(spools) == 1203
    assert fake.paths.count("/spools") == 3


async def test_vanishing_spool_comes_back(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock, spools=[make_spool(1), make_spool(2)])
    kept = fake.spools
    fake.spools = [kept[0]]
    await refresh(hass)

    assert weight(hass, entry, "spool-2").state == STATE_UNAVAILABLE
    assert low(hass, entry, "spool-2").state == STATE_UNAVAILABLE
    assert weight(hass, entry, "spool-1").state == "800.0"

    # nur das Gerät der fehlenden Spule lässt sich entfernen
    devices = dr.async_get(hass)
    gone = devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_spool_spool-2"), entry.entry_id)
    there = devices.async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_spool_spool-1"), entry.entry_id)
    hub = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert await async_remove_config_entry_device(hass, entry, gone) is True
    assert await async_remove_config_entry_device(hass, entry, there) is False
    assert await async_remove_config_entry_device(hass, entry, hub) is False

    fake.spools = kept
    await refresh(hass, 2 * UPDATE_INTERVAL.total_seconds() + 2)
    assert weight(hass, entry, "spool-2").state == "800.0"


async def test_new_spool_appears_without_restart(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    assert entity_id(hass, "sensor", entry, "spool-9_remaining_weight") is None

    fake.spools = [make_spool(1), make_spool(9, fill_level="low")]
    await refresh(hass)
    assert weight(hass, entry, "spool-9").state == "800.0"
    assert low(hass, entry, "spool-9").state == STATE_ON


async def test_slots(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    spool = make_spool(1)
    slots = [make_slot("Bambu X1C", "AMS 1 Slot 1", spool), make_slot("Bambu X1C", "AMS 1 Slot 2")]
    entry, fake = await setup_entry(hass, aioclient_mock, slots=slots)

    full = slot_state(hass, entry, "Bambu X1C", "AMS 1 Slot 1")
    assert full.state == "PLA Basic Schwarz 1"
    assert full.attributes["spool_id"] == "spool-1"
    assert full.attributes["number"] == 1
    assert full.attributes["material"] == "PLA"
    assert full.attributes["color"] == "#111111"

    empty = slot_state(hass, entry, "Bambu X1C", "AMS 1 Slot 2")
    assert empty.state == STATE_UNKNOWN
    assert "spool_id" not in empty.attributes

    printer = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, printer_device_id(entry, "Bambu X1C")), entry.entry_id)
    assert printer.name == "Bambu X1C"

    # neuer Slot, ohne Neustart
    fake.slots = [*slots, make_slot("Prusa MK4", "Slot 1", spool)]
    await refresh(hass)
    assert slot_state(hass, entry, "Prusa MK4", "Slot 1").state == "PLA Basic Schwarz 1"

    # Slot verschwindet
    fake.slots = slots[:1]
    await refresh(hass, 2 * UPDATE_INTERVAL.total_seconds() + 2)
    assert slot_state(hass, entry, "Prusa MK4", "Slot 1").state == STATE_UNAVAILABLE


async def test_two_printers_with_same_name(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.WARNING)
    first, second = make_spool(1), make_spool(2)
    slots = [make_slot("Drucker", "Slot 1", first), make_slot("Drucker", "Slot 1", second)]
    entry, _ = await setup_entry(hass, aioclient_mock, spools=[first, second], slots=slots)

    assert entry.state is ConfigEntryState.LOADED
    assert slot_state(hass, entry, "Drucker", "Slot 1").state == first["title"]
    await refresh(hass)
    assert caplog.text.count("appears more than once") == 1


async def test_rate_limit_respects_retry_after(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.fail(429, "fair_use", {"Retry-After": "1800"})
    await refresh(hass)
    assert weight(hass, entry).state == STATE_UNAVAILABLE
    calls = len(fake.paths)

    # der normale 5-Minuten-Takt darf nicht anklopfen
    await refresh(hass, 2 * UPDATE_INTERVAL.total_seconds())
    assert len(fake.paths) == calls

    fake.recover()
    await refresh(hass, 1800 + UPDATE_INTERVAL.total_seconds())
    assert len(fake.paths) > calls
    assert weight(hass, entry).state == "800.0"


async def test_pro_expired_keeps_entities(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.fail(403, "pro_expired")
    await refresh(hass)

    assert entry.state is ConfigEntryState.LOADED
    assert weight(hass, entry).state == STATE_UNAVAILABLE
    assert entity_id(hass, "sensor", entry, "spool-1_remaining_weight") is not None
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)

    fake.recover()
    await refresh(hass, 2 * UPDATE_INTERVAL.total_seconds() + 2)
    assert weight(hass, entry).state == "800.0"


async def test_revoked_key_starts_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.fail(401, "invalid_key")
    await refresh(hass)

    flows = list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    assert len(flows) == 1


async def test_wrong_key_at_start_starts_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    fake.fail(401, "invalid_key")
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id=key_id(KEY))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))) == 1


async def test_pro_expired_at_start_retries(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    fake.fail(403, "pro_expired")
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id=key_id(KEY))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_account_sensor_is_disabled_by_default(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    entry, _ = await setup_entry(hass, aioclient_mock)
    registry = er.async_get(hass)
    reg_entry = registry.async_get(entity_id(hass, "sensor", entry, "calls_today"))
    assert reg_entry.disabled_by is er.RegistryEntryDisabler.INTEGRATION
    assert reg_entry.entity_category.value == "diagnostic"


async def test_diagnostics_hide_key(hass: HomeAssistant, hass_client, aioclient_mock: AiohttpClientMocker) -> None:
    spool = make_spool(1)
    entry, _ = await setup_entry(hass, aioclient_mock, slots=[make_slot("Mein Drucker", "Slot 1", spool)])
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    dump = json.dumps(result)
    assert KEY not in dump
    assert "Mein Drucker" not in dump
    assert spool["title"] not in dump
    assert result["entry"]["api_key"] == "**REDACTED**"
    assert len(result["spools"]) == 1


async def test_key_is_not_logged(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    entry, fake = await setup_entry(hass, aioclient_mock)
    for status, code in ((429, "fair_use"), (403, "pro_expired"), (401, "invalid_key"), (500, "boom")):
        fake.fail(status, code)
        await refresh(hass, 3 * UPDATE_INTERVAL.total_seconds())
    assert "Authorization" not in caplog.text
    assert KEY not in caplog.text
