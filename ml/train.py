"""Train and compare rain nowcast models on Chennai's hourly rainfall (ml/fetch_rain.py).

    python ml/train.py

Every model sees the last 6 hours of rain on a 7 x 7 grid (Chennai and the sea to its
east) and predicts the next 3 hours on the same grid:

  persistence   the last hour, held: the baseline every model has to beat
  ridge         linear autoregression on each point's 3 x 3 neighbourhood, 6 lags
  lstm, gru     a recurrent network per point, on that point's own series
  convlstm      a convolutional LSTM over the whole grid (Shi et al. 2015)

Seasons 2021 and 2023 (the November 2021 floods and Cyclone Michaung) are held out as
the test; 2022 picks when to stop training. The model with the lowest validation error
is then retrained on every season, flood years included, as an ensemble of five whose
spread is the forecast's uncertainty, and saved for app/services/rain_model.py.

Writes app/data/rain_model.npz (weights) and app/data/rain_models.json (scores, and
each model's forecast through Cyclone Michaung for the website).
"""

import json
import time
from pathlib import Path

import numpy as np
import torch
from torch import nn

ROOT = Path(__file__).resolve().parent.parent
DATA = Path(__file__).with_name("rain_era5.npz")
IN_H, OUT_H = 6, 3
TEST_YEARS, VAL_YEAR = (2021, 2023), 2022
CHENNAI = (3, 2)                                  # grid cell over the city: 13.00 N, 80.25 E
CITY = (slice(2, 5), slice(1, 4))                 # the 3 x 3 cells around it
THRESHOLDS = (0.5, 2.0)                           # mm/h, for hit/miss scores
EVENT = ("2023-12-01T00:00", "2023-12-06T23:00")  # Cyclone Michaung, for the chart
ENSEMBLE = 5
torch.set_num_threads(max(1, torch.get_num_threads()))


# ─── Data ─────────────────────────────────────────────────────────────────────────

def windows(times: np.ndarray, rain: np.ndarray):
    """Every 9-hour stretch of unbroken hours: 6 in, 3 out, and the hour it is made."""
    t = times.astype("datetime64[h]")
    unbroken = np.r_[False, np.diff(t) == np.timedelta64(1, "h")]
    ok = np.ones(len(t) - IN_H - OUT_H + 1, dtype=bool)
    for k in range(1, IN_H + OUT_H):
        ok &= unbroken[k:len(t) - IN_H - OUT_H + 1 + k]
    start = np.flatnonzero(ok)
    x = np.stack([rain[s:s + IN_H] for s in start])
    y = np.stack([rain[s + IN_H:s + IN_H + OUT_H] for s in start])
    return x, y, t[start + IN_H - 1]


def split(x, y, made, years):
    keep = np.isin(made.astype("datetime64[Y]").astype(int) + 1970, years)
    return x[keep], y[keep], made[keep]


def rainy(x, y, rng, dry_share=0.1):
    """Training windows with rain in them, and a tenth of the dry ones: a season is mostly
    dry hours, which would otherwise teach a model to say zero."""
    wet = (x.max(axis=(1, 2, 3)) > 0.1) | (y.max(axis=(1, 2, 3)) > 0.1)
    keep = wet | (rng.random(len(x)) < dry_share)
    return x[keep], y[keep]


# ─── Models ───────────────────────────────────────────────────────────────────────

def persistence(x):
    return np.repeat(x[:, -1:], OUT_H, axis=1)


def neighbourhood(x):
    """[samples, lags, rows, cols] -> [samples * cells, lags * 9] of each cell's 3 x 3."""
    padded = np.pad(x, ((0, 0), (0, 0), (1, 1), (1, 1)), mode="edge")
    rows, cols = x.shape[2:]
    parts = [padded[:, :, dr:dr + rows, dc:dc + cols] for dr in range(3) for dc in range(3)]
    f = np.stack(parts, axis=2)                                 # s, lag, 9, r, c
    return f.transpose(0, 3, 4, 1, 2).reshape(-1, x.shape[1] * 9)


class Ridge:
    def fit(self, x, y, alpha=1.0):
        f = np.c_[neighbourhood(np.log1p(x)), np.ones(len(x) * 49)]
        t = np.log1p(y).transpose(0, 2, 3, 1).reshape(-1, OUT_H)
        self.w = np.linalg.solve(f.T @ f + alpha * np.eye(f.shape[1]), f.T @ t)
        return self

    def predict(self, x):
        f = np.c_[neighbourhood(np.log1p(x)), np.ones(len(x) * 49)]
        p = (f @ self.w).reshape(len(x), 7, 7, OUT_H).transpose(0, 3, 1, 2)
        return np.clip(np.expm1(p), 0, None)


class PointRNN(nn.Module):
    """One series per grid point, the same network for every point."""

    def __init__(self, kind, hidden=32):
        super().__init__()
        self.rnn = (nn.LSTM if kind == "lstm" else nn.GRU)(1, hidden, batch_first=True)
        self.head = nn.Linear(hidden, OUT_H)

    def forward(self, x):                                       # b, t, r, c
        b, t, r, c = x.shape
        seq = x.permute(0, 2, 3, 1).reshape(b * r * c, t, 1)
        out, _ = self.rnn(seq)
        return self.head(out[:, -1]).reshape(b, r, c, OUT_H).permute(0, 3, 1, 2)


