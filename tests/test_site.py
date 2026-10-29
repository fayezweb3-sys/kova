import json

from kova import site


def test_static_site_build(conn, tmp_path):
    r = site.build(conn, tmp_path / "app")
    data = tmp_path / "app" / "data"
    assert r["products"] == 17                          # 15 DPD products + 2 Ontario PINs
    idx = {row[0]: row for row in json.loads((data / "search.json").read_text())}
    assert idx["02261839"][7] == 1 and idx["09858341"][8] == 2       # open shortage; temporary benefit
    shard = json.loads((data / "p" / "00773.json").read_text())
    alts = shard["00773611"]["alt"]["ON"]["alternatives"]
    assert alts[0]["din"] == "02261839" and alts[0]["available"] is False
    assert "window.KOVA_STATIC = true" in (tmp_path / "app" / "index.html").read_text()
    meta = json.loads((data / "meta.json").read_text())
    assert set(meta["sources"]) == {"dpd", "odb", "bc", "shortages", "recalls"}
