"""The alternative finder: what to dispense when a product is unavailable.

Ontario: products the ODB lists in the same interchangeable group (pcg9) as a benefit.
BC: products in the same Low Cost Alternative category; when a product has no LCA
category, products Health Canada lists with the same ingredients, strength, form and
route that PharmaCare currently covers.

Every candidate is checked for an open shortage report and Health Canada marketing
status, then ranked: available first, then by the province's price. Anything that
isn't interchangeable (another release form, a temporary listing) is returned
separately as 'related', so it's never presented as a straight swap.
"""

from __future__ import annotations

import re
import sqlite3

from .. import normalize as N
from .lookup import bc_current, dpd_status, odb_row, open_shortages


_MODIFIED = re.compile(r"\b(ER|LA|SR|CR|XL|XR|DR|ECT|EXT|EXTENDED|LONG|SUSTAINED|CONTROLLED|DELAYED|MODIFIED|SLOW)\b", re.I)
_LIQUID = re.compile(r"\b(SUSP|SOLN|SOL|SYR|SYRUP|LIQ|LIQUID|ELIXIR|DROPS|ORAL SOL)\b", re.I)
_INJ = re.compile(r"\b(INJ|INJECTION|AMP|VIAL|PEN|SYRINGE|IV|IM|SC)\b", re.I)


def release_family(form: str | None, name: str | None = "") -> str:
    """Coarse family used to keep 'related' suggestions sensible: a CR tablet is related to
    other modified-release forms, never to immediate-release tablets."""
    text = f"{form or ''} {name or ''}"
    if _INJ.search(text):
        return "injection"
    if _LIQUID.search(text):
        return "liquid"
    if _MODIFIED.search(text):
        return "modified-release"
    return "immediate-release"


def _annotate(conn, items: list[dict]) -> list[dict]:
    dins = [i["din"] for i in items]
    short, status = open_shortages(conn, dins), dpd_status(conn, dins)
    for i in items:
        s = short.get(i["din"], [])
        hc = status.get(i["din"])
        i["open_shortage"] = bool(s)
        i["shortage"] = [{"status": r["status"], "estimated_end": r["estimated_end"], "tier_3": bool(r["tier_3"])} for r in s]
        i["marketed"] = (hc["current_status"] == "MARKETED") if hc else None
        i["available"] = not s and i["marketed"] is not False
    return items


def _rank(items, price_key):
    return sorted(items, key=lambda i: (not i["available"], i.get(price_key) is None, i.get(price_key) or 0, i.get("name") or ""))


def ontario(conn: sqlite3.Connection, din: str) -> dict:
    me = odb_row(conn, din)
    if not me:
        return {"province": "ON", "din": din, "listed": False, "alternatives": [], "related": [], "not_covered": []}
    group = [dict(r) for r in conn.execute("SELECT * FROM odb_listing WHERE group_id = ? AND din <> ?", (me["group_id"], din))]
    pick = lambda r: {k: r[k] for k in ("din", "name", "strength", "dosage_form", "manufacturer", "unit_price", "limited_use", "lu_codes", "temporary", "benefit", "interchangeable")}
    alts = [pick(r) for r in group if r["benefit"] and me["interchangeable"]]
    not_cov = [pick(r) for r in group if not r["benefit"]]
    fam = release_family(me["dosage_form"], me["name"])
    related = [pick(r) for r in conn.execute(
        """SELECT * FROM odb_listing WHERE generic_id = ? AND group_id <> ? AND REPLACE(strength,' ','') = REPLACE(?,' ','') AND benefit = 1""",
        (me["generic_id"], me["group_id"], me["strength"])) if release_family(r["dosage_form"], r["name"]) == fam]
    for r in related:
        r["why"] = ("Temporary benefit, not interchangeable with this product: needs the prescriber's agreement"
                    if r["temporary"] else f"Different dosage form ({r['dosage_form']}), not interchangeable")
    return {
        "province": "ON", "din": din, "listed": True, "product": pick(me),
        "alternatives": _rank(_annotate(conn, alts), "unit_price"),
        "related": _rank(_annotate(conn, related), "unit_price"),
        "not_covered": _annotate(conn, not_cov),
        "basis": "Ontario Drug Benefit interchangeable group",
    }


def _bc_item(conn, din, extra=None):
    cov = bc_current(conn, din) or {}
    return {"din": din, "name": cov.get("name"), "generic_name": cov.get("generic_name"), "manufacturer": cov.get("manufacturer"),
            "max_price": cov.get("max_price"), "covered": cov.get("covered", False),
            "plans": [p["plan"] for p in cov.get("plans", [])], "partial_benefit": cov.get("partial_benefit"),
            "special_authority": cov.get("special_authority"), **(extra or {})}


def bc(conn: sqlite3.Connection, din: str) -> dict:
    me = bc_current(conn, din)
    lca = conn.execute("SELECT lca_category FROM bc_lca WHERE din = ?", (din,)).fetchone()
    candidates, basis = [], None
    if lca and lca["lca_category"]:
        basis = "BC Low Cost Alternative category"
        candidates = [r["din"] for r in conn.execute("SELECT DISTINCT din FROM bc_lca WHERE lca_category = ? AND din <> ?", (lca["lca_category"], din))]
    else:
        key = conn.execute("SELECT equivalence_key FROM product WHERE din = ? AND equivalence_key IS NOT NULL ORDER BY dataset = 'marketed' DESC", (din,)).fetchone()
        if key:
            basis = "Same ingredients, strength, form and route (Health Canada)"
            candidates = [r["din"] for r in conn.execute("SELECT DISTINCT din FROM product WHERE equivalence_key = ? AND din <> ? AND din <> ''", (key["equivalence_key"], din))]
    items = [_bc_item(conn, d) for d in candidates]
    covered = [i for i in items if i["covered"]]
    return {
        "province": "BC", "din": din, "listed": bool(me), "product": _bc_item(conn, din) if me else None,
        "alternatives": _rank(_annotate(conn, covered), "max_price"),
        "related": [], "not_covered": _annotate(conn, [i for i in items if not i["covered"] and i["name"]]),
        "basis": basis,
    }


def find(conn: sqlite3.Connection, province: str, din: str) -> dict:
    din = N.din(din)
    p = province.strip().upper()
    if p in ("ON", "ONTARIO"):
        return ontario(conn, din)
    if p in ("BC", "BRITISH COLUMBIA"):
        return bc(conn, din)
    raise ValueError("province must be ON or BC")
