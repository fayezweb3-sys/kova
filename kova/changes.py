"""Turn each refresh into change events.

Before a source is reloaded, its current state is snapshotted; after, the two are
compared. Every difference that matters to a pharmacy becomes a change_event row,
and watchlists are alerted from those rows. The first load of a source sets the
baseline and produces no events.
"""

from __future__ import annotations

import json
import sqlite3

from . import db as D


def snapshot(conn: sqlite3.Connection, source: str) -> dict:
    q = conn.execute
    if source == "odb":
        return {r["din"]: dict(r) for r in q("SELECT din, name, strength, dosage_form, benefit, unit_price, limited_use, temporary FROM odb_listing")}
    if source == "bc":
        out: dict[str, dict] = {}
        for r in q("SELECT din, plan, max_price, brand_name, generic_name FROM bc_coverage WHERE is_current = 1"):
            e = out.setdefault(r["din"], {"plans": set(), "max_price": r["max_price"], "name": r["brand_name"] or r["generic_name"]})
            if r["plan"] and r["plan"] != "NB":
                e["plans"].add(r["plan"])
        return out
    if source == "shortages":
        return {r["id"]: dict(r) for r in q("SELECT id, kind, din, status, is_open, brand_name, strength, reason, estimated_end, tier_3 FROM shortage_report")}
    if source == "recalls":
        out = {}
        for r in q("SELECT r.nid, r.title, r.url, d.din FROM recall r LEFT JOIN recall_din d ON d.nid = r.nid"):
            e = out.setdefault(r["nid"], {"title": r["title"], "url": r["url"], "dins": set()})
            if r["din"]:
                e["dins"].add(r["din"])
        return out
    if source == "dpd":
        return {r["din"]: dict(r) for r in q("SELECT din, brand_name, company, current_status, equivalence_key FROM product WHERE din <> ''")}
    raise ValueError(source)


def _money(x):
    return "no price" if x is None else f"${x:.4f}"


def diff(source: str, old: dict, new: dict) -> list[tuple[str, str | None, str, dict]]:
    """Return (kind, din, summary, detail) tuples."""
    ev = []
    if not old:
        return ev
    if source == "odb":
        for din, n in new.items():
            o = old.get(din)
            label = f"{n['name']} {n['strength'] or ''}".strip()
            if o is None:
                what = "temporary benefit" if n["temporary"] else ("benefit" if n["benefit"] else "listing (not a benefit)")
                ev.append(("coverage", din, f"ODB: {label} added as a {what} at {_money(n['unit_price'])}", {"after": n}))
                continue
            if o["benefit"] != n["benefit"]:
                ev.append(("coverage", din, f"ODB: {label} is {'now a benefit' if n['benefit'] else 'no longer a benefit'}", {"before": o, "after": n}))
            if o["limited_use"] != n["limited_use"]:
                ev.append(("coverage", din, f"ODB: {label} {'now needs' if n['limited_use'] else 'no longer needs'} a Limited Use code", {"before": o, "after": n}))
            if o["unit_price"] != n["unit_price"] and o["unit_price"] is not None and n["unit_price"] is not None:
                ev.append(("coverage", din, f"ODB: {label} price {_money(o['unit_price'])} → {_money(n['unit_price'])}", {"before": o, "after": n}))
        for din, o in old.items():
            if din not in new:
                ev.append(("coverage", din, f"ODB: {o['name']} {o['strength'] or ''} removed from the formulary".replace("  ", " "), {"before": o}))
    elif source == "bc":
        for din in set(old) | set(new):
            o, n = old.get(din), new.get(din)
            op, np_ = (o or {}).get("plans", set()), (n or {}).get("plans", set())
            name = (n or o)["name"] or din
            if op != np_:
                added, gone = sorted(np_ - op), sorted(op - np_)
                bits = ([f"now covered under plan {', '.join(added)}"] if added else []) + ([f"no longer covered under plan {', '.join(gone)}"] if gone else [])
                ev.append(("coverage", din, f"BC PharmaCare: {name} {'; '.join(bits)}", {"before": sorted(op), "after": sorted(np_)}))
            elif o and n and o["max_price"] != n["max_price"] and None not in (o["max_price"], n["max_price"]):
                ev.append(("coverage", din, f"BC PharmaCare: {name} maximum price {_money(o['max_price'])} → {_money(n['max_price'])}", {}))
    elif source == "shortages":
        for rid, n in new.items():
            o = old.get(rid)
            word = "discontinuation" if n["kind"] == "discontinuance" else "shortage"
            name = f"{n['brand_name'] or ''} {n['strength'] or ''}".strip()
            if o is None:
                tier = " (Tier 3)" if n["tier_3"] else ""
                ev.append(("shortage", n["din"], f"New {word} report{tier}: {name}, {n['status'].replace('_', ' ')}", {"after": n}))
            elif o["status"] != n["status"]:
                ev.append(("shortage", n["din"], f"{word.capitalize()} report for {name}: {o['status'].replace('_', ' ')} → {n['status'].replace('_', ' ')}", {"before": o, "after": n}))
            elif o["estimated_end"] != n["estimated_end"] and n["is_open"]:
                ev.append(("shortage", n["din"], f"Shortage of {name}: estimated end {o['estimated_end'] or 'unknown'} → {n['estimated_end'] or 'unknown'}", {}))
    elif source == "recalls":
        for nid, n in new.items():
            if nid in old:
                continue
            for din in sorted(n["dins"]) or [None]:
                ev.append(("recall", din, f"Recall or alert: {n['title']}", {"nid": nid, "url": n["url"]}))
    elif source == "dpd":
        old_keys = {o["equivalence_key"] for o in old.values() if o["equivalence_key"]}
        for din, n in new.items():
            o = old.get(din)
            if o is None:
                if n["equivalence_key"] in old_keys:
                    ev.append(("generic", din, f"New product with the same ingredients, strength and form: {n['brand_name']} ({n['company']})", {"after": n}))
            elif o["current_status"] != n["current_status"]:
                ev.append(("status", din, f"{n['brand_name']}: Health Canada status {o['current_status']} → {n['current_status']}", {"before": o, "after": n}))
    return ev


def record(conn: sqlite3.Connection, source: str, events) -> int:
    at = D.now()
    conn.executemany("INSERT INTO change_event (at, source, kind, din, summary, detail) VALUES (?,?,?,?,?,?)",
                     [(at, source, k, din, s, json.dumps(d, default=sorted)) for k, din, s, d in events])
    return len(events)
