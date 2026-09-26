"""Ward labels, and the base-map sheets on disk against their index."""

import pytest

from app.services.drain_network import ward_sheets
from app.services.wards import sheet_files, zone_label


def test_zone_label() -> None:
    assert zone_label("N07") == "Zone VII – Ambattur"
    assert zone_label("") == "Zone not recorded"
    assert zone_label("N99") == "N99"


@pytest.mark.city
def test_every_sheet_is_indexed() -> None:
    # The page links every sheet by ward number, so the files and the index must agree.
    assert set(sheet_files()) == {s["ward"] for s in ward_sheets().values()}
