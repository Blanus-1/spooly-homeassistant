from datetime import timedelta
import json
import logging
from pathlib import Path
import re
import uuid

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.spooly.config_flow import key_id
from custom_components.spooly.const import DOMAIN
from custom_components.spooly.coordinator import slot_key
from custom_components.spooly.entity import printer_device_id
from custom_components.spooly.services import HTTP_ERRORS, SERVICES

from .common import KEY, FakeSpooly, entry_data, make_slot, make_spool
from .test_oauth import setup_oauth

COMPONENT = Path(__file__).parent.parent / "custom_components" / "spooly"
SERVICE_NAMES = {
    "set_weight",
    "refill",
    "assign_slot",
    "clear_slot",
    "move",
    "archive",
    "restore",
    "complete_maintenance",
    "snooze_maintenance",
}


async def setup_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, key: str = KEY, base: str | None = None):
    spool1, spool2 = make_spool(1), make_spool(2)
    fake = FakeSpooly(aioclient_mock) if base is None else FakeSpooly(aioclient_mock, base)
    fake.spools = [spool1, spool2]
    fake.slots = [
        make_slot("Bambu X1C", "AMS 1 Slot 1", spool1, "slot-aaa"),
        make_slot("Bambu X1C", "AMS 1 Slot 2", None, "slot-bbb"),
        make_slot("Prusa", "Slot 1", None),  # ohne slot_id: Slot-Autopilot aus
    ]
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(key), unique_id=key_id(key))
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, fake


def spool_device(hass: HomeAssistant, entry, spool_id: str = "spool-1") -> str:
    device = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, f"{entry.entry_id}_spool_{spool_id}"), entry.entry_id)
    return device.id


def slot_entity(hass: HomeAssistant, entry, printer: str, slot: str) -> str:
    return er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_slot_{slot_key(printer, slot)}")


def weight_state(hass: HomeAssistant, entry, spool_id: str = "spool-1"):
    entity = er.async_get(hass).async_get_entity_id("sensor", DOMAIN, f"{entry.entry_id}_{spool_id}_remaining_weight")
    return hass.states.get(entity)


async def call(hass: HomeAssistant, service: str, data: dict):
    return await hass.services.async_call(DOMAIN, service, data, blocking=True)


async def cooldown(hass: HomeAssistant) -> None:
    """Zwei Dienste kurz hintereinander: der zweite Abruf kommt erst nach der Wartezeit des Koordinators."""
    async_fire_time_changed(hass, dt_util.utcnow() + timedelta(seconds=11))
    await hass.async_block_till_done()


# ── Registrierung ──
async def test_services_exist_without_entry(hass: HomeAssistant) -> None:
    assert await async_setup_component(hass, DOMAIN, {})
    assert set(hass.services.async_services_for_domain(DOMAIN)) == SERVICE_NAMES == set(SERVICES)


