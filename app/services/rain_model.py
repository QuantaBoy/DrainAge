"""The trained rain nowcast (ml/train.py): a ConvLSTM ensemble that reads the last 6
hours of rain on a 7 x 7 grid over Chennai and the sea to its east and predicts the
next 3 hours.

The forward pass is plain numpy, so neither the website nor the forecast job needs
PyTorch; ml/train.py checks it against the torch model it was trained as.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
import numpy as np

from app import config

WEIGHTS = config.DATA_DIR / "rain_model.npz"
REPORT = config.DATA_DIR / "rain_models.json"
LATS = np.round(np.arange(13.75, 12.24, -0.25), 2)      # as in ml/fetch_rain.py
LONS = np.round(np.arange(79.75, 81.26, 0.25), 2)
IN_H, OUT_H = 6, 3
IST = timezone(timedelta(hours=5, minutes=30))
_members: list[dict[str, np.ndarray]] | None = None


def available() -> bool:
    return WEIGHTS.exists()


def reset() -> None:
    global _members
    _members = None


def _load() -> list[dict[str, np.ndarray]]:
    global _members
    if _members is None:
        raw = np.load(WEIGHTS)
        seeds = sorted({k.split(".", 1)[0] for k in raw.files})
        _members = [{k.split(".", 1)[1]: raw[k].astype(np.float64) for k in raw.files if k.startswith(s + ".")}
                    for s in seeds]
    return _members


def _conv(x: np.ndarray, w: np.ndarray, b: np.ndarray) -> np.ndarray:
    """2D convolution, 'same' padding: x [batch, in, r, c], w [out, in, k, k]."""
    k = w.shape[-1]
    p = k // 2
    padded = np.pad(x, ((0, 0), (0, 0), (p, p), (p, p)))
    r, c = x.shape[2:]
    out = np.zeros((x.shape[0], w.shape[0], r, c))
    for dr in range(k):
        for dc in range(k):
            out += np.einsum("oi,birc->borc", w[:, :, dr, dc], padded[:, :, dr:dr + r, dc:dc + c])
    return out + b[None, :, None, None]


def _sigmoid(v: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-v))


def _forward(m: dict[str, np.ndarray], x: np.ndarray) -> np.ndarray:
    """One ConvLSTM member: log rain [batch, 6, r, c] in, log rain [batch, 3, r, c] out."""
    hidden = m["cell.gates.weight"].shape[0] // 4
    b, t, r, c = x.shape
    h = np.zeros((b, hidden, r, c))
    s = np.zeros_like(h)

    def step(frame, h, s):
        g = _conv(np.concatenate([frame, h], 1), m["cell.gates.weight"], m["cell.gates.bias"])
        i, f, o, gg = np.split(g, 4, axis=1)
        s = _sigmoid(f) * s + _sigmoid(i) * np.tanh(gg)
        return _sigmoid(o) * np.tanh(s), s

    for k in range(t):
        h, s = step(x[:, k:k + 1], h, s)
    out, frame = [], x[:, -1:]
    for _ in range(OUT_H):
        h, s = step(frame, h, s)
        frame = _conv(h, m["head.weight"], m["head.bias"])
        out.append(frame)
    return np.concatenate(out, 1)


def predict_ensemble(rain_mm_h: np.ndarray) -> dict[str, np.ndarray]:
    """[batch, 6, 7, 7] mm/h in; the ensemble's mean and spread for the next 3 hours."""
    x = np.log1p(np.clip(np.asarray(rain_mm_h, dtype=np.float64), 0, None))
    runs = np.stack([np.clip(np.expm1(_forward(m, x)), 0, None) for m in _load()])
    return {"mean": runs.mean(0), "spread": runs.std(0)}


def report() -> dict[str, Any] | None:
    return json.loads(REPORT.read_text(encoding="utf-8")) if REPORT.exists() else None


async def nowcast(lat: float, lon: float) -> dict[str, Any]:
    """The model's next 3 hours at the grid cell nearest a place, from the last 6 hours
    of rain on the grid (Open-Meteo's hourly analysis, the closest live match to the
    ERA5 the model was trained on)."""
    la, lo = np.meshgrid(LATS, LONS, indexing="ij")
    params = {"latitude": ",".join(map(str, la.ravel())), "longitude": ",".join(map(str, lo.ravel())),
              "hourly": "precipitation", "past_hours": IN_H + 2, "forecast_hours": 2,
              "timezone": "Asia/Kolkata"}
    async with httpx.AsyncClient(timeout=20.0) as client:
        response = await client.get("https://api.open-meteo.com/v1/forecast", params=params)
        response.raise_for_status()
        points = response.json()
    # Each value is the rain of the hour ending at its time: keep the last 6 finished hours.
    now = datetime.now(IST).replace(tzinfo=None).isoformat(timespec="minutes")
    done = [i for i, t in enumerate(points[0]["hourly"]["time"]) if t <= now][-IN_H:]
    times = [points[0]["hourly"]["time"][i] for i in done]
    grid = np.array([[p["hourly"]["precipitation"][i] or 0.0 for i in done] for p in points])
    grid = grid.T.reshape(IN_H, len(LATS), len(LONS))
    pred = predict_ensemble(grid[None])
    row = int(np.abs(LATS - lat).argmin())
    col = int(np.abs(LONS - lon).argmin())
    last = datetime.fromisoformat(times[-1])
    return {
        "made_from": times,
        "observed_mm_h": grid[:, row, col].round(2).tolist(),
        "rain_mm_h": pred["mean"][0, :, row, col].round(2).tolist(),
        "spread_mm_h": pred["spread"][0, :, row, col].round(2).tolist(),
        "hours": [(last + timedelta(hours=h + 1)).isoformat(timespec="minutes") for h in range(OUT_H)],
        "cell": [float(LATS[row]), float(LONS[col])],
        "grid_mean_mm_h": pred["mean"][0].round(2).tolist(),
    }
