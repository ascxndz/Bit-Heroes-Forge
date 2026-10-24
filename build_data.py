"""Pull ancient-gear data from the Bit Heroes Fandom wiki into data.json.

Run: python build_data.py   (re-run whenever the wiki adds a tier)
"""
import json
import os
import re
import subprocess
import urllib.parse
import urllib.request

API = "https://bit-heroes.fandom.com/api.php?action=parse&prop=wikitext&format=json&formatversion=2&redirects=1&page="

# Items that share one craft recipe; first page holds the recipe, all are reforge versions of each other.
FAMILIES = [
    ["Harvester"],
    ["Starweave"],
    ["Polychromatic Blaster"],
    ["Elementarium"],
    ["Ladener Broom", "Aryagn Sight"],
    ["Primordial Codex"],
    ["Evolvium"],
    ["Flamium Protector", "Firium Helmet"],
    ["Soul of Escarium", "Heart of Escarium"],
    ["64-bit Clover"],
    ["Sword of Escarium", "Spear of Escarium", "Shield of Escarium"],
    ["W3-4TY"],
    ["Sacramentum"],
    ["Nevermore Helmet", "Evermore Helmet"],
]
# Versions that live on one page (no separate page per version).
SAME_PAGE_VERSIONS = {
    "Starweave": ["Neck", "Ring"],
    "Polychromatic Blaster": ["Mainhand", "Offhand"],
    "Elementarium": ["Head", "Body"],
    "Primordial Codex": ["Offensive", "Defensive"],
    "Evolvium": ["Offensive", "Defensive"],
    "Sacramentum": ["Infestus · Offensive", "Effusio · Defensive", "Subsidium · Support"],
}


def wikitext(page):
    req = urllib.request.Request(API + urllib.parse.quote(page), headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["parse"]["wikitext"]


def plain(s):
    """Strip wiki markup down to readable text."""
    s = re.sub(r"<ref>.*?</ref>|<!--.*?-->", "", s, flags=re.S)
    s = re.sub(r"\[\[File:[^\]]*\]\]|\{\{[^}]*\}\}", "", s)
    s = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"<br\s*/?>", ", ", s)
    s = re.sub(r"<[^>]+>|'''?", "", s).replace("&amp;", "&").replace("&nbsp;", " ")
    return re.sub(r"\s+", " ", s).strip(" ,")


def cells(row):
    """Split a wikitable row into cells, ignoring '|' inside [[links]] and {{templates}}."""
    row = re.sub(r"\[\[.*?\]\]|\{\{.*?\}\}", lambda m: m.group().replace("|", "\0"), row, flags=re.S)
    out = [c.replace("\0", "|") for c in row.rstrip().removesuffix("|}").split("\n|")[1:]]
    return [re.sub(r'^\s*(?:rowspan|colspan|style)="[^"]*"\s*\|', "", c).strip() for c in out]


def parse_tiers(text):
    """Gear-material table -> {tier: {legendary, mythic:[...], ancient:[...]}}."""
    tiers, cur = {}, None
    for row in text.split("\n|-")[1:]:
        head = re.search(r"'''<u>T(\d+)</u>'''", row)
        if head:
            cur = int(head.group(1))
            tiers[cur] = {"legendary": None, "legendarySources": [], "mythic": [], "ancient": []}
            continue
        rarity = re.search(r"\{\{Icon(\w+)\}\}", row)
        name = re.search(r"\n\|'''(.+?)'''", row)
        if cur is None or not (rarity and name):
            continue
        src = cells(row)[-1]
        r, n = rarity.group(1), name.group(1).strip()
        if r == "Legendary":
            tiers[cur]["legendary"] = n
            tiers[cur]["legendarySources"] = [plain(x) for x in src.split("\n*")[1:]]
        elif r == "Mythic":
            tiers[cur]["mythic"].append({"name": n, "source": plain(src)})
        elif r == "Ancient":
            tiers[cur]["ancient"].append({"name": n, "source": plain(src)})
    return tiers


def infobox(text):
    box = re.search(r"\{\{AncientInfobox(.*?)\n\}\}", text, re.S).group(1)
    return {k.strip(): v.strip() for k, v in re.findall(r"\|\s*([\w ]+?)\s*=\s*(.*)", box)}


def first_table_after(text, heading_re):
    m = re.search(heading_re + r".*?(\{\|.*?\n\|\})", text, re.S)
    return m.group(1) if m else ""


