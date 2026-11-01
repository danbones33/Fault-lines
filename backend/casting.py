"""
Voice casting: every speaking character gets a voice chosen from the candidate's own bio, not from a slot.

The Cartesia library publishes a NATIVE ACCENT per voice (general-american, southern-us, new-york, british,
indian-english, ...) plus a tagline with age words ("young adult", "matured", "elderly"). That is reliable
metadata, so human characters are cast there: gender + an American native accent are hard requirements, age
band and tone are scored. Fish Audio's library tags are uploader-written and accent is rarely tagged, so Fish is
only used for roles whose voices were listened to and pinned by hand (reporters.FISH_VOICES).

The catalog is cached in data/cartesia_voices.json (ids and taglines only, no secrets) and refreshed from the
API when it is missing or older than 30 days. Casting is deterministic for a given name and never gives two
characters in one run the same voice.
"""
import hashlib
import json
import os
import time

AMERICAN = {"general-american", "southern-us", "new-york", "midwestern-us", "african-american", "texan", "californian", "boston", "western-us"}
ACCENT_PREF = {"general": ["general-american"], "southern": ["southern-us", "texan"], "new_york": ["new-york"], "midwest": ["midwestern-us", "general-american"],
               "west_coast": ["californian", "general-american"], "boston": ["boston", "new-york"], "texas": ["texan", "southern-us"]}
YOUNG = ("young", "youthful", "teen", "gen z", "college", "twenties")
OLDER = ("matured", "mature", "elderly", "senior", "seasoned", "veteran", "wise", "weathered", "older", "grand", "aged", "matriarch", "patriarch")
BAD = ("child", "kid", "whisper", "asmr", "sultry", "sexy", "intimate", "anime", "cartoon", "squeaky", "baby")
# emotion-variant library entries ("Luke - Angry Broadway Voice") are one voice many times; keep the plain one
VARIANT = ("angry", "sad", "happy", "disgusted", "scared", "surprised", "excited", "fearful")
CATALOG_MAX_AGE = 30 * 86400

_CATALOG = None


def catalog(fl_root=None):
    """The slimmed Cartesia voice list, refreshed from the API when stale (needs CARTESIA_API_KEY)."""
    global _CATALOG
    if _CATALOG is not None:
        return _CATALOG
    root = fl_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    path = os.path.join(root, "data", "cartesia_voices.json")
    fresh = os.path.exists(path) and (time.time() - os.path.getmtime(path)) < CATALOG_MAX_AGE
    if not fresh:
        try:
            _CATALOG = _refresh(path)
            return _CATALOG
        except Exception as e:
            print(f"[casting] catalog refresh failed: {e}")
    try:
        with open(path, encoding="utf-8") as f:
            _CATALOG = json.load(f)
    except Exception:
        _CATALOG = []
    return _CATALOG


def _refresh(path):
    import httpx
    key = os.environ.get("CARTESIA_API_KEY", "").strip()
    if not key:
        raise RuntimeError("no CARTESIA_API_KEY")
    out = []
    for g in ("feminine", "masculine"):
        params = {"limit": 100, "is_owner": "false", "gender": g, "language": "en"}
        while True:
            d = httpx.get("https://api.cartesia.ai/voices/", headers={"X-API-Key": key, "Cartesia-Version": "2025-04-16"}, params=params, timeout=40).json()
            for v in d.get("data", []):
                nat = [a["accent"] for a in (v.get("accents") or []) if isinstance(a, dict) and a.get("is_native")]
                out.append({"id": v["id"], "name": v["name"], "gender": v.get("gender"), "native_accents": nat,
                            "tagline": v.get("tagline") or "", "description": v.get("description") or ""})
            if not d.get("has_more") or not d.get("data"):
                break
            params["starting_after"] = d["data"][-1]["id"]
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=1)
    print(f"[casting] Cartesia catalog refreshed: {len(out)} voices")
    return out


def age_band(voice):
    t = (voice.get("tagline", "") + " " + voice.get("description", "") + " " + voice.get("name", "")).lower()
    if any(k in t for k in OLDER):
        return "older"
    if any(k in t for k in YOUNG):
        return "young"
    return "middle"


def score(voice, profile):
    """Higher is better. Gender and an American native accent are required (None = ineligible)."""
    g = (profile.get("gender") or "m")[:1]
    want_g = "feminine" if g == "f" else "masculine"
    if voice.get("gender") != want_g:
        return None
    nat = set(voice.get("native_accents") or [])
    if not nat & AMERICAN:
        return None
    text = (voice.get("tagline", "") + " " + voice.get("description", "") + " " + voice.get("name", "")).lower()
    if any(b in text for b in BAD) or any(w in voice.get("name", "").lower().split() for w in VARIANT):
        return None
    s = 0.0
    prefs = ACCENT_PREF.get(profile.get("accent") or "general", ["general-american"])
    for i, a in enumerate(prefs):
        if a in nat:
            s += 6 - i * 2
            break
    else:
        s += 1 if "general-american" in nat else 0
    band = profile.get("age_band") or "middle"
    vb = age_band(voice)
    s += 5 if vb == band else (1 if "middle" in (vb, band) else -3)
    for w in (profile.get("tone") or "").lower().replace(",", " ").split():
        if len(w) > 3 and w in text:
            s += 1.5
    for w in ("narrat", "storytell", "confident", "authorit", "clear", "warm"):
        if w in text:
            s += 0.4
    return s


def cast(profile, exclude=(), name=""):
    """Best Cartesia voice id for a profile {gender, age_band, accent, tone}; deterministic per name; None if none fits."""
    cands = []
    for v in catalog():
        if v["id"] in exclude:
            continue
        sc = score(v, profile)
        if sc is not None:
            cands.append((sc, v))
    if not cands:
        return None
    cands.sort(key=lambda t: -t[0])
    top = [v for sc, v in cands if sc >= cands[0][0] - 1.0]
    h = int(hashlib.sha1((name or "x").encode()).hexdigest(), 16)
    v = top[h % len(top)]
    return {"id": v["id"], "name": v["name"], "accent": [a for a in v["native_accents"] if a in AMERICAN][0], "age_band": age_band(v), "tagline": v.get("tagline") or v.get("description", "")}


def profile_from_text(text, gender="m"):
    """Rule-only fallback profile when the LLM analysis has none: age from a number in the bio, accent from region words."""
    import re
    t = (text or "").lower()
    band = "middle"
    m = re.search(r"\b(\d{2})\b", t)
    if m:
        n = int(m.group(1))
        if 18 <= n < 36:
            band = "young"
        elif n >= 56:
            band = "older"
    accent = "general"
    if any(w in t for w in ("texas", "georgia", "alabama", "tennessee", "louisiana", "mississippi", "carolina", "kentucky", "arkansas", "southern", "appalach")):
        accent = "southern"
    elif any(w in t for w in ("new york", "brooklyn", "queens", "bronx", "jersey")):
        accent = "new_york"
    elif "boston" in t or "massachusetts" in t:
        accent = "boston"
    return {"gender": gender, "age_band": band, "accent": accent, "tone": ""}
