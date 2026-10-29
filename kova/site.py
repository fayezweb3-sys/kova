"""Build Kova as a static website: no server, so it can be hosted free (GitHub Pages).

    python -m kova build-site --out _site

Output:
  index.html              the Kova web app, in static mode
  data/meta.json          sources and load dates
  data/search.json        compact search index (one row per DIN)
  data/p/<12345>.json     product pages + Ontario and BC alternatives, sharded by the first 5 digits of the DIN
  data/changes.json       the latest change events
These JSON files double as a read-only API for online pharmacies.
A GitHub Actions workflow (.github/workflows/site.yml) refreshes the data and rebuilds nightly.
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from collections import defaultdict
from pathlib import Path

from . import db as D
from .services import alternatives, lookup, watch

WEB = Path(__file__).parent / "web" / "index.html"
ON_CODE = {"benefit": 1, "temporary benefit": 2, "not a benefit": 3}


def dins_to_publish(conn: sqlite3.Connection) -> list[str]:
    q = """SELECT din FROM product WHERE din <> '' AND (class IS NULL OR class = 'Human') AND dataset <> 'cancelled'
           UNION SELECT din FROM odb_listing
           UNION SELECT din FROM bc_coverage WHERE is_current = 1
           UNION SELECT din FROM shortage_report WHERE din IS NOT NULL"""
    return sorted({r[0] for r in conn.execute(q) if r[0]})


def _compact(obj):
    """Drop empty values to keep the shards small."""
    if isinstance(obj, dict):
        return {k: _compact(v) for k, v in obj.items() if v not in (None, "", [], {})}
    if isinstance(obj, list):
        return [_compact(v) for v in obj]
    return obj


def build(conn: sqlite3.Connection, out: Path, events: int = 3000, extra_files: dict[str, Path] | None = None) -> dict:
    out = Path(out)
    data = out / "data"
    if data.exists():
        shutil.rmtree(data)
    (data / "p").mkdir(parents=True)
    dins = dins_to_publish(conn)
    ingredients = defaultdict(set)
    for r in conn.execute("SELECT p.din, pi.moiety FROM product_ingredient pi JOIN product p ON p.drug_code = pi.drug_code WHERE p.din <> ''"):
        ingredients[r[0]].add(r[1])

    shards: dict[str, dict] = defaultdict(dict)
    index = []
    for din in dins:
        p = lookup.product(conn, din)
        if not p:
            continue
        p.pop("sources", None)
        on = alternatives.find(conn, "ON", din) if p["ontario"] else None
        bc = alternatives.find(conn, "BC", din) if p["bc"] or p["health_canada"] else None
        shards[din[:5]][din] = _compact({"product": p, "alt": {"ON": on, "BC": bc}})
        s = p["summary"]
        index.append([
            din, p["name"] or "", p["strength"] or "", p["dosage_form"] or "", p["company"] or "",
            " ".join(sorted(ingredients.get(din, set()))) or (p["ontario"] or {}).get("generic_name", "") or "",
            None if s["marketed"] is None else int(s["marketed"]), int(s["open_shortage"]) + int(s["tier_3"]),
            ON_CODE.get(s["ontario"], 0), (p["ontario"] or {}).get("unit_price"), int(s["bc"] == "covered") if s["bc"] else 0,
        ])
    for prefix, items in shards.items():
        (data / "p" / f"{prefix}.json").write_text(json.dumps(items, separators=(",", ":")))
    (data / "search.json").write_text(json.dumps(index, separators=(",", ":")))
    runs = D.last_runs(conn)
    meta = {"built": D.now(), "products": len(index),
            "sources": {k: {"name": lookup.SOURCE_NAMES[k], "run_at": v["run_at"], "rows": v["rows"], "version": v["version"],
                            "sample": "sample" in (v.get("origin") or "")} for k, v in runs.items()}}
    (data / "meta.json").write_text(json.dumps(meta))
    (data / "changes.json").write_text(json.dumps(
        [{k: e[k] for k in ("id", "at", "source", "kind", "din", "summary")} for e in watch.recent_events(conn, None, events)],
        separators=(",", ":")))
    html = WEB.read_text().replace("<!--STATIC-->", '<script>window.KOVA_STATIC = true;</script>')
    (out / "index.html").write_text(html)
    for name, src in (extra_files or {}).items():
        dest = out / name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
    (out / ".nojekyll").write_text("")
    size = sum(f.stat().st_size for f in out.rglob("*") if f.is_file())
    return {"products": len(index), "shards": len(shards), "megabytes": round(size / 1e6, 1)}