MAT_ICONS = {"gold": "Currency_Gold.png"}  # material name (lowercase) -> wiki icon file


def parse_recipe(table):
    out = []
    for row in table.split("\n|-")[1:]:
        cs = [c for c in cells(row) if c and not c.startswith("{{Icon") and not c.startswith("[[File:")]
        if "Currency_Gold" in row:
            out.append({"name": "Gold", "amount": cs[-1], "source": ""})
        elif len(cs) >= 2:
            out.append({"name": plain(cs[0]), "amount": plain(cs[1]), "source": plain(cs[2]) if len(cs) > 2 else ""})
            if first_file(row):  # recipe rows sometimes spell the name differently from the materials table
                MAT_ICONS.setdefault(out[-1]["name"].lower(), first_file(row))
    return out


def material_icons(gear_text):
    """Every row of the gear-materials table: name -> icon file."""
    for row in gear_text.split("\n|-")[1:]:
        name, f = re.search(r"\n\|'''(.+?)'''", row), first_file(row)
        if name and f:
            MAT_ICONS[name.group(1).strip().lower()] = f


def parse_item(page):
    t = wikitext("Equipment/Ancient/" + page)
    ib = infobox(t)
    extra = None
    if page == "Starweave":  # Ropralt Crystal is itself traded from T9 mythics
        extra = {"name": "Ropralt Crystal", "recipe": parse_recipe(first_table_after(t, r"Ropralt Crystal is created"))}
    notes = []
    if "uptiering a defensive evolvium" in t.lower():
        notes.append("Uptiering a Defensive Evolvium turns it Offensive; reforge it back for 100 legendary mats.")
    return {
        "page": page,
        "title": page,  # the Evermore page reuses Nevermore's infobox title
        "tier": int(ib["tier"]),
        "type": plain(ib.get("type", "")),
        "dungeon": plain(ib.get("dungeon", "") or ib.get("world boss", "")),
        "recipe": parse_recipe(first_table_after(t, r"==\s*Craft\w*\s*==")),
        "subRecipe": extra,
        "notes": notes,
    }


def parse_bonuses(text):
    """Overview table on Equipment/Ancient -> {lowercased item name: [bonus per version]}."""
    bonuses, prev, key = {}, "", None
    for row in text.split("==Upgrade==")[0].split("\n|-")[1:]:
        name = re.search(r"\[\[Equipment/Ancient/[^|\]]+\|'''(.+?)'''\]\]", row)
        raw = cells(row)[-1] if cells(row) else ""
        if name:
            n, last = plain(name.group(1)), plain(raw)
            prev = prev if last == n else last  # row covered by a rowspan bonus from the row above
            key = n.lower()
            bonuses.setdefault(key, []).append(prev)
        elif key and raw and not raw.startswith("'''"):  # extra version row (Sacramentum), not a '''Ring''' type label
            bonuses[key].append(plain(raw))
    return bonuses


def bonus_for(bonuses, title):
    t = title.lower()
    return bonuses.get(t) or bonuses.get(t.removesuffix(" helmet")) or [""]


def version_bonuses(items, versions, bonuses):
    """{version name: bonus}; same-page versions take the matching overview row, or share the one bonus."""
    if len(items) > 1:
        return {i["title"]: bonus_for(bonuses, i["title"])[0] for i in items}
    found = bonus_for(bonuses, items[0]["title"])
    return {v: found[k] if len(found) == len(versions) else found[0] for k, v in enumerate(versions)}


SLOTS = ["Mainhand", "Offhand", "Head", "Body", "Neck", "Ring"]


def slug(*parts):
    return re.sub(r"[^a-z0-9]+", "-", "-".join(parts).lower()).strip("-")


def first_file(cell):
    m = re.search(r"\[\[File:([^|\]]+)", cell)
    return m.group(1).strip() if m else None


def tier_of(cell):
    t = plain(cell).replace("\xa0", "").strip()
    return t  # "T12", "T12+", "All"


def location_of(cell):
    """Location text, keeping an <abbr title> detail like 'Idol Dimension - Mowhi'."""
    title = re.search(r'<abbr title="([^"]+)"', cell)
    loc = plain(cell)
    return f"{loc} ({title.group(1)})" if title else loc


def subtype_of(cell):
    m = re.search(r'<abbr title="([^"]+)"', cell)
    return m.group(1) if m else ""


