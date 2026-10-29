"""Health Product Shortages Canada (healthproductshortages.ca, formerly Drug Shortages Canada).

API v1: POST /login (form fields email, password) returns an `auth-token` header;
GET /search?filter_status=..&orderby=id&order=asc&limit=100&offset=.. returns
{"data": [...], "total": n, "remaining": n}. Limit: 1,000 requests an hour.
The API changed when the site moved on 18 January 2026; field names below follow
the current API. An account is free: https://healthproductshortages.ca

`fetch` saves the raw reports as JSON, so loading never needs the network.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .. import db as D
from .. import normalize as N

API = "https://healthproductshortages.ca/api/v1/"
OPEN = {"active_confirmed", "anticipated_shortage", "to_be_discontinued"}
STATUSES = ["active_confirmed", "anticipated_shortage", "to_be_discontinued", "avoided_shortage",
            "resolved", "discontinued", "reversed"]


def fetch(email: str, password: str, dest: Path, statuses=STATUSES, pause: float = 0.5) -> Path:
    import httpx

    headers = {"Accept": "application/json", "User-Agent": "kova/0.1 (+pharmacy data tool)"}
    with httpx.Client(base_url=API, headers=headers, timeout=60, follow_redirects=True) as c:
        r = c.post("login", data={"email": email, "password": password})
        r.raise_for_status()
        token = r.headers.get("auth-token")
        if not token:
            raise RuntimeError("Login succeeded but no auth-token header came back; check the account.")
        c.headers["auth-token"] = token
        reports = []
        for status in statuses:
            offset = 0
            while True:
                r = c.get("search", params={"filter_status": status, "orderby": "id", "order": "asc",
                                            "limit": 100, "offset": offset})
                r.raise_for_status()
                body = r.json()
                page = body.get("data") or []
                reports.extend(page)
                offset += 100
                if not page or body.get("remaining", 0) <= 0:
                    break
                time.sleep(pause)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(reports))
    return dest


def _get(d: dict, *path):
    for p in path:
        if not isinstance(d, dict):
            return None
        d = d.get(p)
    return d


def _label(v):
    if isinstance(v, dict):
        return v.get("en_reason") or v.get("label") or v.get("en") or v.get("name")
    return v


def normalise(rep: dict) -> dict:
    kind_label = str(_label(rep.get("type")) or "").lower()
    kind = "discontinuance" if ("discontinu" in kind_label or rep.get("discontinuance_reason")) else "shortage"
    status = str(rep.get("status") or "").strip().lower()
    din = N.din(rep.get("din") or _get(rep, "drug", "din"))
    return dict(
        id=f"{kind[0]}{rep.get('id')}", kind=kind, din=din or None, status=status, is_open=int(status in OPEN),
        brand_name=rep.get("en_drug_brand_name") or _get(rep, "drug", "brand_name"),
        common_name=rep.get("en_drug_common_name"),
        ingredients=_label(rep.get("en_ingredients")) if not isinstance(rep.get("en_ingredients"), list)
        else "; ".join(map(str, rep["en_ingredients"])),
        strength=_label(rep.get("drug_strength")), dosage_form=_label(rep.get("drug_dosage_form")),
        route=_label(rep.get("drug_route")), company=rep.get("company_name"),
        atc_code=rep.get("atc_number"), tier_3=int(bool(rep.get("tier_3"))),
        late_submission=int(bool(rep.get("late_submission"))),
        reason=_label(rep.get("shortage_reason") or rep.get("discontinuance_reason")),
        anticipated_start=N.parse_date(str(rep.get("anticipated_start_date") or rep.get("anticipated_discontinuation_date") or "")[:10]),
        actual_start=N.parse_date(str(rep.get("actual_start_date") or "")[:10]),
        estimated_end=None if rep.get("unknown_estimated_end_date") else N.parse_date(str(rep.get("estimated_end_date") or "")[:10]),
        actual_end=N.parse_date(str(rep.get("actual_end_date") or "")[:10]),
        discontinued_on=N.parse_date(str(rep.get("discontinuation_date") or "")[:10]),
        comments=rep.get("en_comments") or rep.get("en_discontinuation_comments") or rep.get("hc_en_comments"),
        created=str(rep.get("created_date") or "")[:19] or None, updated=str(rep.get("updated_date") or "")[:19] or None,
    )


COLUMNS = ["id", "kind", "din", "status", "is_open", "brand_name", "common_name", "ingredients", "strength",
           "dosage_form", "route", "company", "atc_code", "tier_3", "late_submission", "reason", "anticipated_start",
           "actual_start", "estimated_end", "actual_end", "discontinued_on", "comments", "created", "updated"]


def load(conn: sqlite3.Connection, path: Path) -> dict:
    data = json.loads(Path(path).read_text())
    reports = data.get("data", data) if isinstance(data, dict) else data
    rows = {}
    for rep in reports:
        r = normalise(rep)
        prev = rows.get(r["id"])
        if prev is None or (r["updated"] or "") >= (prev["updated"] or ""):
            rows[r["id"]] = r
    conn.execute("DELETE FROM shortage_report")
    conn.executemany(f"INSERT INTO shortage_report ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                     [[r[c] for c in COLUMNS] for r in rows.values()])
    open_n = sum(r["is_open"] for r in rows.values())
    D.log_run(conn, "shortages", str(path), len(rows), max((r["updated"] or "" for r in rows.values()), default=None))
    return {"reports": len(rows), "open": open_n}

