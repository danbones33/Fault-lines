"""
Candidates read their own bios.

analyze_candidates(): one LLM call per candidate turns the full bio into structured facts the rest of the
system uses: entity_type (human / AI / other), appearance (a detailed visual description), novelty_score
(0-10: how unprecedented this candidate is), novelty_notes. Stored on the option as option["analysis"].

Portraits are generated from option["analysis"]["appearance"] only: no stock photos, nothing keyed by slot,
party or gender. An AI candidate is drawn as a visibly non-human machine. Cache key = candidate id + hash of
the appearance text. A perceptual hash (dHash, no extra dependency) rejects near-duplicate portraits and
regenerates with a new seed. The same file is used everywhere (feed avatar, debate stage, broadcast, map).

AI stance per state: enthusiast / curious / skeptical / hostile, from economics and grievances (never
demographics): tech, finance and research economies lean enthusiast; trucking, call-center, manufacturing and
jobs-grievance economies lean hostile; the rest split curious / skeptical with a deterministic jitter.
"""
import hashlib
import io
import os

ANALYSIS_SYSTEM = ("You read a fictional political candidate's bio and return structured facts about them. Be literal: if the bio "
                   "says the candidate is an AI, a robot, a corporation, an animal or anything not a human person, say so. Return JSON only.")

STANCES = ["enthusiast", "curious", "skeptical", "hostile"]
STANCE_TEXT = {
    "enthusiast": "Your state is an AI ENTHUSIAST: its tech, finance or research economy expects to profit from machine government and finds the idea thrilling.",
    "curious": "Your state is AI-CURIOUS: intrigued, open to a machine candidate if it delivers real numbers, but easily spooked.",
    "skeptical": "Your state is AI-SKEPTICAL: it distrusts a machine candidate and the company behind it, and wants humans in charge.",
    "hostile": "Your state is AI-HOSTILE: machines took its jobs; a machine on the ballot is an insult, and it will say so loudly.",
}
STANCE_MARGIN = {"enthusiast": 5, "curious": 1, "skeptical": -3, "hostile": -6}   # points of lean toward / against the AI candidate


def analysis_prompt(option, scenario_text):
    return (f"Scenario context (for reference only; note the year and technology era if stated):\n{scenario_text[:1500]}\n\n"
            f"Candidate name: {option['name']}\nParty / label: {option.get('party','')}\nLane: {option.get('lane','')}\n"
            f"Full bio and platform:\n{option.get('blurb','')}\n\n"
            f"Return {{\"entity_type\": \"human\"|\"AI\"|\"other\", \"appearance\": \"<80-140 words: a precise visual description for an "
            f"illustrator: body, face or chassis, age or model, clothing or casing, posture, setting, colors, lighting; for an AI describe "
            f"a visibly non-human robot or android (metallic or synthetic surfaces, glowing elements, sensor eyes, no human face)>\", "
            f"\"novelty_score\": <0-10, how unprecedented this candidate is in American history (a governor is 1, a celebrity 3, a machine 10)>, "
            f"\"novelty_notes\": \"<= 40 words\", \"short_bio\": \"<= 60 words summary of who they are and what they want\", "
            f"\"voice_gender\": \"f\"|\"m\"|\"synthetic\", \"voice\": {{\"age_band\": \"young\"|\"middle\"|\"older\" (from the stated age: under 36 young, "
            f"56+ older), \"accent\": \"general\"|\"southern\"|\"new_york\"|\"midwest\"|\"west_coast\"|\"boston\"|\"texas\" (from where the bio says they are from; "
            f"general if unclear), \"tone\": \"<3-5 adjectives for a voice actor, e.g. warm, preacherly, firm, raspy>\"}}}}")


