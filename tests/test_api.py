"""The web layer: pages render, and service errors come back as JSON with their status.

The client is not entered as a context manager, so the start-up warm-up (minutes of
model building on a cold cache) does not run.
"""

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_pages_render() -> None:
    for path in ("/", "/resources"):
        response = client.get(path)
        assert response.status_code == 200 and "text/html" in response.headers["content-type"]


def test_service_errors_become_json() -> None:
    response = client.get("/data-collection/tiles/nope/1/1/1.png")
    assert response.status_code == 404 and response.json() == {"detail": "Unknown layer."}
    # Neither a district nor a coordinate pair.
    response = client.get("/data-collection/weather", params={"lat": 13.0})
    assert response.status_code == 400
    assert response.json()["detail"] == "Provide either 'district' or both 'lat' and 'lon'."
    assert client.get("/resources/ward-map/99999").status_code == 404


def test_request_validation() -> None:
    assert client.get("/data-collection/route", params={"from_lat": 13.0}).status_code == 422
    assert client.get("/data-collection/drain-hydraulics").status_code == 422
