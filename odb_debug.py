"""Temporary: print how the ODB extract marks a listing, to fix the temporary-benefit rule."""
import collections, sys
import xml.etree.ElementTree as ET

root = ET.parse("data/raw/odb/formulary.xml").getroot()
parent = {c: p for p in root.iter() for c in p}
target = sys.argv[1] if len(sys.argv) > 1 else "09858341"
for d in root.iter("drug"):
    if d.get("id") == target:
        print(ET.tostring(d, encoding="unicode")[:1500])
        pcg9 = parent[d]; grp = parent[pcg9]; gen = parent[grp]
        print("PCG9:", pcg9.attrib, [(c.tag, (c.text or "")[:80]) for c in pcg9 if c.tag != "drug"])
        print("GROUP:", grp.attrib, [(c.tag, c.attrib, (c.text or "")[:120]) for c in grp if c.tag != "pcg9"])
        print("GENERIC:", gen.attrib, [(c.tag, (c.text or "")[:80]) for c in gen if c.tag not in ("pcgGroup",)])
attrs = collections.Counter()
pin_attrs = collections.Counter()
for d in root.iter("drug"):
    for k, v in d.attrib.items():
        if k != "id":
            attrs[f"{k}={v}"] += 1
            if (d.get("id") or "").startswith("09"):
                pin_attrs[f"{k}={v}"] += 1
print("ALL drug attributes:", attrs.most_common())
print("PIN drug attributes:", pin_attrs.most_common())
tags = collections.Counter(c.tag for d in root.iter("drug") for c in d)
print("drug child tags:", tags.most_common())
names = [d.findtext("name") for d in root.iter("drug") if "emporar" in (d.findtext("name") or "") + (d.findtext("note") or "")]
print("names/notes containing 'temporar':", len(names), names[:5])
notes = collections.Counter((n.get("type"), (n.text or "")[:60]) for n in root.iter("lccNote") if "emporar" in (n.text or ""))
print("lccNotes with 'temporar':", notes.most_common(5))
for el in root.iter():
    if el.tag not in ("drug", "lccNote", "name") and "emporar" in (el.text or ""):
        print("other element mentioning temporary:", el.tag, el.attrib, (el.text or "")[:100]); break
