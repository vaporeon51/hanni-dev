"""Stable leaderboard identities; Discord role mappings are optional metadata."""

# Explicit identity matches across stage-name / group changes. Never use
# fuzzy name-only matching: different idols often share the same name.
ROLE_ALIASES = {
    # https://world.kbs.co.kr/service/contents_view.htm?board_seq=464152&lang=e
    "Y:SY (Lee Seoyeon)": "779847756138807298",
    "Seoyeon (Y:SY)": "779847756138807298",
    # https://www.hellokpop.com/interview/exclusive-interview-latency-on-their-debut-sound-and-band-identity/
    "LATENCY Jeewon": "970868690402816011",
    "LATENCY Hyunjin": "779826921613426708",
    "Rocket Punch Dahyun": "779816698965655583",
    "Rocket Punch Juri": "779816675667214357",
    "Rocket Punch Suyun": "779816690380832768",
    "Rocket Punch Yeonhee": "779808449251770378",
    "Rocket Punch Yunkyoung": "779816692309950516",
    "fromis_9 Nagyung": "779848040235794443",
    "Chuu": "779828161319534595",
    "Yves": "779827944658567189",
    "Yena": "779828194340634625",
    "Hyewon": "779830005642821634",
    "Kwon Eunbi": "779826915627761686",
    "Jo Yuri": "779826917166940162",
    "Lee Chaeyeon": "779826919863615488",
    "Jinni": "945906614341349416",
    "Sejeong": "779846589510058044",
    "Yuju": "779834837418770492",
    "MADEIN Mashiro": "906302029301944321",
    "MADEIN Yeseo": "906301154407907358",
}


# Preserve the existing LOOSSEMBLE display metadata for Hyunjin, independent
# of whether her LATENCY card appears before or after it in the catalog.
CANONICAL_CATALOG_IDS = {"779826921613426708": 1027}


def attach_leaderboard_ids(entries):
    """Preserve catalog IDs (append-only, also used by saved sorter sessions).

    Existing Discord identities keep their rating history. New idols get a
    namespaced identity, without creating fake Discord roles in role_info.
    Group sorting deliberately has no leaderboard identity.
    """
    for entry in entries:
        if entry["kind"] != "idol":
            continue
        entry["role_id"] = entry.get("role_id") or ROLE_ALIASES.get(entry["name"])
        entry["leaderboard_id"] = entry.get("leaderboard_id") or entry.get("role_id") or f"sorter:{entry['id']}"


def catalog_rating_rows(entries):
    """One explicit display entry per person, independent of catalog order."""
    selected = {}
    for entry in entries:
        if entry["kind"] != "idol":
            continue
        identity = entry["leaderboard_id"]
        previous = selected.get(identity)
        if previous is not None:
            canonical_id = CANONICAL_CATALOG_IDS.get(identity)
            if canonical_id not in (previous["id"], entry["id"]):
                raise ValueError(f"Choose a canonical catalog entry for {identity}")
            if entry["id"] != canonical_id:
                continue
        selected[identity] = entry
    return [
        (identity, entry["short"], entry["group"],
         entry.get("local") or entry.get("photo") or entry.get("fallback"))
        for identity, entry in selected.items()
    ]


DISPLAY_NAME_ALIASES = {"Y:SY (Lee Seoyeon)": "Seoyeon (Y:SY)"}


# Discovery follows a person's recognizable current music identity. Keep
# former-group filters when they remain the clearest way to find someone.
# Exact overrides also remove stale memberships from previously built JSON.
MEMBERSHIP_OVERRIDES = {
    "Seoyeon (Y:SY)": ["Seoyeon", "fromis_9"],
    "Hyewon": ["IZ*ONE"],
    "Kwon Eunbi": ["Kwon Eunbi"],
    "Jo Yuri": ["Jo Yuri"],
    "Yena": ["Yena"],
    "Lee Chaeyeon": ["Lee Chaeyeon"],
    "IVE Wonyoung": ["IVE"],
    "IVE Yujin": ["IVE"],
    "LE SSERAFIM Chaewon": ["LE SSERAFIM"],
    "LE SSERAFIM Sakura": ["LE SSERAFIM"],
    "SAY MY NAME Hitomi": ["SAY MY NAME"],
}

