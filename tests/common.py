"""Gemeinsame Hilfen: eine kleine Fake-Version von /api/v1."""

from __future__ import annotations

import re
from typing import Any

from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)

from custom_components.spooly.const import CONF_API_KEY, CONF_URL, DEFAULT_URL

KEY = "spk_geheimer_testschluessel_0815"


def make_spool(n: int, **extra: Any) -> dict[str, Any]:
    spool = {
        "id": f"spool-{n}",
        "number": n,
        "title": f"PLA Basic Schwarz {n}",
        "manufacturer": "Bambu Lab",
        "material": "PLA",
        "finish": "Matt",
        "color_hex": "#111111",
        "remaining_grams": 800.0,
        "spool_size_grams": 1000.0,
        "fill_level": None,
        "location": "Regal 1",
        "archived": False,
    }
    spool.update(extra)
    return spool


def make_slot(printer: str, slot: str, spool: dict[str, Any] | None = None) -> dict[str, Any]:
    item = None
    if spool is not None:
        item = {k: spool[k] for k in ("id", "number", "title", "material", "color_hex")}
    return {"printer": printer, "slot": slot, "spool": item}


class FakeSpooly:
    """Beantwortet /me, /spools und /slots aus dem Speicher. Fehler lassen sich einschalten."""

    def __init__(self, aioclient_mock: AiohttpClientMocker, base_url: str = DEFAULT_URL) -> None:
        self.mock = aioclient_mock
        self.me = {
            "account": "pro",
            "calls_today": 12,
            "daily_limit": 10000,
            "key_name": "Home Assistant",
            "key_kind": "home_assistant",
            "currency": "EUR",
        }
        self.spools: list[dict[str, Any]] = [make_spool(1)]
        self.slots: list[dict[str, Any]] = []
        # (Status, Fehlercode, Header), gilt für alle Pfade
        self.error: tuple[int, str, dict[str, str]] | None = None
        self.paths: list[str] = []
        aioclient_mock.get(re.compile(re.escape(base_url) + "/"), side_effect=self._handle)

    def fail(self, status: int, code: str, headers: dict[str, str] | None = None) -> None:
        self.error = (status, code, headers or {})

    def recover(self) -> None:
        self.error = None

    async def _handle(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        path = url.path.rsplit("/api/v1", 1)[1]
        self.paths.append(path)
        if self.error:
            status, code, headers = self.error
            body = {"detail": {"code": code, "message": "Fehler"}}
            return AiohttpClientMockResponse(method, url, status=status, json=body, headers=headers)
        if path == "/me":
            body = self.me
        elif path == "/slots":
            body = {"items": self.slots}
        elif path == "/spools":
            query = url.query
            limit, offset = int(query["limit"]), int(query["offset"])
            if limit > 500:
                return AiohttpClientMockResponse(method, url, status=422, json={"detail": "limit"})
            body = {
                "items": self.spools[offset : offset + limit],
                "total": len(self.spools),
                "limit": limit,
                "offset": offset,
            }
        else:
            return AiohttpClientMockResponse(method, url, status=404, json={})
        return AiohttpClientMockResponse(method, url, json=body)


def entry_data(api_key: str = KEY) -> dict[str, str]:
    return {CONF_API_KEY: api_key, CONF_URL: DEFAULT_URL}
