import importlib

import pytest
from fastapi.testclient import TestClient

from kova import db, pipeline
from kova.sample import write_all


@pytest.fixture()
def client(tmp_path, monkeypatch):
    raw = write_all(tmp_path / "raw")
    path = tmp_path / "api.sqlite"
    c = db.connect(path)
    pipeline.load_all(c, raw)
    c.close()
    monkeypatch.setenv("KOVA_DB", str(path))
    from kova import api
    importlib.reload(api)
    return TestClient(api.app)


def test_web_app_and_health(client):
    assert "Kova" in client.get("/").text
    h = client.get("/health").json()
    assert h["ok"] and h["products"] == 15 and set(h["sources"]) == {"dpd", "odb", "bc", "shortages", "recalls"}


def test_search_product_coverage_alternatives(client):
    assert client.get("/v1/search", params={"q": "tegretol cr"}).json()[0]["din"] in {"00773611", "00755583"}
    p = client.get("/v1/products/773611").json()
    assert p["summary"]["ontario"] == "benefit"
    on = client.get("/v1/coverage/ON/00773611").json()
    assert on["status"] == "benefit" and on["listing"]["unit_price"] == 0.6161 and on["source"]["version"] == "2026-10-30"
    assert client.get("/v1/coverage/BC/00010405").json()["status"] == "not_covered"
    assert client.get("/v1/coverage/ON/12345678").json()["status"] == "not_listed"
    a = client.get("/v1/alternatives/ON/00773611").json()
    assert a["alternatives"][0]["din"] == "02261839"
    assert client.get("/v1/alternatives/QC/00773611").status_code == 422
    assert client.get("/v1/products/99999999").status_code == 404
    assert client.get("/v1/products/abc").status_code == 422


def test_shortages_and_watchlists(client):
    assert [r["din"] for r in client.get("/v1/shortages").json()] == ["02261839"]
    assert len(client.get("/v1/shortages", params={"open": "false"}).json()) == 2
    r = client.post("/v1/watchlists", json={"name": "Shelf", "text": "773611\n02261839, junk 12", "webhook": "https://example.invalid/hook"})
    assert r.status_code == 201
    w = r.json()
    assert w["dins"] == ["00773611", "02261839"] and len(w["secret"]) == 32
    full = client.get(f"/v1/watchlists/{w['id']}").json()
    assert "secret" not in full and full["products"][1]["open_shortage"] is True
    assert client.post("/v1/watchlists", json={"name": "Empty", "text": "none here"}).status_code == 422
    assert client.delete(f"/v1/watchlists/{w['id']}").status_code == 204
    assert client.get("/v1/watchlists").json() == []


def test_api_keys_when_configured(client, monkeypatch):
    monkeypatch.setenv("KOVA_API_KEYS", "k1,k2")
    assert client.get("/v1/search", params={"q": "tegretol"}).status_code == 401
    assert client.get("/v1/search", params={"q": "tegretol"}, headers={"X-API-Key": "k2"}).status_code == 200
    assert client.get("/health").status_code == 200
