from datetime import timedelta
import json
import logging
import time
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import aiohttp
import pytest

from homeassistant.config_entries import SOURCE_REAUTH, SOURCE_USER, ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.config_entry_oauth2_flow import (
    MY_AUTH_CALLBACK_PATH,
    ImplementationUnavailableError,
    LocalOAuth2ImplementationWithPkce,
    async_get_redirect_uri,
)
from homeassistant.helpers import config_entry_oauth2_flow
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.diagnostics import get_diagnostics_for_config_entry
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.spooly.const import CONF_API_KEY, CONF_URL, DEFAULT_URL, DOMAIN, UPDATE_INTERVAL
from custom_components.spooly.oauth import implementation_domain

from .common import KEY, FakeSpooly, entry_data

BASE = "https://spooly.eu"
DEV_API = "https://dev.spooly.eu/api/v1"
DEV_BASE = "https://dev.spooly.eu"
ACCESS = "access-old"
REFRESH = "spr_refresh-old"


class TokenEndpoint:
    """Die Token-Route von Spooly: zählt mit, schickt fortlaufende Token und kann ausfallen."""

    def __init__(self, mock: AiohttpClientMocker, base: str = BASE) -> None:
        self.calls: list[dict] = []
        self.count = 0
        self.fail: tuple[int, dict] | None = None
        mock.post(f"{base}/api/oauth/token", side_effect=self._handle)

    async def _handle(self, method, url, data):
        self.calls.append(dict(data))
        if self.fail:
            status, body = self.fail
            return AiohttpClientMockResponse(method, url, status=status, json=body)
        self.count += 1
        body = {
            "access_token": f"access-{self.count}",
            "refresh_token": f"spr_refresh-{self.count}",
            "token_type": "Bearer",
            "expires_in": 3600,
            "scope": "spooly.read spooly.write",
        }
        return AiohttpClientMockResponse(method, url, json=body)


def oauth_data(url: str = DEFAULT_URL, expires_in: int = 3600) -> dict:
    base = url.removesuffix("/api/v1")
    return {
        "auth_implementation": implementation_domain(base),
        "token": {
            "access_token": ACCESS,
            "refresh_token": REFRESH,
            "token_type": "Bearer",
            "scope": "spooly.read spooly.write",
            "expires_in": expires_in,
            "expires_at": time.time() + expires_in,
        },
        CONF_URL: url,
    }


async def setup_oauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, url: str = DEFAULT_URL, **kwargs):
    fake = FakeSpooly(aioclient_mock, url)
    tokens = TokenEndpoint(aioclient_mock, url.removesuffix("/api/v1"))
    entry = MockConfigEntry(domain=DOMAIN, data=oauth_data(url, **kwargs), unique_id="acc-1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry, fake, tokens


async def start_oauth(hass: HomeAssistant, advanced: dict | None = None):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "oauth"})
    assert result["step_id"] == "oauth"
    return await hass.config_entries.flow.async_configure(result["flow_id"], {"advanced": advanced or {}})


async def come_back(hass: HomeAssistant, flow_id: str, **params):
    """Was die Rückleitung über my.home-assistant.io tut: Code und Zustand in den Fluss geben."""
    state = {"flow_id": flow_id, "redirect_uri": MY_AUTH_CALLBACK_PATH}
    result = await hass.config_entries.flow.async_configure(flow_id, {"state": state, **(params or {"code": "abcd"})})
    assert result["type"] is FlowResultType.EXTERNAL_STEP_DONE
    return await hass.config_entries.flow.async_configure(flow_id)


async def refresh(hass: HomeAssistant, freezer, seconds: float) -> None:
    freezer.tick(timedelta(seconds=seconds))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()


def reauth_flows(hass: HomeAssistant, entry) -> list:
    return list(entry.async_get_active_flows(hass, {SOURCE_REAUTH}))


# ── Einrichtung ──
async def test_menu_offers_both_ways(hass: HomeAssistant) -> None:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.MENU
    assert result["menu_options"] == ["oauth", "key"]


