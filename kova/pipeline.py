"""Download every source, load it, and record what changed.

Raw files live in data/raw/<source>/ so a load can be repeated without the network:
  dpd/        allfiles.zip, allfiles_ap.zip, allfiles_ia.zip, allfiles_dr.zip
  odb/        formulary.xml
  bc/         pddf.zip, lca.xlsx, rdp.xlsx
  shortages/  reports.json        (needs a free Health Product Shortages Canada account)
  recalls/    recalls.json
"""

from __future__ import annotations

import os
import re
import sqlite3
from pathlib import Path
from urllib.parse import urljoin

from . import changes
from .sources import bc, dpd, odb, recalls, shortages

ORDER = ["dpd", "odb", "bc", "shortages", "recalls"]
UA = {"User-Agent": "Mozilla/5.0 (Kova pharmacy data loader)"}


def _client():
    import httpx
    return httpx.Client(timeout=180, follow_redirects=True, headers=UA)


def _save(client, url: str, dest: Path, must_start: bytes | None = None) -> Path:
    r = client.get(url)
    r.raise_for_status()
    if must_start and not r.content.startswith(must_start):
        raise RuntimeError(f"{url} returned {r.headers.get('content-type')}, not the expected file")
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(r.content)
    print(f"  {dest.relative_to(dest.parents[2])}  {len(r.content) / 1e6:.1f} MB")
    return dest


def _find_link(client, page: str, pattern: re.Pattern) -> str:
    r = client.get(page)
    r.raise_for_status()
    links = pattern.findall(r.text)
    if not links:
        raise RuntimeError(f"No matching download link on {page}; the page may have changed")
    return urljoin(page, sorted(links)[-1])     # newest file names sort last (they carry the date)


def fetch(raw: Path, only: list[str] | None = None) -> list[str]:
    done, skipped = [], []
    with _client() as c:
        for src in only or ORDER:
            print(f"[{src}]")
            try:
                if src == "dpd":
                    for name, url in dpd.urls().items():
                        _save(c, url, raw / "dpd" / name, b"PK")
                elif src == "odb":
                    _save(c, _find_link(c, odb.PAGE_URL, odb.LINK_PATTERN), raw / "odb" / "formulary.xml", b"<")
                elif src == "bc":
                    _save(c, bc.PDDF_URL, raw / "bc" / "pddf.zip", b"PK")
                    _save(c, _find_link(c, bc.LCA_PAGE_URL, bc.LCA_LINK), raw / "bc" / "lca.xlsx", b"PK")
                    _save(c, _find_link(c, bc.LCA_PAGE_URL, bc.RDP_LINK), raw / "bc" / "rdp.xlsx", b"PK")
                elif src == "shortages":
                    email, pw = os.environ.get("KOVA_HPSC_EMAIL"), os.environ.get("KOVA_HPSC_PASSWORD")
                    if not (email and pw):
                        print("  skipped: set KOVA_HPSC_EMAIL and KOVA_HPSC_PASSWORD (free account at healthproductshortages.ca)")
                        skipped.append(src)
                        continue
                    shortages.fetch(email, pw, raw / "shortages" / "reports.json")
                    print("  shortages/reports.json")
                elif src == "recalls":
                    _save(c, recalls.JSON_URL, raw / "recalls" / "recalls.json")
                done.append(src)
            except Exception as e:  # keep going: one broken source shouldn't block the rest
                print(f"  FAILED: {e}")
                skipped.append(src)
    return done


def available(raw: Path) -> dict[str, dict]:
    """Which sources have files on disk, and where."""
    out = {}
    if list((raw / "dpd").glob("*.zip")):
        out["dpd"] = {"source": raw / "dpd"}
    if (raw / "odb" / "formulary.xml").exists():
        out["odb"] = {"path": raw / "odb" / "formulary.xml"}
    if (raw / "bc" / "pddf.zip").exists() or (raw / "bc" / "pddf.csv").exists():
        p = raw / "bc" / "pddf.zip"
        out["bc"] = {"pddf": p if p.exists() else raw / "bc" / "pddf.csv",
                     "lca": next(iter(sorted((raw / "bc").glob("lca.*"))), None),
                     "rdp": next(iter(sorted((raw / "bc").glob("rdp.*"))), None)}
    if (raw / "shortages" / "reports.json").exists():
        out["shortages"] = {"path": raw / "shortages" / "reports.json"}
    for name in ("recalls.json", "recalls.csv"):
        if (raw / "recalls" / name).exists():
            out["recalls"] = {"path": raw / "recalls" / name}
            break
    return out


LOADERS = {"dpd": dpd.load, "odb": odb.load, "bc": bc.load, "shortages": shortages.load, "recalls": recalls.load}


def load_source(conn: sqlite3.Connection, name: str, **paths) -> dict:
    """Load one source in a single transaction and record its change events."""
    with conn:
        before = changes.snapshot(conn, name)
        result = LOADERS[name](conn, **paths)
        if name == "dpd":                   # names may have changed: relink recalls
            recalls.link_products(conn)
        after = changes.snapshot(conn, name)
        result["events"] = changes.record(conn, name, changes.diff(name, before, after))
    return result


def load_all(conn: sqlite3.Connection, raw: Path, only: list[str] | None = None) -> dict:
    found = available(raw)
    report = {}
    for src in only or ORDER:
        if src in found:
            report[src] = load_source(conn, src, **found[src])
        else:
            report[src] = {"skipped": "no files"}
    return report
