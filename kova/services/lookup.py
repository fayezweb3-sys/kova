"""Search and the product page: every source joined on the DIN."""

from __future__ import annotations

import re
import sqlite3

from .. import db as D
from .. import normalize as N

SOURCE_NAMES = {
    "dpd": "Health Canada Drug Product Database",
    "odb": "Ontario Drug Benefit Formulary",
    "bc": "BC PharmaCare Formulary",
    "shortages": "Health Product Shortages Canada",
    "recalls": "Health Canada Recalls and Safety Alerts",
}


def open_shortages(conn: sqlite3.Connection, dins) -> dict[str, list[dict]]:
    dins = [d for d in set(dins) if d]
    out: dict[str, list[dict]] = {}
    for i in range(0, len(dins), 500):
        chunk = dins[i:i + 500]
        for r in conn.execute(f"""SELECT * FROM shortage_report WHERE is_open = 1 AND din IN ({','.join('?' * len(chunk))})
                                  ORDER BY tier_3 DESC, actual_start DESC""", chunk):
            out.setdefault(r["din"], []).append(dict(r))
    return out


def dpd_status(conn: sqlite3.Connection, dins) -> dict[str, dict]:
    dins = [d for d in set(dins) if d]
    out = {}
    for i in range(0, len(dins), 500):
        chunk = dins[i:i + 500]
        for r in conn.execute(f"SELECT din, current_status, dataset, company, brand_name FROM product WHERE din IN ({','.join('?' * len(chunk))})", chunk):
            prev = out.get(r["din"])
            if prev is None or r["dataset"] == "marketed":
                out[r["din"]] = dict(r)
    return out


def bc_current(conn: sqlite3.Connection, din: str) -> dict | None:
    rows = [dict(r) for r in conn.execute("SELECT * FROM bc_coverage WHERE din = ? AND is_current = 1 ORDER BY plan", (din,))]
    if not rows:
        return None
    plans = [r for r in rows if r["plan"] and r["plan"] != "NB"]
    lca = conn.execute("SELECT * FROM bc_lca WHERE din = ?", (din,)).fetchone()
    rdp = conn.execute("SELECT * FROM bc_rdp WHERE din = ?", (din,)).fetchone()
    first = rows[0]
    return {
        "covered": bool(plans),
        "plans": [{"plan": r["plan"], "description": r["plan_desc"], "max_days_supply": r["max_days_supply"]} for r in plans],
        "name": first["brand_name"], "generic_name": first["generic_name"], "manufacturer": first["manufacturer"],
        "dosage_form": first["dosage_form"],
        "max_price": first["max_price"], "lca_price": first["lca_price"] or None,
        "partial_benefit": (first["pay_generic"] == "Y") or (first["lca_ind"] or "").startswith("P"),
        "limited_use": first["limited_use"] in ("Y", "1", "T"),
        "special_authority": bool(lca and (lca["special_auth"] or "").upper() == "Y"),
        "lca_category": lca["lca_category"] if lca else None,
        "rdp": (rdp["rdp"] if rdp else None) or first["rdp_cat"],
    }


def odb_row(conn: sqlite3.Connection, din: str) -> dict | None:
    r = conn.execute("SELECT * FROM odb_listing WHERE din = ?", (din,)).fetchone()
    return dict(r) if r else None


def _display_name(prod, odb, bc):
    if prod:
        return prod["brand_name"]
    if odb:
        return odb["name"]
    if bc:
        return bc["name"]
    return None


