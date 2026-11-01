"""Anmeldung per OAuth: eine Implementierung je Spooly-Adresse, der Zugangstoken kommt bei jedem Aufruf frisch."""

from __future__ import annotations

import hashlib
from urllib.parse import urlparse

from aiohttp import ClientError

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import OAuth2TokenRequestError, OAuth2TokenRequestReauthError
from homeassistant.helpers.config_entry_oauth2_flow import LocalOAuth2ImplementationWithPkce, OAuth2Session

from .api import SpoolyAuthError, SpoolyConnectionError, TokenGetter
from .const import DEFAULT_URL, DOMAIN, OAUTH_CLIENT_ID, OAUTH_SCOPE


def origin(url: str) -> str:
    """Schema und Host der API-Adresse, dort liegen Zustimmungsseite und Token-Route."""
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}"


DEFAULT_ORIGIN = origin(DEFAULT_URL)


def implementation_domain(base: str) -> str:
    """Die Standardadresse heißt wie die Integration, jede andere bekommt eine eigene Kennung."""
    if base == DEFAULT_ORIGIN:
        return DOMAIN
    return f"{DOMAIN}_{hashlib.sha256(base.encode()).hexdigest()[:8]}"


class SpoolyImplementation(LocalOAuth2ImplementationWithPkce):
    """Spooly als öffentlicher Client: PKCE statt Client-Secret, Rückleitung über my.home-assistant.io."""

    def __init__(self, hass: HomeAssistant, base: str) -> None:
        super().__init__(
            hass,
            implementation_domain(base),
            OAUTH_CLIENT_ID,
            f"{base}/oauth/authorize",
            f"{base}/api/oauth/token",
        )
        self._base = base

    @property
    def name(self) -> str:
        return "Spooly" if self._base == DEFAULT_ORIGIN else f"Spooly ({urlparse(self._base).netloc})"

    @property
    def extra_authorize_data(self) -> dict:
        return {"scope": OAUTH_SCOPE, **super().extra_authorize_data}


def token_getter(session: OAuth2Session) -> TokenGetter:
    """Prüft vor jedem Aufruf, ob der Zugangstoken noch gilt, und erneuert ihn bei Bedarf."""

    async def get() -> str:
        try:
            await session.async_ensure_token_valid()
        except OAuth2TokenRequestReauthError as err:
            # invalid_grant: Spooly kennt die Verbindung nicht mehr
            raise SpoolyAuthError("invalid_grant") from err
        except (OAuth2TokenRequestError, ClientError, TimeoutError) as err:
            raise SpoolyConnectionError(type(err).__name__) from err
        return session.token["access_token"]

    return get
