"""
Social identities for the Feed: the 50 state voices, a local-news account per state, and a cast of
NPCs (influencers, pundits, bots, a doomposter, the ratio guy...). All fictional. Deterministic from
a seed so a run replays identically. Avatars are generated once per handle and cached forever under
media/avatars/<handle>.png (shared across runs).
"""
import hashlib
import random

VERIFIED_TIERS = ["none", "blue", "gold", "gray"]


def _h(s):
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


def state_identity(code, s):
    h = _h("state:" + code)
    rng = random.Random(h)
    styles = ["{name} Voices", "Real {name}", "{name} Speaks", "The {name} Thread", "{name} Rising", "Heard in {name}"]
    display = rng.choice(styles).format(name=s["name"])
    followers = int(20_000 + (s["median_income"] / 1000) * rng.randint(400, 1400) + rng.randint(0, 200_000))
    return {
        "id": f"state:{code}", "handle": f"{code.lower()}_voice", "display": display,
        "bio": f"Collective voice of {s['name']}. {s['region']}. " + rng.choice(["Rent, jobs, bills. ", "Working people first. ", "We pay the taxes. ", ""]) + "Not a forecast.",
        "followers": followers, "verified": rng.choice(["none", "blue", "blue", "gold"]), "state": code, "kind": "state",
        "avatar_prompt": f"flat vector emblem badge for a fictional civic account representing {s['name']}: a stylized "
                         f"landscape icon ({', '.join(s['key_industries'][:2])}), bold two-color design, no text, no flags of real parties",
    }


def local_news_identity(code, s):
    h = _h("news:" + code)
    rng = random.Random(h)
    names = ["{code} Daily Wire", "{name} Ledger", "{abbr}Now", "The {name} Dispatch", "Channel {n} {name}"]
    display = rng.choice(names).format(code=code, name=s["name"], abbr=code, n=rng.choice([3, 5, 7, 9, 12]))
    return {
        "id": f"news:{code}", "handle": f"{code.lower()}_news", "display": display,
        "bio": f"Local news for {s['name']}. Tips: dm us. Fictional outlet.",
        "followers": rng.randint(30_000, 900_000), "verified": "gold", "state": code, "kind": "local_news",
        "avatar_prompt": f"logo of a fictional local TV news station in {s['name']}, bold geometric broadcast emblem, "
                         f"red and blue, no text, clean vector style",
    }


NPCS = [
    {"handle": "mainstreet_mike", "display": "Main Street Mike", "kind": "influencer", "verified": "blue", "followers": 1_800_000,
     "persona": "a folksy populist influencer who posts in ALL-CAPS bursts about working families, loves a good meme, blames both parties",
     "voice": "influencer", "avatar_prompt": "portrait of a fictional middle-aged man in a trucker cap and flannel, podcast mic, warm lighting, photorealistic, no text"},
    {"handle": "quantqueen", "display": "Priya the Quant", "kind": "influencer", "verified": "blue", "followers": 640_000,
     "persona": "a finance influencer who turns every event into a chart thread and a hot take about markets",
     "voice": "influencer", "avatar_prompt": "portrait of a fictional young woman in a blazer in front of stock charts, studio lighting, photorealistic, no text"},
    {"handle": "deep_dish_dan", "display": "Deep Dish Dan", "kind": "influencer", "verified": "none", "followers": 210_000,
     "persona": "a Chicago shitposter who turns bad news into sports metaphors and rates states like pizza",
     "voice": "influencer", "avatar_prompt": "portrait of a fictional bearded man in a sports jersey eating pizza, flash photo, no text"},
    {"handle": "profcivics", "display": "Prof. Adaeze Okafor", "kind": "pundit", "verified": "gray", "followers": 95_000,
     "persona": "a constitutional-law pundit who explains, calmly and a little smugly, why everyone is overreacting or underreacting",
     "voice": "field", "avatar_prompt": "portrait of a fictional professor in glasses in a book-lined office, soft light, photorealistic, no text"},
    {"handle": "beltway_brett", "display": "Beltway Brett", "kind": "pundit", "verified": "blue", "followers": 410_000,
     "persona": "a DC insider pundit who always knows 'what the White House is really thinking' and never cites anyone",
     "voice": "field", "avatar_prompt": "portrait of a fictional man in a suit in front of a blurred capitol dome, TV lighting, no text"},
    {"handle": "doomclock_99", "display": "doomclock", "kind": "doomposter", "verified": "none", "followers": 88_000,
     "persona": "a doomposter who reads every event as the beginning of the end, lowercase, no punctuation, stockpiling tips",
     "voice": "influencer", "avatar_prompt": "dark moody illustration of a hooded figure lit by a phone screen, no text"},
    {"handle": "ratio_enjoyer", "display": "ratio", "kind": "troll", "verified": "none", "followers": 12_000,
     "persona": "replies 'ratio' or a one-word dunk to everything; occasionally 'L + rent + no maidens'",
     "voice": "influencer", "avatar_prompt": "cartoon frog-like mascot wearing sunglasses, flat colors, no text"},
    {"handle": "USA_Patriot_1776_bot", "display": "Patriot News Alerts", "kind": "bot", "verified": "none", "followers": 2_300,
     "persona": "an obvious automated account that reposts headlines with 14 hashtags and a flag emoji",
     "voice": "field", "avatar_prompt": "generic eagle silhouette over a sunburst, clip-art style, no text"},
    {"handle": "crypto_liberty_bot", "display": "Liberty Ledger", "kind": "bot", "verified": "none", "followers": 5_100,
     "persona": "a bot that says every crisis proves the need to buy a certain coin, with a referral link vibe",
     "voice": "field", "avatar_prompt": "gold coin icon with a torch, glossy 3d render, no text"},
    {"handle": "organizer_rosa", "display": "Rosa M. (she/her)", "kind": "organizer", "verified": "none", "followers": 47_000,
     "persona": "a union organizer who posts logistics: where the march starts, bring water, know your rights",
     "voice": "field", "avatar_prompt": "portrait of a fictional woman in a union t-shirt with a megaphone at a rally, candid, no text"},
    {"handle": "memelord_general", "display": "General Memes", "kind": "meme_account", "verified": "blue", "followers": 3_400_000,
     "persona": "a meme aggregator account; every post is a meme with a one-line caption",
     "voice": "influencer", "avatar_prompt": "cartoon general in a tiny hat saluting, flat vector, no text"},
    {"handle": "actually_steve", "display": "Steve (actually)", "kind": "reply_guy", "verified": "none", "followers": 900,
     "persona": "the reply guy who starts every reply with 'Actually,' and corrects a number nobody asked about",
     "voice": "field", "avatar_prompt": "portrait of a fictional man with a headset in a cluttered home office, webcam quality, no text"},
]


