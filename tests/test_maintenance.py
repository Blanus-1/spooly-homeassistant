import json
from pathlib import Path
import re

import pytest
import voluptuous as vol

from homeassistant.config_entries import ConfigEntryState
from homeassistant.const import STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker

from custom_components.spooly import async_remove_config_entry_device
from custom_components.spooly.const import DOMAIN, UPDATE_INTERVAL
from custom_components.spooly.entity import maint_printer_device_id

from .common import make_printer, make_slot, make_task
from .test_integration import entity_id, refresh, setup_entry
from .test_services import call, cooldown

NAME = "Bambu X1C"
COMPONENT =Path(__file__).parent.parent / "custom_components" / "spooly"


def task_entity(hass: HomeAssistant, entry, task_id: str = "task-1") -> str:
    return entity_id(hass, "sensor", entry, f"task_{task_id}")


def printer_state(hass: HomeAssistant, entry, suffix: str, printer_id: str = "prn-1"):
    return hass.states.get(entity_id(hass, "sensor", entry, f"{printer_id}_{suffix}"))


def hub_state(hass: HomeAssistant, entry, suffix: str):
    return hass.states.get(entity_id(hass, "sensor", entry, suffix))


async def setup_maintenance(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, **extra):
    attrs = {
        "maint_printers": [make_printer()],
        "tasks": [make_task(), make_task("task-2", status="soon", name="Bett reinigen", eta_days=6)],
        **extra,
    }
    return await setup_entry(hass, aioclient_mock, **attrs)


async def test_task_sensor(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_maintenance(hass, aioclient_mock)

    state = hass.states.get(task_entity(hass, entry))
    assert state.state == "due"
    assert state.name.endswith("Düse wechseln")
    assert state.attributes["device_class"] == "enum"
    assert state.attributes["options"] == ["due", "soon", "ok"]
    assert state.attributes["metric"] == "hours"
    assert state.attributes["interval"] == 200
    assert state.attributes["used"] == 210.0
    assert state.attributes["remaining"] == -10.0
    assert state.attributes["last_done_at"] == "2026-09-01T10:00:00Z"
    assert state.attributes["default_action"] == "replace"
    # null-Felder fehlen
    assert "eta_days" not in state.attributes
    assert "snoozed_until" not in state.attributes

    soon = hass.states.get(task_entity(hass, entry, "task-2"))
    assert soon.state == "soon"
    assert soon.attributes["eta_days"] == 6


async def test_unknown_status_does_not_break(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_maintenance(hass, aioclient_mock, tasks=[make_task(status="neu")])
    assert hass.states.get(task_entity(hass, entry)).state == STATE_UNKNOWN


async def test_printer_device_and_sensors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_maintenance(hass, aioclient_mock)

    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, maint_printer_device_id(entry, "prn-1", NAME)), entry.entry_id
    )
    assert device.name == "Bambu X1C"
    assert device.via_device_id is not None
    # die Aufgaben hängen am selben Gerät
    registry = er.async_get(hass)
    assert registry.async_get(task_entity(hass, entry)).device_id == device.id
    assert registry.async_get(task_entity(hass, entry, "task-2")).device_id == device.id

    due = printer_state(hass, entry, "maintenance_due")
    assert due.state == "1"
    assert due.attributes["soon_count"] == 1

    hours = printer_state(hass, entry, "print_hours")
    assert hours.state == "120.5"
    assert hours.attributes["unit_of_measurement"] == "h"
    assert hours.attributes["state_class"] == "total_increasing"


async def test_printer_without_name_gets_fallback(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_maintenance(
        hass, aioclient_mock, maint_printers=[make_printer(name=None)], tasks=[make_task(printer=None)]
    )
    device = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, maint_printer_device_id(entry, "prn-1", None)), entry.entry_id
    )
    assert device.name in ("Drucker", "Printer")


async def test_slots_and_maintenance_share_one_device(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_maintenance(hass, aioclient_mock, slots=[make_slot(NAME, "1-1")])
    geraete = [d for d in dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id) if d.name == NAME]
    assert len(geraete) == 1


