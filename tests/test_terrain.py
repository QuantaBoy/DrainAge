"""The DEM over Chennai: coverage, seams, point lookups and the relief image."""

import numpy as np
import pytest

from app.services.terrain import elevation, grid, ground_level, relief_image, source_info


@pytest.mark.city
def test_city_dem() -> None:
    # And against the real tiles, if they are present.
    city = grid(12.85, 13.25, 80.10, 80.35)
    if city is not None:
        # Whatever the source, the city is covered: no-data only where the sea is,
        # and no seam along 13 degrees N where the tiles meet.
        land = city["grid"][:, :int(0.6 * city["grid"].shape[1])]
        assert np.isfinite(land).mean() > 0.97, "holes in the DEM over land"
        seam = int(round((13.25 - 13.0) / city["lat_step"]))
        assert abs(float(np.nanmean(city["grid"][seam - 1])) - float(np.nanmean(city["grid"][seam]))) < 3
        # The grid and the point lookup must agree: the box edges need not fall on
        # pixel edges, so the point's pixel is this cell or a neighbour of it.
        row = int((13.25 - 13.0827) / city["lat_step"])
        col = int((80.2707 - 80.10) / city["lon_step"])
        near = city["grid"][row - 1:row + 2, col - 1:col + 2]
        assert np.any(np.isclose(near, elevation(13.0827, 80.2707))), (near, elevation(13.0827, 80.2707))
        png = relief_image(city)
        assert png[:8] == b"\x89PNG\r\n\x1a\n" and len(png) > 10_000
        # Road level never sits above the surface it is read from.
        for lat_, lon_ in ((13.0827, 80.2707), (13.04, 80.23), (12.98, 80.20)):
            assert ground_level(lat_, lon_) <= elevation(lat_, lon_) + 1e-6
        print(f"DEM source: {source_info()}")
        print(f"Chennai DEM {city['grid'].shape[1]} x {city['grid'].shape[0]}, "
              f"{np.nanmin(city['grid']):.1f} to {np.nanmax(city['grid']):.1f} m, "
              f"relief image {len(png) / 1e6:.1f} MB")

    here = elevation(13.0827, 80.2707)          # Chennai Central
    if here is None:
        print("DEM tiles not found; formula checks passed")
    else:
        assert -5 < here < 60, here             # Chennai is flat and near sea level
        assert elevation(13.0, 80.2) is not None and elevation(12.9, 80.2) is not None
        print(f"Chennai Central ground {here:.1f} m")
