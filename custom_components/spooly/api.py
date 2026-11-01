"""Kleiner Client für die REST-Schnittstelle von Spooly (/api/v1)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import re
from typing import Any
from urllib.parse import quote
import uuid

import aiohttp

from .const import PAGE_SIZE

TIMEOUT = aiohttp.ClientTimeout(total=30)

# Spooly sagt nicht immer, wie lange man warten soll
DEFAULT_RETRY_AFTER = 60
MAX_RETRY_AFTER = 3600

# liefert den Zugangstoken für den nächsten Aufruf (bei OAuth frisch, beim Schlüssel fest)
TokenGetter = Callable[[], Awaitable[str]]


class SpoolyError(Exception):
    """Sammelklasse für alles, was beim Abruf schiefgehen kann."""


class SpoolyConnectionError(SpoolyError):
    """Spooly ist nicht erreichbar."""


class SpoolyAuthError(SpoolyError):
    """Der Schlüssel oder die Anmeldung ist unbekannt oder widerrufen (401)."""


class SpoolyProExpiredError(SpoolyError):
    """Das Konto hat kein Pro mehr (403)."""


class SpoolyApiDisabledError(SpoolyError):
    """Die API ist für das Konto nicht freigeschaltet (403)."""


class SpoolyScopeError(SpoolyError):
    """Die Verbindung darf nur lesen (403 insufficient_scope)."""


class SpoolyRateLimitError(SpoolyError):
    """Fair-Use-Grenze erreicht (429)."""

    def __init__(self, retry_after: int) -> None:
        super().__init__(f"rate limited, retry after {retry_after}s")
        self.retry_after = retry_after


class SpoolyHttpError(SpoolyError):
    """Alle anderen Antworten ab 400, mit Status und Fehlercode von Spooly."""

    def __init__(self, status: int, code: str) -> None:
        super().__init__(f"HTTP {status} ({code})")
        self.status = status
        self.code = code


def _retry_after(header: str | None) -> int:
    try:
        seconds = int(header or "")
    except ValueError:
        return DEFAULT_RETRY_AFTER
    return min(max(seconds, 1), MAX_RETRY_AFTER)


async def _error_code(resp: aiohttp.ClientResponse) -> str:
    """Der Fehlercode aus {"detail": {"code": ...}}, sonst leer. Nur einfache Kennungen kommen durch."""
    try:
        body = await resp.json()
        code = str(body["detail"]["code"])
    except (aiohttp.ContentTypeError, ValueError, KeyError, TypeError):
        return ""
    return code if re.fullmatch(r"[A-Za-z0-9_]{1,40}", code) else ""


class SpoolyClient:
    """Liest Konto, Spulen, Slots, Wartung und Statistik und ändert sie über die wenigen erlaubten Pfade."""

    def __init__(self, session: aiohttp.ClientSession, token: str | TokenGetter, base_url: str) -> None:
        self._session = session
        if isinstance(token, str):
            fixed = token

            async def token() -> str:
                return fixed

        self._token = token
        self._base_url = base_url.rstrip("/")

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        # der Kopf entsteht bei jedem Aufruf neu, ein Zugangstoken läuft nach einer Stunde ab
        headers = {"Authorization": f"Bearer {await self._token()}"}
        if method != "GET":
            headers["Idempotency-Key"] = str(uuid.uuid4())
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                params=params,
                json=body,
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
                if resp.status == 403 and code == "insufficient_scope":
                    raise SpoolyScopeError(code)
                if resp.status == 429:
                    raise SpoolyRateLimitError(_retry_after(resp.headers.get("Retry-After")))
                raise SpoolyHttpError(resp.status, code)
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise SpoolyConnectionError(type(err).__name__) from err

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", path, params)

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

    async def get_maintenance(self) -> dict[str, Any]:
        return await self._get("/maintenance")

    async def get_statistics(self, year: int, month: int) -> dict[str, Any]:
        return await self._get("/statistics", {"year": year, "month": month})

    async def get_locations(self) -> list[dict[str, Any]]:
        return (await self._get("/locations"))["items"]

    # Ändern: nur diese Pfade, nichts zum endgültigen Löschen
    async def set_weight(self, spool_id: str, grams: float | None, gross_grams: float | None) -> None:
        body = {k: v for k, v in (("grams", grams), ("gross_grams", gross_grams)) if v is not None}
        await self._request("POST", f"/spools/{quote(spool_id, safe='')}/weight", body=body)

    async def refill(self, spool_id: str, grams: float) -> None:
        await self._request("POST", f"/spools/{quote(spool_id, safe='')}/refill", body={"grams": grams})

    async def move(self, spool_id: str, location_id: str) -> None:
        await self._request("POST", f"/spools/{quote(spool_id, safe='')}/move", body={"location_id": location_id})

    async def archive(self, spool_id: str) -> None:
        await self._request("POST", f"/spools/{quote(spool_id, safe='')}/archive")

    async def restore(self, spool_id: str) -> None:
        await self._request("POST", f"/spools/{quote(spool_id, safe='')}/restore")

    async def assign_slot(self, slot_id: str, spool_id: str, confirm_color: bool) -> None:
        body = {"spool_id": spool_id, "confirm_color": confirm_color}
        await self._request("PUT", f"/slots/{quote(slot_id, safe='')}", body=body)

    async def clear_slot(self, slot_id: str) -> None:
        await self._request("DELETE", f"/slots/{quote(slot_id, safe='')}")

    async def complete_maintenance(self, task_id: str, action: str | None, notes: str | None) -> None:
        body = {k: v for k, v in (("action", action), ("notes", notes)) if v}
        await self._request("POST", f"/maintenance/tasks/{quote(task_id, safe='')}/complete", body=body)

    async def snooze_maintenance(self, task_id: str, days: int, reason: str | None) -> None:
        body = {"days": days, **({"reason": reason} if reason else {})}
        await self._request("POST", f"/maintenance/tasks/{quote(task_id, safe='')}/snooze", body=body)
