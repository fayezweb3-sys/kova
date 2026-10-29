"""Sample data for tests and `kova sample`, in each source's real file layout.

Carbamazepine: DINs, names, makers, strengths, forms, ODB prices and ODB statuses are the
real Ontario Drug Benefit e-Formulary listings retrieved 28 Oct 2026, so the alternative
finder is tested against a real case. Everything else is INVENTED for tests and must
never be shown as fact: DPD drug codes and dates (except Tegretol's codes), all BC
PharmaCare coverage, the LCA category, every shortage report and every recall.
Atorvastatin products are synthetic (DINs start with 99).
"""

from __future__ import annotations

import copy
import csv
import io
import json
import zipfile
from pathlib import Path

from kova.sources.dpd import FILES

# ---------- Health Canada DPD ----------
# (drug_code, din, brand, company, ingredient, strength, unit, dose_val, dose_unit, form, route, status)
CBZ = [
    ("777", "00010405", "TEGRETOL", "NOVARTIS PHARMACEUTICALS CANADA INC", "200", "MG", "", "", "TABLET", "ORAL"),
    ("90001", "00782718", "TEVA-CARBAMAZEPINE", "TEVA CANADA LIMITED", "200", "MG", "", "", "TABLET", "ORAL"),
    ("90002", "02541238", "MINT-CARBAMAZEPINE", "MINT PHARMACEUTICALS INC", "200", "MG", "", "", "TABLET", "ORAL"),
    ("6049", "00369810", "TEGRETOL CHEWTABS 100 MG", "NOVARTIS PHARMACEUTICALS CANADA INC", "100", "MG", "", "", "TABLET (CHEWABLE)", "ORAL"),
    ("6463", "00665088", "TEGRETOL CHEWTABS 200 MG", "NOVARTIS PHARMACEUTICALS CANADA INC", "200", "MG", "", "", "TABLET (CHEWABLE)", "ORAL"),
    ("90003", "02244403", "TARO-CARBAMAZEPINE", "TARO PHARMACEUTICALS INC", "100", "MG", "", "", "TABLET (CHEWABLE)", "ORAL"),
    ("90004", "02244404", "TARO-CARBAMAZEPINE", "TARO PHARMACEUTICALS INC", "200", "MG", "", "", "TABLET (CHEWABLE)", "ORAL"),
    ("8373", "00773611", "TEGRETOL CR", "NOVARTIS PHARMACEUTICALS CANADA INC", "200", "MG", "", "", "TABLET (EXTENDED-RELEASE)", "ORAL"),
    ("8374", "00755583", "TEGRETOL CR", "NOVARTIS PHARMACEUTICALS CANADA INC", "400", "MG", "", "", "TABLET (EXTENDED-RELEASE)", "ORAL"),
    ("90005", "02261839", "SANDOZ CARBAMAZEPINE CR", "SANDOZ CANADA INCORPORATED", "200", "MG", "", "", "TABLET (EXTENDED-RELEASE)", "ORAL"),
    ("90006", "02261847", "SANDOZ CARBAMAZEPINE CR", "SANDOZ CANADA INCORPORATED", "400", "MG", "", "", "TABLET (EXTENDED-RELEASE)", "ORAL"),
    ("19729", "02194333", "TEGRETOL SUSPENSION", "NOVARTIS PHARMACEUTICALS CANADA INC", "100", "MG", "5", "ML", "SUSPENSION", "ORAL"),
    ("90007", "02367394", "TARO-CARBAMAZEPINE", "TARO PHARMACEUTICALS INC", "100", "MG", "5", "ML", "SUSPENSION", "ORAL"),
]
ATOR = [
    ("2001", "99000101", "STATINOR 20", "ORIGINATOR PHARMA INC", "20", "MG", "", "", "TABLET", "ORAL"),
    ("2002", "99000102", "MAPLE-ATORVASTATIN", "MAPLE GENERICS LTD", "0.02", "G", "", "", "TABLET", "ORAL"),
]


def _csv(rows, cols) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_ALL, lineterminator="\r\n")
    for r in rows:
        w.writerow([r.get(c, "") for c in cols])
    return buf.getvalue().encode("utf-8")