def parse_sets(text):
    """Equipment/Set -> (sets {key: {name, bonuses:[{need, of, text}]}}, items [...])."""
    sets, items = {}, []
    for row in text.split("\n|-")[1:]:
        head = row.split("\n")[0]
        key = re.search(r'class="([^" ]+)', head)
        if not key:
            continue
        key = key.group(1)
        if " info" in head:
            name = re.search(r"!'''(.+?)'''", row)
            if name:
                body = plain(re.sub(r"\n\*", " ", row.split("colspan", 1)[-1]))
                parts = re.split(r"\((\d+)/(\d+)\)", body)
                bonuses = [{"need": int(parts[i]), "of": int(parts[i + 1]), "text": parts[i + 2].strip(" *,.") + "."}
                           for i in range(1, len(parts) - 2, 3)]
                sets[key] = {"name": plain(name.group(1)), "bonuses": bonuses}
            continue
        cs = cells(row)
        if len(cs) < 6:
            continue
        slot = plain(re.sub(r"<small>.*?</small>", "", cs[3]))
        if slot not in SLOTS:
            continue
        items.append({"id": slug("s", plain(cs[1]), key), "kind": "set", "name": plain(cs[1]), "slot": slot,
                      "subtype": subtype_of(cs[3]), "tier": tier_of(cs[4]), "set": key,
                      "where": location_of(cs[5]), "icon": first_file(cs[0])})
    return sets, items


def parse_mythics(text):
    items = []
    for row in text.split("\n|-")[1:]:
        cs = cells(row)
        if len(cs) < 6 or "id=" not in row.split("\n")[0]:
            continue
        slot = plain(re.sub(r"<small>.*?</small>", "", cs[2]))
        if slot not in SLOTS:
            continue
        bonus = plain(re.sub(r"'''Element Bonus''':", "· Element bonus:", re.sub(r"'''Bonus''':", "", "\n|".join(cs[5:]))))
        items.append({"id": slug("m", plain(cs[1])), "kind": "mythic", "name": plain(cs[1]), "slot": slot,
                      "subtype": subtype_of(cs[2]), "tier": tier_of(cs[3]), "where": location_of(cs[4]),
                      "bonus": bonus, "icon": first_file(cs[0])})
    return items


def parse_accessories(text):
    items, section = [], ""
    for block in re.split(r"(?m)^(==[^=].*?==)\s*$", text):
        if block.startswith("=="):
            section = block.strip("= ")
            continue
        for table in re.findall(r"\{\|(.*?)\n\|\}", block, re.S):
            rarity = re.search(r"\b(common|rare|epic|legendary|mythical|set)\b", table.split("\n")[0])
            rarity = {"mythical": "mythic"}.get(rarity.group(1), rarity.group(1)) if rarity else section.lower()
            rows = re.split(r"\n\|-(?!c)", table)
            for k, row in enumerate(rows):
                name = re.search(r"\n\|'''(.+?)'''", row)
                if not name:
                    continue
                cs = [c for c in cells(row) if c]
                i = next(j for j, c in enumerate(cs) if "'''" in c and name.group(1) in c)
                rest = cs[i + 1:]
                # the source sits in the first cell of the following row (the item row uses rowspan=2)
                after = [c for c in cells(rows[k + 1]) if c] if k + 1 < len(rows) else []
                source = plain(after[0]) if after else ""
                items.append({"id": slug("x", rarity, plain(name.group(1))), "kind": "accessory", "slot": "Accessory",
                              "name": plain(name.group(1)), "rarity": rarity, "group": section,
                              "stats": plain(rest[0]) if rest else "", "upgrade": plain(rest[1]) if len(rest) > 1 else "",
                              "where": source, "icon": first_file(row)})
    return items


def parse_ancient_icons(text):
    icons = {}
    for row in text.split("==Upgrade==")[0].split("\n|-")[1:]:
        name = re.search(r"\[\[Equipment/Ancient/[^|\]]+\|'''(.+?)'''\]\]", row)
        if name:
            icons[plain(name.group(1)).lower()] = first_file(row)
    return icons


