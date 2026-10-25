"""Flood forecasts and a cache of finished answers.

Speed comes from never running the same storm twice:

  * A finished answer is kept as compressed JSON, so a repeat request is sent as it
    is, with no simulation, no table building and no serialising.
  * Requests for a storm already being computed wait for that run instead of starting
    another.
  * The storms people ask for first - the live forecast and the what-if presets - are
    computed ahead, at start-up and whenever the rain feed updates.

A city is one model; a nationwide deployment runs one of these per city behind one API (docs/technical_approach.tex).
"""

import asyncio
import gzip
import hashlib
import json
import logging
import time
from collections import OrderedDict
from typing import Any

import psutil

from app import config

log = logging.getLogger("uvicorn.error")

# Finished answers kept, most recent first: a few live windows and the presets.
_ANSWERS_KEPT = 24
_answers: "OrderedDict[str, bytes]" = OrderedDict()
_running: dict[str, asyncio.Future] = {}
# What the engine has done since it started, for the Technology page.
stats: dict[str, Any] = {"started": time.time(), "storms_run": 0, "run_seconds": 0.0,
                         "cache_hits": 0, "shared_waits": 0, "precomputed": 0, "paused_refreshes": 0}

BUSY_PERCENT = 85         # above this the background refresh waits its turn


def device_now() -> dict[str, Any]:
    """This machine's cores, memory and how busy it is at this moment."""
    memory = psutil.virtual_memory()
    return {"logical_cores": psutil.cpu_count() or 1,
            "physical_cores": psutil.cpu_count(logical=False) or psutil.cpu_count() or 1,
            "load_percent": psutil.cpu_percent(interval=0.5),
            "available_mb": memory.available / 2**20, "total_mb": memory.total / 2**20}


def _key(rain: dict[str, Any], *settings: Any) -> str:
    raw = json.dumps([rain, settings], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


def _run(rain: dict[str, Any], runoff_coeff: float, drain_condition: float, min_depth_cm: float,
         zone: str | None, ward: str | None) -> bytes:
    """One storm, start to finish: the compressed response."""
    from app.services import forecast

    answer = forecast.street_forecast(rain, runoff_coeff, drain_condition, min_depth_cm, zone, ward)
    return gzip.compress(json.dumps(answer, separators=(",", ":"), allow_nan=False).encode(), 5)


# ─── Server side ─────────────────────────────────────────────────────────────────

async def street_forecast_gz(rain: dict[str, Any], runoff_coeff: float, drain_condition: float,
                             min_depth_cm: float, zone: str | None, ward: str | None) -> bytes:
    """The street forecast for one storm as gzip-compressed JSON, from the cache when it
    has been asked for before."""
    key = _key(rain, runoff_coeff, drain_condition, min_depth_cm, zone, ward)
    if key in _answers:
        _answers.move_to_end(key)
        stats["cache_hits"] += 1
        return _answers[key]
    if key in _running:                       # someone asked a moment ago: share it
        stats["shared_waits"] += 1
        return await asyncio.shield(_running[key])

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _running[key] = future
    try:
        args = (rain, runoff_coeff, drain_condition, min_depth_cm, zone, ward)
        began = time.perf_counter()
        body = await asyncio.to_thread(_run, *args)
        stats["storms_run"] += 1
        stats["run_seconds"] += time.perf_counter() - began
        _answers[key] = body
        if len(_answers) > _ANSWERS_KEPT:
            _answers.popitem(last=False)
        future.set_result(body)
        return body
    except BaseException as exc:
        future.set_exception(exc)
        future.exception()                    # marked retrieved: no "never awaited" noise
        raise
    finally:
        _running.pop(key, None)


# ─── Ahead of time ───────────────────────────────────────────────────────────────

# What the dashboard asks for by default: the city centre, the as-surveyed drains.
PRESET_RATES_MM_H = (15.0, 30.0, 60.0, 100.0)


async def precompute_forever() -> None:
    """Compute the live forecast and the what-if presets before anyone asks, then keep
    the live one fresh as the rain feed updates."""
    from app.services import hydraulics, rainfall

    lat, lon = config.CHENNAI_CENTRE
    defaults = (hydraulics.RUNOFF_COEFF, 1.0, config.REPORT_DEPTH_M * 100, None, None)

    async def one(rate: float | None) -> None:
        try:
            rain = await rainfall.report_steps(round(lat, 4), round(lon, 4), rate)
            await street_forecast_gz(rain, *defaults)
            stats["precomputed"] += 1
        except Exception as exc:             # a failed feed is retried next round
            log.warning("Precompute %s failed: %s", rate or "live", exc)

    await asyncio.gather(one(None), *(one(rate) for rate in PRESET_RATES_MM_H))
    log.info("Flood forecasts ready: live and %d scenarios", len(PRESET_RATES_MM_H))
    while True:
        await asyncio.sleep(config.FEED_STEP_MINUTES * 60 / 3)
        # A refresh nobody is waiting for gives way to a visitor's storm already running
        # (they share one process) and to a machine busy with other work.
        if _running or await asyncio.to_thread(psutil.cpu_percent, 1.0) > BUSY_PERCENT:
            stats["paused_refreshes"] += 1
            continue
        await one(None)


def engine_status() -> dict[str, Any]:
    """What the engine is doing: the device, and how the cache is paying off."""
    runs = stats["storms_run"]
    served = runs + stats["cache_hits"] + stats["shared_waits"]
    return {
        "device": device_now(),
        "uptime_s": round(time.time() - stats["started"]),
        "storms_run": runs,
        "average_run_s": round(stats["run_seconds"] / runs, 1) if runs else None,
        "answers_cached": len(_answers),
        "cache_hits": stats["cache_hits"],
        "shared_waits": stats["shared_waits"],
        "served_without_simulating": round((served - runs) / served, 3) if served else None,
        "precomputed": stats["precomputed"],
        "paused_refreshes": stats["paused_refreshes"],
    }
