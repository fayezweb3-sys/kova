"""Health Canada Drug Product Database (DPD) data extract.

Four zips (marketed, approved, cancelled, dormant). Inside each, comma-delimited,
double-quoted .txt files with no header row; every file carries its status suffix.
Layout: "Read Me File - Drug Product Database (DPD) Data Extract", Health Canada.
"""

from __future__ import annotations

import csv
import io
import sqlite3
import zipfile
from collections import defaultdict
from pathlib import Path
from typing import Iterable, Iterator

from .. import db as D
from .. import normalize as N

BASE_URL = "https://www.canada.ca/content/dam/hc-sc/documents/services/drug-product-database/"

DATASETS = {
    "allfiles.zip": ("marketed", ""),
    "allfiles_ap.zip": ("approved", "_ap"),
    "allfiles_ia.zip": ("cancelled", "_ia"),
    "allfiles_dr.zip": ("dormant", "_dr"),
}

FILES = {
    "drug": ["drug_code", "product_categorization", "class", "din", "brand_name", "descriptor",
             "pediatric_flag", "accession_number", "number_of_ais", "last_update_date", "ai_group_no",
             "class_f", "brand_name_f", "descriptor_f"],
    "ingred": ["drug_code", "active_ingredient_code", "ingredient", "ingredient_supplied_ind", "strength",
               "strength_unit", "strength_type", "dosage_value", "base", "dosage_unit", "notes",
               "ingredient_f", "strength_unit_f", "strength_type_f", "dosage_unit_f"],
    "comp": ["drug_code", "mfr_code", "company_code", "company_name", "company_type",
             "address_mailing_flag", "address_billing_flag", "address_notification_flag", "address_other",
             "suite_number", "street_name", "city_name", "province", "country", "postal_code",
             "post_office_box", "province_f", "country_f"],
    "form": ["drug_code", "pharm_form_code", "pharmaceutical_form", "pharmaceutical_form_f"],
    "route": ["drug_code", "route_of_administration_code", "route_of_administration", "route_of_administration_f"],
    "status": ["drug_code", "current_status_flag", "status", "history_date", "status_f", "lot_number", "expiration_date"],
    "schedule": ["drug_code", "schedule", "schedule_f"],
    "ther": ["drug_code", "tc_atc_number", "tc_atc", "tc_ahfs_number", "tc_ahfs", "tc_atc_f", "tc_ahfs_f"],
}

_DATASET_RANK = {"marketed": 0, "approved": 1, "dormant": 2, "cancelled": 3}


def urls() -> dict[str, str]:
    return {name: BASE_URL + name for name in DATASETS}


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _rows(text: str, ncols: int) -> Iterator[list[str]]:
    for i, row in enumerate(csv.reader(io.StringIO(text))):
        if not row or (len(row) == 1 and not row[0].strip()):
            continue
        if i == 0 and row[0].strip().upper() == "DRUG_CODE":
            continue
        row = [c.strip() for c in row]
        row += [""] * (ncols - len(row))
        yield row[:ncols]


def _members(source: Path) -> Iterable[tuple[str, str, bytes]]:
    suffix_to_ds = {suffix: ds for ds, suffix in DATASETS.values()}

    def classify(filename: str):
        stem = Path(filename).stem.lower()
        for suffix in sorted(suffix_to_ds, key=len, reverse=True):
            if suffix and stem.endswith(suffix):
                base = stem[: -len(suffix)]
                return (suffix_to_ds[suffix], base) if base in FILES else None
        return (suffix_to_ds[""], stem) if stem in FILES else None

    zips = sorted(source.glob("*.zip")) if source.is_dir() else [source]
    for zpath in zips:
        if zpath.suffix.lower() != ".zip":
            continue
        with zipfile.ZipFile(zpath) as zf:
            for info in zf.infolist():
                if not info.is_dir() and info.filename.lower().endswith(".txt"):
                    hit = classify(info.filename)
                    if hit:
                        yield hit[0], hit[1], zf.read(info)