def ancient_items(families, icons, sh):
    """One builder item per Ancient version, placed in its slot(s)."""
    out = []
    for f in families:
        for v, bonus in f["bonuses"].items():
            suffix = re.search(r"\(([^)]+)\)$", v)
            slots = [suffix.group(1)] if suffix and suffix.group(1) in SLOTS else [
                s for s in SLOTS if s in f["type"].split(" / ")[min(list(f["bonuses"]).index(v), len(f["type"].split(" / ")) - 1)].split("/")]
            base = v.split(" (")[0]
            icon = icons.get(base.lower()) or icons.get(base.lower().removesuffix(" helmet"))
            for s in slots:
                out.append({"id": slug("a", v, s), "kind": "ancient", "name": v, "slot": s, "tier": f"T{f['tier']}",
                            "family": f["id"], "where": f["dungeon"], "bonus": bonus, "icon": icon})
    out.append({"id": "a-supercharged-harvester", "kind": "ancient", "name": "Supercharged Harvester", "slot": "Mainhand",
                "tier": "T21", "family": "harvester", "where": sh["dungeon"], "bonus": sh["bonus"],
                "icon": icons.get("harvester")})
    return out


RUNE_TYPES = ["Minor", "Major", "Meta", "Relic", "Artifact"]
STAT_ALIASES = {"Deflect": "Deflect Chance", "Absorb": "Absorb Chance"}


def fill_x(desc, value, columns):
    """Put the mythic value(s) into the X placeholders. In pairs like '15/25' the part that never changes across
    rarities is the health threshold, so it goes in the X before '% health'."""
    parts = value.split("/")
    if len(parts) == 2:
        fixed = [i for i in (0, 1) if len({c.split("/")[i] for c in columns if "/" in c}) == 1]
        m = re.search(r"X%\s*health", desc)
        if fixed and m:
            before = desc[:m.start()].count("X")
            order = [parts[1 - fixed[0]]] * 2
            order[before] = parts[fixed[0]]
            parts = order
    out = desc
    for p in parts:
        out = out.replace("X", p, 1)
    return out


def parse_runes(text):
    """Mythic column of every rune table -> [{id, type, name, text, stat?, value?}]."""
    runes = []
    for typ in RUNE_TYPES:
        sec = re.search(rf"== {typ} Runes ==(.*?)(?=\n==[^=])", text, re.S).group(1)
        descs = dict(re.findall(r"\* '''(.+?)''':\s*(.+)", sec))
        for row in sec.split("\n|-")[1:]:
            m = re.search(r"'''(.+?)'''\s*\|\|(.*)", row)
            if not m:
                continue
            name, cols = m.group(1), [c.strip() for c in m.group(2).split("||")]
            value, desc = cols[-1], descs.get(name, "")
            # per-rune icons the wiki lacks, cropped from in-game screenshots into custom_icons/
            own, generic = f"custom_icons/{typ.lower()}-{name.lower()}.png", f"custom_icons/{typ.lower()}-generic.png"
            r = {"id": slug("r", typ, name), "type": typ, "name": name,
                 "icon": own if os.path.exists(own) else generic if os.path.exists(generic) else f"Rune_{typ}MythicRandom.png"}
            if typ in ("Minor", "Major"):  # flat stats: "+3% Damage"
                r.update(stat=desc.strip(), value=float(value), text=f"+{value}% {desc.strip()}")
            else:
                r["text"] = fill_x(desc.strip(), value, cols)
            runes.append(r)
    return runes


def parse_enchant_bonuses(text):
    """Mythic enchant bonuses: one bonus at the large value, or two at the small value."""
    tb = text[text.index("==Bonuses=="):text.index("==Enchants==")]
    out = []
    for row in tb.split("\n|-")[1:]:
        cs = [c for c in cells(row) if c]
        if len(cs) >= 5 and "/" in cs[4]:
            small, large = (float(x) for x in cs[4].rstrip("%").split("/"))
            stat = plain(cs[0])
            out.append({"stat": STAT_ALIASES.get(stat, stat), "small": small, "large": large})
    return out


