"""Kleiner Client für die lesende REST-Schnittstelle von Spooly (/api/v1)."""

from __future__ import annotations

import asyncio
from typing import Any

import aiohttp

from .const import PAGE_SIZE

TIMEOUT = aiohttp.ClientTimeout(total=30)

# Spooly sagt nicht immer, wie lange man warten soll
DEFAULT_RETRY_AFTER = 60
MAX_RETRY_AFTER = 3600


class SpoolyError(Exception):
    """Sammelklasse für alles, was beim Abruf schiefgehen kann."""


class SpoolyConnectionError(SpoolyError):
    """Spooly ist nicht erreichbar."""


class SpoolyAuthError(SpoolyError):
    """Der Schlüssel ist unbekannt oder widerrufen (401)."""


class SpoolyProExpiredError(SpoolyError):
    """Das Konto hat kein Pro mehr (403)."""


class SpoolyApiDisabledError(SpoolyError):
    """Die API ist für das Konto nicht freigeschaltet (403)."""


class SpoolyRateLimitError(SpoolyError):
    """Fair-Use-Grenze erreicht (429)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


def _retry_after(header: str | None) -> int:
    try:
        seconds = int(header or "")
    except ValueError:
        return DEFAULT_RETRY_AFTER
    return min(max(seconds, 1), MAX_RETRY_AFTER)


async def _error_code(resp: aiohttp.ClientResponse) -> str:
    """Der Fehlercode aus {"detail": {"code": ...}}, sonst leer."""
    try:
        body = await resp.json()
        return str(body["detail"]["code"])
    except (aiohttp.ContentTypeError, ValueError, KeyError, TypeError):
        return ""


class SpoolyClient:
    """Liest Konto, Spulen und Slots. Schreibt nie etwas."""

    def __init__(self, session: aiohttp.ClientSession, api_key: str, base_url: str) -> None:
        self._session = session
        self._headers = {"Authorization": f"Bearer {api_key}"}
        self._base_url = base_url.rstrip("/")

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            async with self._session.get(
                f"{self._base_url}{path}",
                headers=self._headers,
                params=params,
                timeout=TIMEOUT,
            ) as resp:
                if resp.status == 200:
                    return await resp.json()
                code = await _error_code(resp)
                if resp.status == 401:
                    raise SpoolyAuthError(code)
                if resp.status == 403 and code == "pro_expired":
                    raise SpoolyProExpiredError(code)
                if resp.status == 403 and code == "api_disabled":
                    raise SpoolyApiDisabledError(code)
                if resp.status == 429:
                    raise SpoolyRateLimitError(_retry_after(resp.headers.get("Retry-After")))
                raise SpoolyError(f"HTTP {resp.status} ({code})")
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SpoolyConnectionError(type(err).__name__) from err

    async def get_me(self) -> dict[str, Any]:
        return await self._get("/me")

    async def get_spools(self) -> list[dict[str, Any]]:
        """Holt alle aktiven Spulen, Seite für Seite, bis `total` erreicht ist."""
        spools: dict[str, dict[str, Any]] = {}
        offset = 0
        while True:
            page = await self._get("/spools", {"limit": PAGE_SIZE, "offset": offset})
            items = page["items"]
            for item in items:
                spools[item["id"]] = item
            offset += len(items)
            # leere Seite beendet die Schleife auch dann, wenn total nicht stimmt
            if not items or offset >= page["total"]:
                return list(spools.values())

    async def get_slots(self) -> list[dict[str, Any]]:
        return (await self._get("/slots"))["items"]