def load(conn: sqlite3.Connection, source: Path) -> dict:
    for name, cols in FILES.items():
        conn.execute(f"DROP TABLE IF EXISTS raw_dpd_{name}")
        conn.execute(f"CREATE TABLE raw_dpd_{name} (dataset TEXT NOT NULL, {', '.join(c + ' TEXT' for c in cols)})")
        conn.execute(f"CREATE INDEX idx_raw_dpd_{name} ON raw_dpd_{name}(drug_code)")
    counts, total = {}, 0
    for dataset, base, raw in _members(source):
        cols = FILES[base]
        rows = [[dataset, *r] for r in _rows(_decode(raw), len(cols))]
        conn.executemany(f"INSERT INTO raw_dpd_{base} VALUES ({','.join('?' * (len(cols) + 1))})", rows)
        counts[f"{base}/{dataset}"] = len(rows)
        total += len(rows)
    if not counts:
        raise FileNotFoundError(f"No Drug Product Database files found in {source}")
    products = _derive(conn)
    D.log_run(conn, "dpd", str(source), products)
    return {"rows": total, "products": products}


def _derive(conn: sqlite3.Connection) -> int:
    conn.execute("DELETE FROM product")
    conn.execute("DELETE FROM product_ingredient")
    conn.execute("DELETE FROM status_history")

    def grouped(table: str, cols: str):
        out = defaultdict(list)
        for row in conn.execute(f"SELECT drug_code, dataset, {cols} FROM raw_dpd_{table}"):
            out[row["drug_code"]].append(row)
        return out

    ingred = grouped("ingred", "ingredient, strength, strength_unit, dosage_value, dosage_unit")
    forms = grouped("form", "pharmaceutical_form")
    routes = grouped("route", "route_of_administration")
    comps = grouped("comp", "company_name")
    scheds = grouped("schedule", "schedule")
    thers = grouped("ther", "tc_atc_number, tc_atc")
    stats = grouped("status", "current_status_flag, status, history_date")

    drugs = {}
    for row in conn.execute("SELECT * FROM raw_dpd_drug"):
        prev = drugs.get(row["drug_code"])
        if prev is None or _DATASET_RANK.get(row["dataset"], 9) < _DATASET_RANK.get(prev["dataset"], 9):
            drugs[row["drug_code"]] = row

    products, ingredients, history = [], [], []
    for code, d in drugs.items():
        ds = d["dataset"]

        def same_ds(rows):
            picked = [r for r in rows if r["dataset"] == ds]
            return picked or rows

        pairs = []
        for r in same_ds(ingred.get(code, [])):
            m = N.moiety(r["ingredient"])
            c = N.concentration(r["strength"], r["strength_unit"], r["dosage_value"], r["dosage_unit"])
            pairs.append((m, c))
            ingredients.append((code, m, N.clean(r["ingredient"]), c))
        ing_key = N.ingredient_key(pairs)
        moiety_key = "+".join(sorted({m for m, _ in pairs})) or None
        form_key = N.set_key([r["pharmaceutical_form"] for r in same_ds(forms.get(code, []))])
        route_key = N.set_key([r["route_of_administration"] for r in same_ds(routes.get(code, []))])
        comp = same_ds(comps.get(code, []))
        ther = same_ds(thers.get(code, []))

        current, status_date, first = None, None, None
        seen = set()
        for r in stats.get(code, []):
            iso, status = N.parse_date(r["history_date"]), N.clean(r["status"])
            cur = (r["current_status_flag"] or "").strip().upper() == "Y"
            if (status, iso, cur) not in seen:
                seen.add((status, iso, cur))
                history.append((code, status, iso, int(cur)))
            if cur:
                current, status_date = status, iso
            if status == "MARKETED" and iso and (first is None or iso < first):
                first = iso

        products.append((
            code, N.din(d["din"]), N.clean(d["brand_name"]), N.clean(d["descriptor"]) or None,
            ((comp[0]["company_name"] or "").strip() or None) if comp else None, d["class"], ds,
            current or ds.upper(), status_date, first, form_key, route_key,
            N.set_key([r["schedule"] for r in same_ds(scheds.get(code, []))]) or None,
            ther[0]["tc_atc_number"] if ther else None, ther[0]["tc_atc"] if ther else None,
            ing_key, f"{ing_key}#{form_key}#{route_key}" if ing_key else None, moiety_key, len(pairs),
        ))
    conn.executemany(f"INSERT INTO product VALUES ({','.join('?' * 19)})", products)
    conn.executemany("INSERT INTO product_ingredient VALUES (?,?,?,?)", ingredients)
    conn.executemany("INSERT INTO status_history VALUES (?,?,?,?)", history)
    return len(products)