def parse_craft_costs():
    """Cost of crafting a specific mythic rune of each type, and a specific mythic enchant."""
    costs = {}
    for row in wikitext("Template:CraftRune").split("\n|-"):
        m = re.search(r"'''Specific (\w+) Rune'''\s*\n\|(.+)", row)
        if m and "Mythic" in row:
            costs[m.group(1)] = plain(m.group(2))
    m = re.search(r"'''Specific Enchant'''\s*\n\|(.+)", wikitext("Smithy/Craft"))
    costs["Enchant"] = plain(m.group(1)) if m else ""
    return costs


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def all_wiki_images():
    names, cont = [], None
    while True:
        q = {"action": "query", "list": "allimages", "ailimit": 500, "format": "json", "formatversion": 2}
        if cont:
            q["aicontinue"] = cont
        req = urllib.request.Request("https://bit-heroes.fandom.com/api.php?" + urllib.parse.urlencode(q), headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            d = json.load(r)
        names += [i["name"] for i in d["query"]["allimages"]]
        cont = d.get("continue", {}).get("aicontinue")
        if not cont:
            return names


ICON_SKIP = {"mainhandsoulharvester"}  # not an Ancient


def ancient_gallery(families, images):
    """Every static icon and animation the wiki has for each Ancient family's versions."""
    for f in families:
        keys = {norm(re.sub(r"\s*\(.*\)|\s+helmet$", "", v, flags=re.I)) for v in f["versions"]}
        pics = []
        for img in images:
            stem, _, ext = img.rpartition(".")
            if ext.lower() not in ("png", "gif") or norm(stem) in ICON_SKIP or re.search(r"core|trick|strategy|guide", norm(stem)):
                continue
            if any(k in norm(stem) for k in keys):
                label = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", stem.replace("Custom_", "").replace("_", " "))
                pics.append({"file": img, "label": re.sub(r"\s*Animation$", " (animated)", label), "animated": ext.lower() == "gif"})
        f["gallery"] = sorted(pics, key=lambda p: (p["animated"], p["label"]))


def fetch_icons(files, cache="icons"):
    """Download icons once into ./icons and return {file: data URI}. Artifact pages can't load wiki images."""
    import base64
    os.makedirs(cache, exist_ok=True)
    files = sorted({f for f in files if f})
    local = {f: f for f in files if f.startswith("custom_icons/")}  # our own icons, not on the wiki
    todo = [f for f in files if f not in local and not os.path.exists(os.path.join(cache, slug(f)))]
    for i in range(0, len(todo), 50):
        q = urllib.parse.urlencode({"action": "query", "prop": "imageinfo", "iiprop": "url|size", "iiurlwidth": 48,
                                    "format": "json", "formatversion": 2,
                                    "titles": "|".join("File:" + f for f in todo[i:i + 50])})
        req = urllib.request.Request("https://bit-heroes.fandom.com/api.php?" + q, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r:
            pages = json.load(r)["query"]["pages"]
        for p in pages:
            info = (p.get("imageinfo") or [None])[0]
            if not info:
                continue
            url = info["url"] if info.get("width", 99) <= 64 else info.get("thumburl", info["url"])
            # curl uses the OS certificate store; Python's bundled one rejects the image CDN's chain
            subprocess.run(["curl", "-sSfL", "-A", "Mozilla/5.0", "-o", os.path.join(cache, slug(p["title"][5:])), url],
                           check=True, timeout=60)
    uris = {}
    for f in files:
        path = local.get(f) or os.path.join(cache, slug(f))
        if os.path.exists(path):
            raw = open(path, "rb").read()
            mime = "image/gif" if raw[:3] == b"GIF" else "image/webp" if raw[8:12] == b"WEBP" else "image/png"
            uris[f] = f"data:{mime};base64," + base64.b64encode(raw).decode()
    return uris


def main():
    gear_text = wikitext("Template:GearMaterials")
    tiers = parse_tiers(gear_text)
    material_icons(gear_text)
    overview = wikitext("Equipment/Ancient")
    bonuses = parse_bonuses(overview)
    families = []
    for fam in FAMILIES:
        items = [parse_item(p) for p in fam]
        base = items[0]
        versions = [i["title"] for i in items]
        if base["page"] in SAME_PAGE_VERSIONS:
            versions = [f"{base['title']} ({v})" for v in SAME_PAGE_VERSIONS[base["page"]]]
        families.append({
            "id": re.sub(r"\W+", "-", base["page"].lower()).strip("-"),
            "name": " / ".join(i["title"] for i in items),
            "tier": base["tier"],
            "type": " / ".join(dict.fromkeys(i["type"] for i in items)),
            "dungeon": base["dungeon"],
            "recipe": base["recipe"],
            "subRecipe": base["subRecipe"],
            "versions": versions,
            "bonuses": version_bonuses(items, versions, bonuses),
            "notes": sum((i["notes"] for i in items), []),
        })
    sh = parse_item("Supercharged Harvester")
    # shortcut: hand-copied from the page's prose (no table there); re-check if the wiki changes it
    sh["recipe"] = [
        {"name": "Harvester (fully uptiered to T21)", "amount": "1", "source": "Uptier your Harvester to T21"},
        {"name": "Unstable Bitridium", "amount": "1", "source": "Clear T21 Ancient difficulty Project: Goodall (World Boss)"},
        {"name": "Cursed Corn", "amount": "3", "source": "T21 Mythic World Boss equipment"},
        {"name": "Gold", "amount": "50k", "source": ""},
    ]
    sh["notes"] = ["Reforge each element as a 0/4 item to unlock its cosmetic; upgrading to +1 first blocks the unlock."]
    sh["bonus"] = bonus_for(bonuses, "Supercharged Harvester")[0]
    # cut at ==Notes== so the last table row doesn't swallow the notes section
    sets, set_items = parse_sets(wikitext("Equipment/Set").split("==Notes==")[0])
    mythics = parse_mythics(wikitext("Equipment/Mythic").split("==Notes==")[0])
    gear = set_items + mythics + ancient_items(families, parse_ancient_icons(overview), sh)
    # the wiki lists some accessories in two sections; keep the first
    accessories = list({a["id"]: a for a in reversed(parse_accessories(wikitext("Accessories")))}.values())[::-1]
    runes = parse_runes(wikitext("Runes"))
    enchants = parse_enchant_bonuses(wikitext("Enchants"))
    craft = parse_craft_costs()
    ancient_gallery(families, all_wiki_images())
    icons = fetch_icons([i["icon"] for i in gear + accessories + runes] + ["Enchant_Mythic.png"]
                        + [p["file"] for f in families for p in f["gallery"]]
                        + [f"custom_icons/{t.lower()}-generic.png" for t in RUNE_TYPES]  # empty-slot cards
                        + list(MAT_ICONS.values()))
    data = {"tiers": tiers, "families": families, "supercharged": sh,
            "sets": sets, "gear": gear, "accessories": accessories, "icons": icons,
            "runes": runes, "enchants": enchants, "craft": craft, "matIcons": MAT_ICONS,
            "source": "https://bit-heroes.fandom.com/wiki/Equipment/Ancient"}
    with open("data.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)

    # self-check: every tier has a legendary mat, T9+ has 3+ mythics, every family has a recipe and bonuses
    assert all(tiers[t]["legendary"] for t in tiers), [t for t in tiers if not tiers[t]["legendary"]]
    assert all(len(tiers[t]["mythic"]) >= 3 for t in tiers if t >= 8), [t for t in tiers if t >= 8 and len(tiers[t]["mythic"]) < 3]
    assert all(f["recipe"] for f in families), [f["name"] for f in families if not f["recipe"]]
    assert all(all(f["bonuses"].values()) for f in families if f["tier"] >= 8), [f["bonuses"] for f in families if not all(f["bonuses"].values())]
    # builder data: every slot has items, every set piece points at a known set, ids are unique
    assert all(any(g["slot"] == s for g in gear) for s in SLOTS)
    assert all(g["set"] in sets for g in gear if g["kind"] == "set"), {g["set"] for g in gear if g["kind"] == "set"} - set(sets)
    # runes/enchants: every type present, no X left unfilled, craft costs found
    assert {r["type"] for r in runes} == set(RUNE_TYPES)
    assert not [r for r in runes if "X" in r["text"].replace("XP", "")], [r for r in runes if "X" in r["text"]]
    assert len(enchants) >= 10 and set(craft) == set(RUNE_TYPES) | {"Enchant"}, (len(enchants), craft)
    ids = [x["id"] for x in gear + accessories + runes]
    assert len(ids) == len(set(ids)), [i for i in ids if ids.count(i) > 1][:10]
    # embed into the page so it works offline and as a single file
    page = open("index.html", encoding="utf-8").read()
    blob = json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
    page = re.sub(r'(<script id="data" type="application/json">).*?(</script>)',
                  lambda m: m.group(1) + blob + m.group(2), page, count=1, flags=re.S)
    open("index.html", "w", encoding="utf-8").write(page)
    # standalone copy for any static host (GitHub Pages, Netlify, itch.io): full document wrapper
    os.makedirs("docs", exist_ok=True)  # GitHub Pages serves this folder
    body = page.removeprefix('<meta charset="utf-8">\n')
    open("docs/index.html", "w", encoding="utf-8").write(
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">\n'
        '<style>body{margin:0}img{max-width:100%}[hidden]{display:none!important}</style>\n'
        '</head>\n<body>\n' + body + '\n</body>\n</html>\n')
    print(f"tiers T{min(tiers)}-T{max(tiers)}, {len(families)} families -> data.json, index.html")


if __name__ == "__main__":
    main()
