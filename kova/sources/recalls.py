"""Health Canada Recalls and Safety Alerts, open data (Open Government Licence – Canada).

The daily JSON/CSV covers food, vehicles, consumer and health products; Kova keeps
health-product notices only. Recalls rarely carry a structured DIN, so each notice
is linked to products two ways: DINs printed in its text ('din'), and brand names
of marketed products found in its title or product line ('name', weaker).
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
from pathlib import Path

from .. import db as D
from .. import normalize as N

JSON_URL = "https://recalls-rappels.canada.ca/sites/default/files/opendata-donneesouvertes/HCRSAMOpenData.json"
_DIN_IN_TEXT = re.compile(r"\b(?:DIN|PIN|NPN)\s*(?:#|:|no\.?)?\s*(\d{8})\b", re.I)
HEALTH = re.compile(r"health product|drug|medic|pharma|natural health", re.I)


def _key(h):
    return re.sub(r"[^a-z0-9]+", "_", str(h or "").strip().lower()).strip("_")


def _first(row, *names):
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return None


def _records(path: Path) -> list[dict]:
    raw = Path(path).read_bytes().decode("utf-8-sig", errors="replace")
    if raw.lstrip().startswith(("[", "{")):
        data = json.loads(raw)
        if isinstance(data, dict):
            data = next((v for v in data.values() if isinstance(v, list)), [])
        return [{_key(k): v for k, v in r.items()} for r in data]
    return [{_key(k): v for k, v in r.items()} for r in csv.DictReader(io.StringIO(raw))]


def parse(path: Path) -> list[dict]:
    out = []
    for r in _records(path):
        category = _first(r, "category", "categories", "product_category") or ""
        org = _first(r, "organization", "organisation", "department") or ""
        title = _first(r, "title", "recall_title") or ""
        if not (HEALTH.search(category) or ("health canada" in org.lower() and HEALTH.search(title + " " + category))):
            continue
        text = " ".join(filter(None, [title, _first(r, "product", "product_name"), _first(r, "issue", "hazard"),
                                      _first(r, "description", "body", "text")]))
        out.append(dict(
            nid=_first(r, "nid", "id", "recall_id", "number"), title=title, url=_first(r, "url", "link"),
            organization=org, category=category, recall_class=_first(r, "recall_class", "class", "type"),
            issue=_first(r, "issue", "hazard"), product=_first(r, "product", "product_name"),
            published=N.parse_date((_first(r, "date_published", "start_date", "published", "date") or "")[:10]),
            updated=N.parse_date((_first(r, "last_updated", "updated", "modified") or "")[:10]), text=text,
        ))
    return [r for r in out if r["nid"]]


def link_products(conn: sqlite3.Connection) -> int:
    """Rebuild recall_din from recall text against product names in the database."""
    conn.execute("DELETE FROM recall_din")
    brands = {}
    for p in conn.execute("SELECT din, brand_name FROM product WHERE din <> '' AND dataset IN ('marketed','approved')"):
        name = (p["brand_name"] or "").strip()
        if len(name) >= 6:            # short names ('ACT', 'APO') would match everything
            brands.setdefault(name, []).append(p["din"])
    links = []
    for rec in conn.execute("SELECT nid, title, product, text FROM recall"):
        hay = (rec["text"] or "").upper()
        found = {m for m in _DIN_IN_TEXT.findall(hay)}
        links += [(rec["nid"], d, "din") for d in found]
        if not found:
            focus = f" {(rec['title'] or '')} {(rec['product'] or '')} ".upper()
            for name, dins in brands.items():
                if f" {name} " in focus or f" {name}," in focus or f" {name}:" in focus:
                    links += [(rec["nid"], d, "name") for d in dins]
    conn.executemany("INSERT INTO recall_din VALUES (?,?,?)", sorted(set(links)))
    return len(links)


COLUMNS = ["nid", "title", "url", "organization", "category", "recall_class", "issue", "product", "published",
           "updated", "text"]


def load(conn: sqlite3.Connection, path: Path) -> dict:
    rows = {r["nid"]: r for r in parse(path)}
    conn.execute("DELETE FROM recall")
    conn.executemany(f"INSERT INTO recall ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                     [[r[c] for c in COLUMNS] for r in rows.values()])
    links = link_products(conn)
    D.log_run(conn, "recalls", str(path), len(rows), max((r["updated"] or r["published"] or "" for r in rows.values()), default=None))
    return {"recalls": len(rows), "links": links}
