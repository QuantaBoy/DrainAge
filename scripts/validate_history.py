"""Check the flood model against Chennai's flood record, and pick the settings the
record supports.

    python scripts/validate_history.py            # -> docs/validation.md, app/data/validation.json

The physics model is run for three steady storms (30, 60 and 100 mm/h for three
hours) at each combination of the two settings that stand in for what the survey
cannot see - how much rain runs off (runoff coefficient) and how much of each drain's
surveyed capacity is working (drain condition). Each run is scored against:

  * the 753 places recorded flooded in December 2015: the share with a flooded street
    (5 cm or more) within 250 m - the hit rate - set against the share of random
    street points that have one - the chance rate. Hit rate minus chance rate is the
    skill: what the model knows beyond "a lot of the city is wet";
  * the 192 inundation points with a recorded depth: the rank correlation between the
    recorded depth and the deepest modelled street water within 100 m;
  * the flood hazard zones: how much more often streets in High and Very High zones
    flood in the model than streets outside any zone.

The 2015 points are split in two by corporation zone (odd and even): settings are
chosen on the odd zones and the skill reported is measured on the even ones, so the
choice is checked on places it did not see.

What this cannot check: the 2015 floods were made worse by the Chembarambakkam lake
release and the Adyar and Cooum overtopping their banks, which this model (rain and
drains, not rivers) does not simulate, and the record says where it flooded, not
where it stayed dry.
"""

import json
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from itertools import product
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402
from scipy.spatial import cKDTree  # noqa: E402
from scipy.stats import spearmanr  # noqa: E402

STORMS_MM_H = (30.0, 60.0, 100.0)
RUNOFF = (0.6, 0.75, 0.9)
DRAIN_CONDITION = (0.5, 0.75, 1.0)
WET_M = 0.05
HIT_RADIUS_M = 250.0
DEPTH_RADIUS_M = 100.0
CHANCE_SAMPLE = 5000


def _peak_levels(rate: float, runoff: float, drain: float) -> np.ndarray:
    """One storm in a worker: the highest water level each zone reaches."""
    from app import config
    from app.services import forecast

    steps = config.NOWCAST_HOURS * 60 // config.REPORT_STEP_MINUTES
    rain = {"rain_mm": [rate * config.REPORT_STEP_MINUTES / 60] * steps,
            "step_minutes": config.REPORT_STEP_MINUTES}
    system = forecast.coupled_system()
    from app.services import coupled
    run = coupled.simulate(system["surface"], system["network"], rain["rain_mm"],
                           rain["step_minutes"], runoff, drain)
    return run["levels"].max(axis=0)


def _worker_start() -> None:
    from app.services import forecast
    forecast.coupled_system()


def main() -> None:
    from app import config
    from app.services import forecast, history, surface

    if not history.available():
        sys.exit("No historical data: put the study's CSV files in 'Historical Flood/'.")
    began = time.perf_counter()
    model = forecast.coupled_system()["model"]
    _, coords, _, cell, _ = surface.street_cells(model)
    inside = cell >= 0
    street_xy = history.metres_xy(coords[inside, 1], coords[inside, 0])
    street_cell = cell[inside]
    signature = history.street_signature(model)
    hazard = signature["hazard"][inside]
    period = signature["period"][inside]

    pts = history.points_2015()
    pts_xy = history.metres_xy(pts["lat"], pts["lon"])
    zone_no = np.array([int(z) if z.isdigit() else 0 for z in pts["zone"]])
    calibrate, holdout = zone_no % 2 == 1, zone_no % 2 == 0
    rng = np.random.default_rng(26085)
    chance_xy = street_xy[rng.choice(len(street_xy), CHANCE_SAMPLE, replace=False)]
    dp = history.depth_points()
    dp_xy = history.metres_xy(dp["lat"], dp["lon"])

    combos = list(product(STORMS_MM_H, RUNOFF, DRAIN_CONDITION))
    print(f"{len(combos)} runs on the worker pool...", flush=True)
    with ProcessPoolExecutor(max_workers=config.FORECAST_WORKERS or 4, initializer=_worker_start) as pool:
        peaks = list(pool.map(_peak_levels, *zip(*combos)))

    results = []
    for (rate, runoff, drain), peak in zip(combos, peaks):
        depth = surface.depth_grid(model, peak).ravel()[street_cell]
        wet = depth >= WET_M
        wet_tree = cKDTree(street_xy[wet]) if wet.any() else None

        def hit_rate(xy: np.ndarray) -> float:
            if wet_tree is None:
                return 0.0
            dist, _ = wet_tree.query(xy, distance_upper_bound=HIT_RADIUS_M)
            return float(np.isfinite(dist).mean())

        chance = hit_rate(chance_xy)
        hits = {name: hit_rate(pts_xy[mask]) for name, mask in
                (("all", slice(None)), ("calibrate", calibrate), ("holdout", holdout))}

        # Recorded depth against the deepest modelled street water nearby.
        all_tree = cKDTree(street_xy)
        near = all_tree.query_ball_point(dp_xy, DEPTH_RADIUS_M)
        modelled = np.array([depth[i].max() if i else 0.0 for i in near])
        rho = spearmanr(dp["depth"], modelled).statistic if modelled.any() else float("nan")
        depth_hits = float((modelled >= WET_M).mean())

        high = hazard >= 4
        outside = hazard == 0
        often = (period > 0) & (period <= 25)
        results.append({
            "rain_mm_h": rate, "runoff_coeff": runoff, "drain_condition": drain,
            "wet_street_share": round(float(wet.mean()), 4),
            "hit_rate_2015": round(hits["all"], 3),
            "hit_rate_calibrate": round(hits["calibrate"], 3),
            "hit_rate_holdout": round(hits["holdout"], 3),
            "chance_rate": round(chance, 3),
            "skill_calibrate": round(hits["calibrate"] - chance, 3),
            "skill_holdout": round(hits["holdout"] - chance, 3),
            "depth_points_hit": round(depth_hits, 3),
            "depth_rank_correlation": round(float(rho), 3),
            "wet_share_high_hazard": round(float(wet[high].mean()), 4),
            "wet_share_no_hazard": round(float(wet[outside].mean()), 4),
            "hazard_enrichment": round(float(wet[high].mean() / max(wet[outside].mean(), 1e-9)), 2),
            "wet_share_1_in_25yr_extent": round(float(wet[often].mean()), 4) if often.any() else None,
        })

    report = {"generated_s": round(time.perf_counter() - began, 1), "runs": results,
              "settings_in_use": {"runoff_coeff": 0.75, "drain_condition": 1.0}}
    # The settings chosen on the odd zones, for each storm; judged on the even zones.
    report["chosen"] = {}
    for rate in STORMS_MM_H:
        runs = [r for r in results if r["rain_mm_h"] == rate]
        best = max(runs, key=lambda r: r["skill_calibrate"])
        current = next(r for r in runs if r["runoff_coeff"] == 0.75 and r["drain_condition"] == 1.0)
        report["chosen"][str(rate)] = {"best": best, "current": current}

    (config.DATA_DIR / "validation.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(json.dumps(report["chosen"], indent=1))
    print(f"done in {report['generated_s']} s")


if __name__ == "__main__":
    main()