async def test_no_delete_service(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    await setup_entry(hass, aioclient_mock)
    assert not any("delete" in name or "remove" in name for name in hass.services.async_services_for_domain(DOMAIN))


async def test_slot_sensor_carries_slot_id(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_entry(hass, aioclient_mock)
    full = hass.states.get(slot_entity(hass, entry, "Bambu X1C", "AMS 1 Slot 1"))
    empty = hass.states.get(slot_entity(hass, entry, "Bambu X1C", "AMS 1 Slot 2"))
    off = hass.states.get(slot_entity(hass, entry, "Prusa", "Slot 1"))
    assert full.attributes["slot_id"] == "slot-aaa"
    assert empty.attributes["slot_id"] == "slot-bbb"
    assert "slot_id" not in off.attributes


# ── Erfolg ──
@pytest.mark.parametrize(
    ("service", "data", "expected"),
    [
        ("set_weight", {"grams": 500}, ("POST", "/spools/spool-1/weight", {"grams": 500.0})),
        ("set_weight", {"gross_grams": 650}, ("POST", "/spools/spool-1/weight", {"gross_grams": 650.0})),
        ("refill", {"grams": 100}, ("POST", "/spools/spool-1/refill", {"grams": 100.0})),
        ("archive", {}, ("POST", "/spools/spool-1/archive", None)),
        ("restore", {}, ("POST", "/spools/spool-1/restore", None)),
    ],
)
async def test_spool_services(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, service: str, data: dict, expected: tuple
) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    reads = fake.paths.count("/me")

    result = await call(hass, service, {"device_id": spool_device(hass, entry), **data})

    assert result is None
    assert fake.writes == [expected]
    # danach sofort neu abgerufen
    assert fake.paths.count("/me") == reads + 1
    method, path, headers = [r for r in fake.requests if r[0] != "GET"][0]
    assert headers["Authorization"] == f"Bearer {KEY}"
    assert str(uuid.UUID(headers["Idempotency-Key"]))


async def test_every_call_has_its_own_idempotency_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    for _ in range(2):
        await call(hass, "refill", {"device_id": spool_device(hass, entry), "grams": 10})
    keys = [h["Idempotency-Key"] for m, _, h in fake.requests if m == "POST"]
    assert len(keys) == 2 and keys[0] != keys[1]


async def test_weight_shows_up_right_away(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_entry(hass, aioclient_mock)
    assert weight_state(hass, entry).state == "800.0"
    await call(hass, "set_weight", {"device_id": spool_device(hass, entry), "grams": 500})
    await hass.async_block_till_done()
    assert weight_state(hass, entry).state == "500.0"
    await cooldown(hass)
    await call(hass, "refill", {"device_id": spool_device(hass, entry), "grams": 100})
    await hass.async_block_till_done()
    assert weight_state(hass, entry).state == "600.0"


async def test_archive_and_restore(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_entry(hass, aioclient_mock)
    device = spool_device(hass, entry)
    await call(hass, "archive", {"device_id": device})
    await hass.async_block_till_done()
    assert weight_state(hass, entry).state == STATE_UNAVAILABLE
    await cooldown(hass)

    # das Gerät bleibt, deshalb lässt sich die Spule über es zurückholen
    await call(hass, "restore", {"device_id": device})
    await hass.async_block_till_done()
    assert weight_state(hass, entry).state == "800.0"


@pytest.mark.parametrize(("confirm", "expected"), [(None, False), (True, True)])
async def test_assign_slot(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, confirm: bool | None, expected: bool
) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    data = {
        "device_id": spool_device(hass, entry, "spool-2"),
        "slot": slot_entity(hass, entry, "Bambu X1C", "AMS 1 Slot 2"),
    }
    if confirm is not None:
        data["confirm_color"] = confirm
    await call(hass, "assign_slot", data)
    assert fake.writes == [("PUT", "/slots/slot-bbb", {"spool_id": "spool-2", "confirm_color": expected})]


async def test_clear_slot(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    await call(hass, "clear_slot", {"slot": slot_entity(hass, entry, "Bambu X1C", "AMS 1 Slot 1")})
    assert fake.writes == [("DELETE", "/slots/slot-aaa", None)]


@pytest.mark.parametrize("wanted", ["Schrank 01 · Fach vorne", "  schrank 01 · FACH vorne ", "loc-1"])
async def test_move_by_name_or_id(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, wanted: str) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    await call(hass, "move", {"device_id": spool_device(hass, entry), "location": wanted})
    assert fake.writes == [("POST", "/spools/spool-1/move", {"location_id": "loc-1"})]


@pytest.mark.parametrize("wanted", ["Schrank 01 · Fach vorne · Platz 2", "schrank 01 · fach vorne·Platz 2", "loc-1 · Platz 2"])
async def test_move_to_place(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, wanted: str) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    await call(hass, "move", {"device_id": spool_device(hass, entry), "location": wanted})
    assert fake.writes == [("POST", "/spools/spool-1/move", {"location_id": "loc-1", "place": 2})]


async def test_location_named_like_a_place_wins(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    # heißt der Ort selbst so, ist es kein Platz
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.locations.append({"id": "loc-3", "name": "Regal 1 · Platz 3", "is_default": False})
    await call(hass, "move", {"device_id": spool_device(hass, entry), "location": "Regal 1 · Platz 3"})
    assert fake.writes == [("POST", "/spools/spool-1/move", {"location_id": "loc-3"})]


async def test_move_unknown_location(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "move", {"device_id": spool_device(hass, entry), "location": "Keller"})
    assert err.value.translation_key == "location_not_found"
    assert fake.writes == []


async def test_move_ambiguous_location(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.locations.append({"id": "loc-3", "name": "Regal 1", "is_default": False})
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "move", {"device_id": spool_device(hass, entry), "location": "regal 1"})
    assert err.value.translation_key == "location_ambiguous"
    assert fake.writes == []


# ── Fehler von Spooly ──
@pytest.mark.parametrize(
    ("status", "code", "key", "headers"),
    [
        (403, "insufficient_scope", "no_write_permission", None),
        (403, "pro_expired", "pro_expired", None),
        (403, "api_disabled", "api_disabled", None),
        (409, "tare_missing", "tare_missing", None),
        (409, "slot_autopilot_off", "slot_autopilot_off", None),
        (409, "andere_farbe", "other_color", None),
        (409, "conflict", "spool_in_print", None),
        (409, "archiviert", "spool_archived", None),
        (409, "place_taken", "place_taken", None),
        (422, "place_invalid", "place_invalid", None),
        (422, "above_spool_size", "above_spool_size", None),
        (422, "gross_not_above_tare", "gross_not_above_tare", None),
        (422, "invalid_input", "invalid_input", None),
        (404, "not_found", "not_found", None),
        (429, "change_rate_limit", "change_limit", {"Retry-After": "17"}),
    ],
)
async def test_errors_become_validation_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    caplog: pytest.LogCaptureFixture,
    status: int,
    code: str,
    key: str,
    headers: dict | None,
) -> None:
    caplog.set_level(logging.DEBUG)
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.fail_write(status, code, headers)
    reads = len(fake.paths)

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "refill", {"device_id": spool_device(hass, entry), "grams": 10})

    assert err.value.translation_domain == DOMAIN
    assert err.value.translation_key == key
    if key == "change_limit":
        assert err.value.translation_placeholders == {"seconds": "17"}
    # keine internen Texte des Servers, keine Schlüssel
    assert "Fehler" not in str(err.value)
    assert KEY not in caplog.text
    # nach einem Fehler wird nicht neu abgerufen
    assert len(fake.paths) == reads


async def test_pydantic_422_has_no_code(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.fail_write(422, body={"detail": [{"loc": ["body", "grams"], "msg": "innen", "type": "x"}]})
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "refill", {"device_id": spool_device(hass, entry), "grams": 10})
    assert err.value.translation_key == "invalid_input"


async def test_missing_permission_asks_for_new_sign_in(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.fail_write(403, "insufficient_scope")
    with pytest.raises(ServiceValidationError):
        await call(hass, "archive", {"device_id": spool_device(hass, entry)})
    await hass.async_block_till_done()

    flows = list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    assert len(flows) == 1
    assert entry.state is ConfigEntryState.LOADED


async def test_missing_permission_with_oauth_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake, _ = await setup_oauth(hass, aioclient_mock)
    fake.spools = [make_spool(1)]
    await entry.runtime_data.async_refresh()
    fake.fail_write(403, "insufficient_scope")
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "archive", {"device_id": spool_device(hass, entry)})
    assert err.value.translation_key == "no_write_permission"
    await hass.async_block_till_done()

    flows = list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))
    assert [f["step_id"] for f in flows] == ["reauth_oauth"]


async def test_revoked_connection_starts_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    device = spool_device(hass, entry)
    fake.fail(401, "invalid_key")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "refill", {"device_id": device, "grams": 10})
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "auth_failed"
    await hass.async_block_till_done()
    assert len(list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))) == 1