async def test_oauth_flow_creates_entry(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    tokens = TokenEndpoint(aioclient_mock)
    result = await start_oauth(hass)
    assert result["type"] is FlowResultType.EXTERNAL_STEP

    # kein Zugangsdaten-Feld: der Nutzer landet direkt auf der Zustimmungsseite
    url = urlparse(result["url"])
    query = {k: v[0] for k, v in parse_qs(url.query).items()}
    assert f"{url.scheme}://{url.netloc}{url.path}" == f"{BASE}/oauth/authorize"
    assert query["client_id"] == "spooly-home-assistant"
    assert query["response_type"] == "code"
    assert query["scope"] == "spooly.read spooly.write"
    assert query["redirect_uri"] == MY_AUTH_CALLBACK_PATH
    assert query["code_challenge_method"] == "S256"
    state = config_entry_oauth2_flow._decode_jwt(hass, query["state"])
    assert state == {"flow_id": result["flow_id"], "redirect_uri": MY_AUTH_CALLBACK_PATH}

    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()

    # öffentlicher Client: PKCE, kein Secret
    call = tokens.calls[0]
    assert call["grant_type"] == "authorization_code"
    assert call["client_id"] == "spooly-home-assistant"
    assert call["redirect_uri"] == MY_AUTH_CALLBACK_PATH
    assert "client_secret" not in call
    assert query["code_challenge"] == LocalOAuth2ImplementationWithPkce.compute_code_challenge(call["code_verifier"])

    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.unique_id == "acc-1"
    assert entry.title == "Spooly"
    assert entry.data[CONF_URL] == DEFAULT_URL
    assert entry.data["auth_implementation"] == DOMAIN
    assert entry.data["token"]["access_token"] == "access-1"
    assert CONF_API_KEY not in entry.data
    assert entry.state is ConfigEntryState.LOADED
    assert set(fake.tokens) == {"access-1"}


async def test_dev_address_uses_its_own_endpoints(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock, DEV_API)
    tokens = TokenEndpoint(aioclient_mock, DEV_BASE)
    result = await start_oauth(hass, {CONF_URL: DEV_API})
    assert result["url"].startswith(f"{DEV_BASE}/oauth/authorize?")

    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.CREATE_ENTRY
    entry = hass.config_entries.async_entries(DOMAIN)[0]
    assert entry.title == "Spooly (dev.spooly.eu)"
    assert entry.data[CONF_URL] == DEV_API
    assert entry.data["auth_implementation"] == implementation_domain(DEV_BASE) != DOMAIN
    assert len(tokens.calls) == 1
    assert fake.tokens and set(fake.tokens) == {"access-1"}


async def test_two_entries_keep_their_addresses(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    live, live_fake, live_tokens = await setup_oauth(hass, aioclient_mock)
    dev_fake = FakeSpooly(aioclient_mock, DEV_API)
    dev_tokens = TokenEndpoint(aioclient_mock, DEV_BASE)
    dev = MockConfigEntry(domain=DOMAIN, data=oauth_data(DEV_API), unique_id="acc-2")
    dev.add_to_hass(hass)
    await hass.config_entries.async_setup(dev.entry_id)
    await hass.async_block_till_done()
    assert live.state is dev.state is ConfigEntryState.LOADED

    await refresh(hass, freezer, 2 * 3600)
    # jeder Eintrag erneuert bei seiner eigenen Adresse und schickt seinen Token dorthin
    assert [c["grant_type"] for c in live_tokens.calls] == ["refresh_token"]
    assert [c["grant_type"] for c in dev_tokens.calls] == ["refresh_token"]
    assert live_fake.tokens[-1] == "access-1"
    assert dev_fake.tokens[-1] == "access-1"
    assert live.data["auth_implementation"] != dev.data["auth_implementation"]


@pytest.mark.parametrize("bad", ["http://example.com/api/v1", "spooly.eu", "ftp://spooly.eu/api/v1"])
async def test_oauth_rejects_unsafe_address(hass: HomeAssistant, bad: str) -> None:
    result = await start_oauth(hass, {CONF_URL: bad})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_url"}


async def test_same_account_twice(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    TokenEndpoint(aioclient_mock)
    MockConfigEntry(domain=DOMAIN, data=oauth_data(), unique_id="acc-1").add_to_hass(hass)

    result = await start_oauth(hass)
    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_account_already_set_up_with_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    TokenEndpoint(aioclient_mock)
    key_entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id="irgendein-hash")
    key_entry.add_to_hass(hass)
    await hass.config_entries.async_setup(key_entry.entry_id)
    await hass.async_block_till_done()

    result = await start_oauth(hass)
    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_user_declines(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    tokens = TokenEndpoint(aioclient_mock)
    result = await start_oauth(hass)
    result = await come_back(hass, result["flow_id"], error="access_denied")
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "user_rejected_authorize"
    assert not tokens.calls


@pytest.mark.parametrize(("status", "reason"), [(400, "oauth_unauthorized"), (401, "oauth_unauthorized"), (500, "oauth_failed")])
async def test_token_exchange_fails(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, status: int, reason: str
) -> None:
    FakeSpooly(aioclient_mock)
    tokens = TokenEndpoint(aioclient_mock)
    tokens.fail = (status, {"error": "invalid_grant"})
    result = await start_oauth(hass)
    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == reason
    assert not hass.config_entries.async_entries(DOMAIN)


async def test_account_check_failing_aborts(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    TokenEndpoint(aioclient_mock)
    fake.fail(403, "pro_expired")
    result = await start_oauth(hass)
    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "pro_expired"


async def test_old_server_without_account_id(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    TokenEndpoint(aioclient_mock)
    del fake.me["account_id"]
    result = await start_oauth(hass)
    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unknown"


async def test_redirect_goes_through_my_home_assistant(hass: HomeAssistant) -> None:
    # "my" steht als Abhängigkeit im Manifest, dann ist die Rückleitung immer die von my.home-assistant.io
    manifest = json.loads(open("custom_components/spooly/manifest.json").read())
    assert {"auth", "my"} <= set(manifest["dependencies"])
    assert async_get_redirect_uri(hass) == "https://my.home-assistant.io/redirect/oauth"


# ── Reauth ──
async def test_reauth_same_account(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake, tokens = await setup_oauth(hass, aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_oauth"

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    assert result["type"] is FlowResultType.EXTERNAL_STEP
    assert result["url"].startswith(f"{BASE}/oauth/authorize?")
    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()

    assert entry.data["token"]["access_token"] == "access-1"
    assert entry.data["token"]["refresh_token"] == "spr_refresh-1"
    assert entry.unique_id == "acc-1"
    assert entry.state is ConfigEntryState.LOADED
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reauth_wrong_account(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake, tokens = await setup_oauth(hass, aioclient_mock)
    result = await entry.start_reauth_flow(hass)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
    fake.me["account_id"] = "acc-fremd"

    result = await come_back(hass, result["flow_id"])
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "wrong_account"
    assert entry.data["token"]["access_token"] == ACCESS
    assert entry.unique_id == "acc-1"


async def test_reauth_of_key_entry_still_asks_for_key(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id="hash")
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"


# ── Erneuerung ──
async def test_token_is_fresh_for_every_call(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    entry, fake, tokens = await setup_oauth(hass, aioclient_mock)
    assert not tokens.calls
    assert set(fake.tokens) == {ACCESS}

    # nach zwei Stunden ist der Zugangstoken abgelaufen: erst erneuern, dann abrufen
    await refresh(hass, freezer, 2 * 3600)
    assert len(tokens.calls) == 1
    assert tokens.calls[0] == {"grant_type": "refresh_token", "client_id": "spooly-home-assistant", "refresh_token": REFRESH}
    assert fake.tokens[-1] == "access-1"
    # Spooly tauscht den Erneuerungstoken bei jedem Einsatz: der neue steht im Eintrag
    assert entry.data["token"]["refresh_token"] == "spr_refresh-1"
    assert entry.data["token"]["access_token"] == "access-1"

    # solange er gilt, wird nicht erneut erneuert
    await refresh(hass, freezer, UPDATE_INTERVAL.total_seconds() + 1)
    assert len(tokens.calls) == 1
    assert fake.tokens[-1] == "access-1"

    # der nächste Ablauf nimmt den neuen Erneuerungstoken
    await refresh(hass, freezer, 2 * 3600)
    assert [c["refresh_token"] for c in tokens.calls] == [REFRESH, "spr_refresh-1"]
    assert fake.tokens[-1] == "access-2"


async def test_invalid_grant_starts_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer
) -> None:
    entry, fake, tokens = await setup_oauth(hass, aioclient_mock)
    tokens.fail = (400, {"error": "invalid_grant"})
    await refresh(hass, freezer, 2 * 3600)

    assert len(reauth_flows(hass, entry)) == 1
    assert entry.state is ConfigEntryState.LOADED


async def test_invalid_grant_at_start(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    tokens = TokenEndpoint(aioclient_mock)
    tokens.fail = (400, {"error": "invalid_grant"})
    entry = MockConfigEntry(domain=DOMAIN, data=oauth_data(expires_in=-100), unique_id="acc-1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.SETUP_ERROR
    assert len(reauth_flows(hass, entry)) == 1


@pytest.mark.parametrize("failure", ["server", "network"])
async def test_refresh_problems_are_not_reauth(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer, failure: str
) -> None:
    entry, fake, tokens = await setup_oauth(hass, aioclient_mock)
    if failure == "server":
        tokens.fail = (503, {"error": "temporarily_unavailable"})
    else:
        aioclient_mock.clear_requests()
        aioclient_mock.post(f"{BASE}/api/oauth/token", exc=aiohttp.ClientConnectionError())
    await refresh(hass, freezer, 2 * 3600)

    assert not reauth_flows(hass, entry)
    assert entry.state is ConfigEntryState.LOADED
    assert not entry.runtime_data.last_update_success


async def test_connection_error_at_start_retries(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    aioclient_mock.post(f"{BASE}/api/oauth/token", exc=aiohttp.ClientConnectionError())
    entry = MockConfigEntry(domain=DOMAIN, data=oauth_data(expires_in=-100), unique_id="acc-1")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


async def test_implementation_unavailable_retries(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    FakeSpooly(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=oauth_data(), unique_id="acc-1")
    entry.add_to_hass(hass)
    with patch(
        "custom_components.spooly.async_get_config_entry_implementation",
        side_effect=ImplementationUnavailableError("nicht da"),
    ):
        await hass.config_entries.async_setup(entry.entry_id)
    assert entry.state is ConfigEntryState.SETUP_RETRY


# ── bestehende Einträge, Diagnose, Logs ──
async def test_key_entry_keeps_working(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    fake = FakeSpooly(aioclient_mock)
    tokens = TokenEndpoint(aioclient_mock)
    entry = MockConfigEntry(domain=DOMAIN, data=entry_data(), unique_id="hash")
    entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    assert entry.state is ConfigEntryState.LOADED
    assert set(fake.tokens) == {KEY}
    assert not tokens.calls


async def test_diagnostics_hide_tokens(hass: HomeAssistant, hass_client, aioclient_mock: AiohttpClientMocker) -> None:
    entry, fake, _ = await setup_oauth(hass, aioclient_mock)
    result = await get_diagnostics_for_config_entry(hass, hass_client, entry)

    dump = json.dumps(result)
    for secret in (ACCESS, REFRESH, "acc-1"):
        assert secret not in dump
    assert result["entry"]["token"] == "**REDACTED**"
    assert result["me"]["account_id"] == "**REDACTED**"


async def test_tokens_are_not_logged(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    FakeSpooly(aioclient_mock)
    tokens = TokenEndpoint(aioclient_mock)
    result = await start_oauth(hass)
    await come_back(hass, result["flow_id"])
    await hass.async_block_till_done()
    entry = hass.config_entries.async_entries(DOMAIN)[0]

    await refresh(hass, freezer, 2 * 3600)
    tokens.fail = (400, {"error": "invalid_grant"})
    await refresh(hass, freezer, 2 * 3600)
    tokens.fail = (500, {"error": "boom"})
    await refresh(hass, freezer, 2 * 3600)

    assert entry.data["token"]["access_token"].startswith("access-")
    for secret in ("access-1", "access-2", "spr_refresh-1", "spr_refresh-2", "abcd", "code_verifier"):
        assert secret not in caplog.text
    assert "Bearer access" not in caplog.text and "Bearer spr_" not in caplog.text
