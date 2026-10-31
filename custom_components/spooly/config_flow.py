"""Einrichtung mit dem API-Schlüssel aus Spooly, dazu Reauth, wenn der Schlüssel widerrufen wird."""

from __future__ import annotations

from collections.abc import Mapping
import hashlib
import logging
from typing import Any
from urllib.parse import urlparse

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.data_entry_flow import section
from homeassistant.helpers.aiohttp_client import async_get_clientsession
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


class SpoolyConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _check_key(self, api_key: str, url: str) -> str | None:
        """Fragt /me ab und gibt den Fehlerschlüssel zurück, None heißt: Schlüssel passt."""
        client = SpoolyClient(async_get_clientsession(self.hass), api_key, url)
        try:
            await client.get_me()
        except SpoolyAuthError:
            return "invalid_auth"
        except SpoolyProExpiredError:
            return "pro_expired"
        except SpoolyApiDisabledError:
            return "api_disabled"
        except SpoolyRateLimitError:
            return "rate_limited"
        except SpoolyConnectionError:
            return "cannot_connect"
        except SpoolyError:
            _LOGGER.exception("Unexpected response from Spooly")
            return "unknown"
        return None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
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

        # die Adresse bleibt eingeklappt, sie ist nur für Tests gegen eine andere Umgebung gedacht
        schema = vol.Schema(
            {
                vol.Required(CONF_API_KEY): KEY_SELECTOR,
                vol.Required(ADVANCED, default={}): section(
                    vol.Schema({vol.Optional(CONF_URL, default=DEFAULT_URL): str}),
                    {"collapsed": True},
                ),
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=self.add_suggested_values_to_schema(schema, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

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
                # Spooly nennt keine Konto-Id, ein Schlüssel eines anderen Kontos fällt hier nicht auf
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