def product(conn: sqlite3.Connection, din: str) -> dict | None:
    din = N.din(din)
    if not din:
        return None
    prod = conn.execute("""SELECT * FROM product WHERE din = ?
                           ORDER BY CASE dataset WHEN 'marketed' THEN 0 WHEN 'approved' THEN 1 WHEN 'dormant' THEN 2 ELSE 3 END
                           LIMIT 1""", (din,)).fetchone()
    prod = dict(prod) if prod else None
    odb, bc = odb_row(conn, din), bc_current(conn, din)
    reports = [dict(r) for r in conn.execute(
        "SELECT * FROM shortage_report WHERE din = ? ORDER BY is_open DESC, COALESCE(actual_start, anticipated_start, created) DESC", (din,))]
    recalls = [dict(r) for r in conn.execute(
        "SELECT r.nid, r.title, r.url, r.published, r.recall_class, d.how FROM recall_din d JOIN recall r ON r.nid = d.nid WHERE d.din = ? ORDER BY r.published DESC", (din,))]
    if not (prod or odb or bc or reports):
        return None
    ingredients = []
    if prod:
        ingredients = [dict(r) for r in conn.execute(
            "SELECT moiety, concentration FROM product_ingredient WHERE drug_code = ? ORDER BY moiety", (prod["drug_code"],))]
    open_reports = [r for r in reports if r["is_open"]]
    marketed = (prod["current_status"] == "MARKETED") if prod else None
    runs = D.last_runs(conn)
    return {
        "din": din,
        "is_pin": din.startswith("09") and not prod,
        "name": _display_name(prod, odb, bc),
        "company": (prod or {}).get("company") or (odb or {}).get("manufacturer") or (bc or {}).get("manufacturer"),
        "ingredients": ingredients,
        "strength": (odb or {}).get("strength") or ", ".join(i["concentration"] for i in ingredients) or None,
        "dosage_form": (odb or {}).get("dosage_form") or (prod or {}).get("form_key") or (bc or {}).get("dosage_form"),
        "route": (prod or {}).get("route_key"),
        "atc": {"code": prod["atc_code"], "name": prod["atc_name"]} if prod and prod["atc_code"] else None,
        "health_canada": {"status": prod["current_status"], "since": prod["status_date"], "first_marketed": prod["first_marketed"],
                          "schedules": prod["schedules"]} if prod else None,
        "summary": {
            "marketed": marketed,
            "open_shortage": bool(open_reports),
            "tier_3": any(r["tier_3"] for r in open_reports),
            "recalls": len(recalls),
            "ontario": None if not odb else ("temporary benefit" if odb["temporary"] else "benefit" if odb["benefit"] else "not a benefit"),
            "bc": None if not bc else ("covered" if bc["covered"] else "not covered"),
        },
        "ontario": odb,
        "bc": bc,
        "shortages": reports,
        "recalls": recalls,
        "sources": {k: {"name": SOURCE_NAMES[k], "checked": v["run_at"], "version": v["version"]} for k, v in runs.items()},
    }


