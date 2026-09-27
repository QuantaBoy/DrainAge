"""Flood forecasts on every core: a pool of worker processes, each holding the city
model, and a cache of finished answers.

One storm cannot be split across cores - each minute of the simulation needs the
minute before it - so speed comes from running different storms at once and from never
running the same one twice:

  * Worker processes each load the coupled model once (seconds, from the disk cache)
    and then run storms side by side, one per core.
  * A finished answer is kept as compressed JSON, so a repeat request is sent as it
    is, with no simulation, no table building and no serialising.
  * Requests for a storm already being computed wait for that run instead of starting
    another.
  * The storms people ask for first - the live forecast and the what-if presets - are
    computed ahead, at start-up and whenever the rain feed updates.

A city is one model; a nationwide deployment runs one of these per city, each with
its own pool, behind one API (docs/technical_approach.tex).
"""

import asyncio
import gzip
import hashlib
import json
import logging
import os
from collections import OrderedDict
from concurrent.futures import ProcessPoolExecutor
from typing import Any

from app import config

log = logging.getLogger("uvicorn.error")

# Finished answers kept, most recent first: a few live windows and the presets.
_ANSWERS_KEPT = 24
_answers: "OrderedDict[str, bytes]" = OrderedDict()
_running: dict[str, asyncio.Future] = {}
_pool: ProcessPoolExecutor | None = None


def _key(rain: dict[str, Any], *settings: Any) -> str:
    raw = json.dumps([rain, settings], sort_keys=True, default=str)
    return hashlib.sha1(raw.encode()).hexdigest()


# ─── Worker side ─────────────────────────────────────────────────────────────────

def _worker_start() -> None:
    """Runs once in each worker: load the model before the first storm arrives."""
    from app.services import forecast
    forecast.warm()


def _run(rain: dict[str, Any], runoff_coeff: float, drain_condition: float, min_depth_cm: float,
         zone: str | None, ward: str | None) -> tuple[bytes, dict[str, Any]]:
    """One storm, start to finish: the compressed response, and the run behind it."""
    from app.services import forecast

    answer = forecast.street_forecast(rain, runoff_coeff, drain_condition, min_depth_cm, zone, ward)
    body = gzip.compress(json.dumps(answer, separators=(",", ":"), allow_nan=False).encode(), 5)
    return body, forecast.run_storm(rain, runoff_coeff, drain_condition)


# ─── Server side ─────────────────────────────────────────────────────────────────

def start(workers: int) -> None:
    """Start the worker pool. With 0 workers every storm runs in a thread here."""
    global _pool
    if workers <= 0 or _pool is not None:
        return
    # One maths thread per worker: the workers are the parallelism, and N workers each
    # spawning N threads would fight over the same cores.
    for var in ("OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS"):
        os.environ.setdefault(var, "1")
    _pool = ProcessPoolExecutor(max_workers=workers, initializer=_worker_start)
    log.info("Flood forecast pool: %d worker processes", workers)


def stop() -> None:
    global _pool
    if _pool is not None:
        _pool.shutdown(wait=False, cancel_futures=True)
        _pool = None


async def street_forecast_gz(rain: dict[str, Any], runoff_coeff: float, drain_condition: float,
                             min_depth_cm: float, zone: str | None, ward: str | None) -> bytes:
    """The street forecast for one storm as gzip-compressed JSON, from the cache when it
    has been asked for before."""
    key = _key(rain, runoff_coeff, drain_condition, min_depth_cm, zone, ward)
    if key in _answers:
        _answers.move_to_end(key)
        return _answers[key]
    if key in _running:                       # someone asked a moment ago: share it
        return await asyncio.shield(_running[key])

    loop = asyncio.get_running_loop()
    future: asyncio.Future = loop.create_future()
    _running[key] = future
    try:
        args = (rain, runoff_coeff, drain_condition, min_depth_cm, zone, ward)
        if _pool is not None:
            body, run = await loop.run_in_executor(_pool, _run, *args)
            # The router reads the same run: hand it over so it is not simulated again.
            from app.services import forecast
            forecast.remember_run(rain, runoff_coeff, drain_condition, run)
        else:
            body, _ = await asyncio.to_thread(_run, *args)
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
        except Exception as exc:             # a failed feed is retried next round
            log.warning("Precompute %s failed: %s", rate or "live", exc)

    await asyncio.gather(one(None), *(one(rate) for rate in PRESET_RATES_MM_H))
    log.info("Flood forecasts ready: live and %d scenarios", len(PRESET_RATES_MM_H))
    while True:
        await asyncio.sleep(config.FEED_STEP_MINUTES * 60 / 3)
        await one(None)