async def test_server_error_is_not_a_validation_error(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.fail_write(500, "boom")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "refill", {"device_id": spool_device(hass, entry), "grams": 10})
    assert not isinstance(err.value, ServiceValidationError)
    assert err.value.translation_key == "rejected"
    assert err.value.translation_placeholders == {"status": "500", "code": "boom"}


async def test_unreachable_spooly(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    fake.write_exc = aiohttp.ClientConnectionError()
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, "refill", {"device_id": spool_device(hass, entry), "grams": 10})
    assert err.value.translation_key == "cannot_connect"


# ── falsche Ziele und Eingaben, es geht nichts raus ──
async def test_wrong_targets(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    devices = dr.async_get(hass)
    other = MockConfigEntry(domain="other")
    other.add_to_hass(hass)
    foreign = devices.async_get_or_create(config_entry_id=other.entry_id, identifiers={("other", "x")})
    printer = devices.async_get_device_by_identifier((DOMAIN, printer_device_id(entry, "Bambu X1C")), entry.entry_id)
    hub = devices.async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)

    for device_id in (foreign.id, printer.id, hub.id, "gibt-es-nicht"):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, "archive", {"device_id": device_id})
        assert err.value.translation_key == "not_a_spool"

    spool = spool_device(hass, entry)
    for slot in (weight_entity := er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{entry.entry_id}_spool-1_remaining_weight"
    ), "sensor.gibt_es_nicht"):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, "assign_slot", {"device_id": spool, "slot": slot})
        assert err.value.translation_key == "not_a_slot"
    assert weight_entity
    assert fake.writes == []


