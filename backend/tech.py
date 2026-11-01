"""
TECHNOLOGY CLOCK: the world advances between seasons.

data/tech_tree.json holds six tracks of tiered milestones. A run carries a tech state (runs/<id>/tech.json and the
scenario's "tech" block): the year, each track's progress points and unlocked tiers, the pace setting, and a log.
Season rollover (`advance`, run before each Continue) adds rule-based progress per track for the years that passed,
with modifiers read from the season just played (AI bans slow AI, a defense-minded president speeds Military Autonomy,
space funding speeds Space, disasters and recessions slow everything). Tiers unlock when their cost and prerequisites are
met; one is gated behind a disclosure flag or an accelerated pace. The LLM writes only the flavor: "Since last season"
headlines and one anchor segment.

Gameplay effects are rules read by other modules through `effects(state)`: unemployment shifts by sector (persona
grievance), an escalation multiplier (scoring), federal force and bloc factory leverage (conflict), new companies
(market), a Mars vote (debate), the era name (UI).
"""
import json
import os

TREE = None


def tree(fl_root=None):
    global TREE
    if TREE is None:
        root = fl_root or os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        with open(os.path.join(root, "data", "tech_tree.json"), encoding="utf-8") as f:
            TREE = json.load(f)
    return TREE


def initial(pace="realistic", year=None, flags=None):
    t = tree()
    return {"year": int(year or t["start_year"]), "pace": pace if pace in t["pace_points"] else "realistic", "flags": list(flags or []),
            "tracks": {name: {"progress": 0.0, "unlocked": []} for name in t["tracks"]}, "log": [], "headlines": [], "segment": None, "era": "Present Day"}


def tier_index(state, track):
    return len(state["tracks"][track]["unlocked"])


def unlocked_ids(state):
    return {tid for tr in state["tracks"].values() for tid in tr["unlocked"]}


def era_name(state):
    t = tree()
    best = "Present Day"
    for e in t["eras"]:
        if all(tier_index(state, tr) >= n for tr, n in e["when"].items()):
            best = e["name"]
    return best


# ---------- rollover ----------
def modifiers_from_season(parent):
    """Rule-read modifiers from the season just played: {track: multiplier} plus 'all'."""
    mods = {"all": 1.0}
    tl = parent.get("timeline") or {}
    gv = (tl.get("govern") or {})
    pols = [p.get("name", "").lower() + " " + (p.get("summary") or "").lower() for p in gv.get("policies", []) if p.get("status") in ("passed", "watered", "executive_order")]
    txt = " ".join(pols)
    if any(w in txt for w in ("ban", "breakup", "antitrust", "moratorium", "pause")) and any(w in txt for w in ("ai", "machine", "algorithm", "helix")):
        mods["AI"] = 0.5
    if any(w in txt for w in ("defense", "military", "security", "autonomous")):
        mods["Military Autonomy"] = 1.4
    if any(w in txt for w in ("space", "nasa", "mars", "orbital", "lunar")):
        mods["Space"] = 1.6
    if any(w in txt for w in ("fusion", "reactor", "energy", "grid")):
        mods["Energy"] = 1.3
    pres = gv.get("president") or {}
    if pres.get("lane") == "right":
        mods["Military Autonomy"] = mods.get("Military Autonomy", 1.0) * 1.15
    kinds = [e.get("name", "").lower() for q in gv.get("quarters", []) for e in q.get("events", [])]
    shocks = sum(1 for n in kinds if any(w in n for w in ("hurricane", "recession", "disaster", "flood", "wildfire")))
    mods["all"] = max(0.6, 1.0 - 0.1 * shocks)
    conflict = tl.get("conflict") or {}
    if conflict.get("war_room"):
        mods["all"] *= 0.85
    return mods


def advance(state, years, parent=None, pace=None):
    """Add rule-based progress for `years`, unlock tiers, bump the year. Returns the list of newly unlocked tiers."""
    t = tree()
    state = state or initial()
    if pace:
        state["pace"] = pace if pace in t["pace_points"] else state["pace"]
    mods = modifiers_from_season(parent or {})
    per_year = t["pace_points"][state["pace"]]
    bonus = 0.0
    if "ai6" in unlocked_ids(state):
        bonus = 0.3
    new = []
    for name, tiers in t["tracks"].items():
        tr = state["tracks"].setdefault(name, {"progress": 0.0, "unlocked": []})
        gain = per_year * years * mods.get(name, 1.0) * mods["all"] * (1 + bonus)
        tr["progress"] = round(tr["progress"] + gain, 1)
    # unlock in order: cost is cumulative along the track, prerequisites must be unlocked (possibly this same rollover)
    changed = True
    while changed:
        changed = False
        have = unlocked_ids(state)
        for name, tiers in t["tracks"].items():
            tr = state["tracks"][name]
            nxt = tiers[len(tr["unlocked"])] if len(tr["unlocked"]) < len(tiers) else None
            if not nxt:
                continue
            if tr["progress"] < nxt["cost"] or not all(p in have for p in nxt.get("prereq", [])):
                continue
            gate = nxt.get("gated")
            if gate and not (state["pace"] in gate or any(f in gate for f in state.get("flags", []))):
                continue
            tr["unlocked"].append(nxt["id"])
            new.append({"track": name, "id": nxt["id"], "name": nxt["name"], "year": state["year"] + years, "companies": nxt.get("companies", [])})
            changed = True
    state["year"] += years
    state["era"] = era_name(state)
    state["log"].append({"year": state["year"], "unlocked": [n["name"] for n in new], "mods": mods})
    return new