def write_dpd(folder: Path, extra: list | None = None, statuses: dict | None = None) -> Path:
    """statuses: din -> current status (default MARKETED)."""
    folder.mkdir(parents=True, exist_ok=True)
    files = {k: [] for k in FILES}
    for code, din, brand, comp, strength, unit, dv, du, form, route in CBZ + ATOR + (extra or []):
        ingredient = "ATORVASTATIN (ATORVASTATIN CALCIUM)" if din.startswith("99") else "CARBAMAZEPINE"
        files["drug"].append({"drug_code": code, "class": "Human", "din": din, "brand_name": brand, "number_of_ais": "1"})
        files["ingred"].append({"drug_code": code, "ingredient": ingredient, "strength": strength, "strength_unit": unit,
                                "dosage_value": dv, "dosage_unit": du})
        files["comp"].append({"drug_code": code, "company_name": comp})
        files["form"].append({"drug_code": code, "pharmaceutical_form": form})
        files["route"].append({"drug_code": code, "route_of_administration": route})
        st = (statuses or {}).get(din, "MARKETED")
        files["status"].append({"drug_code": code, "current_status_flag": "Y", "status": st, "history_date": "2015-01-01"})
        files["schedule"].append({"drug_code": code, "schedule": "Prescription"})
        files["ther"].append({"drug_code": code, "tc_atc_number": "C10AA05" if din.startswith("99") else "N03AF01",
                              "tc_atc": "ATORVASTATIN" if din.startswith("99") else "CARBAMAZEPINE"})
    with zipfile.ZipFile(folder / "allfiles.zip", "w") as zf:
        for base, rows in files.items():
            zf.writestr(f"{base}.txt", _csv(rows, FILES[base]))
    return folder


# ---------- Ontario Drug Benefit XML ----------
# group id -> (strength, dosage form, limited use?, [(din, name, maker id, price or None, notABenefit)])
ODB_GROUPS = {
    "G-CH100": ("100mg", "Chew Tab", False, [("02244403", "Taro-Carbamazepine", "TAR", "0.1702", False),
                                            ("00369810", "Tegretol (Not a Benefit)", "NOV", None, True)]),
    "G-CH200": ("200mg", "Chew Tab", False, [("02244404", "Taro-Carbamazepine", "TAR", "0.3302", False),
                                            ("00665088", "Tegretol (Not a Benefit)", "NOV", None, True)]),
    "G-ER200": ("200mg", "ER Tab", False, [("09858341", "Carbamazepine ER Tablets, USP", "SET", "4.7080", False)]),   # marked sec3c="Y", as in the real extract
    "G-ER400": ("400mg", "ER Tab", False, [("09858342", "Carbamazepine ER Tablets, USP", "SET", "5.2216", False)]),
    "G-LA200": ("200mg", "LA Tab", True, [("02261839", "Sandoz Carbamazepine CR", "SDZ", "0.3845", False),
                                         ("00773611", "Tegretol CR", "NOV", "0.6161", False)]),
    "G-LA400": ("400mg", "LA Tab", True, [("02261847", "Sandoz Carbamazepine CR", "SDZ", "0.7689", False),
                                         ("00755583", "Tegretol CR", "NOV", "1.2324", False)]),
    "G-SU100": ("100mg/5mL", "Oral Susp", False, [("02367394", "Taro-Carbamazepine", "TAR", "0.0578", False),
                                                 ("02194333", "Tegretol", "NOV", "0.1182", False)]),
    "G-TB200": ("200mg", "Tab", False, [("02541238", "Mint-Carbamazepine", "MIN", "0.2217", False),
                                       ("00010405", "Tegretol (Not a Benefit)", "NOV", None, True),
                                       ("00782718", "Teva-Carbamazepine", "TEV", "0.2217", False)]),
}
MAKERS = {"TAR": "Taro Pharmaceuticals Inc", "NOV": "Novartis Pharmaceuticals Canada Inc", "SET": "SET (name not looked up)",
          "SDZ": "Sandoz Canada Inc", "MIN": "Mint Pharmaceuticals Inc", "TEV": "Teva Canada Limited"}


