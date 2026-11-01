"""
Per-state field reporters for Fault Lines Network: one fictional on-the-ground reporter per state,
with a state-specific portrait prompt (backdrop, light, weather, industry cues) and a voice.
Portraits are generated once with Flux and cached under media/avatars/reporter_<XX>.png.
"""
import hashlib

BACKDROP = {
    "AL": "outside a brick auto plant gate at dusk in Alabama, humid summer haze, pine trees",
    "AK": "on a wet fishing dock in Alaska with snow-capped mountains and a ferry behind, cold gray light, parka",
    "AZ": "in front of saguaro cactus and a desert highway at golden hour, Phoenix skyline far behind, dry heat",
    "AR": "on a small-town main street in Arkansas with a grain elevator and a trucking depot, overcast",
    "CA": "on a Los Angeles street with palm trees and a container port crane in the distance, bright sun",
    "CO": "on a Denver sidewalk with the Rocky Mountains snow-capped behind, crisp blue sky",
    "CT": "outside an insurance-company office tower in Hartford, autumn leaves, gray afternoon",
    "DE": "on a coastal boardwalk in Delaware with a chemical plant on the horizon, windy, overcast",
    "FL": "on a Florida street with palms, a pastel condo tower and storm clouds building, humid",
    "GA": "in downtown Atlanta near a film-studio lot gate and a data center, warm evening light",
    "HI": "on a Honolulu waterfront with volcanic ridges and a cruise ship, tropical light, aloha shirt",
    "ID": "at the edge of an Idaho potato field with irrigation pivots and foothills, dry late summer",
    "IL": "on a Chicago sidewalk with the elevated train and skyscrapers, cold wind, winter coat",
    "IN": "outside a pharmaceutical plant in Indiana with flat farmland behind, flat gray light",
    "IA": "beside a cornfield and wind turbines in Iowa, big sky, late afternoon",
    "KS": "on a Kansas wheat field edge with an aircraft factory hangar behind, strong wind",
    "KY": "at a Kentucky bourbon warehouse with rolling green hills and a coal truck passing, soft light",
    "LA": "on a New Orleans street with wrought-iron balconies and a refinery flare on the horizon, humid night",
    "ME": "on a Maine harbor pier with lobster boats and fog, foul-weather jacket",
    "MD": "outside a federal office building in Maryland with the Chesapeake Bay behind, overcast",
    "MA": "on a Boston street with brick rowhouses and a biotech lab tower, cold spring light",
    "MI": "outside a Detroit auto plant with a shuttered storefront across the street, gray winter",
    "MN": "on a Minneapolis skyway bridge with snow falling and a hospital campus behind, cold",
    "MS": "on a Mississippi delta road with a shipyard crane and cotton fields, heavy humid light",
    "MO": "on a St. Louis street with the Gateway Arch far behind and storm damage, dramatic sky",
    "MT": "at a Montana ranch fence with mountains and a mining headframe, bright thin air",
    "NE": "on an Omaha street with a rail yard and an insurance headquarters, flat prairie light",
    "NV": "on the Las Vegas Strip at dusk with neon reflections, dry desert heat",
    "NH": "on a New Hampshire town green with white church steeple and autumn foliage",
    "NJ": "on a Newark street with a commuter rail station and a pharma campus, rainy evening",
    "NM": "outside an adobe building in Santa Fe with high desert and a national lab fence behind, clear light",
    "NY": "on a Manhattan avenue with yellow cabs and scaffolding, night, neon",
    "NC": "on a Charlotte street with bank towers and a flooded creek from hurricane rains, humid",
    "ND": "on a North Dakota oil pad road with pumpjacks and endless flat horizon, wind",
    "OH": "outside a Columbus warehouse with a half-built chip fab and farmland, gray light",
    "OK": "on an Oklahoma main street with a tornado-damaged sign and oil pumpjacks, big sky",
    "OR": "on a Portland bridge with evergreen hills and a homeless encampment across the river, drizzle",
    "PA": "on a Pittsburgh street with steel bridges and a transit stop, early evening",
    "RI": "on a Providence waterfront near a closed bridge span, overcast coastal light",
    "SC": "on a Charleston street with palmettos and a container port, bright humid sun",
    "SD": "on a South Dakota prairie road with a grain bin and the Badlands behind, dry light",
    "TN": "on a Nashville street with honky-tonk signs and a hospital tower, warm evening",
    "TX": "on a Houston street with refinery stacks and power lines, hot haze",
    "UT": "on a Salt Lake City street with the Wasatch mountains and a dried lakebed haze",
    "VT": "on a Vermont village road with a covered bridge and flood debris, autumn",
    "VA": "outside a data center campus in northern Virginia with power lines, overcast",
    "WA": "on a Seattle waterfront with a ferry, cranes and drizzle, raincoat",
    "WV": "on a West Virginia mountain road with a coal tipple and a hospital sign, misty",
    "WI": "on a Milwaukee street with a dairy co-op truck and a shuttered factory, cold gray",
    "WY": "at a Wyoming coal mine overlook with sagebrush and wind, wide sky",
}

FIRST = ["Jordan", "Casey", "Morgan", "Riley", "Avery", "Quinn", "Taylor", "Dakota", "Reese", "Emerson", "Hayden", "Skyler",
         "Rowan", "Sawyer", "Finley", "Blake", "Marlowe", "Kendall", "Sloane", "Devin"]
