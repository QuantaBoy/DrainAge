"""The Technology page's endpoints."""

from fastapi.testclient import TestClient

from app.api.flood import snap
from app.main import app

client = TestClient(app)


def test_technology_page_and_engine() -> None:
    assert client.get("/technology").status_code == 200
    engine = client.get("/data-collection/engine").json()
    assert engine["device"]["logical_cores"] >= 1
    inventory = client.get("/data-collection/inventory").json()
    assert inventory["total"] == len(inventory["resources"]) > 10


def test_what_if_settings_snap_to_shared_steps() -> None:
    assert snap(12.4, 0.73, 0.52) == (12.0, 0.75, 0.5)
    assert snap(None, 0.01, 0.0) == (None, 0.05, 0.0)