async def test_slot_without_autopilot(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "clear_slot", {"slot": slot_entity(hass, entry, "Prusa", "Slot 1")})
    assert err.value.translation_key == "slot_autopilot_off"
    assert fake.writes == []


async def test_spool_and_slot_of_different_accounts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    first, fake = await setup_entry(hass, aioclient_mock)
    second, other_fake = await setup_entry(hass, aioclient_mock, "spk_zweiter", "https://dev.spooly.eu/api/v1")
    with pytest.raises(ServiceValidationError) as err:
        await call(
            hass,
            "assign_slot",
            {
                "device_id": spool_device(hass, first),
                "slot": slot_entity(hass, second, "Bambu X1C", "AMS 1 Slot 2"),
            },
        )
    assert err.value.translation_key == "different_accounts"
    assert fake.writes == other_fake.writes == []


@pytest.mark.parametrize("data", [{}, {"grams": 500, "gross_grams": 650}])
async def test_weight_needs_exactly_one_value(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, data: dict
) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "set_weight", {"device_id": spool_device(hass, entry), **data})
    assert err.value.translation_key == "weight_exactly_one"
    assert fake.writes == []


async def test_service_after_unload(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_entry(hass, aioclient_mock)
    device = spool_device(hass, entry)
    await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "archive", {"device_id": device})
    assert err.value.translation_key == "entry_not_loaded"
    assert fake.writes == []


async def test_oauth_token_is_fresh_for_a_service_call(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    from datetime import timedelta

    entry, fake, tokens = await setup_oauth(hass, aioclient_mock)
    freezer.tick(timedelta(hours=2))
    await call(hass, "refill", {"device_id": spool_device(hass, entry), "grams": 10})

    assert len(tokens.calls) == 1
    method, _, headers = [r for r in fake.requests if r[0] == "POST"][0]
    assert headers["Authorization"] == "Bearer access-1"


# ── Texte ──
def _message_keys(path: Path) -> set[str]:
    return set(json.loads(path.read_text(encoding="utf-8"))["exceptions"])


def test_every_error_has_a_translation() -> None:
    source = (COMPONENT / "services.py").read_text(encoding="utf-8")
    used = set(HTTP_ERRORS.values()) | set(re.findall(r'translation_key="(\w+)"', source))
    used |= set(re.findall(r'invalid\("(\w+)"', source))
    for name in ("strings.json", "translations/en.json", "translations/de.json"):
        assert used <= _message_keys(COMPONENT / name), name


def test_translations_have_the_same_keys() -> None:
    def keys(node, prefix=""):
        if isinstance(node, dict):
            return {k for key, value in node.items() for k in keys(value, f"{prefix}/{key}")}
        return {prefix}

    files = {name: json.loads((COMPONENT / name).read_text(encoding="utf-8")) for name in ("strings.json", "translations/en.json", "translations/de.json")}
    assert keys(files["strings.json"]) == keys(files["translations/en.json"]) == keys(files["translations/de.json"])
    assert files["strings.json"] == files["translations/en.json"]
    # alle Dienste stehen in services.yaml, strings und Übersetzungen
    for data in files.values():
        assert set(data["services"]) == SERVICE_NAMES


def test_services_yaml_matches_strings() -> None:
    from homeassistant.helpers.selector import validate_selector
    from homeassistant.util.yaml import load_yaml

    services = load_yaml(str(COMPONENT / "services.yaml"))
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))["services"]
    assert set(services) == set(strings) == SERVICE_NAMES
    for name, service in services.items():
        assert set(service["fields"]) == set(strings[name]["fields"]), name
        for field in service["fields"].values():
            validate_selector(field["selector"])
