"""The rain the flood model runs on: the next three hours, in 5 minute steps.

Live rain comes from our own ConvLSTM nowcast (app/services/rain_model.py, trained by
ml/train.py on Chennai's past monsoons, flood years included) when its weights are
present, and from Open-Meteo's 15 minute product otherwise. A DWR feed would replace
the model's input and nothing else, since everything downstream takes millimetres per
hour on a clock.
"""

import time
from datetime import datetime, timedelta
from typing import Any

import httpx

from app import config
from app.errors import UpstreamError
from app.services import rain_model
from app.services.weather import local_current_step

NOWCAST_URL = "https://api.open-meteo.com/v1/forecast"
FEED_STEPS = config.NOWCAST_HOURS * 60 // config.FEED_STEP_MINUTES
REPORT_STEPS = config.NOWCAST_HOURS * 60 // config.REPORT_STEP_MINUTES

# The feed's own grid is about 0.1 degrees and it updates every 15 minutes: places
# closer than that, asked within a few minutes, get the same answer without a new call.
NOWCAST_TTL_S = 300
_nowcasts: dict[tuple[float, float], tuple[float, dict[str, Any]]] = {}


async def nowcast(lat: float, lon: float) -> dict[str, Any]:
    """Rainfall for the next three hours at a point, in the feed's 15 minute steps."""
    cell = (round(lat, 1), round(lon, 1))
    hit = _nowcasts.get(cell)
    if hit and time.monotonic() - hit[0] < NOWCAST_TTL_S:
        return hit[1]
    answer = await _fetch_nowcast(*cell)
    if len(_nowcasts) > 256:
        _nowcasts.clear()
    _nowcasts[cell] = (time.monotonic(), answer)
    return answer


async def _fetch_nowcast(lat: float, lon: float) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=15.0) as client:
        try:
            response = await client.get(NOWCAST_URL, params={
                "latitude": lat,
                "longitude": lon,
                "minutely_15": "precipitation",
                # A few steps of slack: the feed's own first step can sit a little behind
                # the clock (its data cycle, not this server's), and the window is
                # trimmed to "now" below rather than trusted to start there.
                "forecast_minutely_15": FEED_STEPS + 4,
                "timezone": "auto",
            })
        except httpx.RequestError as exc:
            raise UpstreamError("Rainfall nowcast unavailable", status_code=503) from exc
    if response.status_code != 200:
        raise UpstreamError("Rainfall nowcast unavailable")

    body = response.json()
    block = body.get("minutely_15", {})
    all_times = block.get("time", [])
    all_rates = [(value or 0.0) * (60 / config.FEED_STEP_MINUTES) for value in block.get("precipitation", [])]

    # The feed's first slot is not always "now": drop whatever in it has already
    # passed, so +5 min on the page is 5 minutes from the clock, not from a stale slot.
    now = local_current_step(body.get("utc_offset_seconds", 0), config.FEED_STEP_MINUTES)
    first = next((i for i, time in enumerate(all_times) if datetime.fromisoformat(time) >= now), 0)
    times = all_times[first:first + FEED_STEPS]
    rates = all_rates[first:first + FEED_STEPS]
    return {
        "source": "Open-Meteo 15-minute precipitation",
        "lat": body.get("latitude", lat),
        "lon": body.get("longitude", lon),
        "step_minutes": config.FEED_STEP_MINUTES,
        # The clock this window starts on, i.e. times[0], kept explicit so a caller
        # never has to reverse-engineer "now" from a feed timestamp.
        "starts_at": times[0] if times else now.isoformat(timespec="minutes"),
        "times": times,
        "rain_mm_h": [round(rate, 2) for rate in rates],
    }


_model_runs: dict[tuple[float, float], tuple[float, dict[str, Any] | None]] = {}


async def model_nowcast(lat: float, lon: float) -> dict[str, Any] | None:
    """The ConvLSTM's next 3 hours near a place, or None without weights or input, so
    the caller falls back to the Open-Meteo product. Kept for NOWCAST_TTL_S per grid cell."""
    if not rain_model.available():
        return None
    cell = (round(lat * 4) / 4, round(lon * 4) / 4)
    hit = _model_runs.get(cell)
    if hit and time.monotonic() - hit[0] < NOWCAST_TTL_S:
        return hit[1]
    try:
        answer = await rain_model.nowcast(lat, lon)
    except (httpx.HTTPError, KeyError, ValueError, IndexError):
        answer = None
    _model_runs[cell] = (time.monotonic(), answer)
    return answer


async def report_steps(lat: float, lon: float, rain_mm_h: float | None) -> dict[str, Any]:
    """Rain in each 5 minute step of the next 3 hours, and the clock time each step ends.

    With `rain_mm_h` the rain is a scenario held at that rate; without it, the live
    nowcast at the place asked about, or the city centre if that is outside Chennai.
    The feed's 15 minute values are spread evenly over their three 5 minute steps:
    that is the honest limit of the input.
    """
    per = config.FEED_STEP_MINUTES // config.REPORT_STEP_MINUTES
    step = config.REPORT_STEP_MINUTES
    if rain_mm_h is not None:
        rates = [rain_mm_h] * REPORT_STEPS
        ends = [None] * REPORT_STEPS
        source = (f"Scenario: rain held at {rain_mm_h} mm/h for {config.NOWCAST_HOURS} h, "
                  "hypothetical, not a forecast")
    else:
        # The drains are Chennai's: rain anywhere else is not what they will carry.
        moved = not config.in_chennai(lat, lon)
        if moved:
            lat, lon = config.CHENNAI_CENTRE
        predicted = await model_nowcast(lat, lon)
        if predicted:
            # The model's hourly rates, spread evenly over each hour's 5 minute steps.
            hourly_steps = 60 // step
            rates = [rate for rate in predicted["rain_mm_h"] for _ in range(hourly_steps)][:REPORT_STEPS]
            start = datetime.fromisoformat(predicted["hours"][0]) - timedelta(hours=1)
            source = (f"ConvLSTM rain nowcast (ensemble of {len(rain_model._load())}, trained on Chennai's "
                      f"monsoons 2005-2025) from the last {rain_model.IN_H} h of rain")
        else:
            live = await nowcast(lat, lon)
            rates = [rate for rate in live["rain_mm_h"] for _ in range(per)][:REPORT_STEPS]
            start = datetime.fromisoformat(live["starts_at"])
            source = f"{live['source']} at {lat:.3f}, {lon:.3f}"
        rates += [0.0] * (REPORT_STEPS - len(rates))
        ends = [(start + timedelta(minutes=step * (i + 1))).isoformat(timespec="minutes")
                for i in range(REPORT_STEPS)]
        source += (f"{' (city centre: the place asked about is outside Chennai)' if moved else ''}, "
                   f"next {config.NOWCAST_HOURS} h")
    return {
        # A scenario is never passed off as a forecast: every response says which it is.
        "mode": "scenario" if rain_mm_h is not None else "live",
        "source": source,
        "step_minutes": step,
        "rain_mm_h": [round(r, 2) for r in rates],
        "rain_mm": [r * step / 60 for r in rates],
        "ends": ends,
        "total_mm": round(sum(rates) * step / 60, 1),
    }
