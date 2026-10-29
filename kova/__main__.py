"""Kova command line.

  python -m kova fetch                 download every source into data/raw/
  python -m kova load                  load whatever is in data/raw/ and record changes
  python -m kova refresh               fetch + load + send watchlist alerts (run this nightly)
  python -m kova serve                 web app and API on http://127.0.0.1:8000
  python -m kova lookup <din|name>     product page in the terminal
  python -m kova alternatives ON <din> what to dispense instead (ON or BC)
  python -m kova watch add "Front shelf" dins.csv --email you@pharmacy.ca
  python -m kova verify                check derived Ontario fields against the e-Formulary
  python -m kova status                what's loaded and when
  python -m kova sample                load built-in sample data into data/sample.sqlite (for trying the app)
  python -m kova build-site            static website in _site/ (home page + app), for free hosting
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from . import db, pipeline
from .services import alternatives, lookup, watch

DATA = Path(os.environ.get("KOVA_DATA", "data"))


def _db(args) -> Path:
    return Path(args.db) if args.db else Path(os.environ.get("KOVA_DB", DATA / "kova.sqlite"))


def _conn(args):
    p = _db(args)
    p.parent.mkdir(parents=True, exist_ok=True)
    return db.connect(p)


def _money(x):
    return "–" if x is None else f"${x:.4f}"


def cmd_fetch(args):
    pipeline.fetch(DATA / "raw", args.only)


def cmd_load(args):
    c = _conn(args)
    for src, r in pipeline.load_all(c, DATA / "raw", args.only).items():
        print(f"{src:10} {json.dumps(r, default=str)}")


def cmd_refresh(args):
    cmd_fetch(args)
    cmd_load(args)
    for r in watch.deliver(_conn(args), DATA / "outbox"):
        print(f"alerts  {r['watchlist']}: {r['events']} change(s); {'; '.join(r['results'])}")


def cmd_serve(args):
    import uvicorn
    os.environ["KOVA_DB"] = str(_db(args))
    print(f"Kova on http://{args.host}:{args.port}  (database {_db(args)})")
    uvicorn.run("kova.api:app", host=args.host, port=args.port)


def cmd_lookup(args):
    c = _conn(args)
    q = " ".join(args.query)
    p = lookup.product(c, q) if q.replace(" ", "").isdigit() else None
    if not p:
        hits = lookup.search(c, q, 20)
        if not hits:
            print("Nothing found.")
            return
        if len(hits) > 1:
            for h in hits:
                flags = ", ".join(x for x in [
                    "OPEN SHORTAGE" if h["open_shortage"] else "", f"ON {h['ontario']}" if h["ontario"] else "",
                    "BC covered" if h["bc"] else "", "not marketed" if h["marketed"] is False else ""] if x)
                print(f"{h['din']}  {h['name']} {h['strength'] or ''}  [{flags}]")
            return
        p = lookup.product(c, hits[0]["din"])
    s = p["summary"]
    print(f"\n{p['name']}  {p['strength'] or ''}  {p['dosage_form'] or ''}\n{'PIN' if p['is_pin'] else 'DIN'} {p['din']} · {p['company'] or ''}")
    hc = p["health_canada"]
    print(f"  Health Canada  {hc['status'] if hc else 'no record (provincial PIN)'}")
    print(f"  Shortage       {'OPEN' + (' (Tier 3)' if s['tier_3'] else '') if s['open_shortage'] else 'no open report'}")
    o = p["ontario"]
    print(f"  Ontario        {s['ontario'] or 'not listed'}" + (f", {_money(o['unit_price'])}" + (", Limited Use" if o["limited_use"] else "") if o else ""))
    b = p["bc"]
    print(f"  BC             {s['bc'] or 'not listed'}" + (f", plans {', '.join(x['plan'] for x in b['plans'])}, max {_money(b['max_price'])}" if b and b["covered"] else ""))
    for r in p["shortages"][:5]:
        print(f"    report {r['id']}: {r['status']} {r['actual_start'] or r['anticipated_start'] or ''} → {r['estimated_end'] or r['actual_end'] or '?'}  {r['reason'] or ''}")
    for r in p["recalls"][:5]:
        print(f"    recall: {r['title']} {r['url'] or ''}")
    print("  Sources: " + "; ".join(f"{v['name']} checked {v['checked'][:10]}" for v in p["sources"].values()))


def cmd_alternatives(args):
    c = _conn(args)
    a = alternatives.find(c, args.province, args.din)
    if not a["listed"]:
        print(f"{a['din']} isn't listed in {a['province']}.")
    print(f"Basis: {a.get('basis')}")
    for i in a["alternatives"]:
        price = i.get("unit_price", i.get("max_price"))
        print(f"  {'✓' if i['available'] else '!'} {i['din']}  {i['name']} {i.get('strength') or ''}  {_money(price)}"
              + ("" if i["available"] else "  (open shortage report)" if i["open_shortage"] else "  (not marketed)"))
    for i in a["related"]:
        print(f"  ~ {i['din']}  {i['name']} {i.get('strength') or ''}  {_money(i.get('unit_price'))}  {i['why']}")
    if not a["alternatives"]:
        print("  No covered interchangeable product found.")


def cmd_watch(args):
    c = _conn(args)
    if args.action == "add":
        text = Path(args.file).read_text() if args.file and Path(args.file).exists() else (args.file or "")
        dins = watch.parse_dins(text)
        w = watch.create(c, args.name, dins, args.email, args.webhook)
        print(f"Watchlist {w['id']} '{w['name']}': {len(w['dins'])} products.")
        if w.get("secret"):
            print(f"Webhook signing secret (shown once): {w['secret']}")
    elif args.action == "list":
        for w in watch.all_lists(c):
            print(f"{w['id']}  {w['name']}  {len(w['dins'])} products  email={w['email'] or '-'}  webhook={w['webhook'] or '-'}")
    elif args.action == "send":
        for r in watch.deliver(c, DATA / "outbox"):
            print(f"{r['watchlist']}: {r['events']} change(s); {'; '.join(r['results'])}")


# Rows read from the Ontario e-Formulary on 28 Oct 2026: (DIN, interchangeable, limited use, benefit status)
EFORMULARY_CHECKS = [
    ("02244403", True, False, "benefit"), ("00369810", True, False, "not a benefit"),
    ("09858341", False, False, "temporary benefit"), ("02261839", True, True, "benefit"),
    ("00773611", True, True, "benefit"), ("02367394", True, False, "benefit"),
    ("00010405", True, False, "not a benefit"), ("00782718", True, False, "benefit"),
]


def cmd_verify(args):
    c = _conn(args)
    ok = bad = missing = 0
    for din, ic, lu, status in EFORMULARY_CHECKS:
        r = lookup.odb_row(c, din)
        if not r:
            missing += 1
            print(f"  ? {din} not in the loaded extract")
            continue
        got = ("temporary benefit" if r["temporary"] else "benefit" if r["benefit"] else "not a benefit")
        diffs = [n for n, a, b in [("interchangeable", bool(r["interchangeable"]), ic), ("limited use", bool(r["limited_use"]), lu),
                                   ("status", got, status)] if a != b]
        if diffs:
            bad += 1
            print(f"  ✗ {din} {r['name']}: {', '.join(diffs)} differ from the e-Formulary  (flags: {r['flags']})")
        else:
            ok += 1
    print(f"{ok} match, {bad} differ, {missing} missing. Re-check rows that differ on formulary.health.gov.on.ca before trusting the rule.")
    sys.exit(1 if bad else 0)


def cmd_status(args):
    c = _conn(args)
    runs = db.last_runs(c)
    for k, name in lookup.SOURCE_NAMES.items():
        r = runs.get(k)
        print(f"{name:42} " + (f"{r['rows']:>7} rows  loaded {r['run_at']}  version {r['version'] or '-'}" if r else "not loaded"))
    print(f"Change events: {c.execute('SELECT COUNT(*) FROM change_event').fetchone()[0]}  ·  Watchlists: {c.execute('SELECT COUNT(*) FROM watchlist').fetchone()[0]}")


def cmd_sample(args):
    from . import sample
    raw = DATA / "sample" / "raw"
    sample.write_all(raw)
    path = DATA / "sample.sqlite"
    if path.exists():
        path.unlink()
    c = db.connect(path)
    pipeline.load_all(c, raw)
    # A second, invented refresh so the Changes page and watchlists have something to show
    w = watch.create(c, "Sample shelf", ["00773611", "02261839", "00782718"])
    groups = sample.odb_groups_copy()
    st, form, lu, drugs = groups["G-LA200"]
    groups["G-LA200"] = (st, form, lu, [d if d[0] != "00773611" else (d[0], d[1], d[2], "0.5900", d[4]) for d in drugs])
    sample.write_odb(raw / "odb" / "formulary.xml", groups)
    sample.write_shortages(raw / "shortages" / "reports.json", [
        sample.report(910001, "02261839", "SANDOZ CARBAMAZEPINE CR", "200MG", "active_confirmed"),
        sample.report(910002, "00782718", "TEVA-CARBAMAZEPINE", "200MG", "resolved"),
        sample.report(910003, "00773611", "TEGRETOL CR", "200MG", "anticipated_shortage", tier3=True)])
    pipeline.load_all(c, raw, only=["odb", "shortages"])
    print(f"Sample data loaded into {path}. Start the app on it with:\n  python -m kova serve --db {path}\n"
          "Ontario carbamazepine listings are real (28 Oct 2026); BC coverage, shortage reports and recalls are invented.")


def cmd_build_site(args):
    from . import site
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    home = Path(args.home) if args.home else Path("website/index.html")
    if home.exists():
        (out / "index.html").write_text(home.read_text())
    r = site.build(_conn(args), out / "app")
    print(f"Site built in {out}: home page {'copied' if home.exists() else 'missing'}; app at {out / 'app'} "
          f"({r['products']} products, {r['shards']} files, {r['megabytes']} MB)")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kova", description="Drug supply and coverage for Ontario and BC.")
    ap.add_argument("--db", help="database file (default data/kova.sqlite)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("fetch", "load", "refresh"):
        p = sub.add_parser(name)
        p.add_argument("--only", nargs="*", choices=pipeline.ORDER)
    p = sub.add_parser("serve")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8000)
    p = sub.add_parser("lookup")
    p.add_argument("query", nargs="+")
    p = sub.add_parser("alternatives")
    p.add_argument("province", choices=["ON", "BC", "on", "bc"])
    p.add_argument("din")
    p = sub.add_parser("watch")
    p.add_argument("action", choices=["add", "list", "send"])
    p.add_argument("name", nargs="?")
    p.add_argument("file", nargs="?", help="CSV or text file of DINs, or the DINs themselves")
    p.add_argument("--email")
    p.add_argument("--webhook")
    p = sub.add_parser("build-site", help="static website for free hosting")
    p.add_argument("--out", default="_site")
    p.add_argument("--home", help="marketing page to use as the site's home (default website/index.html)")
    for name in ("verify", "status", "sample"):
        sub.add_parser(name)
    args = ap.parse_args(argv)
    globals()[f"cmd_{args.cmd.replace('-', '_')}"](args)


if __name__ == "__main__":
    main()
