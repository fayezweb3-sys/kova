"""Ontario Drug Benefit Formulary: monthly XML data extract (Edition 43 schema, April 2021).

Structure (from the published extract):
  <extract createDate>
    <manufacturerList><manufacturer id>Name</manufacturer>...</manufacturerList>
    <formulary edition updateVer formularyDate createDate>
      <pcg2 id><name/>                       therapeutic class
        <pcg6 id><name/>                     sub-class
          <genericName id><name/>
            <pcgGroup lccId?>                one coverage group; lccNote = Limited Use notes
              <lccNote seq reasonForUseId type>text</lccNote>
              <pcg9 id><itemNumber/><strength/><dosageForm/><dailyCost/>?
                <drug id notABenefit? sec3? sec3b? sec3bEAP? sec12? dinStatus?>
                  <name/><manufacturerId/><individualPrice/><dailyCost/>?<listingDate/><amountMOHLTCPays/>?

Derived fields, and the rule behind each (checked against the e-Formulary by `kova verify`):
  interchangeable  the drug shares its pcg9 group with at least one other listed product
  limited_use      its pcgGroup carries a Limited Use note (lccNote with reasonForUseId)
  temporary        "temporary benefit" appears in its name or note
  benefit          no notABenefit attribute
"""

from __future__ import annotations

import re
import sqlite3
import xml.etree.ElementTree as ET
from pathlib import Path

from .. import db as D
from .. import normalize as N

PAGE_URL = "https://www.ontario.ca/document/ontario-drug-benefit-odb-formulary-comparative-drug-index-cdi-and-monthly-formulary-0"
LINK_PATTERN = re.compile(r'href="([^"]*formulary[^"]*data-extract[^"]*\.xml)"', re.I)
FLAG_ATTRS = ("sec3", "sec3b", "sec3bEAP", "sec12", "dinStatus")
_TEMP = re.compile(r"temporary\s+benefit", re.I)
_NAME_SUFFIX = re.compile(r"\s*\((?:not a benefit|temporary benefit)\)\s*$", re.I)


def _text(el, tag):
    child = el.find(tag)
    return (child.text or "").strip() if child is not None and child.text else ""


def parse(path: Path) -> tuple[list[dict], dict]:
    tree = ET.parse(path)
    root = tree.getroot()
    makers = {m.get("id"): (m.text or "").strip() for m in root.iter("manufacturer")}
    formulary = root.find("formulary") if root.tag != "formulary" else root
    if formulary is None:
        raise ValueError(f"{path} has no <formulary> element; is it the ODB data extract?")
    meta = {k: formulary.get(k) for k in ("edition", "updateVer", "formularyDate", "createDate")}
    meta["createDate"] = meta["createDate"] or root.get("createDate")

    rows = []
    for pcg2 in formulary.iter("pcg2"):
        klass = _text(pcg2, "name")
        for gen in pcg2.iter("genericName"):
            gname, gid = _text(gen, "name"), gen.get("id")
            for group in gen.findall("pcgGroup"):
                lu = [n.get("reasonForUseId") for n in group.findall("lccNote") if n.get("reasonForUseId")]
                for pcg9 in group.findall("pcg9"):
                    drugs = pcg9.findall("drug")
                    listed = [d for d in drugs if d.get("id")]
                    for d in listed:
                        name, note = _text(d, "name"), _text(d, "note")
                        mid = _text(d, "manufacturerId")
                        price = N.money(_text(d, "individualPrice"))
                        pays = N.money(_text(d, "amountMOHLTCPays"))
                        rows.append(dict(
                            din=N.din(d.get("id")), is_pin=int(N.din(d.get("id")).startswith("09")),
                            name=_NAME_SUFFIX.sub("", name), generic_name=gname, generic_id=gid,
                            strength=_text(pcg9, "strength"), dosage_form=_text(pcg9, "dosageForm"),
                            group_id=pcg9.get("id"), group_size=len(listed),
                            manufacturer_id=mid, manufacturer=makers.get(mid, mid),
                            unit_price=price, amount_odb_pays=pays,
                            daily_cost=N.money(_text(d, "dailyCost") or _text(pcg9, "dailyCost")),
                            listing_date=N.parse_date(_text(d, "listingDate")) or _text(d, "listingDate") or None,
                            benefit=0 if d.get("notABenefit") else 1,
                            interchangeable=int(len(listed) > 1),
                            limited_use=int(bool(lu)), lu_codes=",".join(sorted(set(lu))) or None,
                            temporary=int(bool(_TEMP.search(name) or _TEMP.search(note))),
                            flags=",".join(f"{a}={d.get(a)}" for a in FLAG_ATTRS if d.get(a)) or None,
                            therapeutic_class=klass, formulary_date=meta["formularyDate"],
                        ))
    return rows, meta


COLUMNS = ["din", "is_pin", "name", "generic_name", "generic_id", "strength", "dosage_form", "group_id",
           "group_size", "manufacturer_id", "manufacturer", "unit_price", "amount_odb_pays", "daily_cost",
           "listing_date", "benefit", "interchangeable", "limited_use", "lu_codes", "temporary", "flags",
           "therapeutic_class", "formulary_date"]


def load(conn: sqlite3.Connection, path: Path) -> dict:
    rows, meta = parse(path)
    if not rows:
        raise ValueError(f"No drug listings found in {path}")
    conn.execute("DELETE FROM odb_listing")
    # A DIN can appear in more than one group (rare); keep the benefit listing, then the cheapest.
    rows.sort(key=lambda r: (r["din"], -r["benefit"], r["unit_price"] if r["unit_price"] is not None else 9e9))
    seen, unique = set(), []
    for r in rows:
        if r["din"] and r["din"] not in seen:
            seen.add(r["din"])
            unique.append(r)
    conn.executemany(f"INSERT INTO odb_listing ({','.join(COLUMNS)}) VALUES ({','.join('?' * len(COLUMNS))})",
                     [[r[c] for c in COLUMNS] for r in unique])
    D.log_run(conn, "odb", str(path), len(unique), meta.get("formularyDate") or meta.get("createDate"))
    return {"listings": len(unique), **meta}
