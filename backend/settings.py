"""
SETTINGS: what the user can change from the app's Settings panel instead of editing .env by hand.

Every field is optional. Values are saved to .env next to the app (gitignored, never committed) and applied to the
running server at once (app.reload_providers). Secrets never leave the server again: the panel only ever sees
whether a key is set and its last four characters.
"""
import os
import re

import httpx

FL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENV_PATH = os.path.join(FL_ROOT, ".env")

# key, label, group, kind (secret | text | choice | number), extra
FIELDS = [
    {"key": "OPENROUTER_API_KEY", "label": "OpenRouter key", "group": "text", "kind": "secret", "test": "openrouter",
     "help": "Get one at openrouter.ai/keys (add a few dollars of credit)."},
    {"key": "FISH_AUDIO_API_KEY", "label": "Fish Audio key", "group": "voices", "kind": "secret", "test": "fish",
     "help": "Voiced anchors, reporters and candidates, with emotion. fish.audio/app/api-keys (API credit is separate from the app's credit)."},
    {"key": "CARTESIA_API_KEY", "label": "Cartesia key", "group": "voices", "kind": "secret", "test": "cartesia",
     "help": "Voices too; used when there is no Fish key, or when Fish fails. play.cartesia.ai/keys"},
    {"key": "FL_FALLBACK_VOICE", "label": "With no voice key", "group": "voices", "kind": "choice",
     "choices": [["off", "Silent: show the words"], ["browser", "Read aloud with the browser's voice"]],
     "help": "What the news, debates and episodes do when no voice key is set."},
    {"key": "RUN_BUDGET_USD", "label": "Spending cap per run ($)", "group": "spend", "kind": "number",
     "help": "Paid pictures and voices stop at this amount for each run. The simulation keeps going."},
]
BY_KEY = {f["key"]: f for f in FIELDS}
SECRET_RE = re.compile(r"^[A-Za-z0-9_\-\.:/+=]{8,300}$")


def mask(v):
    v = v or ""
    return ("•••• " + v[-4:]) if len(v) >= 12 else ("••••" if v else "")


def view():
    """What the Settings panel may see: values for plain fields, set/masked for secrets."""
    out = []
    for f in FIELDS:
        v = os.environ.get(f["key"], "")
        item = {k: f[k] for k in ("key", "label", "group", "kind", "help", "choices", "test") if k in f}
        if f["kind"] == "secret":
            item.update({"set": bool(v), "hint": mask(v)})
        else:
            item["value"] = v
        out.append(item)
    return out


def validate(key, value):
    f = BY_KEY.get(key)
    if not f:
        raise ValueError(f"unknown setting {key}")
    value = (value or "").strip()
    if value == "":
        return ""
    if "\n" in value or "\r" in value:
        raise ValueError(f"{f['label']}: one line only")
    if f["kind"] == "secret" and not SECRET_RE.match(value):
        raise ValueError(f"{f['label']}: that does not look like an API key (paste just the key)")
    if f["kind"] == "choice" and value not in [c[0] for c in f["choices"]]:
        raise ValueError(f"{f['label']}: pick one of the options")
    if f["kind"] == "number":
        try:
            n = float(value)
        except ValueError:
            raise ValueError(f"{f['label']}: a number, like 5")
        if not 0 <= n <= 1000:
            raise ValueError(f"{f['label']}: between 0 and 1000")
        value = f"{n:g}"
    return value


def _write_env(updates):
    """Rewrite .env: changed keys in place, new keys appended, removed keys dropped. Comments and other lines stay."""
    try:
        lines = open(ENV_PATH, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        lines = ["# FAULT LINES settings. Written by the Settings panel; you can edit it by hand too. Never commit this file."]
    seen, out = set(), []
    for line in lines:
        k = line.split("=", 1)[0].strip() if "=" in line and not line.lstrip().startswith("#") else None
        if k in updates:
            seen.add(k)
            if updates[k]:
                out.append(f"{k}={updates[k]}")
            continue
        out.append(line)
    for k, v in updates.items():
        if k not in seen and v:
            out.append(f"{k}={v}")
    tmp = ENV_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out) + "\n")
    os.replace(tmp, ENV_PATH)


def apply(values, clear=()):
    """values: {KEY: new value}; a blank secret means 'keep the saved one'. clear: keys to remove. Returns changed keys."""
    updates = {}
    for k, v in (values or {}).items():
        f = BY_KEY.get(k)
        if not f:
            raise ValueError(f"unknown setting {k}")
        v = validate(k, v)
        if f["kind"] == "secret" and v == "":
            continue
        if v != os.environ.get(k, ""):
            updates[k] = v
    for k in clear or ():
        if k not in BY_KEY:
            raise ValueError(f"unknown setting {k}")
        if os.environ.get(k):
            updates[k] = ""
    if updates:
        _write_env(updates)
        for k, v in updates.items():
            if v:
                os.environ[k] = v
            else:
                os.environ.pop(k, None)
    return sorted(updates)


def test(name, value=None, timeout=12):
    """Checks a key with a free call (no generation, no charge). Returns {ok, message}."""
    keys = {"openrouter": "OPENROUTER_API_KEY", "cartesia": "CARTESIA_API_KEY", "fish": "FISH_AUDIO_API_KEY"}
    if name not in keys:
        return {"ok": False, "message": "unknown test"}
    key = (value or "").strip() or os.environ.get(keys[name], "")
    if not key:
        return {"ok": False, "message": "paste a key first"}
    if not SECRET_RE.match(key):
        return {"ok": False, "message": "that does not look like an API key"}
    try:
        if name == "openrouter":
            r = httpx.get("https://openrouter.ai/api/v1/key", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
            if r.status_code == 404:
                r = httpx.get("https://openrouter.ai/api/v1/auth/key", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
            if r.status_code == 200:
                d = (r.json() or {}).get("data") or {}
                left = d.get("limit_remaining")
                return {"ok": True, "message": "works" + (f" · ${left:.2f} left on this key" if isinstance(left, (int, float)) else "")}
            return _bad(r)
        if name == "cartesia":
            r = httpx.get("https://api.cartesia.ai/voices/", params={"limit": 1},
                          headers={"Authorization": f"Bearer {key}", "Cartesia-Version": "2025-04-16"}, timeout=timeout)
            return {"ok": True, "message": "works"} if r.status_code == 200 else _bad(r)
        if name == "fish":
            r = httpx.get("https://api.fish.audio/wallet/self/api-credit", headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
            if r.status_code == 200:
                credit = (r.json() or {}).get("credit")
                try:
                    c = float(credit)
                    if c <= 0:
                        return {"ok": False, "message": "the key works, but it has no API credit (top up API credit, not app credit)"}
                    return {"ok": True, "message": f"works · {c:.2f} API credit"}
                except (TypeError, ValueError):
                    return {"ok": True, "message": "works"}
            return _bad(r)
    except httpx.HTTPError as e:
        return {"ok": False, "message": f"could not reach the service ({type(e).__name__}); check your internet connection"}
    return {"ok": False, "message": "unknown test"}


def _bad(r):
    if r.status_code in (401, 403):
        return {"ok": False, "message": "the service says this key is not valid (check you copied all of it, or make a new one)"}
    if r.status_code == 402:
        return {"ok": False, "message": "the key works, but the account is out of credit"}
    if r.status_code == 429:
        return {"ok": False, "message": "the service is rate-limiting right now; try again in a minute"}
    return {"ok": False, "message": f"the service answered {r.status_code}"}
