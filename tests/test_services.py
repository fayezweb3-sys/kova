from kova import db, pipeline
from kova.services import alternatives, lookup, watch
from kova import sample as F


def test_product_page_joins_every_source(conn):
    p = lookup.product(conn, "773611")
    assert p["din"] == "00773611" and p["name"] == "TEGRETOL CR"
    assert p["summary"] == {"marketed": True, "open_shortage": False, "tier_3": False, "recalls": 0,
                            "ontario": "benefit", "bc": "covered"}
    assert p["ontario"]["limited_use"] == 1
    assert [x["plan"] for x in p["bc"]["plans"]] == ["C", "I"] and p["bc"]["partial_benefit"] is True
    assert set(p["sources"]) == {"dpd", "odb", "bc", "shortages", "recalls"}


def test_product_page_for_pin_without_health_canada_record(conn):
    p = lookup.product(conn, "09858341")
    assert p["is_pin"] and p["health_canada"] is None and p["summary"]["ontario"] == "temporary benefit"


def test_search_by_name_strength_and_din(conn):
    hits = lookup.search(conn, "carbamazepine 200")
    dins = {h["din"] for h in hits}
    assert {"02541238", "00782718", "00773611", "02261839", "09858341"} <= dins
    assert "02244403" not in dins                       # 100 mg chewable filtered by strength
    assert lookup.search(conn, "02261839")[0]["open_shortage"] is True
    assert lookup.search(conn, "tegretol cr")[0]["name"].upper().startswith("TEGRETOL CR")


def test_ontario_alternatives_flag_shortage_and_keep_temporary_separate(conn):
    a = alternatives.find(conn, "ON", "00773611")
    assert [x["din"] for x in a["alternatives"]] == ["02261839"]
    sandoz = a["alternatives"][0]
    assert sandoz["open_shortage"] is True and sandoz["available"] is False    # invented open report in fixtures
    assert [x["din"] for x in a["related"]] == ["09858341"]
    assert "Temporary benefit" in a["related"][0]["why"]


def test_ontario_alternatives_rank_available_then_price(conn):
    a = alternatives.find(conn, "ON", "02541238")            # Mint 200 mg tablet
    assert [x["din"] for x in a["alternatives"]] == ["00782718"]
    assert [x["din"] for x in a["not_covered"]] == ["00010405"]
    assert {x["din"] for x in a["related"]} == {"02244404"}  # 200 mg chewable: same release family, other form


def test_bc_alternatives_from_lca_category(conn):
    a = alternatives.find(conn, "BC", "00773611")
    assert a["basis"] == "BC Low Cost Alternative category"
    assert [x["din"] for x in a["alternatives"]] == ["02261839"]


def test_bc_alternatives_fall_back_to_health_canada_equivalents(conn):
    a = alternatives.find(conn, "BC", "00010405")            # no LCA category in fixtures
    assert a["basis"].startswith("Same ingredients")
    assert [x["din"] for x in a["alternatives"]] == ["00782718"]   # Mint's coverage ended


def test_refresh_produces_events_and_watchlist_alerts(conn, raw, tmp_path):
    w = watch.create(conn, "Front shelf", watch.parse_dins("Tegretol CR 773611, Teva 00782718\n02261839"), email="rx@example.com")
    assert w["dins"] == ["00773611", "00782718", "02261839"]

    groups = F.odb_groups_copy()
    strength, form, lu, drugs = groups["G-LA200"]
    groups["G-LA200"] = (strength, form, lu, [d if d[0] != "00773611" else (d[0], d[1], d[2], "0.5900", d[4]) for d in drugs])
    del groups["G-TB200"]                                     # whole 200 mg tablet group removed
    F.write_odb(raw / "odb" / "formulary.xml", groups)
    F.write_shortages(raw / "shortages" / "reports.json", [
        F.report(910001, "02261839", "SANDOZ CARBAMAZEPINE CR", "200MG", "resolved", updated="2026-11-01T10:00:00"),
        F.report(910002, "00782718", "TEVA-CARBAMAZEPINE", "200MG", "resolved"),
        F.report(910003, "00773611", "TEGRETOL CR", "200MG", "anticipated_shortage", tier3=True),
    ])
    rep = pipeline.load_all(conn, raw, only=["odb", "shortages"])
    assert rep["odb"]["events"] == 4 and rep["shortages"]["events"] == 2

    summaries = [e["summary"] for e in watch.events_for(conn, w["id"])]
    assert "ODB: Tegretol CR 200mg price $0.6161 → $0.5900" in summaries
    assert any("Tier 3" in s and "TEGRETOL CR" in s for s in summaries)
    assert any("SANDOZ" in s and "active confirmed → resolved" in s for s in summaries)
    assert any(s.startswith("ODB: Teva-Carbamazepine") and "removed" in s for s in summaries)

    sent = watch.deliver(conn, tmp_path / "outbox")
    assert sent[0]["events"] == 4 and "written to" in sent[0]["results"][0]
    assert watch.events_for(conn, w["id"]) == []              # nothing is sent twice
    eml = next((tmp_path / "outbox").glob("*.eml")).read_text()
    assert "Front shelf" in eml and "DIN 00773611" in eml


def test_new_generic_and_status_change_events(conn, raw):
    F.write_dpd(raw / "dpd", extra=[("90100", "02999999", "NEW-CARBAMAZEPINE CR", "NEW GENERICS INC", "200", "MG", "", "",
                                      "TABLET (EXTENDED-RELEASE)", "ORAL")], statuses={"00010405": "DORMANT"})
    rep = pipeline.load_all(conn, raw, only=["dpd"])
    kinds = sorted(e["kind"] for e in watch.recent_events(conn))
    assert rep["dpd"]["events"] == 2 and kinds == ["generic", "status"]


def test_webhook_signature_is_hmac_sha256():
    assert watch.sign("k", b"{}") == "sha256=" + __import__("hmac").new(b"k", b"{}", "sha256").hexdigest()