# ---------- effects read by the rest of the sim ----------
def effects(state):
    """Aggregate rule effects of everything unlocked."""
    t = tree()
    out = {"unemployment": {}, "escalation": 1.0, "federal_force": 0.0, "bloc_factories": 0.0, "grievances": [], "companies": [], "prices": 0.0,
           "mars_vote": False, "offworld": False, "machine_candidates": False, "guard_ai": False}
    if not state:
        return out
    out["unlocked_ids"] = sorted(unlocked_ids(state))
    by_id = {tier["id"]: tier for tiers in t["tracks"].values() for tier in tiers}
    for tid in unlocked_ids(state):
        tier = by_id.get(tid) or {}
        fx = tier.get("effects") or {}
        for sector, v in (fx.get("unemployment") or {}).items():
            out["unemployment"][sector] = round(out["unemployment"].get(sector, 0) + v, 2)
        if fx.get("escalation"):
            out["escalation"] = round(out["escalation"] * fx["escalation"], 3)
        out["federal_force"] += fx.get("federal_force", 0)
        out["bloc_factories"] += fx.get("bloc_factories", 0)
        if fx.get("grievance") and fx["grievance"] not in out["grievances"]:
            out["grievances"].append(fx["grievance"])
        out["prices"] += fx.get("prices", 0)
        for k in ("mars_vote", "offworld", "guard_ai"):
            if fx.get(k):
                out[k] = True
        if fx.get("novelty") == "machine_candidates":
            out["machine_candidates"] = True
        out["companies"] += tier.get("companies", [])
    return out


def tier_names(state):
    t = tree()
    out = {}
    for name, tiers in t["tracks"].items():
        n = tier_index(state, name) if state else 0
        out[name] = tiers[n - 1]["name"] if n else "nothing beyond today's tech"
    return out


def context_text(state):
    """One paragraph every agent, candidate, desk and feed account sees."""
    if not state:
        return ""
    tn = tier_names(state)
    fx = effects(state)
    lines = [f"THE YEAR IS {state['year']} ({state['era']}). Technology in this world:"]
    for track, name in tn.items():
        lines.append(f"{track}: {name}.")
    if fx["unemployment"]:
        lines.append("Automation has cut jobs most in: " + ", ".join(f"{s} (+{v:.1f} pts unemployment)" for s, v in sorted(fx["unemployment"].items(), key=lambda kv: -kv[1])[:4]) + ".")
    if fx["escalation"] < 1:
        lines.append("Robot police patrol most cities; crowds disperse faster and resent it.")
    if fx["mars_vote"]:
        lines.append("There is a Mars outpost; whether to fund a colony is a live political question.")
    lines.append("Speak and argue as people living in this year, not in 2026.")
    return " ".join(lines)


def timeline_view(state):
    t = tree()
    if not state:
        return None
    rows = []
    year_of = {}
    for entry in state.get("log", []):
        for nm in entry.get("unlocked", []):
            year_of[nm] = entry["year"]
    for name, tiers in t["tracks"].items():
        n = tier_index(state, name)
        rows.append({"track": name, "tiers": [{"id": tier["id"], "name": tier["name"], "unlocked": i < n, "year": year_of.get(tier["name"]), "gated": bool(tier.get("gated"))} for i, tier in enumerate(tiers)]})
    return {"year": state["year"], "era": state["era"], "pace": state["pace"], "tracks": rows, "headlines": state.get("headlines", []), "segment": state.get("segment")}


# ---------- flavor ----------
FLAVOR_SYSTEM = "You write the technology news of a fictional simulated America between seasons. No real people or companies. Return JSON only."


def flavor(llm, run_id, state, new, years):
    """3-5 'Since last season' headlines and one anchor segment. Flavor only; the unlocks are already decided by rules."""
    unl = "; ".join(f"{n['track']}: {n['name']}" for n in new) or "no new milestones"
    fb_heads = [f"Since last season: {n['name']} ({n['track']})" for n in new][:5] or [f"Since last season: {years} years of steady, unremarkable progress."]
    fb_seg = f"Four years on, the technology map has moved: {unl}. The country is arguing about what that means for work, policing and who gets to leave the planet."
    if llm is None:
        return fb_heads, fb_seg
    try:
        d = llm.chat_json(FLAVOR_SYSTEM, f"It is now {state['year']} ({state['era']}). {years} years passed. Newly unlocked: {unl}. Current state: {tier_names(state)}.\n\n"
                          f"Write {{\"headlines\": [\"<3-5 headlines, each starting 'Since last season:'\"], \"segment\": \"<60-90 words an anchor says recapping the breakthroughs and what people fear or hope>\"}}",
                          kind="tech", run_id=run_id, temperature=0.7, max_tokens=500)
        heads = [str(h)[:160] for h in (d.get("headlines") or [])][:5] or fb_heads
        return heads, str(d.get("segment") or fb_seg)[:800]
    except Exception as e:
        print(f"[tech] flavor failed: {e}")
        return fb_heads, fb_seg
