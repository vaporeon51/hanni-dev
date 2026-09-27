"""Stable leaderboard identities; Discord role mappings are optional metadata."""

# Explicit identity matches across stage-name / group changes. Never use
# fuzzy name-only matching: different idols often share the same name.
ROLE_ALIASES = {
    # https://world.kbs.co.kr/service/contents_view.htm?board_seq=464152&lang=e
    "Y:SY (Lee Seoyeon)": "779847756138807298",
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
