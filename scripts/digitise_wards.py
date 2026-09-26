"""Read the GCC / SECON-JBA ward base-map sheets (Ward/*.pdf) into data.

Each sheet is a vector CAD plot of one ward's storm water drains, surveyed December
2020 in UTM zone 44N (WGS 84) with levels above mean sea level. It carries, drawn as
line strokes rather than text:

  * a LIST OF POINTS: every manhole, whether its drain is closed or open, the drain
    top, invert and road-edge levels (m above MSL), and the drain's width and depth;
  * a LIST OF CONTROL POINTS: surveyed benchmarks with latitude, longitude, UTM
    easting / northing and MSL level;
  * the map itself: each manhole drawn where it is, labelled with its id and invert,
    on a UTM grid.

Because the lettering is strokes, it is read by OCR. Step one renders every sheet in
tiles and caches what the OCR saw (slow, once); step two parses the cache into
app/data/ward_points.json (fast, rerun freely).

    python scripts/digitise_wards.py ocr        # all sheets, cached under .ward_ocr/
    python scripts/digitise_wards.py parse      # -> app/data/ward_points.json

Needs, for this tool only (not the website): pip install pymupdf rapidocr_onnxruntime
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WARD_DIR = ROOT / "Ward"
CACHE = ROOT / ".ward_ocr"
OUT = ROOT / "app" / "data" / "ward_points.json"

DPI = 200
TILE_PX = 2000
OVERLAP_PX = 200


def ocr_sheet(path: Path, engine) -> list[dict]:
    """Every piece of lettering on one sheet, in PDF points (72 per inch)."""
    import pymupdf

    page = pymupdf.open(path)[0]
    scale = DPI / 72
    width, height = page.rect.width * scale, page.rect.height * scale
    step = TILE_PX - OVERLAP_PX
    seen: dict[tuple, dict] = {}
    for top in range(0, int(height), step):
        for left in range(0, int(width), step):
            clip = pymupdf.Rect(left / scale, top / scale, (left + TILE_PX) / scale, (top + TILE_PX) / scale)
            pix = page.get_pixmap(dpi=DPI, clip=clip)
            result, _ = engine(pix.tobytes("png"))
            for box, text, conf in result or []:
                xs = [p[0] for p in box]
                ys = [p[1] for p in box]
                x0, x1 = (min(xs) + left) / scale, (max(xs) + left) / scale
                y0, y1 = (min(ys) + top) / scale, (max(ys) + top) / scale
                # The same word read in two overlapping tiles is kept once.
                key = (text, round((x0 + x1) / 2 / 4), round((y0 + y1) / 2 / 4))
                if key not in seen or seen[key]["conf"] < conf:
                    seen[key] = {"text": text.strip(), "x0": x0, "y0": y0, "x1": x1, "y1": y1,
                                 "conf": round(float(conf), 3)}
    return list(seen.values())


def run_ocr(names: list[str]) -> None:
    from rapidocr_onnxruntime import RapidOCR

    engine = RapidOCR()
    CACHE.mkdir(exist_ok=True)
    sheets = sorted(p for p in WARD_DIR.glob("*.pdf") if re.search(r"\d", p.stem))
    for path in sheets:
        if names and path.name not in names:
            continue
        target = CACHE / (path.stem + ".json")
        if target.exists():
            continue
        words = ocr_sheet(path, engine)
        target.write_text(json.dumps(words), encoding="utf-8")
        print(f"{path.name}: {len(words)} words", flush=True)


# ─── Parsing ─────────────────────────────────────────────────────────────────────

# What the OCR makes of the dash the tables print for "not measured".
DASHES = {"二", "一", "-", "—", "–", "_", "~", "=", "ー"}
ID_RE = re.compile(r"^([A-Z]{1,3}\d{1,3})(\s*\(OL\))?$")
VALUE_RE = re.compile(r"^-?\d{1,2}\.\d{1,3}$")
# The colour the UTM grid lines are drawn in (RGB, 0-1).
GRID_GREY = 0.502


def _drop_fragments(words: list[dict]) -> list[dict]:
    """Where tiles overlap, OCR can read "1.100" in one and "100" in the other."""
    words = sorted(words, key=lambda w: w["y0"])
    keep = []
    for i, a in enumerate(words):
        inside = False
        for b in words[max(0, i - 40):i + 40]:
            if b is a or len(b["text"]) <= len(a["text"]) or a["text"] not in b["text"]:
                continue
            if b["x0"] - 1.5 <= a["x0"] and a["x1"] <= b["x1"] + 1.5 and b["y0"] - 1.5 <= a["y0"] and a["y1"] <= b["y1"] + 1.5:
                inside = True
                break
        if not inside:
            keep.append(a)
    return keep


def _lines(words: list[dict], tolerance: float = 3.0) -> list[list[dict]]:
    """Words grouped into printed lines, each read left to right."""
    words = sorted(words, key=lambda w: (w["y0"] + w["y1"]) / 2)
    lines, current = [], []
    for w in words:
        yc = (w["y0"] + w["y1"]) / 2
        if current and yc - current[-1]["_yc"] > tolerance:
            lines.append(sorted(current, key=lambda v: v["x0"]))
            current = []
        w["_yc"] = yc
        current.append(w)
    if current:
        lines.append(sorted(current, key=lambda v: v["x0"]))
    return lines


def _value(text: str) -> tuple[bool, float | None]:
    """(readable, value): a number, a printed dash (None), or unreadable."""
    t = text.strip()
    if t in DASHES:
        return True, None
    if VALUE_RE.match(t):
        return True, float(t)
    return False, None


def parse_points(words: list[dict]) -> tuple[list[dict], set[int]]:
    """Rows of the LIST OF POINTS tables, and the ids of the words they used.

    The sheets print two or three tables side by side whose rows do not line up, so a
    row is read from its manhole id: the words level with it, to its right, up to the
    next id.
    """
    points, used = [], set()
    for w in words:
        match = ID_RE.match(w["text"])
        if not match:
            continue
        yc = (w["y0"] + w["y1"]) / 2
        row = sorted((v for v in words if v is not w and w["x1"] - 1 < v["x0"] < w["x1"] + 420
                      and abs((v["y0"] + v["y1"]) / 2 - yc) < 2.5), key=lambda v: v["x0"])
        stop = next((k for k, v in enumerate(row) if ID_RE.match(v["text"])), len(row))
        row = row[:stop]
        if len(row) < 6 or row[0]["text"].upper() not in ("C", "O", "0"):
            continue
        read = [_value(c["text"]) for c in row[1:6]]
        if not all(ok for ok, _ in read):
            continue
        top, invert, road, width, depth = (v for _, v in read)
        points.append({"id": match.group(1), "outlet": bool(match.group(2)),
                       "closed": row[0]["text"].upper() == "C", "top_m": top, "invert_m": invert,
                       "road_edge_m": road, "width_m": width, "depth_m": depth})
        used.update(id(c) for c in [w, *row[:6]])
    # One row per manhole: a second reading of the same id (OCR at a tile seam) is dropped.
    unique = {}
    for p in points:
        unique.setdefault(p["id"], p)
    return list(unique.values()), used


def parse_controls(lines: list[list[dict]]) -> list[dict]:
    """Surveyed control points: UTM easting, northing and MSL level."""
    out = []
    for line in lines:
        texts = [w["text"] for w in line]
        east = next((float(t) for t in texts if re.fullmatch(r"[2-5]\d{5}\.\d{1,3}", t)), None)
        north = next((float(t) for t in texts if re.fullmatch(r"1[3-4]\d{5}\.\d{1,3}", t)), None)
        if east is None or north is None:
            continue
        after = texts[texts.index(next(t for t in texts if re.fullmatch(r"1[3-4]\d{5}\.\d{1,3}", t))) + 1:]
        level = next((float(t) for t in after if re.fullmatch(r"-?\d{1,2}\.\d{1,3}", t)), None)
        name = next((t for t in texts if re.fullmatch(r"PR\s?\d+", t)), None)
        out.append({"name": name, "easting": east, "northing": north, "msl_m": level})
    return out


def _circles_in(items: list) -> list[tuple[float, float]]:
    """Manhole circles in one drawing path: four or more Bezier arcs closing on
    themselves, 2-4 points across, whether in their own path or inside a drain line's.

    Polygon "circles" are not accepted: the lettering is drawn as strokes too, and an
    "o" or "0" is a small closed polygon just the same.
    """
    found, run = [], []
    for item in items + [("end",)]:
        if item[0] == "c":
            run.extend(item[1:5])
            continue
        if len(run) >= 16:
            xs, ys = [q.x for q in run], [q.y for q in run]
            w, h = max(xs) - min(xs), max(ys) - min(ys)
            if 2.0 < w < 4.0 and abs(w - h) < 0.5:
                found.append(((max(xs) + min(xs)) / 2, (max(ys) + min(ys)) / 2))
        run = []
    return found


def _displayed_drawings(path: Path) -> list[dict]:
    """The page's vector paths in displayed coordinates, the ones the OCR read in.

    16 sheets are stored rotated 270 degrees: their drawing commands are in the
    unrotated frame, and read as they are, the grid, the manholes and the drains land
    hundreds of points from their own labels (sheet 105's grid looked 350 m out).
    """
    import pymupdf

    if _displayed_drawings.cache[0] == path:
        return _displayed_drawings.cache[1]
    page = pymupdf.open(path)[0]
    drawings = page.get_drawings()
    if page.rotation:
        turn = page.rotation_matrix
        drawings = [{**d, "items": [(item[0], *[q * turn if isinstance(q, pymupdf.Point) else q
                                               for q in item[1:]]) for item in d["items"]]}
                    for d in drawings]
    # One sheet's drawings, kept while that sheet is being read.
    _displayed_drawings.cache = (path, drawings)
    return drawings


_displayed_drawings.cache = (None, None)


def _page_geometry(path: Path) -> dict:
    """Manhole circles and the long straight grid lines, from the vector drawing."""
    import pymupdf

    circles, vertical, horizontal = [], [], []
    for d in _displayed_drawings(path):
        circles.extend(_circles_in(d["items"]))
        # The UTM grid is drawn in thin mid-grey right across the map; table rules and
        # streets are black or coloured, so the colour is what tells a grid line apart.
        colour = d.get("color") or (0, 0, 0)
        if not all(abs(c - GRID_GREY) < 0.03 for c in colour):
            continue
        for item in d["items"]:
            if item[0] != "l":
                continue
            p, q = item[1], item[2]
            if abs(p.x - q.x) < 0.2 and abs(p.y - q.y) > 300:
                vertical.append((p.x + q.x) / 2)
            elif abs(p.y - q.y) < 0.2 and abs(p.x - q.x) > 300:
                horizontal.append((p.y + q.y) / 2)
    # A circle drawn twice (outline and fill) is one manhole.
    unique = {(round(x, 1), round(y, 1)) for x, y in circles}
    return {"circles": sorted(unique), "vertical": sorted(set(round(v, 2) for v in vertical)),
            "horizontal": sorted(set(round(v, 2) for v in horizontal))}


GRID_STEP_M = 500.0


# Every grid number is printed this far to one side of its grid line (measured on
# sheets 100, 104, 106, 109 and 33: 6.2 +- 0.3 pt, on either side).
LABEL_OFFSET_PT = 6.2
# The sheets are plotted at one scale: 1.7637 m of ground per PDF point.
SCALE_M_PER_PT = 1.7637


def georeference(words: list[dict], geometry: dict) -> dict | None:
    """Sheet points to UTM from the grid: easting = a + b x, northing = c + d y.

    The printed grid numbers are the truth: on sheet 105 the latitude graticule agrees
    with them and not with the grey lines, which there are drawn 350 m out. A grey line
    counts only where a number sits LABEL_OFFSET_PT beside it; then the fit is exact.
    Where no line does, the numbers' own positions are fitted, to about 10 m, and the
    result says which it is.
    """
    import numpy as np

    east_lab, north_lab = [], []
    for w in words:
        t = w["text"].replace(" ", "")
        xc, yc = (w["x0"] + w["x1"]) / 2, (w["y0"] + w["y1"]) / 2
        # Some sheets print the grid as "414000", others as "E414000" / "N1447000", and
        # some draw it on the 250 m offsets (416250). Corner annotations such as N1441800
        # are not grid values and are left out.
        east = re.fullmatch(r"E?([2-5]\d{2}(?:000|250|500|750))", t)
        north = re.fullmatch(r"N?(1[3-4]\d{2}(?:000|250|500|750))", t)
        if east:
            east_lab.append((xc, float(east.group(1))))
        elif north:
            north_lab.append((yc, float(north.group(1))))

    def fit(pos: np.ndarray, val: np.ndarray, sign: float) -> tuple | None:
        if len(set(val.tolist())) < 2:
            return None
        b, a = np.polyfit(pos, val, 1)
        # One scale for the whole plot; anything else is a misread number.
        if abs(abs(b) - SCALE_M_PER_PT) > 0.01 * SCALE_M_PER_PT or np.sign(b) != sign:
            return None
        return float(a), float(b), float(np.abs(a + b * pos - val).max())

    def axis(labels: list[tuple], lines: list[float], sign: float) -> tuple | None:
        if not labels:
            return None
        snapped = []
        for p, v in labels:
            near = [g for g in lines if abs(abs(g - p) - LABEL_OFFSET_PT) < 1.2]
            if near:
                snapped.append((min(near, key=lambda g: abs(abs(g - p) - LABEL_OFFSET_PT)), v))
        if snapped:
            pos, val = np.array(sorted(set(snapped))).T
            exact = fit(pos, val, sign)
            if exact and exact[2] < 2.0:
                return (*exact, "grid lines")
        # No line to snap to: the numbers themselves, dropping any that disagree.
        pos, val = np.array(labels).T
        rough = fit(pos, val, sign)
        if rough is None:
            return None
        keep = np.abs(rough[0] + rough[1] * pos - val) < 25.0
        refined = fit(pos[keep], val[keep], sign) if keep.sum() >= 2 else None
        return (*refined, "grid numbers") if refined else None

    ex = axis(east_lab, geometry["vertical"], 1.0)
    ny = axis(north_lab, geometry["horizontal"], -1.0)
    if ex is None or ny is None:
        return None
    return {
        "east": [ex[0], ex[1]], "north": [ny[0], ny[1]],
        "m_per_pt": [round(ex[1], 4), round(-ny[1], 4)],
        "residual_m": round(max(ex[2], ny[2]), 3),
        "from": sorted({ex[3], ny[3]}),
    }


def utm_to_latlon(easting: float, northing: float, zone: int = 44) -> tuple[float, float]:
    """Inverse transverse Mercator on WGS 84, northern hemisphere (Snyder 1987)."""
    import math

    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    x = easting - 500000.0
    m = northing / k0
    mu = m / (a * (1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256))
    e1 = (1 - math.sqrt(1 - e2)) / (1 + math.sqrt(1 - e2))
    phi1 = (mu + (3 * e1 / 2 - 27 * e1 ** 3 / 32) * math.sin(2 * mu)
            + (21 * e1 ** 2 / 16 - 55 * e1 ** 4 / 32) * math.sin(4 * mu)
            + (151 * e1 ** 3 / 96) * math.sin(6 * mu) + (1097 * e1 ** 4 / 512) * math.sin(8 * mu))
    c1 = ep2 * math.cos(phi1) ** 2
    t1 = math.tan(phi1) ** 2
    n1 = a / math.sqrt(1 - e2 * math.sin(phi1) ** 2)
    r1 = a * (1 - e2) / (1 - e2 * math.sin(phi1) ** 2) ** 1.5
    d = x / (n1 * k0)
    lat = phi1 - (n1 * math.tan(phi1) / r1) * (
        d ** 2 / 2 - (5 + 3 * t1 + 10 * c1 - 4 * c1 ** 2 - 9 * ep2) * d ** 4 / 24
        + (61 + 90 * t1 + 298 * c1 + 45 * t1 ** 2 - 252 * ep2 - 3 * c1 ** 2) * d ** 6 / 720)
    lon = (d - (1 + 2 * t1 + c1) * d ** 3 / 6
           + (5 - 2 * c1 + 28 * t1 - 3 * c1 ** 2 + 8 * ep2 + 24 * t1 ** 2) * d ** 5 / 120) / math.cos(phi1)
    return math.degrees(lat), (zone - 1) * 6 - 180 + 3 + math.degrees(lon)


def parse_map_labels(lines: list[list[dict]], used: set[int], circles: list[tuple]) -> dict[str, dict]:
    """Manhole labels on the map: the id, the invert printed under it, and its circle."""
    import numpy as np

    words = [w for line in lines for w in line if id(w) not in used]
    values = [w for w in words if VALUE_RE.match(w["text"])]
    centres = np.array(circles) if circles else np.zeros((0, 2))
    found: dict[str, dict] = {}
    for w in words:
        match = ID_RE.match(w["text"])
        if not match:
            continue
        # The invert is printed just below the id.
        below = [v for v in values if 0 < v["y0"] - w["y0"] < 14 and abs(v["x0"] - w["x0"]) < 20]
        level = float(min(below, key=lambda v: v["y0"] - w["y0"])["text"]) if below else None
        xc, yc = (w["x0"] + w["x1"]) / 2, w["y1"]
        circle = None
        if centres.size:
            dist = np.hypot(centres[:, 0] - xc, centres[:, 1] - yc)
            k = int(dist.argmin())
            if dist[k] < 12:
                circle = (float(centres[k, 0]), float(centres[k, 1]), float(dist[k]))
        # No symbol found beside it: the label stands in, within about 10 points of it.
        position = (circle[0], circle[1], "manhole symbol") if circle else (xc, (w["y0"] + w["y1"]) / 2, "label")
        label = {"invert_label_m": level, "circle": circle, "position": position}
        # An id printed twice on one sheet keeps the reading with a circle.
        if match.group(1) not in found or (circle and not found[match.group(1)]["circle"]):
            found[match.group(1)] = label
    return found


# ─── The drawn network ───────────────────────────────────────────────────────────

# How each sheet draws a storm water drain: blue, and pink where the survey found a
# reverse gradient (the pink rows of its table). Colour (RGB 0-1) and line width, pt.
DRAIN_BLUE = ((0.0, 0.0, 1.0), 0.84)
DRAIN_PINK = ((1.0, 0.5, 0.62), 0.96)
# Line ends closer than this are one vertex - a drain drawn up to the rim of a manhole
# circle (1.4 pt across its radius) meets the one leaving it; a circle, or another
# drain's end, this close to a line is on it.
VERTEX_SNAP_PT = 1.6
ON_LINE_PT = 1.6
# A manhole label sits beside its manhole; one found no circle is put on the nearest
# drain line within this distance.
LABEL_TO_LINE_PT = 14.0
# A section is carried along the network from the nearest measured one, this far at most.
CARRY_SECTION_M = 500.0


def _drain_segments(path: Path) -> list[tuple]:
    """Every straight piece of every drawn drain: x0, y0, x1, y1, drawn pink."""
    import pymupdf

    out = []
    for d in _displayed_drawings(path):
        key = (tuple(round(c, 2) for c in (d.get("color") or ())), round(d.get("width") or 0, 2))
        if key not in (DRAIN_BLUE, DRAIN_PINK):
            continue
        for item in d["items"]:
            if item[0] == "l":
                a, b = item[1], item[2]
            elif item[0] == "c":
                a, b = item[1], item[4]         # a gentle drawn curve, taken as its chord
            else:
                continue
            if abs(a - b) > 1e-6:
                out.append((a.x, a.y, b.x, b.y, key == DRAIN_PINK))
    return out


# A flow arrow is a black chevron of two strokes, each this long, drawn on the drain.
ARROW_ARM_PT = (2.5, 8.0)
ARROW_ON_LINE_PT = 3.0


def _flow_arrows(path: Path) -> list[tuple[float, float, float, float]]:
    """The survey's own flow arrows: apex x, y and the unit direction they point.

    Each is a black 0.6 pt path of two strokes meeting at the apex; the arrow points
    from the middle of its two open ends to the apex.
    """
    import math

    out = []
    for d in _displayed_drawings(path):
        if tuple(round(c, 2) for c in (d.get("color") or ())) != (0.0, 0.0, 0.0):
            continue
        if round(d.get("width") or 0, 2) != 0.6 or len(d["items"]) != 2:
            continue
        if any(it[0] != "l" for it in d["items"]):
            continue
        (_, p0, p1), (_, q0, q1) = (it[:3] for it in d["items"])
        if abs(p1 - q0) > 0.05:
            continue
        arms = abs(p1 - p0), abs(q1 - q0)
        if not all(ARROW_ARM_PT[0] <= a <= ARROW_ARM_PT[1] for a in arms):
            continue
        apex = p1
        base = ((p0.x + q1.x) / 2, (p0.y + q1.y) / 2)
        dx, dy = apex.x - base[0], apex.y - base[1]
        norm = math.hypot(dx, dy)
        opening = abs(p0 - q1)
        if norm < 1.0 or opening < 1.0:          # a straight stroke, not a chevron
            continue
        out.append((apex.x, apex.y, dx / norm, dy / norm))
    return out


def sheet_network(path: Path, circles: list, labels: dict, by_id: dict, m_per_pt: float,
                  words: list[dict] | None = None) -> dict:
    """The drain network a sheet draws, from manhole to manhole, in sheet points.

    Drains are the blue and pink lines; manholes are the circles on them, split into
    the line where they sit part-way along a piece. Nodes are the manholes, the line
    junctions and the line ends; an edge is the drawn drain between two nodes, with its
    own drawn path and length. A manhole carries its table row (invert, section, closed
    or open) through its map label.
    """
    import numpy as np
    from scipy.spatial import cKDTree

    segs = _drain_segments(path)
    if not segs:
        return {"nodes": [], "edges": []}
    # Where a sheet draws its manholes in a way the circle finder misses, the label
    # still sits beside its manhole: the manhole is the nearest point on a drain line.
    seg_a = np.array([s[:2] for s in segs])
    seg_ab = np.array([s[2:4] for s in segs]) - seg_a
    seg_l2 = np.maximum((seg_ab ** 2).sum(1), 1e-12)
    markers = [tuple(c) for c in circles]
    marker_of: dict[str, tuple] = {}
    # Every place an id is printed - the table's own id column among them - and the
    # one beside a drain line is the map label.
    printed: dict[str, list[tuple[float, float]]] = {}
    for w in words or []:
        match = ID_RE.match(w["text"])
        if match and match.group(1) in by_id:
            printed.setdefault(match.group(1), []).append(((w["x0"] + w["x1"]) / 2, (w["y0"] + w["y1"]) / 2))
    for mid in by_id:
        if labels.get(mid, {}).get("circle"):
            continue
        best = None
        for x, y in printed.get(mid, []):
            u = np.clip(((np.array([x, y]) - seg_a) * seg_ab).sum(1) / seg_l2, 0.0, 1.0)
            foot = seg_a + seg_ab * u[:, None]
            dist = np.sqrt(((foot - (x, y)) ** 2).sum(1))
            k = int(dist.argmin())
            if dist[k] < LABEL_TO_LINE_PT and (best is None or dist[k] < best[0]):
                best = (float(dist[k]), (float(foot[k, 0]), float(foot[k, 1])))
        if best:
            marker_of[mid] = best[1]
            markers.append(best[1])
    circles = markers
    # Line ends split the lines they touch too: a side drain drawn up to a trunk meets
    # it part-way along a straight piece, where the trunk has no vertex of its own.
    line_ends = np.array([s[:2] for s in segs] + [s[2:4] for s in segs])
    centres = np.vstack([np.array(circles).reshape(-1, 2), line_ends])
    pieces = []
    for x0, y0, x1, y1, pink in segs:
        a, b = np.array([x0, y0]), np.array([x1, y1])
        ab = b - a
        cuts = [0.0, 1.0]
        if centres.size:
            u = np.clip(((centres - a) @ ab) / float(ab @ ab), 0.0, 1.0)
            off = np.sqrt(((centres - (a + np.outer(u, ab))) ** 2).sum(1))
            cuts = [0.0, *sorted(u[(off < ON_LINE_PT) & (u > 0.02) & (u < 0.98)].tolist()), 1.0]
        for t0, t1 in zip(cuts, cuts[1:]):
            p0, p1 = a + ab * t0, a + ab * t1
            pieces.append((p0, p1, pink))

    ends = np.array([p0 for p0, _, _ in pieces] + [p1 for _, p1, _ in pieces])
    tree = cKDTree(ends)
    vertex = -np.ones(len(ends), dtype=int)
    points = []
    for i in range(len(ends)):
        if vertex[i] >= 0:
            continue
        group = [g for g in tree.query_ball_point(ends[i], VERTEX_SNAP_PT) if vertex[g] < 0]
        vertex[group] = len(points)
        points.append(ends[group].mean(axis=0))
    points = np.array(points)
    count = len(pieces)
    around: dict[int, list[tuple[int, int]]] = {}
    for k, (_, _, pink) in enumerate(pieces):
        a, b = int(vertex[k]), int(vertex[k + count])
        if a != b:
            around.setdefault(a, []).append((b, k))
            around.setdefault(b, []).append((a, k))

    near = cKDTree(points)
    manhole: dict[int, str | None] = {}
    for cx, cy in circles:
        dist, v = near.query((cx, cy))
        if dist < ON_LINE_PT:
            manhole.setdefault(int(v), None)
    for mid, label in labels.items():
        spot = label["circle"][:2] if label["circle"] else marker_of.get(mid)
        if spot is not None and mid in by_id:
            dist, v = near.query(spot)
            if dist < ON_LINE_PT:
                manhole[int(v)] = mid

    keys = set(manhole) | {v for v, nb in around.items() if len(nb) != 2}
    taken: set[int] = set()
    edges = []
    for start in sorted(keys):
        for first, piece in around.get(start, []):
            if piece in taken:
                continue
            taken.add(piece)
            path_pts, pink, current = [points[start], points[first]], pieces[piece][2], first
            while current not in keys:
                onward = [(v, k) for v, k in around[current] if k not in taken]
                if not onward:
                    break
                current, piece = onward[0]
                taken.add(piece)
                pink = pink or pieces[piece][2]
                path_pts.append(points[current])
            line = np.array(path_pts)
            length = float(np.sqrt((np.diff(line, axis=0) ** 2).sum(1)).sum()) * m_per_pt
            if current != start and length > 0.5:
                edges.append({"a": start, "b": int(current), "xy": line, "length_m": length, "pink": pink})

    used = sorted({e["a"] for e in edges} | {e["b"] for e in edges})
    index = {v: i for i, v in enumerate(used)}
    nodes = []
    for v in used:
        row = by_id.get(manhole.get(v)) if manhole.get(v) else None
        nodes.append({"xy": points[v], "id": manhole.get(v), "manhole": v in manhole,
                      "invert_m": row["invert_m"] if row else None,
                      "top_m": row["top_m"] if row else None,
                      "road_edge_m": row["road_edge_m"] if row else None,
                      "width_m": row["width_m"] if row else None,
                      "depth_m": row["depth_m"] if row else None,
                      "closed": row["closed"] if row else None,
                      "outlet": bool(row and row["outlet"])})
    for e in edges:
        e["a"], e["b"] = index[e["a"]], index[e["b"]]
    _fill_inverts(nodes, edges)
    _fill_sections(nodes, edges)
    _read_arrows(edges, _flow_arrows(path))
    _orient(nodes, edges)
    return {"nodes": nodes, "edges": edges}


def _read_arrows(edges: list[dict], arrows: list[tuple]) -> None:
    """Each drain's drawn flow arrows, as votes for a -> b (+1) or b -> a (-1)."""
    import numpy as np

    for e in edges:
        e["arrow_votes"] = 0
    if not arrows or not edges:
        return
    pieces, owner = [], []
    for i, e in enumerate(edges):
        xy = e["xy"]
        for p, q in zip(xy[:-1], xy[1:]):
            pieces.append((*p, *q))
            owner.append(i)
    seg = np.array(pieces)
    a, ab = seg[:, :2], seg[:, 2:] - seg[:, :2]
    l2 = np.maximum((ab ** 2).sum(1), 1e-12)
    for x, y, ux, uy in arrows:
        u = np.clip((((x, y) - a) * ab).sum(1) / l2, 0.0, 1.0)
        dist = np.sqrt(((a + ab * u[:, None] - (x, y)) ** 2).sum(1))
        k = int(dist.argmin())
        if dist[k] > ARROW_ON_LINE_PT:
            continue
        along = (ux * ab[k, 0] + uy * ab[k, 1]) / np.sqrt(l2[k])
        if abs(along) > 0.7:                      # pointing along the drain, not across it
            edges[owner[k]]["arrow_votes"] += 1 if along > 0 else -1


def _orient(nodes: list[dict], edges: list[dict]) -> None:
    """Which way each drain carries its water, from the best evidence the sheet gives.

    Measured against the survey's own flow arrows on 16 sheets (873 drains): the chain
    numbering (A3 -> A2 -> A1(OL)) agrees 87 % of the time, the surveyed gradient 74 %,
    the route to the nearest outlet 68 %. So a drain takes, in that order: its drawn
    arrows; the numbering of its two manholes; the direction of the drain it continues
    through a manhole with nothing else joining; its surveyed fall; the way to the
    outlet. Sets edge["to"] ("a", "b" or None) and edge["to_from"], the evidence used.
    """
    import re

    def chain(node: dict) -> tuple[str, int] | None:
        match = re.fullmatch(r"([A-Z]+)(\d+)", node["id"] or "")
        return (match.group(1), int(match.group(2))) if match else None

    for e in edges:
        e["to"], e["to_from"] = None, None
        if e["arrow_votes"]:
            e["to"], e["to_from"] = ("b" if e["arrow_votes"] > 0 else "a"), "flow arrow on the sheet"
            continue
        ca, cb = chain(nodes[e["a"]]), chain(nodes[e["b"]])
        if ca and cb and ca[0] == cb[0] and abs(ca[1] - cb[1]) == 1:
            e["to"], e["to_from"] = ("a" if ca[1] < cb[1] else "b"), "manhole numbering"

    # Through a manhole where only two drains meet, the line carries on the same way.
    touching: dict[int, list[int]] = {}
    for i, e in enumerate(edges):
        touching.setdefault(e["a"], []).append(i)
        touching.setdefault(e["b"], []).append(i)
    changed = True
    while changed:
        changed = False
        for node, pair in touching.items():
            if len(pair) != 2:
                continue
            known = [i for i in pair if edges[i]["to"]]
            if len(known) != 1:
                continue
            k, u = known[0], (pair[0] if pair[1] == known[0] else pair[1])
            into = (edges[k]["b"] if edges[k]["to"] == "b" else edges[k]["a"]) == node
            e = edges[u]
            here_is = "a" if e["a"] == node else "b"
            other = "b" if here_is == "a" else "a"
            e["to"] = other if into else here_is
            e["to_from"] = "continues the drain it joins"
            changed = True

    for e in edges:
        if e["to"]:
            continue
        a, b = nodes[e["a"]], nodes[e["b"]]
        if a["invert_from"] == b["invert_from"] == "survey" and a["invert_m"] != b["invert_m"]:
            e["to"], e["to_from"] = ("a" if a["invert_m"] < b["invert_m"] else "b"), "surveyed fall"
    adjacency: dict[int, list[tuple[int, float]]] = {}
    for e in edges:
        adjacency.setdefault(e["a"], []).append((e["b"], e["length_m"]))
        adjacency.setdefault(e["b"], []).append((e["a"], e["length_m"]))
    distance = _network_distance(adjacency, [i for i, n in enumerate(nodes) if n["outlet"]])
    for e in edges:
        if e["to"]:
            continue
        da, db = distance.get(e["a"]), distance.get(e["b"])
        if da is not None and db is not None and da != db:
            e["to"], e["to_from"] = ("a" if da < db else "b"), "towards the sheet's outlet"


def _network_distance(touching: dict, sources: list[int]) -> dict[int, float]:
    """Distance along the drains from every reachable node to the nearest source."""
    import heapq

    best: dict[int, float] = {}
    heap = [(0.0, s) for s in sources]
    while heap:
        d, v = heapq.heappop(heap)
        if v in best:
            continue
        best[v] = d
        for w, length in touching.get(v, []):
            if w not in best:
                heapq.heappush(heap, (d + length, w))
    return best


def _fill_inverts(nodes: list[dict], edges: list[dict]) -> None:
    """Inverts at nodes the survey did not level, interpolated along the drains.

    Between two levelled manholes a drain's bed is a straight fall, so an unlevelled
    node takes the length-weighted average of its neighbours: the harmonic solve on
    the graph, with every surveyed invert held fixed. A piece of network with no
    surveyed invert at all keeps none.
    """
    import numpy as np
    from scipy.sparse import lil_matrix
    from scipy.sparse.csgraph import connected_components
    from scipy.sparse.linalg import spsolve

    n = len(nodes)
    for node in nodes:
        node["invert_from"] = "survey" if node["invert_m"] is not None else None
    if not n:
        return
    link = lil_matrix((n, n))
    for e in edges:
        w = 1.0 / max(e["length_m"], 1.0)
        link[e["a"], e["b"]] += w
        link[e["b"], e["a"]] += w
    link = link.tocsr()
    _, component = connected_components(link, directed=False)
    known = np.array([node["invert_m"] is not None for node in nodes])
    value = np.array([node["invert_m"] if node["invert_m"] is not None else 0.0 for node in nodes])
    for c in np.unique(component):
        members = np.flatnonzero(component == c)
        unknown = members[~known[members]]
        if not unknown.size or not known[members].any():
            continue
        sub = link[unknown][:, unknown]
        degree = np.asarray(link[unknown].sum(axis=1)).ravel()
        system = (-sub).tolil()
        system.setdiag(degree)
        rhs = link[unknown][:, members[known[members]]] @ value[members[known[members]]]
        solved = np.atleast_1d(spsolve(system.tocsc(), rhs))
        for i, v in zip(unknown, solved):
            nodes[i]["invert_m"] = round(float(v), 3)
            nodes[i]["invert_from"] = "interpolated along the drain"


def _fill_sections(nodes: list[dict], edges: list[dict]) -> None:
    """Each edge's section: that of its upstream manhole, else its downstream one, else
    the nearest measured one along the network within CARRY_SECTION_M - a drain keeps
    its size between the manholes that record it."""
    import heapq

    def measured(node: dict) -> bool:
        return bool(node["width_m"] and node["depth_m"])

    for e in edges:
        a, b = nodes[e["a"]], nodes[e["b"]]
        if a["invert_m"] is not None and b["invert_m"] is not None and b["invert_m"] > a["invert_m"]:
            upstream, downstream = b, a
        else:
            upstream, downstream = a, b
        source = upstream if measured(upstream) else downstream if measured(downstream) else None
        if source:
            e.update(width_m=source["width_m"], depth_m=source["depth_m"],
                     closed=source["closed"], section_from="survey")
        else:
            e.update(width_m=None, depth_m=None, closed=upstream["closed"] or downstream["closed"],
                     section_from=None)
    # Edges still without one take the nearest measured edge along the network.
    touching: dict[int, list[int]] = {}
    for i, e in enumerate(edges):
        touching.setdefault(e["a"], []).append(i)
        touching.setdefault(e["b"], []).append(i)
    heap = [(0.0, i, i) for i, e in enumerate(edges) if e["section_from"] == "survey"]
    best: dict[int, tuple[float, int]] = {}
    heapq.heapify(heap)
    while heap:
        dist, i, src = heapq.heappop(heap)
        if i in best or dist > CARRY_SECTION_M:
            continue
        best[i] = (dist, src)
        e = edges[i]
        for node in (e["a"], e["b"]):
            for j in touching[node]:
                if j not in best:
                    heapq.heappush(heap, (dist + (e["length_m"] + edges[j]["length_m"]) / 2, j, src))
    for i, e in enumerate(edges):
        if e["section_from"] is None and i in best:
            dist, src = best[i]
            e.update(width_m=edges[src]["width_m"], depth_m=edges[src]["depth_m"],
                     closed=e["closed"] if e["closed"] is not None else edges[src]["closed"],
                     section_from=f"carried {dist:.0f} m along the drain")


def parse_sheet(path: Path) -> dict:
    words = _drop_fragments(json.loads((CACHE / (path.stem + ".json")).read_text(encoding="utf-8")))
    lines = _lines(words)
    points, used = parse_points(words)
    controls = parse_controls(lines)
    geometry = _page_geometry(path)
    geo = georeference(words, geometry)
    labels = parse_map_labels(lines, used, geometry["circles"])

    for p in points:
        label = labels.get(p["id"])
        p["on_map"] = label is not None
        p["label_agrees"] = (label is not None and label["invert_label_m"] is not None
                             and p["invert_m"] is not None
                             and abs(label["invert_label_m"] - p["invert_m"]) < 0.0015)
        if geo and p["on_map"]:
            x, y, source = label["position"]
            p["position_from"] = source
            e = geo["east"][0] + geo["east"][1] * x
            n = geo["north"][0] + geo["north"][1] * y
            lat, lon = utm_to_latlon(e, n)
            p.update({"easting": round(e, 1), "northing": round(n, 1),
                      "lat": round(lat, 6), "lon": round(lon, 6)})
    network = {"nodes": [], "edges": []}
    if geo:
        by_id = {p["id"]: p for p in points}
        network = sheet_network(path, geometry["circles"], labels, by_id, geo["m_per_pt"][0], words)
        to_utm = lambda xy: (geo["east"][0] + geo["east"][1] * xy[..., 0],
                             geo["north"][0] + geo["north"][1] * xy[..., 1])
        for node in network["nodes"]:
            e, n = to_utm(node.pop("xy"))
            node["easting"], node["northing"] = float(e), float(n)
        for edge in network["edges"]:
            e, n = to_utm(edge.pop("xy"))
            edge["en"] = [[float(x), float(y)] for x, y in zip(e, n)]
    return {"sheet": path.name, "ward": int(re.search(r"\d+", path.stem).group()),
            "georeference": geo, "controls": controls, "points": points,
            "circles": len(geometry["circles"]), "network": network}


def _survey_vertices():
    """Every vertex of every drain in the GCC survey CSV, in UTM metres, in a tree."""
    import csv

    import numpy as np
    from scipy.spatial import cKDTree

    path = ROOT / "Cross-Checked Data" / "gcc_storm_water_drains (1).csv"
    pts = []
    with open(path, encoding="utf-8-sig", newline="") as fh:
        for row in csv.DictReader(fh):
            for pair in re.findall(r"(-?\d+\.\d+)\s+(-?\d+\.\d+)", row.get("wkt_geometry", "")):
                pts.append((float(pair[0]), float(pair[1])))
    lon, lat = np.array(pts).T
    east, north = latlon_to_utm(lat, lon)
    return cKDTree(np.c_[east, north])


def latlon_to_utm(lat, lon, zone: int = 44):
    """Forward transverse Mercator on WGS 84 (Snyder 1987); arrays or scalars."""
    import numpy as np

    a, f, k0 = 6378137.0, 1 / 298.257223563, 0.9996
    e2 = f * (2 - f)
    ep2 = e2 / (1 - e2)
    phi = np.radians(lat)
    lam = np.radians(np.asarray(lon) - ((zone - 1) * 6 - 180 + 3))
    n = a / np.sqrt(1 - e2 * np.sin(phi) ** 2)
    t = np.tan(phi) ** 2
    c = ep2 * np.cos(phi) ** 2
    big_a = np.cos(phi) * lam
    m = a * ((1 - e2 / 4 - 3 * e2 ** 2 / 64 - 5 * e2 ** 3 / 256) * phi
             - (3 * e2 / 8 + 3 * e2 ** 2 / 32 + 45 * e2 ** 3 / 1024) * np.sin(2 * phi)
             + (15 * e2 ** 2 / 256 + 45 * e2 ** 3 / 1024) * np.sin(4 * phi)
             - (35 * e2 ** 3 / 3072) * np.sin(6 * phi))
    east = 500000 + k0 * n * (big_a + (1 - t + c) * big_a ** 3 / 6
                              + (5 - 18 * t + t ** 2 + 72 * c - 58 * ep2) * big_a ** 5 / 120)
    north = k0 * (m + n * np.tan(phi) * (big_a ** 2 / 2 + (5 - t + 9 * c + 4 * c ** 2) * big_a ** 4 / 24
                                         + (61 - 58 * t + t ** 2 + 600 * c - 330 * ep2) * big_a ** 6 / 720))
    return east, north


def register(points: list[dict], tree) -> tuple[float, float, int] | None:
    """A shift that lays a sheet's manholes onto the surveyed drains, for sheets fixed
    only by their grid numbers (about 10-15 m): each manhole's nearest drain vertex
    within 60 m votes, the median vote is applied, three times over."""
    import numpy as np

    xy = np.array([[p["easting"], p["northing"]] for p in points])
    shift = np.zeros(2)
    used = 0
    for _ in range(3):
        dist, idx = tree.query(xy + shift)
        near = dist < 60.0
        used = int(near.sum())
        if used < 8:
            return None
        shift += np.median(tree.data[idx[near]] - (xy + shift)[near], axis=0)
    return float(shift[0]), float(shift[1]), used


def _network_latlon(network: dict) -> None:
    """UTM to latitude and longitude for a sheet's network, in place."""
    for node in network["nodes"]:
        lat, lon = utm_to_latlon(node.pop("easting"), node.pop("northing"))
        node["lat"], node["lon"] = round(lat, 7), round(lon, 7)
    for edge in network["edges"]:
        edge["coords"] = [[round(lon, 7), round(lat, 7)]
                          for lat, lon in (utm_to_latlon(x, y) for x, y in edge.pop("en"))]
        edge["length_m"] = round(edge["length_m"], 1)


def run_parse(names: list[str]) -> None:
    import numpy as np

    sheets = sorted(p for p in WARD_DIR.glob("*.pdf") if re.search(r"\d", p.stem)
                    and (CACHE / (p.stem + ".json")).exists() and (not names or p.name in names))
    tree = _survey_vertices()
    out = []
    for path in sheets:
        sheet = parse_sheet(path)
        pts = sheet["points"]
        geo = sheet["georeference"]
        placed = [p for p in pts if "easting" in p]
        if geo and geo["from"] != ["grid lines"] and placed:
            moved = register(placed, tree)
            geo["registered_to_survey_m"] = None if moved is None else [round(moved[0], 1), round(moved[1], 1)]
            if moved:
                for p in placed:
                    p["easting"] = round(p["easting"] + moved[0], 1)
                    p["northing"] = round(p["northing"] + moved[1], 1)
                    p["lat"], p["lon"] = (round(v, 6) for v in utm_to_latlon(p["easting"], p["northing"]))
                for node in sheet["network"]["nodes"]:
                    node["easting"] += moved[0]
                    node["northing"] += moved[1]
                for edge in sheet["network"]["edges"]:
                    edge["en"] = [[x + moved[0], y + moved[1]] for x, y in edge["en"]]
        _network_latlon(sheet["network"])
        # Independent check: the CSV survey was digitised separately; its drains should
        # pass within metres of the manholes the sheet puts on the same streets.
        if placed:
            dist, _ = tree.query(np.array([[p["easting"], p["northing"]] for p in placed]))
            sheet["survey_distance_m"] = {"median": round(float(np.median(dist)), 1),
                                          "within_15m": round(float((dist < 15).mean()), 2)}
        grid = (f"{'+'.join(geo['from'])}, residual {geo['residual_m']} m"
                + (f", shifted {geo['registered_to_survey_m']} m" if geo.get("registered_to_survey_m") else "")
                if geo else "not georeferenced")
        check = sheet.get("survey_distance_m")
        net = sheet["network"]
        with_section = sum(1 for e in net["edges"] if e["section_from"] == "survey")
        print(f"{path.name:>14}: network {len(net['nodes'])} nodes, {len(net['edges'])} drains "
              f"{sum(e['length_m'] for e in net['edges']) / 1000:.1f} km, {with_section} sized by survey | "
              f"{len(pts):4d} points, {len(placed):4d} placed, "
              f"{sum(p['label_agrees'] for p in pts):4d} label = table | grid {grid} | "
              f"to survey drains {check['median'] if check else '-'} m median, "
              f"{int(100 * check['within_15m']) if check else '-'}% within 15 m")
        out.append(sheet)
    if not names:
        OUT.write_text(json.dumps({"source": "GCC / SECON-JBA storm water drain base maps, Ward/*.pdf, "
                                             "surveyed Dec 2020, UTM 44N, levels m above MSL; read by "
                                             "digitise_wards.py", "sheets": out}), encoding="utf-8")
        print(f"wrote {OUT.relative_to(ROOT)}")


def selfcheck() -> None:
    """The network filling rules on a made-up drain, and the UTM conversions both ways."""
    node = lambda inv, w=None, d=None: {"invert_m": inv, "width_m": w, "depth_m": d, "closed": True}
    # A - B - C - D along one drain, 100 m apart; A and D levelled, B and C not.
    nodes = [node(10.0, 1.0, 0.8), node(None), node(None), node(9.4)]
    edges = [{"a": 0, "b": 1, "length_m": 100.0}, {"a": 1, "b": 2, "length_m": 100.0},
             {"a": 2, "b": 3, "length_m": 100.0}]
    _fill_inverts(nodes, edges)
    assert abs(nodes[1]["invert_m"] - 9.8) < 1e-9 and abs(nodes[2]["invert_m"] - 9.6) < 1e-9, nodes
    assert nodes[1]["invert_from"] == "interpolated along the drain" and nodes[0]["invert_from"] == "survey"
    _fill_sections(nodes, edges)
    # A to B takes A's measured section; the rest carry it along the drain.
    assert edges[0]["section_from"] == "survey" and edges[0]["width_m"] == 1.0
    assert edges[1]["width_m"] == 1.0 and edges[1]["section_from"].startswith("carried 100 m")
    # ...but not past CARRY_SECTION_M, measured centre to centre (here 550 m).
    far = [{"a": 0, "b": 1, "length_m": 100.0}, {"a": 1, "b": 2, "length_m": 1000.0}]
    _fill_sections([node(10.0, 1.0, 0.8), node(9.9), node(9.0)], far)
    assert far[1]["width_m"] is None, far[1]
    # A drain with no levelled manhole anywhere keeps no invert.
    alone = [node(None), node(None)]
    _fill_inverts(alone, [{"a": 0, "b": 1, "length_m": 50.0}])
    assert alone[0]["invert_m"] is None
    # PR1 on the sheets: 415705.262 E, 1445469.265 N is 13 04 27.075 N, 80 13 20.855 E.
    lat, lon = utm_to_latlon(415705.262, 1445469.265)
    assert abs(lat - (13 + 4 / 60 + 27.075 / 3600)) < 1e-6 and abs(lon - (80 + 13 / 60 + 20.855 / 3600)) < 1e-6
    east, north = latlon_to_utm(lat, lon)
    assert abs(east - 415705.262) < 1e-3 and abs(north - 1445469.265) < 1e-3
    print("digitise_wards self-check passed")


if __name__ == "__main__":
    command = sys.argv[1] if len(sys.argv) > 1 else ""
    if command == "ocr":
        run_ocr(sys.argv[2:])
    elif command == "parse":
        run_parse(sys.argv[2:])
    elif command == "check":
        selfcheck()
    else:
        print(__doc__)