def analyze_candidates(llm, run_id, election, scenario_text):
    for o in election["options"]:
        a = None
        if (o.get("analysis") or {}).get("appearance"):
            a = o["analysis"]          # carried over from the parent season: same face, same voice
        if llm is not None and a is None:
            try:
                d = llm.chat_json(ANALYSIS_SYSTEM, analysis_prompt(o, scenario_text), kind="candidates", run_id=run_id, temperature=0.3, max_tokens=700)
                et = str(d.get("entity_type", "human")).strip()
                et = "AI" if et.upper() in ("AI", "MACHINE", "ROBOT", "ANDROID", "MODEL") else ("other" if et.lower() == "other" else "human")
                try:
                    nov = max(0, min(10, int(float(d.get("novelty_score", 1)))))
                except Exception:
                    nov = 1
                a = {"entity_type": et, "appearance": str(d.get("appearance", ""))[:900], "novelty_score": nov,
                     "novelty_notes": str(d.get("novelty_notes", ""))[:200], "short_bio": str(d.get("short_bio", ""))[:400],
                     "voice_gender": str(d.get("voice_gender", "m"))[:9],
                     "voice_profile": _voice_profile(d.get("voice"), str(d.get("voice_gender", "m"))[:1], o)}
            except Exception as e:
                print(f"[candidates] analysis failed for {o['name']}: {e}")
        if not a:
            blob = (o.get("blurb", "") + " " + o["name"]).lower()
            is_ai = any(k in blob for k in ("ai model", "artificial intelligence", "robot", "android", "flagship ai", "machine"))
            a = {"entity_type": "AI" if is_ai else "human", "appearance": "", "novelty_score": 10 if is_ai else 1,
                 "novelty_notes": "machine candidate" if is_ai else "", "short_bio": o.get("blurb", "")[:300], "voice_gender": o.get("voice", "m")}
        if a["entity_type"] == "AI" and a["novelty_score"] < 8:
            a["novelty_score"] = 9
        if not a["appearance"]:
            a["appearance"] = ("a sleek humanoid robot with a brushed-titanium chassis, a smooth faceplate with two soft blue sensor lights "
                               "instead of eyes, articulated hands, standing at a podium" if a["entity_type"] == "AI"
                               else f"a person in their 50s in plain professional clothes, calm expression, at a podium")
        if a["entity_type"] == "AI":
            o["voice"] = "synthetic"
        elif a.get("voice_gender") in ("f", "m"):
            o["voice"] = a["voice_gender"]
        o["analysis"] = a
    cast_voices(election)
    return election


def _voice_profile(v, gender, option):
    from casting import profile_from_text
    base = profile_from_text(option.get("blurb", ""), gender)
    if isinstance(v, dict):
        if str(v.get("age_band", "")) in ("young", "middle", "older"):
            base["age_band"] = str(v["age_band"])
        if str(v.get("accent", "")) in ("general", "southern", "new_york", "midwest", "west_coast", "boston", "texas"):
            base["accent"] = str(v["accent"])
        base["tone"] = str(v.get("tone", ""))[:80]
    return base


def cast_voices(election):
    """Every human candidate gets a Cartesia voice matched to gender, age band and American accent from the bio; no two alike."""
    from casting import cast, profile_from_text
    from reporters import VOICES, STATE_VOICES
    used = set(VOICES.values()) | {v[0] for v in STATE_VOICES.values()}
    for o in election["options"]:
        a = o.get("analysis") or {}
        if a.get("entity_type") == "AI":
            o.pop("voice_id", None)
            continue
        if o.get("voice_id"):
            used.add(o["voice_id"])
            continue
        prof = a.get("voice_profile") or profile_from_text(o.get("blurb", ""), (o.get("voice") or "m")[:1])
        prof["gender"] = (o.get("voice") or prof.get("gender") or "m")[:1]
        pick = cast(prof, exclude=used, name=o["name"])
        if pick:
            o["voice_id"] = pick["id"]
            o["voice_cast"] = pick
            used.add(pick["id"])
            print(f"[casting] {o['name']}: {pick['name']} ({pick['accent']}, {pick['age_band']}) for profile {prof}")
    return election


