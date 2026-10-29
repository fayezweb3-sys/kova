from kova import pipeline
from kova.sources import bc, odb


def test_every_source_loads(raw, tmp_path):
    from kova import db
    c = db.connect(tmp_path / "k.sqlite")
    report = pipeline.load_all(c, raw)
    assert report["dpd"]["products"] == 15
    assert report["odb"]["listings"] == 15
    assert report["bc"]["current"] == 6            # the ended Mint record is history
    assert report["shortages"] == {"reports": 2, "open": 1, "events": 0}
    assert report["recalls"]["recalls"] == 1        # the food recall is filtered out
    assert all(r.get("events", 0) == 0 for r in report.values())   # first load is the baseline


def test_odb_derived_fields(conn):
    rows = {r["din"]: dict(r) for r in conn.execute("SELECT * FROM odb_listing")}
    assert rows["02261839"]["limited_use"] == 1 and rows["02261839"]["lu_codes"] == "999"
    assert rows["00773611"]["interchangeable"] == 1
    # matches the e-Formulary: the temporary ER tablets are alone in their group and not interchangeable
    assert rows["09858341"]["interchangeable"] == 0 and rows["09858341"]["temporary"] == 1 and rows["09858341"]["is_pin"] == 1
    assert rows["09858341"]["name"] == "Carbamazepine ER Tablets, USP"
    assert rows["00010405"]["benefit"] == 0 and rows["00010405"]["interchangeable"] == 1
    assert rows["02541238"]["unit_price"] == 0.2217 and rows["02541238"]["manufacturer"] == "Mint Pharmaceuticals Inc"


def test_odb_parse_meta(tmp_path):
    from kova.sample import write_odb
    rows, meta = odb.parse(write_odb(tmp_path / "f.xml"))
    assert meta["formularyDate"] == "2026-10-30" and meta["edition"] == "43"
    assert len(rows) == 15


def test_bc_current_records_only(conn):
    current = {(r["din"], r["plan"]) for r in conn.execute("SELECT din, plan FROM bc_coverage WHERE is_current = 1")}
    assert ("02541238", "I") not in current
    assert ("00773611", "C") in current
    lca = conn.execute("SELECT lca_category, full_partial FROM bc_lca WHERE din = '00773611'").fetchone()
    assert tuple(lca) == ("0123", "P")


def test_bc_header_matching_tolerates_extra_columns(tmp_path):
    p = tmp_path / "pddf.csv"
    p.write_text("Extra Col,DIN/PIN,Plan,Rec Eff Date,Rec End Date,Max Price\nx,773611,C,20200101,99991231,0.6161\n")
    rows = bc.parse_pddf(p)
    assert rows[0]["din"] == "00773611" and rows[0]["max_price"] == 0.6161 and rows[0]["is_current"] == 1


def test_shortage_normalisation(conn):
    r = conn.execute("SELECT * FROM shortage_report WHERE din = '02261839'").fetchone()
    assert r["kind"] == "shortage" and r["is_open"] == 1 and r["estimated_end"] == "2026-12-15"
    assert r["reason"].startswith("Disruption")


def test_recall_linked_by_printed_din(conn):
    links = [tuple(r) for r in conn.execute("SELECT nid, din, how FROM recall_din")]
    assert links == [("R1", "02261839", "din")]
