"""Watchlists and alert delivery.

A watchlist is a name, an optional email and/or webhook, and a list of DINs.
After each refresh, `deliver` sends every new change event that touches a watched
DIN, then remembers the last event delivered so nothing is sent twice.

Email goes through SMTP when KOVA_SMTP_HOST is set; otherwise messages are written
to data/outbox/ as .eml files (handy for testing). Webhooks get a JSON POST signed
with HMAC-SHA256 of the body, in the X-Kova-Signature header.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import smtplib
import sqlite3
from email.message import EmailMessage
from pathlib import Path

from .. import db as D
from .. import normalize as N

KINDS = ("shortage", "recall", "coverage", "generic", "status")
KIND_LABEL = {"shortage": "Shortage", "recall": "Recall", "coverage": "Coverage", "generic": "New generic", "status": "Status"}


def parse_dins(text: str) -> list[str]:
    """Pull DINs out of pasted text or a CSV export: any 6–8 digit run, zero-padded."""
    found = [N.din(m) for m in re.findall(r"(?<!\d)\d{6,8}(?!\d)", text or "")]
    return list(dict.fromkeys(d for d in found if d))


def create(conn, name: str, dins: list[str], email: str | None = None, webhook: str | None = None,
           kinds: list[str] | None = None) -> dict:
    kinds = [k for k in (kinds or KINDS) if k in KINDS]
    with conn:
        cur = conn.execute("INSERT INTO watchlist (name, email, webhook, secret, kinds, created, last_event) VALUES (?,?,?,?,?,?,(SELECT COALESCE(MAX(id),0) FROM change_event))",
                           (name, email, webhook, secrets.token_hex(16) if webhook else None, ",".join(kinds), D.now()))
        wid = cur.lastrowid
        conn.executemany("INSERT OR IGNORE INTO watch_item VALUES (?,?)", [(wid, d) for d in dins])
    return get(conn, wid)


def get(conn, wid: int) -> dict | None:
    w = conn.execute("SELECT * FROM watchlist WHERE id = ?", (wid,)).fetchone()
    if not w:
        return None
    out = dict(w)
    out["dins"] = [r["din"] for r in conn.execute("SELECT din FROM watch_item WHERE watchlist_id = ? ORDER BY din", (wid,))]
    out["kinds"] = out["kinds"].split(",")
    return out


def all_lists(conn) -> list[dict]:
    return [get(conn, r["id"]) for r in conn.execute("SELECT id FROM watchlist ORDER BY id")]


def set_items(conn, wid: int, dins: list[str]) -> dict:
    with conn:
        conn.execute("DELETE FROM watch_item WHERE watchlist_id = ?", (wid,))
        conn.executemany("INSERT OR IGNORE INTO watch_item VALUES (?,?)", [(wid, d) for d in dins])
    return get(conn, wid)


def delete(conn, wid: int) -> None:
    with conn:
        conn.execute("DELETE FROM watchlist WHERE id = ?", (wid,))


def events_for(conn, wid: int, since: int | None = None, limit: int = 200) -> list[dict]:
    w = get(conn, wid)
    if not w:
        return []
    since = w["last_event"] if since is None else since
    kinds = w["kinds"]
    rows = conn.execute(f"""SELECT e.* FROM change_event e JOIN watch_item i ON i.din = e.din AND i.watchlist_id = ?
                            WHERE e.id > ? AND e.kind IN ({','.join('?' * len(kinds))}) ORDER BY e.id LIMIT ?""",
                        (wid, since, *kinds, limit))
    return [dict(r) | {"detail": json.loads(r["detail"] or "{}")} for r in rows]


def recent_events(conn, dins: list[str] | None = None, limit: int = 50) -> list[dict]:
    if dins:
        rows = conn.execute(f"SELECT * FROM change_event WHERE din IN ({','.join('?' * len(dins))}) ORDER BY id DESC LIMIT ?", (*dins, limit))
    else:
        rows = conn.execute("SELECT * FROM change_event ORDER BY id DESC LIMIT ?", (limit,))
    return [dict(r) | {"detail": json.loads(r["detail"] or "{}")} for r in rows]


def render_email(w: dict, events: list[dict]) -> EmailMessage:
    msg = EmailMessage()
    msg["Subject"] = f"Kova: {len(events)} change{'s' if len(events) != 1 else ''} on {w['name']}"
    msg["From"] = os.environ.get("KOVA_MAIL_FROM", "alerts@kova.local")
    msg["To"] = w["email"]
    lines = [f"{len(events)} change{'s' if len(events) != 1 else ''} to products on your watchlist \"{w['name']}\":", ""]
    for e in events:
        lines.append(f"[{KIND_LABEL.get(e['kind'], e['kind'])}] DIN {e['din']}: {e['summary']}")
    lines += ["", "Each change comes from an official source; check the source before acting.",
              "Kova supports professional judgement and doesn't replace it."]
    msg.set_content("\n".join(lines))
    return msg


def _send_email(msg: EmailMessage, outbox: Path) -> str:
    host = os.environ.get("KOVA_SMTP_HOST")
    if not host:
        outbox.mkdir(parents=True, exist_ok=True)
        path = outbox / f"{D.now().replace(':', '')}-{secrets.token_hex(3)}.eml"
        path.write_bytes(bytes(msg))
        return f"written to {path}"
    with smtplib.SMTP(host, int(os.environ.get("KOVA_SMTP_PORT", "587")), timeout=30) as s:
        s.starttls()
        if os.environ.get("KOVA_SMTP_USER"):
            s.login(os.environ["KOVA_SMTP_USER"], os.environ.get("KOVA_SMTP_PASSWORD", ""))
        s.send_message(msg)
    return f"sent to {msg['To']}"


def sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def _post_webhook(w: dict, events: list[dict]) -> str:
    import httpx
    body = json.dumps({"watchlist": {"id": w["id"], "name": w["name"]},
                       "events": [{k: e[k] for k in ("id", "at", "source", "kind", "din", "summary")} for e in events]}).encode()
    r = httpx.post(w["webhook"], content=body, timeout=20,
                   headers={"Content-Type": "application/json", "X-Kova-Signature": sign(w["secret"], body)})
    r.raise_for_status()
    return f"webhook {r.status_code}"


def deliver(conn: sqlite3.Connection, outbox: Path) -> list[dict]:
    report = []
    for w in all_lists(conn):
        events = events_for(conn, w["id"])
        if not events:
            continue
        results = []
        if w["email"]:
            results.append(_send_email(render_email(w, events), outbox))
        if w["webhook"]:
            try:
                results.append(_post_webhook(w, events))
            except Exception as e:
                results.append(f"webhook failed: {e}")      # retried next run: last_event isn't advanced
                report.append({"watchlist": w["name"], "events": len(events), "results": results})
                continue
        with conn:
            conn.execute("UPDATE watchlist SET last_event = ? WHERE id = ?", (events[-1]["id"], w["id"]))
        report.append({"watchlist": w["name"], "events": len(events), "results": results})
    return report
