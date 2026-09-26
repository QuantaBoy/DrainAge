"""The street-level flood forecast: one storm through the coupled model, reported
street by street.

The chain, every minute of the next three hours:

  rainfall (app/services/rainfall.py)
      -> runoff on every terrain storage zone and every drain's strip
      -> caught by the drains' inlets and routed through the drain network, each drain
         carrying up to its surveyed capacity (app/services/coupled.py)
      -> what the drains cannot take surcharges out of the manholes
      -> the surface spreads it, zone to zone over the terrain, towards the sea
      -> a depth in centimetres, on a street, at a time.

The model is built once (and kept on disk); each storm is run once and kept, so the
map, the street table and the router all read the same run.
"""

import math
import re
import threading
from collections import OrderedDict
from typing import Any

import numpy as np
from scipy.spatial import cKDTree

from app import config
from app.errors import Unavailable
from app.services import channels, coupled, diskcache, drain_network, surface, terrain


def surface_model() -> dict[str, Any]:
    """The conditioned terrain and its storage zones over the study area."""
    south, west, north, east = config.CHENNAI_BBOX
    model = surface.load(south, north, west, east)
    if model is None:
        raise Unavailable("No DEM tiles in app/data")
    return model


# The surface zones and the drain network coupled onto them, built once.
_system: dict[str, Any] = {}
_system_lock = threading.Lock()


def coupled_system() -> dict[str, Any]:
    """The storage zones and the drain network coupled onto them, from disk when it can."""
    with _system_lock:
        if not _system:
            model = surface_model()
            drains, network_graph = drain_network.load_drains(), drain_network.drain_graph()
            inputs = [*drain_network.network_inputs(), *surface.model_inputs(),
                      *diskcache.sources(coupled)]
            zones, network = diskcache.cached(
                "coupled", inputs,
                lambda: (lambda s: (s, coupled.build_network(model, s, drains, network_graph)))(
                    coupled.build_surface(model)))
            _system.update({"model": model, "surface": zones, "network": network,
                            "summary": coupled.coupled_summary(network, zones)})
    return _system


def warm() -> None:
    """Build everything a first forecast needs, ahead of the first request."""
    system = coupled_system()
    surface.street_cells(system["model"])
    _place_index()


# A run is six seconds of CPU; the map, the street table and the router ask for the
# same storm, so each run is kept, and only one is computed at a time.
_runs: "OrderedDict[tuple, dict[str, Any]]" = OrderedDict()
_RUNS_KEPT = 12
_run_lock = threading.Lock()


def run_storm(rain: dict[str, Any], runoff_coeff: float, drain_condition: float) -> dict[str, Any]:
    """The coupled model for one storm, cached by its rain and settings."""
    key = (tuple(round(r, 4) for r in rain["rain_mm"]), round(runoff_coeff, 4), round(drain_condition, 4))
    with _run_lock:
        if key in _runs:
            _runs.move_to_end(key)
            return _runs[key]
        system = coupled_system()
        run = coupled.simulate(system["surface"], system["network"], rain["rain_mm"],
                               rain["step_minutes"], runoff_coeff, drain_condition)
        _runs[key] = run
        if len(_runs) > _RUNS_KEPT:
            _runs.popitem(last=False)
        return run


# A street is put in the ward of the nearest surveyed drain, if one is this close. The
# survey is the only ward layer the site has; further out the guess is not worth making.
PLACE_RADIUS_M = 400.0
_places: dict[str, Any] = {}


def _place_index() -> dict[str, Any]:
    """Drain outlets in a KD-tree, in metres, carrying the ward, zone and locality."""
    if not _places:
        drains = drain_network.load_drains()
        xy = np.array([d["outlet"] for d in drains], dtype=np.float64)
        scale = terrain.M_PER_DEG * math.cos(math.radians(13.0))
        _places.update({
            "tree": cKDTree(np.c_[xy[:, 0] * scale, xy[:, 1] * terrain.M_PER_DEG]),
            "scale": scale,
            "props": [{
                "ward": d["props"].get("WARD"),
                "zone": d["props"].get("ZONE"),
                # "Menambedu,Chennai." is Menambedu: the city is every row's suffix.
                "locality": re.sub(r"[\s,.]*chennai[\s.]*$", "", d["props"].get("LOCATION") or "",
                                   flags=re.I).strip(" ,.") or None,
            } for d in drains],
        })
    return _places


def _label_basins(stretches: list[dict[str, Any]]) -> None:
    """The river basin and named channel each stretch's water belongs to.

    Chennai's drainage is reported by basin - Kosasthalayar, Cooum, Adyar, Kovalam -
    and the basin model names every channel, so a flooded street can be placed in the
    system that is supposed to carry its water away.
    """
    if not stretches:
        return
    found = channels.nearest_many([tuple(f["properties"]["deepest"]) for f in stretches])
    for feature, near in zip(stretches, found):
        feature["properties"]["basin"] = near["subbasin"] if near else None
        feature["properties"]["channel"] = near["name"] if near else None
        feature["properties"]["channel_m"] = near["metres_away"] if near else None


