from unittest.mock import patch

import pytest

from homeassistant.config_entries import SOURCE_USER
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.test_util.aiohttp import AiohttpClientMocker
import aiohttp

from custom_components.spooly.config_flow import key_id
from custom_components.spooly.const import CONF_API_KEY, CONF_URL, DEFAULT_URL, DOMAIN

from .common import KEY, FakeSpooly, entry_data


async def start(hass: HomeAssistant):
    """Startet die Einrichtung und wählt im Menü den Weg mit Schlüssel."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "key"})


async def test_setup_ok(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    result = await start(hass)
    assert result["type"] is FlowResultType.FORM
    assert "advanced" in result["data_schema"].schema

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: f"  {KEY} "})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_API_KEY: KEY, CONF_URL: DEFAULT_URL}
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    # die ID ist ein Hash, nie der Schlüssel
    assert entry.unique_id == key_id(KEY)
    assert KEY not in entry.unique_id


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (401, "invalid_key", "invalid_auth"),
        (403, "pro_expired", "pro_expired"),
        (403, "api_disabled", "api_disabled"),
        (429, "fair_use", "rate_limited"),
        (500, "boom", "unknown"),
    ],
)
async def test_errors_then_recovery(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int, code: str, error: str
) -> None:
    fake = FakeSpooly(aioclient_mock)
    fake.fail(status, code, {"Retry-After": "30"})
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": error}

    fake.recover()
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_cannot_connect(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.get(f"{DEFAULT_URL}/me", exc=aiohttp.ClientConnectionError())
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["errors"] == {"base": "cannot_connect"}


async def test_same_key_twice(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id=key_id(KEY)).add_to_hass(hass)
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: KEY})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_url_only_https(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    FakeSpooly(aioclient_mock, "https://dev.spooly.eu/api/v1")
    result = await start(hass)

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: KEY, "advanced": {CONF_URL: "http://example.com/api/v1"}}
    )
    assert result["errors"] == {"base": "invalid_url"}

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: KEY, "advanced": {CONF_URL: "https://dev.spooly.eu/api/v1"}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_URL] == "https://dev.spooly.eu/api/v1"


async def test_http_allowed_for_localhost(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock, "http://localhost:8001/api/v1")
    result = await start(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_API_KEY: KEY, "advanced": {CONF_URL: "http://localhost:8001/api/v1"}}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id=key_id(KEY))
    entry.add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    fake.fail(401, "invalid_key")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "spk_noch_falsch"})
    assert result["errors"] == {"base": "invalid_auth"}

    fake.recover()
    with patch("custom_components.spooly.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "spk_neu"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_API_KEY] == "spk_neu"
    assert entry.unique_id == key_id("spk_neu")
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_with_key_of_other_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id=key_id(KEY))
    entry.add_to_hass(hass)
    MockConfigEntry(domain=DOMAIN, data=entry_data("spk_zweiter"), unique_id=key_id("spk_zweiter")).add_to_hass(hass)

    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_API_KEY: "spk_zweiter"})
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
