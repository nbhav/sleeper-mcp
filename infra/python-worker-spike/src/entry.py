"""Issue #40 spike: probe whether shared sleeper_tooling modules import under Pyodide.

GET /                         -> same body as the TS Worker's GET /
GET /probe                    -> import probes through the real sleeper_tooling/__init__.py
GET /probe?bypass_init=1      -> same probes with a stub package (simulates a lazy __init__)
"""

import importlib
import json
import os
import sys
import time
import traceback
import types

from workers import Response, WorkerEntrypoint

PROBE_MODULES = [
    "sleeper_tooling.scoring",
    "sleeper_tooling.reports",
    "sleeper_tooling.player_usage_context",
    "sleeper_tooling.market_state",
    "sleeper_tooling.decision_reports",
    "sleeper_tooling.roster_analysis",
    "sleeper_tooling.context_providers",
]
PKG_DIR = os.path.join(os.path.dirname(__file__), "sleeper_tooling")


def _reset_package(stub: bool) -> None:
    for name in [n for n in sys.modules if n == "sleeper_tooling" or n.startswith("sleeper_tooling.")]:
        del sys.modules[name]
    if stub:
        pkg = types.ModuleType("sleeper_tooling")
        pkg.__path__ = [PKG_DIR]
        sys.modules["sleeper_tooling"] = pkg


def _probe(name: str) -> dict:
    start = time.perf_counter()
    try:
        importlib.import_module(name)
        result = {"module": name, "ok": True}
    except BaseException as exc:  # noqa: BLE001 - report every failure
        result = {
            "module": name,
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "trace": traceback.format_exc().splitlines()[-3:],
        }
    result["import_ms"] = round((time.perf_counter() - start) * 1000, 1)
    return result


def _smoke() -> dict:
    """Exercise a pure helper plus zoneinfo to show code runs, not just imports."""
    from zoneinfo import ZoneInfo
    from datetime import datetime

    scoring = importlib.import_module("sleeper_tooling.scoring")
    fns = sorted(n for n in dir(scoring) if not n.startswith("_") and callable(getattr(scoring, n)))
    return {
        "zoneinfo_new_york": datetime(2026, 11, 4, 12, tzinfo=ZoneInfo("America/New_York")).isoformat(),
        "scoring_callables": fns,
    }


def _json(body: dict, status: int = 200) -> Response:
    return Response(json.dumps(body), status=status, headers={"content-type": "application/json"})


class Default(WorkerEntrypoint):
    async def fetch(self, request):
        from urllib.parse import urlparse, parse_qs

        url = urlparse(request.url)
        if url.path == "/probe":
            stub = parse_qs(url.query).get("bypass_init") == ["1"]
            _reset_package(stub)
            body = {"python": sys.version.split()[0], "bypass_init": stub,
                    "probes": [_probe(m) for m in PROBE_MODULES]}
            if stub and all(p["ok"] for p in body["probes"]):
                try:
                    body["smoke"] = _smoke()
                except BaseException as exc:  # noqa: BLE001
                    body["smoke"] = {"error": f"{type(exc).__name__}: {exc}"}
            return _json(body)
        if request.method == "GET" and url.path == "/":
            return _json({"name": "sleeper-mcp", "endpoint": "/mcp"})
        return Response("not found", status=404)