def _basin_table(stretches: list[dict[str, Any]], impassable_cm: float) -> list[dict[str, Any]]:
    """One row per river basin: how much of it goes under, and how deep."""
    rows: dict[str, dict[str, Any]] = {}
    for feature in stretches:
        p = feature["properties"]
        name = p.get("basin") or "outside the mapped basins"
        row = rows.setdefault(name, {"basin": name, "stretches": 0, "wet_m": 0.0,
                                     "max_depth_cm": 0.0, "impassable": 0, "streets": set()})
        row["stretches"] += 1
        row["wet_m"] += p["wet_m"]
        row["max_depth_cm"] = max(row["max_depth_cm"], p["max_depth_cm"])
        row["impassable"] += p["max_depth_cm"] >= impassable_cm
        if p["name"]:
            row["streets"].add((p["name"], p.get("ward")))
    table = []
    for row in rows.values():
        row["named_streets"] = len(row.pop("streets"))
        row["wet_km"] = round(row.pop("wet_m") / 1000, 2)
        table.append(row)
    table.sort(key=lambda r: r["wet_km"], reverse=True)
    return table


def _label_places(stretches: list[dict[str, Any]]) -> None:
    """Ward, zone and locality on every stretch, from the drain nearest its deepest point."""
    if not stretches:
        return
    index = _place_index()
    points = np.array([f["properties"]["deepest"] for f in stretches])       # lat, lon
    distance, nearest = index["tree"].query(
        np.c_[points[:, 1] * index["scale"], points[:, 0] * terrain.M_PER_DEG])
    for feature, metres, i in zip(stretches, distance, nearest):
        place = index["props"][i] if metres <= PLACE_RADIUS_M else {"ward": None, "zone": None, "locality": None}
        feature["properties"].update(place)