def write_odb(path: Path, groups: dict | None = None, formulary_date: str = "2026-10-30") -> Path:
    groups = groups or ODB_GROUPS
    out = [f'<extract createDate="2026-10-28"><manufacturerList>']
    out += [f'<manufacturer id="{k}">{v}</manufacturer>' for k, v in MAKERS.items()]
    out.append(f'</manufacturerList><formulary edition="43" updateVer="1" formularyDate="{formulary_date}" createDate="2026-10-28">')
    out.append('<pcg2 id="28"><name>CENTRAL NERVOUS SYSTEM AGENTS</name><pcg6 id="281208"><name>ANTICONVULSANTS</name>')
    out.append('<genericName id="G1"><name>CARBAMAZEPINE</name>')
    for gid, (strength, form, lu, drugs) in groups.items():
        out.append('<pcgGroup lccId="1">' if lu else "<pcgGroup>")
        if lu:
            out.append('<lccNote seq="1" reasonForUseId="999" type="L">For patients who have tried an immediate-release product.</lccNote>')
        out.append(f'<pcg9 id="{gid}"><itemNumber>1</itemNumber><strength>{strength}</strength><dosageForm>{form}</dosageForm>')
        for din, name, mid, price, nab in drugs:
            attrs = f' id="{din}"' + (' notABenefit="Y"' if nab else "") + (' sec3c="Y"' if din.startswith("0985") else "")
            body = f"<name>{name.replace('&', '&amp;')}</name><manufacturerId>{mid}</manufacturerId>"
            if price:
                body += f"<individualPrice>{price}</individualPrice>"
            body += "<listingDate>2010-01-01</listingDate>"
            out.append(f"<drug{attrs}>{body}</drug>")
        out.append("</pcg9></pcgGroup>")
    out.append("</genericName></pcg6></pcg2></formulary></extract>")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(out), encoding="utf-8")
    return path


def odb_groups_copy():
    return copy.deepcopy(ODB_GROUPS)


# ---------- BC PharmaCare (INVENTED coverage, real layout) ----------
PDDF_HEADER = ["DIN/PIN", "Plan", "Rec Eff Date", "Rec End Date", "Ben Grp List", "LCA Ind", "Pay Gen Ind", "Brand Nm",
               "Manuf", "Generic Nm", "Dosage Form", "Trial Flg", "Max Price", "LCA Price", "RDP Cat", "RDP Sub Cat",
               "RDP Price", "RDP Excl Plans", "Can Fed Reg Cd", "Pcare Plan Desc", "Max Days Supply", "Qty Limit",
               "Formulary List Date", "Ltd Use Flag"]
# din, plan, eff, end, brand, manuf, generic, form, max price, lca ind
BC_ROWS = [
    ("00773611", "C", "20200101", "99991231", "TEGRETOL CR", "NOV", "CARBAMAZEPINE 200MG CR TAB", "TABLETS", "0.6161", "P"),
    ("00773611", "I", "20200101", "99991231", "TEGRETOL CR", "NOV", "CARBAMAZEPINE 200MG CR TAB", "TABLETS", "0.6161", "P"),
    ("02261839", "C", "20200101", "99991231", "SANDOZ CARBAMAZEPINE CR", "SDZ", "CARBAMAZEPINE 200MG CR TAB", "TABLETS", "0.3845", ""),
    ("02261839", "I", "20200101", "99991231", "SANDOZ CARBAMAZEPINE CR", "SDZ", "CARBAMAZEPINE 200MG CR TAB", "TABLETS", "0.3845", ""),
    ("00782718", "I", "20200101", "99991231", "TEVA-CARBAMAZEPINE", "TEV", "CARBAMAZEPINE 200MG TAB", "TABLETS", "0.2217", ""),
    ("02541238", "I", "20200101", "20250131", "MINT-CARBAMAZEPINE", "MIN", "CARBAMAZEPINE 200MG TAB", "TABLETS", "0.2217", ""),  # ended
    ("00010405", "NB", "20200101", "99991231", "TEGRETOL", "NOV", "CARBAMAZEPINE 200MG TAB", "TABLETS", "0", ""),
]


