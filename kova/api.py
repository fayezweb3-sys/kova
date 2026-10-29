"""Kova HTTP API (v1) and the web app.

    uvicorn kova.api:app            or            python -m kova serve

Set KOVA_DB to choose the database file (default data/kova.sqlite). Set KOVA_API_KEYS
to a comma-separated list to require an X-API-Key header on /v1 routes.
"""

from __future__ import annotations

import os
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from . import db
from . import normalize as N
from .services import alternatives, lookup, watch

DB_PATH = Path(os.environ.get("KOVA_DB", "data/kova.sqlite"))
WEB = Path(__file__).parent / "web" / "index.html"

app = FastAPI(title="Kova API", version="0.1.0",
              description="Drug supply and coverage for Ontario and BC, from official public sources.")
_conn = None


def conn():
    global _conn
    if _conn is None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        _conn = db.connect(DB_PATH)
    return _conn


def api_key(x_api_key: str | None = Header(default=None)):
    keys = {k.strip() for k in os.environ.get("KOVA_API_KEYS", "").split(",") if k.strip()}
    if keys and x_api_key not in keys:
        raise HTTPException(401, "Missing or invalid X-API-Key")


def _din(value: str) -> str:
    d = N.din(value)
    if not d:
        raise HTTPException(422, "Not a DIN or PIN")
    return d


@app.get("/", include_in_schema=False)
def web():
    return FileResponse(WEB)


@app.get("/health")
def health():
    c = conn()
    return {"ok": True, "products": c.execute("SELECT COUNT(*) FROM product").fetchone()[0],
            "sources": db.last_runs(c)}


v1 = [Depends(api_key)]


@app.get("/v1/sources", dependencies=v1)
def sources():
    runs = db.last_runs(conn())
    return [{"id": k, "name": lookup.SOURCE_NAMES[k], **(runs.get(k) or {"run_at": None})} for k in lookup.SOURCE_NAMES]


@app.get("/v1/search", dependencies=v1)
def search(q: str = Query(..., min_length=2), limit: int = Query(25, le=100)):
    return lookup.search(conn(), q, limit)


@app.get("/v1/products/{din}", dependencies=v1)
def product(din: str):
    p = lookup.product(conn(), _din(din))
    if not p:
        raise HTTPException(404, "No product with that DIN or PIN in any source")
    return p


@app.get("/v1/coverage/{province}/{din}", dependencies=v1)
def coverage(province: str, din: str):
    d, p = _din(din), province.upper()
    c = conn()
    if p == "ON":
        row = lookup.odb_row(c, d)
        status = None if not row else ("temporary_benefit" if row["temporary"] else "benefit" if row["benefit"] else "not_a_benefit")
        return {"din": d, "province": "ON", "listed": bool(row), "status": status or "not_listed", "listing": row,
                "source": {"name": lookup.SOURCE_NAMES["odb"], **(db.last_runs(c).get("odb") or {})}}
    if p == "BC":
        cov = lookup.bc_current(c, d)
        return {"din": d, "province": "BC", "listed": bool(cov), "status": ("covered" if cov and cov["covered"] else "not_covered") if cov else "not_listed",
                "coverage": cov, "source": {"name": lookup.SOURCE_NAMES["bc"], **(db.last_runs(c).get("bc") or {})}}
    raise HTTPException(422, "province must be ON or BC")


@app.get("/v1/alternatives/{province}/{din}", dependencies=v1)
def alts(province: str, din: str):
    try:
        return alternatives.find(conn(), province, _din(din))
    except ValueError as e:
        raise HTTPException(422, str(e))


@app.get("/v1/shortages", dependencies=v1)
def shortages(din: str | None = None, open_only: bool = Query(True, alias="open"), limit: int = Query(100, le=1000)):
    sql, args = "SELECT * FROM shortage_report WHERE 1=1", []
    if din:
        sql, args = sql + " AND din = ?", [*args, _din(din)]
    if open_only:
        sql += " AND is_open = 1"
    sql += " ORDER BY tier_3 DESC, COALESCE(updated, created) DESC LIMIT ?"
    return [dict(r) for r in conn().execute(sql, (*args, limit))]


@app.get("/v1/events", dependencies=v1)
def events(din: str | None = None, limit: int = Query(50, le=500)):
    return watch.recent_events(conn(), [_din(din)] if din else None, limit)


class WatchlistIn(BaseModel):
    name: str = Field(..., min_length=1, max_length=80)
    dins: list[str] = []
    text: str | None = Field(None, description="Pasted text or CSV; every DIN found is added")
    email: str | None = None
    webhook: str | None = None
    kinds: list[str] | None = None


def _dins_from(body: WatchlistIn) -> list[str]:
    found = [N.din(d) for d in body.dins] + watch.parse_dins(body.text or "")
    return list(dict.fromkeys(d for d in found if d))


@app.get("/v1/watchlists", dependencies=v1)
def watchlists():
    return [{k: v for k, v in w.items() if k != "secret"} for w in watch.all_lists(conn())]


@app.post("/v1/watchlists", dependencies=v1, status_code=201)
def create_watchlist(body: WatchlistIn):
    dins = _dins_from(body)
    if not dins:
        raise HTTPException(422, "No DINs found")
    return watch.create(conn(), body.name, dins, body.email, body.webhook, body.kinds)   # secret shown once, for webhook signing


@app.get("/v1/watchlists/{wid}", dependencies=v1)
def get_watchlist(wid: int):
    w = watch.get(conn(), wid)
    if not w:
        raise HTTPException(404, "No such watchlist")
    w.pop("secret", None)
    w["products"] = lookup.search_dins(conn(), w["dins"])
    return w


@app.put("/v1/watchlists/{wid}", dependencies=v1)
def update_watchlist(wid: int, body: WatchlistIn):
    if not watch.get(conn(), wid):
        raise HTTPException(404, "No such watchlist")
    w = watch.set_items(conn(), wid, _dins_from(body))
    w.pop("secret", None)
    return w


@app.delete("/v1/watchlists/{wid}", dependencies=v1, status_code=204)
def delete_watchlist(wid: int):
    watch.delete(conn(), wid)


@app.get("/v1/watchlists/{wid}/events", dependencies=v1)
def watchlist_events(wid: int, since: int = 0):
    return watch.events_for(conn(), wid, since=since)