def npc_identities():
    out = []
    for n in NPCS:
        rng = random.Random(_h("npc:" + n["handle"]))
        out.append({"id": f"npc:{n['handle']}", "handle": n["handle"], "display": n["display"], "kind": n["kind"],
                    "verified": n["verified"], "followers": n["followers"] + rng.randint(0, 9_999), "state": None,
                    "bio": n["persona"][0].upper() + n["persona"][1:] + ".", "persona": n["persona"],
                    "voice": n["voice"], "avatar_prompt": n["avatar_prompt"]})
    return out


def candidate_identities(election):
    """Two (or more) candidate accounts built from the user's prompts; they campaign in the Feed every round."""
    out = []
    for o in election["options"]:
        handle = f"candidate_{o['id'].lower()}"
        name = o["name"]
        party = o.get("party") or ""
        a = o.get("analysis") or {}
        blurb = a.get("short_bio") or o.get("blurb") or ""
        if a.get("entity_type") == "AI":
            blurb = f"[An AI system, not a human: {a.get('novelty_notes', '')}] " + blurb
        if o.get("incumbent"):
            persona = (f"PRESIDENT {name} ({party}), the incumbent, posting from the official account. Governs rather than campaigns: "
                       f"announces what the administration did this quarter, defends the policies by name, answers the quarter's events, "
                       f"speaks to specific states about what reached them (jobs, prices, aid), and in year four asks for a second term on the record. "
                       f"Never says 'as President I will'; says what was done.")
            bio = f"President of the United States. {blurb}"
        else:
            persona = (f"{name}, the {party or 'presidential'} candidate (option {o['id']}{', ' + o['lane'] + ' lane' if o.get('lane') else ''}). Platform: {blurb or 'see posts'}. "
                       f"Campaigning hard: announces rallies in specific states, attacks the opponent's platform on policy "
                       f"(never personally), promises concrete things to the states that are hurting, stays on message, "
                       f"ends posts with a slogan.")
            bio = f"{party} candidate. {blurb}".strip()
        out.append({"id": f"npc:{handle}", "handle": handle, "display": name, "kind": "candidate", "verified": "gray",
                    "followers": 4_000_000 + _h(handle) % 3_000_000, "state": None, "option": o["id"], "color": o["color"],
                    "bio": bio, "persona": persona,
                    "voice": "field", "avatar": o.get("portrait"), "entity_type": a.get("entity_type", "human"),
                    "avatar_prompt": None})   # candidate portraits come from candidates.py (bio-driven), never a preset prompt
    return out


def build_roster(states, election=None):
    """states: {code: state dict}. Returns {identity_id: identity}."""
    roster = {}
    for code, s in states.items():
        si = state_identity(code, s)
        ni = local_news_identity(code, s)
        roster[si["id"]] = si
        roster[ni["id"]] = ni
    for n in npc_identities():
        roster[n["id"]] = n
    if election:
        for c in candidate_identities(election):
            roster[c["id"]] = c
    roster["wire"] = {"id": "wire", "handle": "national_wire", "display": "National Wire", "kind": "wire", "verified": "gold",
                      "followers": 12_400_000, "state": None, "bio": "Headlines only. Fictional wire service.",
                      "avatar_prompt": "minimal globe-and-lightning wire service logo, monochrome, no text"}
    return roster