def normalize_catalog_memberships(entries, definitions):
    """Repair filters without changing saved card IDs or rating identities.

    Old duplicate cards remain addressable for session replay, but point at
    one selectable card. Rebuilds apply the same repairs as the shipped JSON.
    """
    import re

    aliases = {"Busters β": "Busters", "Akmu": "AKMU"}

    def clean(value):
        value = re.sub(r"\\+u([0-9a-fA-F]{4})", lambda m: chr(int(m[1], 16)), value)
        return aliases.get(value, value)

    by_key = {g["key"]: g for g in definitions}
    for entry in entries:
        if entry["kind"] != "idol":
            continue
        entry["name"] = DISPLAY_NAME_ALIASES.get(entry["name"], clean(entry["name"]))
        if entry["name"] == "Seoyeon (Y:SY)":
            entry["short"] = "Seoyeon (Y:SY)"
            entry["search_aliases"] = ["Lee Seoyeon", "Y:SY (Lee Seoyeon)"]
        entry["group"] = clean(entry["group"])
        entry["groups"] = list(dict.fromkeys(MEMBERSHIP_OVERRIDES.get(
            entry["name"], [clean(g) for g in entry["groups"]])))
        if entry["name"] == "Hyewon":
            entry["group"] = "IZ*ONE"
        # Remove metadata from the earlier separate scoring policy on rebuild.
        entry.pop("leaderboard_groups", None)
        for key in entry["groups"]:
            if key not in by_key:
                definition = {"name": key, "key": key, "gen": entry.get("gen", [])}
                definitions.append(definition)
                by_key[key] = definition

    definitions[:] = [g for g in definitions if g["key"] != "Hyewon"]
    for definition in definitions:
        if definition["key"] == "Seoyeon":
            definition["name"] = "Seoyeon (Y:SY)"

    # These recovered filters had no upstream group cards. Append covers so
    # group mode displays the group name rather than its first member's name.
    covers = {label for e in entries if e["kind"] == "group"
              for label in e.get("group_labels", [e["group"]])}
    for key in ("Urban Zakapa", "AKMU"):
        if key in covers or key not in by_key:
            continue
        member = next(e for e in entries if e["kind"] == "idol" and key in e["groups"])
        entries.append({
            "id": max(e["id"] for e in entries) + 1,
            "kind": "group", "name": key, "short": key, "group": key,
            "groups": [], "gen": [], "role_id": None,
            **{field: member.get(field) for field in ("img", "local", "photo", "fallback")},
        })

    by_identity = {}
    for entry in entries:
        if entry["kind"] == "idol":
            by_identity.setdefault(entry["leaderboard_id"], []).append(entry)
    for identity, cards in by_identity.items():
        if len(cards) < 2:
            continue
        canonical_id = CANONICAL_CATALOG_IDS.get(identity)
        canonical = next((e for e in cards if e["id"] == canonical_id), None)
        if canonical is None:
            raise ValueError(f"Choose a canonical catalog entry for {identity}")
        canonical["groups"] = list(dict.fromkeys(g for e in cards for g in e["groups"]))
        canonical["gen"] = list(dict.fromkeys(g for e in cards for g in e.get("gen", [])))
        if not canonical.get("local"):
            canonical["local"] = next((e["local"] for e in cards if e.get("local")), None)
        for card in cards:
            if card is not canonical:
                card["canonical_id"] = canonical["id"]


def catalog_group_memberships(entries, definitions):
    """Unique person/group score memberships from the sorter filters."""
    group_names = {g["key"]: g["name"] for g in definitions if g.get("gen")}
    return [
        {"role_id": identity, "group_name": name}
        for identity, name in sorted({
            (e["leaderboard_id"], group_names[g])
            for e in entries if e["kind"] == "idol" and not e.get("canonical_id")
            for g in e["groups"] if g in group_names
        })
    ]