def search(conn: sqlite3.Connection, q: str, limit: int = 25) -> list[dict]:
    q = (q or "").strip()
    if not q:
        return []
    hits: dict[str, dict] = {}

    def add(din, name, strength=None, form=None, company=None, score=0):
        if not din:
            return
        h = hits.setdefault(din, {"din": din, "name": name, "strength": strength, "dosage_form": form, "company": company, "score": score})
        h["score"] = max(h["score"], score)
        for k, v in (("name", name), ("strength", strength), ("dosage_form", form), ("company", company)):
            h[k] = h[k] or v

    digits = re.sub(r"\D", "", q)
    if len(digits) >= 5 and len(digits) == len(q.replace(" ", "")):
        like = f"%{digits}%"
        for r in conn.execute("SELECT din, brand_name, company, form_key FROM product WHERE din LIKE ? LIMIT 50", (like,)):
            add(r["din"], r["brand_name"], None, r["form_key"], r["company"], 3)
        for r in conn.execute("SELECT din, name, strength, dosage_form, manufacturer FROM odb_listing WHERE din LIKE ? LIMIT 50", (like,)):
            add(r["din"], r["name"], r["strength"], r["dosage_form"], r["manufacturer"], 3)
        for r in conn.execute("SELECT DISTINCT din, brand_name, generic_name, dosage_form, manufacturer FROM bc_coverage WHERE din LIKE ? AND is_current = 1 LIMIT 50", (like,)):
            add(r["din"], r["brand_name"], None, r["dosage_form"], r["manufacturer"], 3)
    else:
        words = [w for w in re.split(r"\s+", q.upper()) if w]
        strength = next((w for w in words if re.match(r"^\d+(\.\d+)?(MG|MCG|G|ML)?$", w)), None)
        terms = [w for w in words if w != strength and not re.match(r"^(MG|MCG|G|ML)$", w)] or words
        cond = " AND ".join(["(brand_name LIKE ? OR drug_code IN (SELECT drug_code FROM product_ingredient WHERE moiety LIKE ?))"] * len(terms))
        params = [p for t in terms for p in (f"%{t}%", f"{t}%")]
        for r in conn.execute(f"""SELECT din, brand_name, company, form_key, current_status FROM product
                                  WHERE din <> '' AND {cond} ORDER BY dataset = 'marketed' DESC LIMIT 400""", params):
            add(r["din"], r["brand_name"], None, r["form_key"], r["company"], 2 if r["current_status"] == "MARKETED" else 0)
        cond2 = " AND ".join(["(UPPER(name) LIKE ? OR UPPER(generic_name) LIKE ?)"] * len(terms))
        for r in conn.execute(f"SELECT din, name, strength, dosage_form, manufacturer FROM odb_listing WHERE {cond2} LIMIT 400",
                              [p for t in terms for p in (f"%{t}%", f"%{t}%")]):
            add(r["din"], r["name"], r["strength"], r["dosage_form"], r["manufacturer"], 2)
        if strength:
            num = re.match(r"^\d+(\.\d+)?", strength).group(0)
            def has_strength(h):
                text = f"{h['strength'] or ''}"
                if not text:
                    ing = conn.execute("SELECT GROUP_CONCAT(concentration) c FROM product_ingredient pi JOIN product p ON p.drug_code = pi.drug_code WHERE p.din = ?", (h["din"],)).fetchone()
                    text = ing["c"] or ""
                return re.search(rf"(?<![\d.]){re.escape(num)}(?![\d.])", text) is not None
            hits = {d: h for d, h in hits.items() if has_strength(h)}

    dins = list(hits)
    shortages = open_shortages(conn, dins)
    odb = {r["din"]: r for r in conn.execute(f"SELECT din, benefit, temporary, unit_price, strength, dosage_form FROM odb_listing WHERE din IN ({','.join('?' * len(dins))})", dins)} if dins else {}
    bc = {r["din"] for r in conn.execute(f"SELECT DISTINCT din FROM bc_coverage WHERE is_current = 1 AND plan <> 'NB' AND din IN ({','.join('?' * len(dins))})", dins)} if dins else set()
    status = dpd_status(conn, dins)
    out = []
    for d, h in hits.items():
        o = odb.get(d)
        h.update({
            "strength": h["strength"] or (o["strength"] if o else None),
            "marketed": (status[d]["current_status"] == "MARKETED") if d in status else None,
            "open_shortage": d in shortages,
            "ontario": None if not o else ("temporary benefit" if o["temporary"] else "benefit" if o["benefit"] else "not a benefit"),
            "ontario_price": o["unit_price"] if o else None,
            "bc": "covered" if d in bc else None,
        })
        out.append(h)
    out.sort(key=lambda h: (-h["score"], h["marketed"] is False, (h["name"] or ""), h["strength"] or ""))
    for h in out:
        h.pop("score", None)
    return out[:limit]


def search_dins(conn: sqlite3.Connection, dins: list[str]) -> list[dict]:
    """Status rows for a known list of DINs (a watchlist), in the given order."""
    out = []
    for d in dins:
        p = product(conn, d)
        if not p:
            out.append({"din": d, "name": None, "found": False})
            continue
        out.append({"din": d, "name": p["name"], "strength": p["strength"], "dosage_form": p["dosage_form"],
                    "company": p["company"], "found": True, **p["summary"]})
    return out