def novelty(election):
    """Highest novelty among candidates, and the candidate(s) carrying it."""
    best = max(election["options"], key=lambda o: (o.get("analysis") or {}).get("novelty_score", 0))
    score = (best.get("analysis") or {}).get("novelty_score", 0)
    return score, best


def shock_context(election):
    """The SHOCK CONTEXT block injected into every agent and NPC prompt when novelty >= 7."""
    score, best = novelty(election)
    if score < 7:
        return ""
    a = best.get("analysis") or {}
    what = {"AI": "A MACHINE IS RUNNING FOR PRESIDENT", "other": "SOMETHING THAT IS NOT A PERSON IS RUNNING FOR PRESIDENT"}.get(a.get("entity_type"), "AN UNPRECEDENTED CANDIDATE IS RUNNING FOR PRESIDENT")
    return (f" SHOCK CONTEXT: {what}. {best['name']} ({a.get('entity_type','')}) is on the ballot: {a.get('novelty_notes','')} "
            f"This is the most unprecedented event in American history and nobody is neutral about it: you have strong feelings, "
            f"for, against, or terrified, and they color everything you say about jobs, trust, and who should hold power.")


def ai_stance(state):
    """Deterministic stance from economics and grievances only."""
    inds = " ".join(state.get("key_industries", [])).lower()
    pressures = " ".join(state.get("policy_pressures", [])).lower()
    score = 0.0
    for k in ("tech", "semiconductor", "biotech", "federal labs", "finance", "research", "data center", "aerospace"):
        if k in inds:
            score += 1.5
    for k in ("trucking", "logistics", "manufacturing", "auto", "call center", "warehous", "retail", "insurance"):
        if k in inds:
            score -= 1.5
    if "layoff" in pressures or "job" in pressures:
        score -= 1.0
    if "data center power" in pressures:
        score -= 0.5
    if state.get("median_income", 70000) > 90000:
        score += 0.5
    if state.get("unemployment_pct", 4) > 4.5:
        score -= 0.7
    jitter = (int(hashlib.sha1(("stance:" + state["code"]).encode()).hexdigest(), 16) % 200) / 100.0 - 1.0
    score += jitter * 0.9
    if score >= 1.5:
        return "enthusiast"
    if score >= 0.2:
        return "curious"
    if score >= -1.2:
        return "skeptical"
    return "hostile"


# ---------- portraits ----------
def portrait_prompt(option):
    a = option.get("analysis") or {}
    app = a.get("appearance") or "a person at a podium"
    if a.get("entity_type") == "AI":
        return (f"photorealistic 16:9 press-conference frame of a visibly non-human robot candidate: {app}. It must be unmistakably a "
                f"machine: metallic or synthetic surfaces, exposed joints or panel seams, glowing sensor elements, no human face, "
                f"no skin. Standing at a podium with microphones, campaign backdrop in {_accent(option)}, studio lighting, chest-up "
                f"with clear headroom, centered. No text, no logos, no real people.")
    return (f"photorealistic 16:9 press-conference frame of a fictional candidate: {app}. Standing at a podium with microphones, "
            f"campaign backdrop in {_accent(option)}, studio lighting, chest-up with clear headroom, centered. No text, no logos, "
            f"no real people, not resembling any real politician.")


def _accent(option):
    return {"right": "deep red", "left": "navy blue", "center": "purple"}.get(option.get("lane"), "amber")


def portrait_key(option):
    a = option.get("analysis") or {}
    h = hashlib.sha1((a.get("appearance", "") + "|" + a.get("entity_type", "")).encode()).hexdigest()[:8]
    return f"candidate_{option['id'].lower()}_{h}.png"


def dhash(png_bytes, size=8):
    """Difference hash: resize to (size+1) x size grayscale, compare neighbours. Returns an int."""
    from PIL import Image
    im = Image.open(io.BytesIO(png_bytes)).convert("L").resize((size + 1, size))
    px = list(im.getdata())
    bits = 0
    for y in range(size):
        for x in range(size):
            left, right = px[y * (size + 1) + x], px[y * (size + 1) + x + 1]
            bits = (bits << 1) | (1 if left > right else 0)
    return bits


