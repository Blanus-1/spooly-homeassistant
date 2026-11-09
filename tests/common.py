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
        "location": "Regal 1 · Platz 4",
        "location_id": "loc-2",
        "place": 4,
        "archived": False,
    }
    spool.update(extra)
    return spool


def make_printer(printer_id: str = "prn-1", name: str | None = "Bambu X1C", **extra: Any) -> dict[str, Any]:
    printer = {
        "printer_id": printer_id,
        "printer": name,
        "hours": 120.5,
        "prints": 40,
        "filament_kg": 3.2,
        "due_count": 1,
        "soon_count": 1,
        "health_score": 80,
        "hours_confirmed": True,
    }
    printer.update(extra)
    return printer


def make_task(task_id: str = "task-1", printer_id: str = "prn-1", **extra: Any) -> dict[str, Any]:
    task = {
        "id": task_id,
        "printer_id": printer_id,
        "printer": "Bambu X1C",
        "name": "Düse wechseln",
        "status": "due",
        "metric": "hours",
        "interval": 200,
        "used": 210.0,
        "remaining": -10.0,
        "eta_days": None,
        "snoozed_until": None,
        "last_done_at": "2026-09-01T10:00:00Z",
        "default_action": "replace",
    }
    task.update(extra)
    return task


def make_slot(
    printer: str, slot: str, spool: dict[str, Any] | None = None, slot_id: str | None = None
) -> dict[str, Any]:
    item = None
    if spool is not None:
        item = {k: spool[k] for k in ("id", "number", "title", "material", "color_hex")}
    slot_item = {"printer": printer, "slot": slot, "spool": item}
    if slot_id is not None:
        slot_item["slot_id"] = slot_id
    return slot_item


