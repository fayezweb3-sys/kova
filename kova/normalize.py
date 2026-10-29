"""Normalisation helpers shared by every Kova source loader.

Two products are treated as equivalence candidates when they share the same
active moieties at the same concentration, the same dosage form and the same
route. That is the DPD-level test only: whether a pharmacist may actually
substitute one for the other is decided by each province's interchangeability
list, which is layered on later.
"""

from __future__ import annotations

import re
from datetime import date, datetime

_MASS_TO_MG = {"MG": 1.0, "G": 1000.0, "MCG": 0.001, "UG": 0.001, "KG": 1_000_000.0}
_VOLUME_TO_ML = {"ML": 1.0, "L": 1000.0}
_WS = re.compile(r"\s+")

_DATE_FORMATS = ("%Y-%m-%d", "%d-%b-%Y", "%d-%b-%y", "%Y/%m/%d", "%m/%d/%Y", "%Y-%m-%d %H:%M:%S")


def clean(text: str | None) -> str:
    return _WS.sub(" ", (text or "").strip()).upper()


def moiety(ingredient: str | None) -> str:
    """'ATORVASTATIN (ATORVASTATIN CALCIUM)' -> 'ATORVASTATIN'.

    DPD writes salts as 'MOIETY (SALT FORM)'; strength is usually expressed
    as the moiety, so the part before the parenthesis is what matters.
    """
    text = clean(ingredient)
    if "(" in text and not text.startswith("("):
        text = text.split("(", 1)[0].strip()
    return text


def _to_float(value: str | None) -> float | None:
    if value is None:
        return None
    value = value.strip().replace(",", "")
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _fmt(x: float) -> str:
    return f"{x:.6g}"


def concentration(strength: str | None, strength_unit: str | None,
                  dosage_value: str | None, dosage_unit: str | None) -> str:
    """Normalise strength to a canonical string, e.g. '1.34 MG/ML' or '20 MG'.

    Mass units go to MG and volume units to ML, so '50 MG / 5 ML' and
    '10 MG / ML' produce the same key.
    """
    amount = _to_float(strength)
    unit = clean(strength_unit)
    if amount is None:
        return f"? {unit}".strip()

    if unit in _MASS_TO_MG:
        amount, unit = amount * _MASS_TO_MG[unit], "MG"

    per_amount = _to_float(dosage_value)
    per_unit = clean(dosage_unit)
    if per_unit:
        if per_unit in _VOLUME_TO_ML:
            per_amount = (per_amount or 1.0) * _VOLUME_TO_ML[per_unit]
            per_unit = "ML"
        if per_amount and per_amount != 1.0:
            amount = amount / per_amount
        return f"{_fmt(amount)} {unit}/{per_unit}"
    return f"{_fmt(amount)} {unit}"


def ingredient_key(rows: list[tuple[str, str]]) -> str:
    """rows: [(moiety, concentration)] -> stable, order-independent key."""
    return ";".join(sorted(f"{m}={c}" for m, c in rows))


def set_key(values: list[str]) -> str:
    return "+".join(sorted({clean(v) for v in values if clean(v)}))


def parse_date(value: str | None) -> str | None:
    """Return ISO date (YYYY-MM-DD) or None. Tolerates the formats DPD has used."""
    if not value or not value.strip():
        return None
    raw = value.strip()
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(raw, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def today() -> str:
    return date.today().isoformat()


def din(value) -> str:
    """Canonical 8-character DIN/PIN with leading zeros; '' when not a plausible identifier."""
    s = re.sub(r"\D", "", str(value or ""))
    if not s or len(s) > 8:
        return ""
    return s.zfill(8)


def money(value) -> float | None:
    if value is None:
        return None
    s = str(value).strip().replace("$", "").replace(",", "")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def yyyymmdd(value) -> str | None:
    """'20261030' -> '2026-10-30'. Also accepts ISO dates."""
    s = str(value or "").strip()
    if len(s) == 8 and s.isdigit():
        return f"{s[:4]}-{s[4:6]}-{s[6:]}"
    return parse_date(s[:10]) if s else None