def hamming(a, b):
    return bin(a ^ b).count("1")


def generate_portraits(imagegen, run_dir, run_id, election, min_distance=12):
    """Generate every candidate portrait from its analysis; reject near-duplicates (dHash distance < min_distance)
    by regenerating with a different seed. Returns {option_id: relative media path}. Asserts files are distinct."""
    out_dir = os.path.join(run_dir, "media", "candidates")
    os.makedirs(out_dir, exist_ok=True)
    done = {}
    hashes = {}
    for o in election["options"]:
        key = portrait_key(o)
        path = os.path.join(out_dir, key)
        prompt = portrait_prompt(o)
        with open(os.path.join(out_dir, key.replace(".png", ".prompt.txt")), "w", encoding="utf-8") as f:
            f.write(prompt)
        png = None
        if os.path.exists(path):
            with open(path, "rb") as f:
                png = f.read()
        for attempt in range(3):
            if png is None:
                png = imagegen.generate(prompt + (f" Variation {attempt + 1}." if attempt else ""), kind="avatars", run_id=run_id,
                                        aspect="16:9", seed=1000 * attempt + 7)
            if not png:
                break
            h = dhash(png)
            clash = [oid for oid, hh in hashes.items() if hamming(hh, h) < min_distance]
            if not clash:
                with open(path, "wb") as f:
                    f.write(png)
                hashes[o["id"]] = h
                done[o["id"]] = f"candidates/{key}"
                break
            print(f"[candidates] portrait for {o['name']} too similar to {clash}; regenerating with a new seed")
            png = None
        else:
            if png:
                with open(path, "wb") as f:
                    f.write(png)
                hashes[o["id"]] = dhash(png)
                done[o["id"]] = f"candidates/{key}"
    files = list(done.values())
    assert len(files) == len(set(files)), "two candidates share a portrait file"
    return done



VOICE_SYSTEM = "You cast voices for a fictional political simulation from a candidate's bio. Return JSON only."


def voice_profile_prompt(option):
    return (f"Candidate: {option['name']}. Bio:\n{option.get('blurb', '')[:2500]}\n\n"
            "Return {\"age_band\": \"young\"|\"middle\"|\"older\" (under 36 young, 56+ older), \"accent\": \"general\"|\"southern\"|\"new_york\"|"
            "\"midwest\"|\"west_coast\"|\"boston\"|\"texas\" (where the bio says they are from; general if unclear), "
            "\"tone\": \"<3-5 adjectives for a voice actor>\"}")


def voice_profile_llm(llm, run_id, option):
    """A voice profile for an existing candidate (runs analyzed before casting existed): age band, accent, tone from the bio."""
    gender = (option.get("voice") or "m")[:1]
    if llm is not None:
        try:
            d = llm.chat_json(VOICE_SYSTEM, voice_profile_prompt(option), kind="candidates", run_id=run_id, temperature=0.2, max_tokens=200)
            return _voice_profile(d, gender, option)
        except Exception as e:
            print(f"[casting] voice profile failed for {option['name']}: {e}")
    from casting import profile_from_text
    return profile_from_text(option.get("blurb", ""), gender)


def stage_prompt(election):
    """One wide shot of the whole debate stage: identical podiums left to right in ballot order, each candidate from their own appearance."""
    opts = election["options"]
    who = "; ".join(f"podium {i + 1} (from the left): {((o.get('analysis') or {}).get('appearance') or 'a candidate in a dark suit')[:260]}" for i, o in enumerate(opts))
    return (f"Wide establishing shot of a televised presidential debate stage, {len(opts)} identical dark-wood podiums in one row, evenly spaced, each "
            f"with a microphone, one person behind each. {who}. Deep blue studio backdrop with soft spotlights, broadcast lighting, a moderator's desk "
            f"out of focus in the foreground, 16:9, photorealistic. Every podium the same size, the people the same scale, all facing the camera. "
            f"No text, no logos, no flags with real emblems, no real people.")
