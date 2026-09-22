"""Cross-check the GCC drain survey against the GCC / SECON-JBA ward base maps.

    python "Cross-Checked Data/ward_cross_check.py"

Two official sources, compared ward by ward:

  * Ward/*.pdf   - the storm water drain base-map sheets produced for the Greater
                   Chennai Corporation under the Real Time Flood Forecasting SDSS
                   project (SECON-JBA, funded through TNUIFSL). The ward and zone
                   printed in each title block are indexed in app/data/ward_sheets.json.
  * gcc_storm_water_drains (1).csv - the drain survey digitised from those sheets,
                   which is what the site maps and sizes.

Writes gcc_wards_vs_survey.csv next to this file: one row per ward known to either
source, and what each says about it. The site shows the surveyed value and flags any
disagreement rather than silently correcting either source.
"""

import csv
import sys
from collections import defaultdict
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))

from app.routes.drains import _load_csv, _ward_sheets  # noqa: E402
from app.routes.resources import _sheet_files, _zone_label  # noqa: E402


def main() -> None:
    sheets = _ward_sheets()                     # official: ward code -> title block
    files = _sheet_files()                      # official: ward number -> PDF
    drains = _load_csv()                        # survey

    surveyed: dict[str, dict] = defaultdict(lambda: {"drains": 0, "length_m": 0.0, "zones": set()})
    for drain in drains:
        props = drain["props"]
        ward = props.get("WARD") or ""
        if not ward:
            continue
        entry = surveyed[ward]
        entry["drains"] += 1
        entry["length_m"] += drain["length_m"]
        if props.get("ZONE"):
            entry["zones"].add(props["ZONE"])

    rows = []
    for ward in sorted(set(surveyed) | set(sheets)):
        sheet = sheets.get(ward)
        survey = surveyed.get(ward)
        survey_zones = sorted(survey["zones"]) if survey else []
        sheet_zone = sheet["zone"] if sheet else None
        number = sheet["ward"] if sheet else int(ward.lstrip("N") or 0)

        if sheet and survey and survey_zones and sheet_zone not in survey_zones:
            verdict = "zone disagrees"
        elif sheet and not survey:
            verdict = "sheet only: ward not in the survey"
        elif survey and not sheet:
            verdict = "survey only: no base-map sheet here"
        else:
            verdict = "agrees"

        rows.append([
            ward, number,
            "yes" if sheet else "no",
            "yes" if number in files else "no",
            sheet_zone or "", _zone_label(sheet_zone) if sheet_zone else "",
            sheet["zone_name"] if sheet else "",
            "/".join(survey_zones),
            survey["drains"] if survey else 0,
            round(survey["length_m"], 1) if survey else 0.0,
            verdict,
        ])

    out = HERE / "gcc_wards_vs_survey.csv"
    with out.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["ward", "ward_number", "base_map_sheet", "pdf_in_repo",
                         "zone_on_sheet", "zone_label", "zone_name_on_sheet",
                         "zone_in_survey", "drains_in_survey", "surveyed_length_m", "check"])
        writer.writerows(rows)

    counts = defaultdict(int)
    for row in rows:
        counts[row[-1]] += 1
    print(f"wrote {out.name}: {len(rows)} wards")
    print(f"  wards in the survey CSV      : {len(surveyed)}")
    print(f"  base-map sheets indexed      : {len(sheets)} ({len(files)} PDFs in Ward/)")
    print(f"  wards with both              : {sum(1 for r in rows if r[2] == 'yes' and r[8] > 0)}")
    for verdict, count in sorted(counts.items()):
        print(f"  {verdict:<32}: {count}")
    for row in rows:
        if row[-1] != "agrees" and row[-1] != "survey only: no base-map sheet here":
            print(f"    ward {row[1]}: sheet says {row[4] or '-'} "
                  f"({row[6] or '-'}), survey says {row[7] or '-'}, "
                  f"{row[8]} drains -> {row[-1]}")


if __name__ == "__main__":
    main()