class ConvLSTMCell(nn.Module):
    def __init__(self, cin, hidden, k=3):
        super().__init__()
        self.hidden = hidden
        self.gates = nn.Conv2d(cin + hidden, 4 * hidden, k, padding=k // 2)

    def forward(self, x, h, c):
        i, f, o, g = self.gates(torch.cat([x, h], 1)).chunk(4, 1)
        c = torch.sigmoid(f) * c + torch.sigmoid(i) * torch.tanh(g)
        h = torch.sigmoid(o) * torch.tanh(c)
        return h, c


class ConvLSTM(nn.Module):
    """Encoder-forecaster: read the 6 hours in, then roll forward 3, each predicted hour
    fed back in as the next input."""

    def __init__(self, hidden=24):
        super().__init__()
        self.cell = ConvLSTMCell(1, hidden)
        self.head = nn.Conv2d(hidden, 1, 1)

    def forward(self, x):                                       # b, t, r, c (log rain)
        b, t, r, c = x.shape
        h = x.new_zeros(b, self.cell.hidden, r, c)
        s = torch.zeros_like(h)
        for k in range(t):
            h, s = self.cell(x[:, k:k + 1], h, s)
        out, frame = [], x[:, -1:]
        for _ in range(OUT_H):
            h, s = self.cell(frame, h, s)
            frame = self.head(h)
            out.append(frame)
        return torch.cat(out, 1)


def train_net(make, x, y, xv, yv, seed, epochs=40, batch=256):
    torch.manual_seed(seed)
    net = make()
    opt = torch.optim.Adam(net.parameters(), lr=2e-3)
    tx, ty = torch.tensor(np.log1p(x)), torch.tensor(np.log1p(y))
    vx, vy = torch.tensor(np.log1p(xv)), torch.tensor(np.log1p(yv))
    best, best_state, stale = np.inf, None, 0
    for epoch in range(epochs):
        net.train()
        order = torch.randperm(len(tx))
        for i in range(0, len(tx), batch):
            idx = order[i:i + batch]
            loss = nn.functional.mse_loss(net(tx[idx]), ty[idx])
            opt.zero_grad()
            loss.backward()
            opt.step()
        net.eval()
        with torch.no_grad():
            val = float(nn.functional.mse_loss(net(vx), vy))
        if val < best - 1e-5:
            best, best_state, stale = val, {k: v.clone() for k, v in net.state_dict().items()}, 0
        else:
            stale += 1
            if stale >= 5:                                      # early stopping
                break
    net.load_state_dict(best_state)
    net.eval()
    return net


def net_predict(net, x, batch=2048):
    with torch.no_grad():
        out = [net(torch.tensor(np.log1p(x[i:i + batch]))).numpy() for i in range(0, len(x), batch)]
    return np.clip(np.expm1(np.concatenate(out)), 0, None)


# ─── Scores ───────────────────────────────────────────────────────────────────────

def scores(pred, y):
    """Errors over the whole grid and over the city, and hit/miss skill at thresholds."""
    out = {
        "rmse_mm_h": float(np.sqrt(np.mean((pred - y) ** 2))),
        "mae_mm_h": float(np.mean(np.abs(pred - y))),
        "city_rmse_mm_h": float(np.sqrt(np.mean((pred[:, :, CITY[0], CITY[1]] - y[:, :, CITY[0], CITY[1]]) ** 2))),
        # What drives the flood model: the rain the next 3 hours bring to the city.
        "city_3h_total_mae_mm": float(np.mean(np.abs(pred[:, :, CITY[0], CITY[1]].sum(1) - y[:, :, CITY[0], CITY[1]].sum(1)))),
        "by_hour_rmse": [float(np.sqrt(np.mean((pred[:, h] - y[:, h]) ** 2))) for h in range(OUT_H)],
    }
    for th in THRESHOLDS:
        hit = ((pred >= th) & (y >= th)).sum()
        miss = ((pred < th) & (y >= th)).sum()
        false = ((pred >= th) & (y < th)).sum()
        out[f"csi_{th}"] = float(hit / max(hit + miss + false, 1))
    return out


def main() -> None:
    began = time.perf_counter()
    rng = np.random.default_rng(26085)
    d = np.load(DATA)
    rain = np.nan_to_num(d["rain"]).astype(np.float32)
    x, y, made = windows(d["time"], rain)
    years = sorted(set(made.astype("datetime64[Y]").astype(int) + 1970))
    train_years = [yr for yr in years if yr not in TEST_YEARS and yr != VAL_YEAR]
    xtr, ytr, _ = split(x, y, made, train_years)
    xva, yva, _ = split(x, y, made, [VAL_YEAR])
    xte, yte, mte = split(x, y, made, list(TEST_YEARS))
    xtr, ytr = rainy(xtr, ytr, rng)
    xva_r, yva_r = rainy(xva, yva, rng, dry_share=1.0)
    # Scored on hours with rain about: a dry hour is trivially right for every model.
    wet = (xte.max(axis=(1, 2, 3)) > 0.1) | (yte.max(axis=(1, 2, 3)) > 0.1)
    xs, ys = xte[wet], yte[wet]
    print(f"train {len(xtr)} windows ({train_years[0]}-{train_years[-1]} less {VAL_YEAR}, {TEST_YEARS}), "
          f"validation {len(xva_r)}, test {len(xs)} wet windows", flush=True)

    makers = {
        "lstm": lambda: PointRNN("lstm"),
        "gru": lambda: PointRNN("gru"),
        "convlstm": lambda: ConvLSTM(),
    }
    names = {"persistence": "Persistence (baseline)", "ridge": "Linear autoregression (ridge)",
             "lstm": "LSTM (per point)", "gru": "GRU (per point)", "convlstm": "ConvLSTM (whole grid)"}
    predictors, results, val_rmse = {}, {}, {}
    predictors["persistence"] = persistence
    ridge = Ridge().fit(xtr, ytr)
    predictors["ridge"] = ridge.predict
    for key, make in makers.items():
        t0 = time.perf_counter()
        net = train_net(make, xtr, ytr, xva_r, yva_r, seed=0)
        predictors[key] = lambda xx, net=net: net_predict(net, xx)
        print(f"  {key} trained in {time.perf_counter() - t0:.0f} s", flush=True)
    for key, predict in predictors.items():
        val_rmse[key] = scores(predict(xva_r), yva_r)["rmse_mm_h"]
        results[key] = {"name": names[key], "validation_rmse_mm_h": val_rmse[key], **scores(predict(xs), ys)}
        r = results[key]
        print(f"  {key:12s} test rmse {r['rmse_mm_h']:.3f}  city 3h total MAE {r['city_3h_total_mae_mm']:.2f} mm  "
              f"CSI@0.5 {r['csi_0.5']:.3f}  CSI@2 {r['csi_2.0']:.3f}", flush=True)

    learned = [k for k in predictors if k != "persistence"]
    best = min(learned, key=lambda k: val_rmse[k])
    print(f"best on validation: {best}", flush=True)

    # Cyclone Michaung through every model's eyes: the rain 3 hours ahead at Chennai.
    lo, hi = (np.datetime64(t, "h") for t in EVENT)
    at = (mte >= lo) & (mte <= hi)
    chart = {"made": [str(t) for t in mte[at]],
             "observed_3h_ahead": yte[at][:, -1, CHENNAI[0], CHENNAI[1]].round(2).tolist(),
             "models": {k: predictors[k](xte[at])[:, -1, CHENNAI[0], CHENNAI[1]].round(2).tolist()
                        for k in predictors}}

    # The chosen model, retrained on every season (flood years included) as an ensemble.
    xall, yall = rainy(x, y, rng)
    weights = {}
    if best in makers:
        for seed in range(ENSEMBLE):
            net = train_net(makers[best], xall, yall, xva_r, yva_r, seed=seed)
            for name, value in net.state_dict().items():
                weights[f"m{seed}.{name}"] = value.numpy()
        # The numpy forward pass the site uses must agree with torch.
        if best == "convlstm":
            import sys
            sys.path.insert(0, str(ROOT))
            from app.services import rain_model
            np.savez(ROOT / "app" / "data" / "rain_model.npz", **weights)
            rain_model.reset()
            mine = rain_model.predict_ensemble(xte[:8])["mean"]
            theirs = np.mean([net_predict(_load(seed, weights), xte[:8]) for seed in range(ENSEMBLE)], axis=0)
            assert np.allclose(mine, theirs, atol=1e-4), np.abs(mine - theirs).max()
            print("numpy forward pass matches torch", flush=True)
    report = {
        "trained": time.strftime("%Y-%m-%d"),
        "data": {"source": "ERA5 reanalysis, hourly, 0.25 degree, via Open-Meteo",
                 "grid": "7 x 7 points, 12.25-13.75 N, 79.75-81.25 E (Chennai and the sea to its east)",
                 "seasons": f"September-December {years[0]}-{years[-1]}",
                 "train_windows": int(len(xtr)), "test_windows": int(len(xs)),
                 "test_seasons": list(TEST_YEARS), "validation_season": VAL_YEAR,
                 "input_hours": IN_H, "output_hours": OUT_H},
        "chosen": best, "ensemble": ENSEMBLE if best in makers else 1,
        "models": results, "michaung": chart,
        "seconds": round(time.perf_counter() - began),
    }
    (ROOT / "app" / "data" / "rain_models.json").write_text(json.dumps(report), encoding="utf-8")
    print(f"done in {report['seconds']} s", flush=True)


def _load(seed, weights):
    net = ConvLSTM()
    net.load_state_dict({k.split(".", 1)[1]: torch.tensor(v) for k, v in weights.items() if k.startswith(f"m{seed}.")})
    return net.eval()


if __name__ == "__main__":
    main()