def write_bc(folder: Path, rows=None) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(PDDF_HEADER)
    for din, plan, eff, end, brand, man, gen, form, price, lca in rows or BC_ROWS:
        rec = dict.fromkeys(PDDF_HEADER, "")
        rec.update({"DIN/PIN": din, "Plan": plan, "Rec Eff Date": eff, "Rec End Date": end, "Brand Nm": brand, "Manuf": man,
                    "Generic Nm": gen, "Dosage Form": form, "Max Price": price, "LCA Ind": lca,
                    "Pay Gen Ind": "Y" if lca.startswith("P") else "N", "Pcare Plan Desc": {"C": "Income Based", "I": "Fair PharmaCare"}.get(plan, ""),
                    "Max Days Supply": "100"})
        w.writerow([rec[h] for h in PDDF_HEADER])
    with zipfile.ZipFile(folder / "pddf.zip", "w") as zf:
        zf.writestr("pddf.csv", buf.getvalue())
    lca = io.StringIO()
    lw = csv.writer(lca, lineterminator="\n")
    lw.writerow(["LCA Category no.", "DIN", "Chemical name", "Drug name", "MAN", "Full/Partial LCA Benefit", "Plan B Only",
                 "RDP", "SA", "PROV", "Pan-Canadian", "Tiered Pricing Categories", "Max Price", "LCA Price"])
    lw.writerow(["0123", "00773611", "CARBAMAZEPINE", "TEGRETOL CR", "NOV", "P", "", "", "", "", "", "", "0.6161", "0.3845"])
    lw.writerow(["0123", "02261839", "CARBAMAZEPINE", "SANDOZ CARBAMAZEPINE CR", "SDZ", "F", "", "", "", "", "", "", "0.3845", ""])
    (folder / "lca.csv").write_text(lca.getvalue())
    return {"pddf": folder / "pddf.zip", "lca": folder / "lca.csv"}


# ---------- Health Product Shortages Canada (INVENTED reports, real API field names) ----------
def report(id_, din, brand, strength, status, *, tier3=False, kind="shortage", estimated_end="2026-12-15", updated="2026-10-20T10:00:00"):
    base = {"id": id_, "din": din, "status": status, "type": {"label": "Shortage" if kind == "shortage" else "Discontinuance"},
            "en_drug_brand_name": brand, "en_drug_common_name": "CARBAMAZEPINE", "en_ingredients": "CARBAMAZEPINE",
            "drug_strength": strength, "drug_dosage_form": "TABLET (EXTENDED-RELEASE)", "drug_route": "ORAL",
            "company_name": "TEST COMPANY", "atc_number": "N03AF01", "tier_3": tier3, "late_submission": False,
            "created_date": "2026-09-01 09:00:00", "updated_date": updated}
    if kind == "shortage":
        base.update({"shortage_reason": {"en_reason": "Disruption of the manufacture of the drug."},
                     "actual_start_date": "2026-09-01", "estimated_end_date": estimated_end, "unknown_estimated_end_date": False})
    else:
        base.update({"discontinuance_reason": {"en_reason": "Business decision."}, "discontinuation_date": "2026-12-31"})
    return base


def write_shortages(path: Path, reports=None) -> Path:
    reports = reports if reports is not None else [
        report(910001, "02261839", "SANDOZ CARBAMAZEPINE CR", "200MG", "active_confirmed"),
        report(910002, "00782718", "TEVA-CARBAMAZEPINE", "200MG", "resolved"),
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"data": reports, "total": len(reports), "remaining": 0}))
    return path


# ---------- Recalls (INVENTED notices) ----------
def write_recalls(path: Path, records=None) -> Path:
    records = records if records is not None else [
        {"NID": "R1", "Title": "Test recall: one lot of a carbamazepine CR product", "URL": "https://example.invalid/r1",
         "Organization": "Health Canada", "Category": "Health products - Drugs", "Recall class": "Type II",
         "Issue": "Product quality", "Product": "Sandoz Carbamazepine CR 200 mg, DIN 02261839", "Date published": "2026-10-01",
         "Last updated": "2026-10-02"},
        {"NID": "R2", "Title": "Test recall: a food product", "URL": "https://example.invalid/r2",
         "Organization": "Canadian Food Inspection Agency", "Category": "Food", "Date published": "2026-10-01"},
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records))
    return path


def write_all(raw: Path) -> Path:
    write_dpd(raw / "dpd")
    write_odb(raw / "odb" / "formulary.xml")
    write_bc(raw / "bc")
    write_shortages(raw / "shortages" / "reports.json")
    write_recalls(raw / "recalls" / "recalls.json")
    return raw