async def test_hub_sensors(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    stats = {
        "grams": 1234.5,
        "prints": 7,
        "hours": 20,
        "filament_cost": 31.2,
        "currency": "EUR",
        "stock": {"active_spools": 9, "remaining_grams": 5000, "stock_value": 187.5, "total_spent": 400},
    }
    entry, _ = await setup_maintenance(hass, aioclient_mock, statistics=stats)

    grams = hub_state(hass, entry, "filament_month")
    assert grams.state == "1234.5"
    assert grams.attributes["unit_of_measurement"] == "g"

    stock = hub_state(hass, entry, "stock_value")
    assert stock.state == "187.5"
    assert stock.attributes["device_class"] == "monetary"
    assert stock.attributes["unit_of_measurement"] == "EUR"

    registry = er.async_get(hass)
    hub = dr.async_get(hass).async_get_device_by_identifier((DOMAIN, entry.entry_id), entry.entry_id)
    assert registry.async_get(hub_state(hass, entry, "filament_month").entity_id).device_id == hub.id
    assert registry.async_get(stock.entity_id).device_id == hub.id


async def test_statistics_use_month_of_the_instance(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    # 01.12. 03:00 UTC ist in der Zeitzone der Test-Instanz (US/Pacific) noch der 30.11.
    freezer.move_to("2026-12-01 03:00:00+00:00")
    _, fake = await setup_maintenance(hass, aioclient_mock)
    assert fake.statistics_queries[0] == ("2026", "11")


async def test_task_vanishes_and_comes_back(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    kept = fake.tasks
    fake.tasks = kept[:1]
    await refresh(hass)

    assert hass.states.get(task_entity(hass, entry, "task-2")).state == STATE_UNAVAILABLE
    assert hass.states.get(task_entity(hass, entry)).state == "due"

    fake.tasks = kept
    await refresh(hass, 2 * UPDATE_INTERVAL.total_seconds() + 2)
    assert hass.states.get(task_entity(hass, entry, "task-2")).state == "soon"


async def test_printer_vanishes_and_devices(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    devices = dr.async_get(hass)
    device = devices.async_get_device_by_identifier((DOMAIN, maint_printer_device_id(entry, "prn-1", NAME)), entry.entry_id)
    assert await async_remove_config_entry_device(hass, entry, device) is False

    fake.maint_printers, fake.tasks = [], []
    await refresh(hass)
    assert printer_state(hass, entry, "maintenance_due").state == STATE_UNAVAILABLE
    assert printer_state(hass, entry, "print_hours").state == STATE_UNAVAILABLE
    assert hass.states.get(task_entity(hass, entry)).state == STATE_UNAVAILABLE
    assert await async_remove_config_entry_device(hass, entry, device) is True


async def test_new_task_appears_without_restart(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    fake.tasks = [*fake.tasks, make_task("task-9", name="Lüfter prüfen", status="ok")]
    await refresh(hass)
    assert hass.states.get(task_entity(hass, entry, "task-9")).state == "ok"


async def test_empty_maintenance(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, _ = await setup_entry(hass, aioclient_mock)
    assert entry.state is ConfigEntryState.LOADED
    assert entity_id(hass, "sensor", entry, "task_task-1") is None


async def test_diagnostics_have_no_maintenance(
    hass: HomeAssistant, hass_client, aioclient_mock: AiohttpClientMocker
) -> None:
    entry, _ = await setup_maintenance(hass, aioclient_mock)
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)
    assert set(result) == {"entry", "me", "spools", "slots"}
    assert "Düse" not in json.dumps(result, ensure_ascii=False)


# ── Dienste ──
async def test_complete_maintenance(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    reads = fake.paths.count("/maintenance")

    result = await call(
        hass,
        "complete_maintenance",
        {"task": task_entity(hass, entry), "action": "replace", "notes": "  neue Düse 0.4  "},
    )
    await hass.async_block_till_done()

    assert result is None
    assert fake.writes == [("POST", "/maintenance/tasks/task-1/complete", {"action": "replace", "notes": "neue Düse 0.4"})]
    # sofort neu abgerufen, der neue Stand ist da
    assert fake.paths.count("/maintenance") == reads + 1
    assert hass.states.get(task_entity(hass, entry)).state == "ok"


async def test_complete_maintenance_without_options(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    await call(hass, "complete_maintenance", {"task": task_entity(hass, entry), "notes": "  "})
    assert fake.writes == [("POST", "/maintenance/tasks/task-1/complete", {})]


async def test_snooze_maintenance(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    await call(hass, "snooze_maintenance", {"task": task_entity(hass, entry), "days": "14", "reason": "Urlaub"})
    await hass.async_block_till_done()
    assert fake.writes == [("POST", "/maintenance/tasks/task-1/snooze", {"days": 14, "reason": "Urlaub"})]
    assert hass.states.get(task_entity(hass, entry)).attributes["snoozed_until"] == "2026-12-31"

    await cooldown(hass)
    await call(hass, "snooze_maintenance", {"task": task_entity(hass, entry), "days": 7})
    assert fake.writes[-1] == ("POST", "/maintenance/tasks/task-1/snooze", {"days": 7})


@pytest.mark.parametrize(
    ("service", "data"),
    [
        ("complete_maintenance", {"action": "lackieren"}),
        ("snooze_maintenance", {"days": 5}),
        ("snooze_maintenance", {}),
    ],
)
async def test_invalid_input_sends_nothing(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, service: str, data: dict
) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    with pytest.raises(vol.Invalid):
        await call(hass, service, {"task": task_entity(hass, entry), **data})
    assert fake.writes == []


@pytest.mark.parametrize(
    ("service", "extra"), [("complete_maintenance", {}), ("snooze_maintenance", {"days": 7})]
)
@pytest.mark.parametrize(
    ("status", "code", "key"),
    [(403, "insufficient_scope", "no_write_permission"), (404, "not_found", "not_found")],
)
async def test_maintenance_errors(
    hass: HomeAssistant,
    aioclient_mock: AiohttpClientMocker,
    service: str,
    extra: dict,
    status: int,
    code: str,
    key: str,
) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    fake.fail_write(status, code)
    reads = len(fake.paths)

    with pytest.raises(ServiceValidationError) as err:
        await call(hass, service, {"task": task_entity(hass, entry), **extra})

    assert err.value.translation_key == key
    assert "Fehler" not in str(err.value)
    assert len(fake.paths) == reads
    if status == 403:
        assert len(list(entry.async_get_active_flows(hass, {"reauth"}))) == 1


async def test_task_removed_in_spooly(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    # die Aufgabe ist in Spooly weg, der Sensor kennt sie noch bis zum nächsten Abruf
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    entity = task_entity(hass, entry)
    fake.tasks = []
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, "complete_maintenance", {"task": entity})
    assert err.value.translation_key == "not_found"


async def test_wrong_task_target(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake = await setup_maintenance(hass, aioclient_mock)
    for entity in (printer_state(hass, entry, "print_hours").entity_id, "sensor.gibt_es_nicht"):
        with pytest.raises(ServiceValidationError) as err:
            await call(hass, "complete_maintenance", {"task": entity})
        assert err.value.translation_key == "not_a_task"
    assert fake.writes == []


def test_translation_keys_exist() -> None:
    """Jeder translation_key in Entitäten, Geräten und Auswahlfeldern hat einen Text."""
    strings = json.loads((COMPONENT / "strings.json").read_text(encoding="utf-8"))
    for platform in ("sensor", "binary_sensor"):
        source = (COMPONENT / f"{platform}.py").read_text(encoding="utf-8")
        for key in re.findall(r'_attr_translation_key = "(\w+)"', source):
            assert key in strings["entity"][platform], key
    for key in re.findall(r'\["translation_key"\] = "(\w+)"', (COMPONENT / "entity.py").read_text(encoding="utf-8")):
        assert key in strings["device"], key
    for key in re.findall(r"translation_key: (\w+)", (COMPONENT / "services.yaml").read_text(encoding="utf-8")):
        assert key in strings["selector"], key