class FakeSpooly:
    """Beantwortet /me, /spools, /slots, /locations, /maintenance und /statistics aus dem Speicher und nimmt Änderungen an.

    Fehler lassen sich einschalten: `fail` für alle Pfade, `fail_write` nur für ändernde Aufrufe.
    """

    def __init__(self, aioclient_mock: AiohttpClientMocker, base_url: str = DEFAULT_URL) -> None:
        self.mock = aioclient_mock
        self.me = {
            "account": "pro",
            "calls_today": 12,
            "daily_limit": 10000,
            "key_name": "Home Assistant",
            "key_kind": "home_assistant",
            "account_id": "acc-1",
            "currency": "EUR",
        }
        self.spools: list[dict[str, Any]] = [make_spool(1)]
        self.archived: list[dict[str, Any]] = []
        self.slots: list[dict[str, Any]] = []
        self.maint_printers: list[dict[str, Any]] = []
        self.tasks: list[dict[str, Any]] = []
        self.statistics: dict[str, Any] = {
            "grams": 0,
            "prints": 0,
            "hours": 0,
            "filament_cost": 0,
            "currency": "EUR",
            "stock": {"active_spools": 0, "remaining_grams": 0, "stock_value": 0, "total_spent": 0},
        }
        # Abfragen von /statistics: (Jahr, Monat)
        self.statistics_queries: list[tuple[str, str]] = []
        self.locations: list[dict[str, Any]] = [
            {"id": "loc-1", "name": "Schrank 01 · Fach vorne", "is_default": False},
            {"id": "loc-2", "name": "Regal 1", "is_default": True},
        ]
        # (Status, Fehlercode, Header), gilt für alle Pfade
        self.error: tuple[int, str, dict[str, str]] | None = None
        # (Status, Körper, Header), gilt nur für ändernde Aufrufe
        self.write_error: tuple[int, Any, dict[str, str]] | None = None
        self.write_exc: Exception | None = None
        self.paths: list[str] = []
        # alle Aufrufe mit ihrem Autorisierungskopf: (Methode, Pfad, Kopfzeilen)
        self.requests: list[tuple[str, str, dict[str, str]]] = []
        # nur ändernde Aufrufe: (Methode, Pfad, Körper)
        self.writes: list[tuple[str, str, Any]] = []
        pattern = re.compile(re.escape(base_url) + "/")
        for register in (aioclient_mock.get, aioclient_mock.post, aioclient_mock.put, aioclient_mock.delete):
            register(pattern, side_effect=self._handle)

    def fail(self, status: int, code: str, headers: dict[str, str] | None = None) -> None:
        self.error = (status, code, headers or {})

    def fail_write(
        self, status: int, code: str = "", headers: dict[str, str] | None = None, body: Any = None
    ) -> None:
        self.write_error = (status, body if body is not None else {"detail": {"code": code, "message": "Fehler"}}, headers or {})

    def recover(self) -> None:
        self.error = None
        self.write_error = None
        self.write_exc = None

    @property
    def tokens(self) -> list[str]:
        """Die Zugangstoken, mit denen die Aufrufe kamen, in der Reihenfolge."""
        return [h["Authorization"].removeprefix("Bearer ") for _, _, h in self.requests]

    async def _handle(self, method: str, url: Any, data: Any) -> AiohttpClientMockResponse:
        method = method.upper()
        path = url.path.rsplit("/api/v1", 1)[1]
        headers = dict(self.mock.mock_calls[-1][3] or {})
        self.requests.append((method, path, headers))
        if method == "GET":
            self.paths.append(path)
        else:
            self.writes.append((method, path, data))
        if self.error:
            status, code, err_headers = self.error
            body = {"detail": {"code": code, "message": "Fehler"}}
            return AiohttpClientMockResponse(method, url, status=status, json=body, headers=err_headers)
        if method != "GET":
            return self._write(method, url, path, data)
        if path == "/me":
            body = self.me
        elif path == "/slots":
            body = {"items": self.slots}
        elif path == "/maintenance":
            body = {
                "printers": self.maint_printers,
                "tasks": self.tasks,
                "printers_without_plan": [],
                "due_count": sum(t["status"] == "due" for t in self.tasks),
                "soon_count": sum(t["status"] == "soon" for t in self.tasks),
                "snoozed_count": 0,
            }
        elif path == "/statistics":
            self.statistics_queries.append((url.query["year"], url.query["month"]))
            body = self.statistics
        elif path == "/locations":
            body = {"items": self.locations}
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

    def _write(self, method: str, url: Any, path: str, data: Any) -> AiohttpClientMockResponse:
        if self.write_exc:
            raise self.write_exc
        if self.write_error:
            status, body, err_headers = self.write_error
            return AiohttpClientMockResponse(method, url, status=status, json=body, headers=err_headers)
        parts = path.strip("/").split("/")
        if parts[0] == "maintenance" and len(parts) == 4:
            task = next((t for t in self.tasks if t["id"] == parts[2]), None)
            if task is None:
                body = {"detail": {"code": "not_found", "message": "Fehler"}}
                return AiohttpClientMockResponse(method, url, status=404, json=body)
            if parts[3] == "complete":
                task.update(status="ok", remaining=task["interval"], used=0)
            else:
                task["snoozed_until"] = "2026-12-31"
        if parts[0] == "spools" and len(parts) == 3:
            spool = next((s for s in [*self.spools, *self.archived] if s["id"] == parts[1]), None)
            if spool is not None:
                self._apply(spool, parts[2], data or {})
        return AiohttpClientMockResponse(method, url, json={"change_id": 1, "summary": "ok", "replayed": False})

    def _apply(self, spool: dict[str, Any], action: str, data: dict[str, Any]) -> None:
        if action == "weight":
            spool["remaining_grams"] = data.get("grams", data.get("gross_grams", 0) - 150)
        elif action == "refill":
            spool["remaining_grams"] += data["grams"]
        elif action == "archive" and spool in self.spools:
            self.spools.remove(spool)
            self.archived.append(spool)
        elif action == "restore" and spool in self.archived:
            self.archived.remove(spool)
            self.spools.append(spool)


def entry_data(api_key: str = KEY) -> dict[str, str]:
    return {CONF_API_KEY: api_key, CONF_URL: DEFAULT_URL}