def _street_table(stretches: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """One row per street per ward: when it goes under, how deep, and when it clears.

    Grouping is by name and ward, not name alone: Chennai has a "5th Street" in dozens
    of neighbourhoods, and merging them would put one depth on streets kilometres apart.
    A street floods when its first stretch does and clears when its last one does.
    """
    def earliest(a: int | None, b: int | None) -> int | None:
        return b if a is None else a if b is None else min(a, b)

    rows: dict[tuple, dict[str, Any]] = {}
    for feature in stretches:
        p = feature["properties"]
        key = (p["name"], p["ward"] or f"{p['deepest'][0]:.2f},{p['deepest'][1]:.2f}")
        row = rows.get(key)
        if row is None:
            row = rows[key] = {
                "street": p["name"], "highway": p["highway"],
                "ward": p["ward"], "zone": p["zone"], "locality": p["locality"],
                "max_depth_cm": 0.0, "wet_m": 0.0, "stretches": 0, "_depth_x_m": 0.0,
                "floods_at_min": None, "impassable_at_min": None,
                "clears_at_min": 0, "series_cm": p["series_cm"],
            }
        row["stretches"] += 1
        row["wet_m"] += p["wet_m"]
        row["_depth_x_m"] += p["mean_depth_cm"] * p["wet_m"]
        row["floods_at_min"] = earliest(row["floods_at_min"], p["floods_at_min"])
        row["impassable_at_min"] = earliest(row["impassable_at_min"], p["impassable_at_min"])
        # Still wet at the end of the window anywhere means still wet, full stop.
        row["clears_at_min"] = (None if row["clears_at_min"] is None or p["clears_at_min"] is None
                                else max(row["clears_at_min"], p["clears_at_min"]))
        row["series_cm"] = [max(a, b) for a, b in zip(row["series_cm"], p["series_cm"])]
        if p["max_depth_cm"] > row["max_depth_cm"]:
            row["max_depth_cm"] = p["max_depth_cm"]
            row["deepest"] = p["deepest"]
            row["peak_at_min"] = p["peak_at_min"]
    table = []
    for row in rows.values():
        row["mean_depth_cm"] = round(row.pop("_depth_x_m") / row["wet_m"], 1) if row["wet_m"] else 0.0
        row["wet_m"] = round(row["wet_m"], 0)
        table.append(row)
    # Soonest under first: the order a control room works through them.
    table.sort(key=lambda r: (r["floods_at_min"] if r["floods_at_min"] is not None else 10**6,
                              -r["max_depth_cm"]))
    return table


# A manhole is reported once this much water has come out of it over the window.
SURCHARGE_REPORT_M3 = 1.0


def _manholes(run: dict[str, Any], step_minutes: float, zone: str | None,
              ward: str | None) -> list[dict[str, Any]]:
    """The junctions the drains surcharge out of: where, how much, when, how deep."""
    system = coupled_system()
    network, drains = drain_network.drain_graph(), drain_network.load_drains()
    carrier = network["graph"]["carrier"]
    points = network["points"]
    total = run["surcharge_m3"].sum(axis=0)
    step_s = step_minutes * 60
    out = []
    for node in np.flatnonzero(total >= SURCHARGE_REPORT_M3).tolist():
        props = drains[carrier[node]]["props"] if node in carrier else {}
        if (zone and props.get("ZONE") != zone) or (ward and props.get("WARD") != ward):
            continue
        series = run["surcharge_m3"][:, node]
        depth = run["node_depth_m"][:, node]
        first = int(np.flatnonzero(series > 0)[0])
        out.append({
            "type": "Feature",
            "geometry": {"type": "Point", "coordinates": [round(points[node][0], 6), round(points[node][1], 6)]},
            "properties": {
                "node": node,
                # The drain the water is trying to get into: the one that is full.
                "street": props.get("ST_NAME"), "location": props.get("LOCATION"),
                "ward": props.get("WARD"), "zone": props.get("ZONE"),
                "ground_m": None if not math.isfinite(system["network"]["ground"][node])
                else round(float(system["network"]["ground"][node]), 2),
                "surcharge_m3": round(float(total[node]), 1),
                "peak_l_s": round(float(series.max()) / step_s * 1000, 1),
                "surcharge_l_s": np.round(series / step_s * 1000, 1).tolist(),
                "depth_cm": np.round(depth * 100, 1).tolist(),
                "starts_at_min": int(round((first + 1) * step_minutes)),
            },
        })
    out.sort(key=lambda f: f["properties"]["surcharge_m3"], reverse=True)
    return out


def street_forecast(rain: dict[str, Any], runoff_coeff: float, drain_condition: float,
                    min_depth_cm: float, zone: str | None, ward: str | None) -> dict[str, Any]:
    """One storm, reported street by street: the whole response the page draws from.

    `rain` is what app.services.rainfall.report_steps returns. Runs in a worker thread:
    the first run of a storm takes seconds of CPU.
    """
    impassable = config.IMPASSABLE_DEPTH_M * 100
    result = run_storm(rain, runoff_coeff, drain_condition)
    system = coupled_system()
    stretches, water = surface.flooded_streets(
        system["model"], result["levels"], rain["step_minutes"], min_depth_cm / 100,
        config.IMPASSABLE_DEPTH_M)
    _label_places(stretches)
    _label_basins(stretches)
    if zone or ward:
        stretches = [f for f in stretches
                     if (not zone or f["properties"]["zone"] == zone)
                     and (not ward or f["properties"]["ward"] == ward)]
        used = {str(z) for f in stretches for z in f["properties"]["storage"] if z}
        water = {z: v for z, v in water.items() if z in used}
    manholes = _manholes(result, rain["step_minutes"], zone, ward)
    table = _street_table(stretches)
    named = [r for r in table if r["street"]]
    # Per step: how many named streets are under, and how many are impassable.
    per_step = [
        {"wet": sum(1 for r in named if r["series_cm"][i] >= min_depth_cm),
         "impassable": sum(1 for r in named if r["series_cm"][i] >= impassable)}
        for i in range(len(rain["rain_mm_h"]))
    ]
    return {
        "type": "FeatureCollection",
        "features": stretches,
        "streets": table,
        "manholes": {"type": "FeatureCollection", "features": manholes},
        # Chennai's drainage is organised by river basin; this says which of them the
        # flooding is in, from the basin model's own channel layer.
        "basins": _basin_table(stretches, impassable),
        # Water level in every zone a stretch touches, one per step, null while dry:
        # depth at a vertex at step i is water[zone][i] - ground_m.
        "water_level_m": water,
        "rain": {key: value for key, value in rain.items() if key != "rain_mm"},
        "runoff_coeff": runoff_coeff,
        "drain_condition": drain_condition,
        "model": {
            **system["summary"],
            "coupling_step_s": coupled.COUPLE_S,
            "surface_step_s": coupled.COUPLE_S / coupled.SURFACE_SUBSTEPS,
        },
        # Where every cubic metre of runoff went, and how well the books close.
        "balance_m3": result["balance"],
        "stretches": len(stretches),
        "named_streets": len(named),
        "impassable_streets": sum(1 for r in named if r["max_depth_cm"] >= impassable),
        "wet_km": round(sum(r["wet_m"] for r in table) / 1000, 2),
        "surcharging_manholes": len(manholes),
        "per_step": per_step,
        "thresholds": {"reported_cm": min_depth_cm, "impassable_cm": impassable},
    }


def road_depths_cm(rain: dict[str, Any], runoff_coeff: float, drain_condition: float,
                   first_step: int, last_step: int) -> np.ndarray:
    """The deepest water on every road of the street graph over a window of steps, cm."""
    from app.services import routing

    model = coupled_system()["model"]
    levels = run_storm(rain, runoff_coeff, drain_condition)["levels"]
    worst = levels[first_step:last_step + 1].max(axis=0)
    return routing.edge_depths_cm(model, surface.depth_grid(model, worst))
