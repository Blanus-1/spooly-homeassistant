"""Einrichtung mit Anmeldung bei Spooly (OAuth) oder mit API-Schlüssel, dazu Reauth für beide Wege."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler
from homeassistant.helpers.selector import TextSelector, TextSelectorConfig, TextSelectorType

from .api import (
    SpoolyApiDisabledError,
    SpoolyAuthError,
    SpoolyClient,
    SpoolyConnectionError,
    SpoolyError,
    SpoolyProExpiredError,
    SpoolyRateLimitError,
)
from .const import CONF_API_KEY, CONF_URL, DEFAULT_URL, DOMAIN
from .oauth import SpoolyImplementation, origin

_LOGGER = logging.getLogger(__name__)

KEY_SELECTOR = TextSelector(TextSelectorConfig(type=TextSelectorType.PASSWORD))

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}

ADVANCED = "advanced"


def key_id(api_key: str) -> str:
    """Unique-ID: nur der Hash, der Schlüssel selbst steht nie in der ID."""
    return hashlib.sha256(api_key.encode()).hexdigest()


def url_allowed(url: str) -> bool:
    """Standard ist Spooly. Eine andere Adresse nur per https, http nur für localhost (Tests)."""
    parts = urlparse(url)
    if not parts.hostname:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOCAL_HOSTS)


def advanced_section() -> section:
    # die Adresse bleibt eingeklappt, sie ist nur für Tests gegen eine andere Umgebung gedacht
    return section(
        vol.Schema({vol.Optional(CONF_URL, default=DEFAULT_URL): str}),
        {"collapsed": True},
    )


async def fetch_me(client: SpoolyClient) -> tuple[dict[str, Any] | None, str | None]:
    """Fragt /me ab. Ergebnis: (Konto, None) oder (None, Fehlerschlüssel)."""
    try:
        return await client.get_me(), None
    except SpoolyAuthError:
        return None, "invalid_auth"
    except SpoolyProExpiredError:
        return None, "pro_expired"
    except SpoolyApiDisabledError:
        return None, "api_disabled"
    except SpoolyRateLimitError:
        return None, "rate_limited"
    except SpoolyConnectionError:
        return None, "cannot_connect"
    except SpoolyError:
        _LOGGER.exception("Unexpected response from Spooly")
        return None, "unknown"


class SpoolyConfigFlow(AbstractOAuth2FlowHandler, domain=DOMAIN):
    DOMAIN = DOMAIN
    VERSION = 1

    def __init__(self) -> None:
        super().__init__()
        self._api_url = DEFAULT_URL

    @property
    def logger(self) -> logging.Logger:
        return _LOGGER

    async def _check_key(self, api_key: str, url: str) -> str | None:
        """Gibt den Fehlerschlüssel zurück, None heißt: Schlüssel passt."""
        client = SpoolyClient(async_get_clientsession(self.hass), api_key, url)
        return (await fetch_me(client))[1]

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="user", menu_options=["oauth", "key"])

    # ── Anmeldung bei Spooly ──
    async def async_step_oauth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            url = user_input.get(ADVANCED, {}).get(CONF_URL, DEFAULT_URL).strip()
            if url_allowed(url):
                self._api_url = url
                self.flow_impl = SpoolyImplementation(self.hass, origin(url))
                return await self.async_step_auth()
            errors["base"] = "invalid_url"

        schema = vol.Schema({vol.Required(ADVANCED, default={}): advanced_section()})
        return self.async_show_form(
            step_id="oauth",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        client = SpoolyClient(async_get_clientsession(self.hass), data["token"]["access_token"], self._api_url)
        me, error = await fetch_me(client)
        if me is None:
            return self.async_abort(reason=error)
        account_id = me.get("account_id")
        if not account_id:
            return self.async_abort(reason="unknown")

        await self.async_set_unique_id(account_id)
        if self.source == SOURCE_REAUTH:
            self._abort_if_unique_id_mismatch(reason="wrong_account")
            return self.async_update_reload_and_abort(self._get_reauth_entry(), data_updates=data)
        self._abort_if_unique_id_configured()
        # dasselbe Konto kann schon mit einem Schlüssel eingerichtet sein
        for other in self._async_current_entries():
            coordinator = getattr(other, "runtime_data", None)
            if coordinator is not None and coordinator.data and coordinator.data.me.get("account_id") == account_id:
                return self.async_abort(reason="already_configured")
        return self.async_create_entry(title=self.flow_impl.name, data={**data, CONF_URL: self._api_url})

    # ── Schlüssel ──
    async def async_step_key(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            url = user_input.get(ADVANCED, {}).get(CONF_URL, DEFAULT_URL).strip()
            if not url_allowed(url):
                errors["base"] = "invalid_url"
            else:
                await self.async_set_unique_id(key_id(api_key))
                self._abort_if_unique_id_configured()
                if error := await self._check_key(api_key, url):
                    errors["base"] = error
                else:
                    return self.async_create_entry(
                        title="Spooly",
                        data={CONF_API_KEY: api_key, CONF_URL: url},
                    )

        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): KEY_SELECTOR,
                vol.Required(ADVANCED, default={}): advanced_section(),
            }
        )
        return self.async_show_form(
            step_id="key",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    # ── Reauth ──
    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        if CONF_API_KEY in entry_data:
            return await self.async_step_reauth_confirm()
        self._api_url = entry_data.get(CONF_URL, DEFAULT_URL)
        return await self.async_step_reauth_oauth()

    async def async_step_reauth_oauth(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is None:
            return self.async_show_form(step_id="reauth_oauth")
        self.flow_impl = SpoolyImplementation(self.hass, origin(self._api_url))
        return await self.async_step_auth()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            unique_id = key_id(api_key)
            other = self.hass.config_entries.async_entry_for_domain_unique_id(DOMAIN, unique_id)
            if other is not None and other.entry_id != entry.entry_id:
                return self.async_abort(reason="already_configured")
            if error := await self._check_key(api_key, entry.data.get(CONF_URL, DEFAULT_URL)):
                errors["base"] = error
            else:
                # Einträge mit Schlüssel behalten ihre ID, ein Schlüssel eines anderen Kontos fällt hier nicht auf
                return self.async_update_reload_and_abort(
                    entry,
                    unique_id=unique_id,
                    data_updates={CONF_API_KEY: api_key},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_API_KEY): KEY_SELECTOR}),
            errors=errors,
        )