LAST = ["Alvarez", "Brooks", "Chen", "Dawson", "Ellison", "Fitzgerald", "Guzman", "Holloway", "Iverson", "Jackson", "Kowalski",
        "Lindqvist", "Marsh", "Nakamura", "Okafor", "Petrov", "Quinlan", "Rahman", "Santos", "Thornton", "Underwood", "Vance", "Whitaker"]

# Cartesia voices (public library ids). Override per role with CARTESIA_VOICE_<ROLE>.
# All American accents (checked against Cartesia's accent metadata) except anchor_world, which is the
# deliberately British world-service desk.
VOICES = {
    "anchor_a": "a33f7a4c-100f-41cf-a1fd-5822e8fc253f",   # Lauren - Lively Narrator, general-american (FLN Red)
    "anchor_b": "47c38ca4-5f35-497b-b1a3-415245fb35e1",   # Daniel - Modern Assistant, general-american (FLN Blue)
    "anchor_world": "7cf0e2b1-8daf-4fe4-89ad-f6039398f359",  # Benedict - Royal Narrator, british (FLN World Service); Clive sounded robotic
    "field_f": "25d7abcb-4d6d-4aca-adce-8a1c85620c8b",    # Jessica - Clear Communicator, general-american
    "field_m": "1fcd23d0-bf12-4896-8f60-4f21ef5c9b98",    # Austin - Conversational Guide, general-american
    "field_south": "d1d9c946-7cfc-4378-85a4-07d09827cb7e",  # Jolene - Warm Storyteller, southern-us
    "influencer": "87286a8d-7ea7-4235-a41a-dd9fa6630feb",  # Henry - Plainspoken Guy, general-american
}
# Fish Audio (s2.1-pro) library voices: generic news/narration voices only, no clones of real people.
# Override any role with FISH_VOICE_<ROLE>. A role missing here falls back to the Cartesia voice above.
FISH_VOICES = {
    "anchor_a": "763dbc7b640f4847be926578c45bb8b4",      # "news anchor 3": energetic female, standard American (FLN Red)
    "anchor_b": "63d5460e91e2411fa2e6bf95e7456f03",      # "Anchor News": deep professional male (FLN Blue)
    "field_f": "28a0af5271bd4bd5b70f11c522008f8d",       # "News Woman": clear energetic female announcer
    "field_m": "05aff7ff557d45d2ace9a639452451ea",       # "News reporter": professional male
    "influencer": "5c5d9d973cc14d0b845c9dffcd64362d",    # "chand": energetic male, works with [angry] cues
    "moderator": "74e2e3eface047b69dd352afbcf8c88c",     # "patrick222": professional authoritative male
    "A_f": "6fe12809b59441ecae0c6e20f953adca",           # "C12 anchor": clear serious female
    "A_m": "d4b8fa31c9984346a741ea3362de10e7",           # "Valentino New": mature deep authoritative male
    "B_f": "e649dfa32586489cab1ccea881b4ba10",           # "Professional News Anchor": confident female
    "B_m": "80e34d5e0b2b4577a486f3a77e357261",           # "New History": mature, gravitas male
}
# Default emotion cues (Fish S2 bracket syntax) by role; the debate sends a cue per line from the writers' room.
ROLE_EMOTION = {
    "anchor_a": "urgent, alarmed, breathless", "anchor_b": "calm, dismissive, slightly bored", "anchor_world": "calm",
    "influencer": "angry, shouting", "moderator": "composed, firm",
}
FIELD_EMOTION = {"calm": "steady", "petition": "steady, slightly tense", "protest": "tense, speaking over a crowd",
                 "strike": "tense, raised voice", "riot": "shaken, shouting over noise"}

SOUTH = {"AL", "AR", "GA", "KY", "LA", "MS", "NC", "SC", "TN", "TX", "WV", "OK"}

# Per-state Cartesia voice overrides: {code: (voice_id, "m"|"f")}. Env CARTESIA_VOICE_STATE_<XX> also works.
# Pin a Cartesia voice to one state's field reporter with CARTESIA_VOICE_STATE_<CODE> in .env (e.g. CARTESIA_VOICE_STATE_PA).
STATE_VOICES = {}


def state_voice(code):
    import os
    env = os.environ.get(f"CARTESIA_VOICE_STATE_{code}")
    if env:
        return env, "m"
    return STATE_VOICES.get(code, (None, None))


def _h(s):
    return int(hashlib.sha1(s.encode()).hexdigest(), 16)


def reporter(code, state_name):
    h = _h("reporter:" + code)
    name = f"{FIRST[h % len(FIRST)]} {LAST[(h // 7) % len(LAST)]}"
    feminine = (h // 3) % 2 == 0
    voice_id, gender = state_voice(code)
    if voice_id:
        feminine = gender == "f"
    voice_role = "field_south" if code in SOUTH and feminine else ("field_f" if feminine else "field_m")
    look = "a woman" if feminine else "a man"
    prompt = (f"photorealistic medium shot of a fictional local TV field reporter, {look} in their 30s, holding a handheld "
              f"microphone with a plain colored flag cube (no legible letters), looking into the camera, standing {BACKDROP[code]}. "
              f"Broadcast news look, slight motion blur in the background, ENG camera framing. No text, no logos, no real people.")
    return {"code": code, "name": name, "handle": f"reporter_{code.lower()}", "state_name": state_name,
            "voice_role": voice_role, "voice_id": voice_id, "portrait_prompt": prompt, "cache": f"reporter_{code.lower()}.png"}
