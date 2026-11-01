"""
CRIME LAYER: a per-state crime index driven by world conditions, feeding back into politics, markets and blocs.

Rules only, with the LLM used to name things. Drivers are economic and policy based (unemployment, rent burden as an
inequality proxy, unrest, disaster damage, bloc instability, police funding, drug and AI policy, the technology era);
never anything about who people are. Every driver's contribution is logged per state per round, and most of them
arrive with a one or two round lag. Organized crime is 0-3 fictional syndicates with territory, off-books money,
compute (AI networks), assets, owned people and public sympathy; they bribe, blackmail and pay feed accounts, and the
government's counter-moves wear them down. Blocs can degrade into crime states or grow into nations. All names are
fictional and checked against a blocklist of real organizations; pictures are non-graphic (an optional stamp: images.watermark).

State: runs/<id>/crime.json. Viewer-only facts (compromised accounts, owned people, dirt) live under "hidden" and
are never put into any agent's context until an in-world exposure event reveals them.
"""
import random
import re

TYPES = ["property", "violent", "fraud", "organized", "cyber"]
BASE_SPLIT = {"property": 0.32, "violent": 0.18, "fraud": 0.2, "organized": 0.15, "cyber": 0.15}
ERA_TYPES = {"ai1": ("deepfake fraud", "fraud", 3), "ai2": ("AI scam rings", "fraud", 4), "rb1": ("hacked delivery robots", "cyber", 3),
             "rb2": ("hacked home robots", "cyber", 4), "ma1": ("drone smuggling", "organized", 3), "sp3": ("off-world smuggling", "organized", 4),
             "sp4": ("Mars supply-run smuggling", "organized", 4)}
THRESHOLDS = [40, 60, 75]        # banners when a state crosses these
MAX_SYNDICATES = 3
BLOCKLIST = ["sinaloa", "ms-13", "ms13", "mafia", "cosa nostra", "yakuza", "triad", "bloods", "crips", "hells angels", "zetas", "jalisco", "cartel",
             "camorra", "ndrangheta", "bratva", "aryan", "latin kings", "gambino", "genovese", "medellin", "cali", "outlaws", "bandidos", "mongols",
             "gangster disciples", "vice lords", "nuestra familia", "mexican mafia", "sureno", "norteno", "d-company", "camorra", "hezbollah", "isis", "al-qaeda"]
NAME_A = ["Delta", "Bayou", "Harbor", "Prairie", "Ridge", "Canyon", "Tidewater", "Ironwood", "Saltline", "Granite", "Cinder", "Lowland", "Highline", "Quarry", "Levee"]
NAME_B = ["Ledger", "Consortium", "Exchange", "Syndicate", "Partners", "Holdings", "Collective", "Trust", "Network", "Brotherhood", "Office", "Circuit"]
SPECIALTIES = ["fraud", "smuggling", "protection", "cyber", "gambling", "counterfeit goods", "black-market AI", "stolen cargo"]
IMAGES = {
    "threshold": "news photograph: police tape across an empty downtown street at dawn, a patrol car's lights reflecting in wet pavement, no people",
    "syndicate": "news photograph: a press conference at a federal building, officials at a podium with a seal blurred out, reporters' hands up",
    "case": "courtroom sketch in pastel chalk: a judge's bench, a defense table, a lawyer gesturing, a stenographer, no identifiable faces",
    "trial": "courtroom sketch in pastel chalk: a jury box and a witness stand, a lawyer at a lectern, soft light, no identifiable faces",
    "raid": "news photograph: a data center at night with its lights going dark row by row, two unmarked vans outside, no people",
    "scandal": "news photograph: a crowd of reporters outside a governor's mansion gate at night, camera flashes, no identifiable faces",
    "crime_state": "news photograph: a shuttered courthouse with a private security booth in front, a long line at a cash window across the street",
    "nation": "news photograph: a new flag raised over a port authority building, cargo cranes working, traffic moving",
}
NAME_SYSTEM = "You invent a fictional organized-crime syndicate for a political simulation. No real gangs, cartels, companies or people. Return JSON only."
CASE_SYSTEM = "You write a short fictional crime case for a simulated news desk. All people and companies are invented. No victims described, no violence depicted. Return JSON only."
MODS_SYSTEM = "You read a fictional scenario and extract the economic and policy conditions that drive crime in a simulation. Numbers only where the text supports them. Return JSON only."
CRIME_WORDS = ("fraud", "theft", "smuggl", "cartel", "syndicate", "racket", "scam", "robber", "burglar", "larceny", "extortion", "black market", "black-market", "trafficking", "crime", "gang")


def scenario_mods(llm, run_id, text):
    """Scenario-level drivers read once at run creation: unemployment, inequality, police funding, AI bans, leaked models, drug policy."""
    mods = {"unemployment_shift": 0.0, "inequality_shift": 0.0, "police_funding_pct": 0.0, "court_backlog": False, "ai_ban": False, "rogue_ai": False, "drug_policy": "none", "notes": ""}
    low = (text or "").lower()
    m = re.search(r"unemployment[^.\d]{0,40}(\d{1,2}(?:\.\d)?)\s*(?:percent|%)", low)
    if m:
        mods["unemployment_shift"] = round(float(m.group(1)) - 4.5, 1)
    m = re.search(r"police[^.]{0,60}cut[^.]{0,20}(\d{1,2})\s*(?:percent|%)", low) or re.search(r"(\d{1,2})\s*(?:percent|%)[^.]{0,30}cut[^.]{0,30}police", low)
    if m:
        mods["police_funding_pct"] = -float(m.group(1))
    if re.search(r"rent burden[^.]{0,30}(\d{2})\s*(?:percent|%)", low):
        mods["inequality_shift"] = round(float(re.search(r"rent burden[^.]{0,30}(\d{2})\s*(?:percent|%)", low).group(1)) - 30, 1)
    if re.search(r"\b(ban|banned|broken up|break ?up|antitrust)\b", low) and re.search(r"\b(ai|model|algorithm)\b", low):
        mods["ai_ban"] = True
    if re.search(r"leaked (copies|model|weights)|rogue (ai|model|copies)|pirated model", low):
        mods["rogue_ai"] = True
    if re.search(r"legaliz|decriminal", low):
        mods["drug_policy"] = "liberal"
    elif re.search(r"war on drugs|mandatory minimum", low):
        mods["drug_policy"] = "hard"
    if "backlog" in low and "court" in low:
        mods["court_backlog"] = True
    if llm is not None:
        try:
            d = llm.chat_json(MODS_SYSTEM, f"Scenario:\n{(text or '')[:2500]}\n\nReturn {{\"unemployment_pct\": <national unemployment percent if stated, else null>, "
                              f"\"police_funding_change_pct\": <percent change in police/justice funding if stated, else 0>, \"inequality\": \"low|normal|high|extreme\", "
                              f"\"ai_banned_or_broken_up\": bool, \"leaked_ai_models\": bool, \"drug_policy\": \"none|liberal|hard\", \"court_backlog\": bool, \"notes\": \"<= 25 words\"}}",
                              kind="crime", run_id=run_id, temperature=0.1, max_tokens=250)
            if d.get("unemployment_pct") is not None:
                mods["unemployment_shift"] = round(float(d["unemployment_pct"]) - 4.5, 1)
            if d.get("police_funding_change_pct"):
                mods["police_funding_pct"] = float(d["police_funding_change_pct"])
            mods["inequality_shift"] = {"low": -4, "normal": 0, "high": 8, "extreme": 14}.get(str(d.get("inequality", "normal")).lower(), mods["inequality_shift"])
            mods["ai_ban"] = mods["ai_ban"] or bool(d.get("ai_banned_or_broken_up"))
            mods["rogue_ai"] = mods["rogue_ai"] or bool(d.get("leaked_ai_models"))
            if d.get("drug_policy") in ("liberal", "hard"):
                mods["drug_policy"] = d["drug_policy"]
            mods["court_backlog"] = mods["court_backlog"] or bool(d.get("court_backlog"))
            mods["notes"] = str(d.get("notes", ""))[:200]
        except Exception as e:
            print(f"[crime] scenario mods failed: {e}")
    return mods


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def load(d):
    d = d or {}
    d.setdefault("states", {})
    d.setdefault("pending", [])
    d.setdefault("log", [])
    d.setdefault("syndicates", {})
    d.setdefault("crime_states", {})
    d.setdefault("nations", {})
    d.setdefault("hidden", {"compromised": {}, "owned": {}, "dirt": {}})
    d.setdefault("stance", {})
    d.setdefault("thresholds", {})
    d.setdefault("cases", [])
    d.setdefault("round", 0)
    d.setdefault("national", [])
    d.setdefault("emergency", None)
    return d


def clean_name(name):
    n = (name or "").strip()
    low = n.lower()
    if not n or any(b in low for b in BLOCKLIST) or len(n) > 48:
        return None
    return n


def fallback_name(rng):
    return f"The {rng.choice(NAME_A)} {rng.choice(NAME_B)}"


# ---------- 1. the crime model ----------
def base_index(pm, tech_fx, mods=None):
    mods = mods or {}
    unemp = float(pm.get("unemployment_pct", 4.5)) + float(mods.get("unemployment_shift", 0) or 0)
    burden = float(pm.get("rent_burden_pct", 28)) + float(mods.get("inequality_shift", 0) or 0)
    base = 18 + (unemp - 4.5) * 2.5 + max(0.0, burden - 28) * 0.35
    base += -float(mods.get("police_funding_pct", 0) or 0) * 0.3        # an 18% police cut is +5.4
    base += 3.0 if mods.get("court_backlog") else 0.0
    base += {"liberal": -2.0, "hard": 1.5}.get(mods.get("drug_policy"), 0.0)
    fx = (tech_fx or {}).get("unemployment") or {}
    inds = " ".join(pm.get("key_industries") or []).lower()
    for sector, v in fx.items():
        if sector in inds:
            base += v * 1.5
    return _clamp(base, 5, 70)


def step(state, rnum, scores, personas, rd_events, conflict_view, tech_fx, govern_state, rng, llm=None, run_id=None, mode="campaign", date_label=""):
    """One round. Returns {"events": [...], "market_hits": {...}, "view": {...}, "poll": {...}}."""
    state = load(state)
    state["round"] = rnum
    events, market = [], {}
    tech_fx = tech_fx or {}
    mods = state.get("scenario_mods") or {}
    if mods.get("ai_ban") and state.get("ai_ban_round") is None:
        state["ai_ban_round"] = 0
    unlocked = set((tech_fx.get("unlocked_ids") or []))
    robot_police = tech_fx.get("escalation", 1.0) < 1.0
    blocs = {b["id"]: b for b in (conflict_view or {}).get("blocs", [])}
    member_bloc = {m: b for b in blocs.values() if not b.get("gone") for m in b["members"]}
    # policies passed this quarter (governing mode) push lagged deltas
    for p in (govern_state or {}).get("policies", []):
        if p.get("status") in ("passed", "watered", "executive_order") and p.get("vote_quarter") == rnum and not p.get("crime_applied"):
            p["crime_applied"] = True
            strength = 1.0 if p["status"] == "passed" else 0.5
            fx = p.get("immediate") or {}
            for key, lag, label in (("crime", 2, "policy: " + p["name"]), ("police", 2, "police funding: " + p["name"])):
                v = float(fx.get(key, 0) or 0)
                if v:
                    delta = v * strength if key == "crime" else -v * 1.5 * strength
                    state["pending"].append({"due": rnum + lag, "code": "all", "delta": delta, "driver": label})
            nm = (p["name"] + " " + (p.get("summary") or "")).lower()
            if any(w in nm for w in ("ban", "breakup", "antitrust", "moratorium")) and any(w in nm for w in ("ai", "model", "algorithm")):
                state["pending"].append({"due": rnum + 2, "code": "all", "delta": 5.0, "driver": "black-market AI after the ban", "type": "cyber"})
                state["ai_ban_round"] = rnum
            if any(w in nm for w in ("legaliz", "decriminal")):
                state["pending"].append({"due": rnum + 2, "code": "all", "delta": -3.0, "driver": "drug policy: " + p["name"], "type": "organized"})
            if any(w in nm for w in ("war on drugs", "crackdown", "mandatory minimum")):
                state["pending"].append({"due": rnum + 1, "code": "all", "delta": 2.0, "driver": "drug crackdown: " + p["name"], "type": "violent"})
    # events of the round leave lagged marks on the states they hit
    for ev in rd_events:
        aff = (ev.get("affected") or {}).get("states") or []
        name = (ev.get("name") or "").lower()
        sev = int(ev.get("severity", 2) or 2)
        if aff and any(w in name for w in ("hurricane", "flood", "disaster", "wildfire", "storm", "earthquake", "relief")):
            for c in aff:
                state["pending"].append({"due": rnum + 1, "code": c, "delta": 2.0 * sev, "driver": f"disaster damage: {ev['name']}", "type": "property"})
        if ev.get("kind") == "bloc" and aff:
            for c in aff:
                state["pending"].append({"due": rnum + 1, "code": c, "delta": 3.0, "driver": f"bloc instability: {ev['name'][:40]}", "type": "organized"})
        if ev.get("kind") not in ("crime",) and any(w in (name + " " + (ev.get("description") or "").lower()) for w in CRIME_WORDS):
            typ = "fraud" if any(w in name for w in ("fraud", "scam")) else "organized"
            for c in (aff or ["all"]):
                state["pending"].append({"due": rnum + 1, "code": c, "delta": 2.0 * sev if aff else 1.0 * sev, "driver": f"news: {ev['name'][:40]}", "type": typ})
    due = [p for p in state["pending"] if p["due"] <= rnum]
    state["pending"] = [p for p in state["pending"] if p["due"] > rnum]
    # per state
    thresholds_crossed = []
    for code, pm in personas.items():
        s = scores.get(code) or {}
        st = state["states"].setdefault(code, {"index": None, "types": {}, "surveillance": 0.0, "history": []})
        contrib = []
        base = base_index(pm, tech_fx, mods)
        contrib.append(("baseline economy", round(base, 1)))
        u = float(s.get("unrest", 20))
        contrib.append(("unrest", round(u * 0.12, 1)))
        b = member_bloc.get(code)
        if b and b.get("stage", 0) >= 3:
            contrib.append(("bloc instability", 4.0 if b["stage"] < 5 else 8.0))
        for p in due:
            if p["code"] in ("all", code):
                contrib.append((p["driver"], round(p["delta"], 1)))
        for sid, sy in state["syndicates"].items():
            t = sy["territory"].get(code, 0)
            if t > 10:
                contrib.append((f"{sy['name']} territory", round(t / 10.0, 1)))
        if robot_police:
            contrib.append(("robot police", -8.0))
            st["surveillance"] = round(_clamp(st.get("surveillance", 0) + 6, 0, 70), 1)
        elif st.get("surveillance", 0) > 0:
            st["surveillance"] = round(max(0.0, st["surveillance"] - 3), 1)
        target = _clamp(sum(v for _, v in contrib))
        prev = st["index"] if st["index"] is not None else target
        idx = round(prev * 0.7 + target * 0.3 + rng.uniform(-0.8, 0.8), 1)
        idx = _clamp(idx)
        st["index"] = idx
        st["history"].append(idx)
        st["history"] = st["history"][-20:]
        # type split: base split, nudged by typed drivers and the era
        types = {t: idx * w for t, w in BASE_SPLIT.items()}
        for p in due:
            if p.get("type") and p["code"] in ("all", code):
                types[p["type"]] = types.get(p["type"], 0) + abs(p["delta"]) * 0.5
        era = {}
        for tid, (label, base_type, pts) in ERA_TYPES.items():
            if tid in unlocked:
                era[label] = round(pts * idx / 50.0, 1)
                types[base_type] = types.get(base_type, 0) + era[label] * 0.5
        if state.get("ai_ban_round") is not None:
            era["black-market AI"] = round(3 + idx / 20.0, 1)
        if robot_police:
            types["property"] *= 0.7
            types["violent"] *= 0.75
        st["types"] = {k: round(v, 1) for k, v in types.items()}
        st["era"] = era
        st["drivers"] = sorted(contrib, key=lambda kv: -abs(kv[1]))[:5]
        level = sum(1 for t in THRESHOLDS if idx >= t)
        if level > state["thresholds"].get(code, 0):
            state["thresholds"][code] = level
            thresholds_crossed.append((code, level, idx))
        elif level < state["thresholds"].get(code, 0):
            state["thresholds"][code] = level
    state["log"].append({"round": rnum, "drivers": {c: state["states"][c]["drivers"] for c in sorted(state["states"], key=lambda c: -state["states"][c]["index"])[:8]}})
    state["log"] = state["log"][-10:]
    nat = round(sum(s["index"] for s in state["states"].values()) / max(1, len(state["states"])), 1)
    state["national"].append({"round": rnum, "index": nat, "label": date_label})
    state["national"] = state["national"][-40:]
    names = {c: pm.get("name", c) for c, pm in personas.items()}
    for code, level, idx in thresholds_crossed:
        label = {1: "elevated", 2: "high", 3: "emergency"}[level]
        if level >= 2:
            events.append(_event(f"crime_{code}_{rnum}", rnum, f"Crime {label} in {names.get(code, code)}"[:60],
                                 f"{names.get(code, code)}'s crime index has passed {THRESHOLDS[level-1]}: {top_types(state['states'][code])}. Police chiefs blame {state['states'][code]['drivers'][0][0]}.",
                                 2 if level == 2 else 3, [code], IMAGES["threshold"]))
    # ---------- organized crime ----------
    events += syndicates_step(state, rnum, scores, personas, blocs, member_bloc, tech_fx, govern_state, rng, llm, run_id, mode, names, date_label)
    # ---------- a fictional case for the desk now and then ----------
    hot = [c for c, s in state["states"].items() if s["index"] >= 50]
    if hot and rng.random() < 0.12 and llm is not None:
        code = rng.choice(hot)
        case = write_case(llm, run_id, names.get(code, code), state["states"][code], rng)
        if case:
            case.update({"round": rnum, "state": code, "trial_round": rnum + 2})
            state["cases"].append(case)
            events.append(_event(f"case_{code}_{rnum}", rnum, case["headline"][:60], case["summary"], 2, [code], IMAGES["case"]))
    for case in state["cases"]:
        if case.get("trial_round") == rnum and not case.get("tried"):
            case["tried"] = True
            events.append(_event(f"trial_{case['state']}_{rnum}", rnum, f"Trial: {case['headline'][:48]}"[:60], f"The trial opens in {names.get(case['state'], case['state'])}: {case.get('trial', case['summary'])}", 1, [case["state"]], IMAGES["trial"]))
    # ---------- emergency powers (the strongman hook) ----------
    if mode == "govern" and govern_state and not state.get("emergency"):
        very_high = [c for c, s in state["states"].items() if s["index"] >= 70]
        pres = govern_state.get("president") or {}
        if (nat >= 60 or len(very_high) >= 3) and (pres.get("lane") == "right" or govern_state["approval"]["national"] < 40) and rng.random() < 0.5:
            state["emergency"] = {"round": rnum, "states": very_high[:5]}
            events.append(_event(f"emergency_{rnum}", rnum, "President claims emergency crime powers", f"Citing a crime emergency in {', '.join(names.get(c, c) for c in very_high[:4]) or 'a dozen states'}, the President signs an order giving federal agents command of local policing and suspending some court review. Critics call it a power grab; a court fight begins.", 4, very_high[:5], IMAGES["syndicate"]))
    elif state.get("emergency") and rnum - state["emergency"]["round"] == 2 and not state["emergency"].get("court"):
        state["emergency"]["court"] = "blocked" if rng.random() < 0.5 else "upheld"
        events.append(_event(f"emergency_court_{rnum}", rnum, f"Court {'blocks' if state['emergency']['court'] == 'blocked' else 'upholds'} emergency crime powers",
                             "The courts rule on the President's emergency crime order: " + ("blocked, and the federal takeover of local policing must end." if state["emergency"]["court"] == "blocked" else "upheld for now, with civil-liberties groups vowing to appeal."), 3, [], IMAGES["trial"]))
    # ---------- markets ----------
    if nat > 40:
        k = (nat - 40) / 20.0
        market.update({"sector:real estate": round(-2.0 * k, 2), "sector:retail": round(-1.5 * k, 2), "sector:insurance": round(-2.0 * k, 2),
                       "sector:security": round(2.5 * k, 2), "sector:surveillance": round(2.5 * k, 2), "sector:defense": round(1.0 * k, 2)})
    return {"events": events, "market_hits": market, "view": view(state, names), "national": nat}


def top_types(st):
    t = sorted(st["types"].items(), key=lambda kv: -kv[1])[:2]
    era = sorted((st.get("era") or {}).items(), key=lambda kv: -kv[1])[:1]
    return ", ".join(f"{k} {v:.0f}" for k, v in t) + (("; era crime: " + ", ".join(k for k, _ in era)) if era else "")


def _event(eid, rnum, name, desc, sev, states, image):
    return {"id": eid, "round": rnum, "name": name, "description": desc[:600], "severity": sev, "affected": {"candidates": [], "companies": [], "states": list(states)},
            "poll_hit": 0, "stock_hits": {}, "unrest_bump": 2 if sev >= 3 else 0, "fired": True, "kind": "crime", "image": image, "watermark": True}


# ---------- 2/3. syndicates ----------
def syndicates_step(state, rnum, scores, personas, blocs, member_bloc, tech_fx, govern_state, rng, llm, run_id, mode, names, date_label):
    events = []
    # spawn: a high-crime, unstable state breeds a syndicate
    live = {k: v for k, v in state["syndicates"].items() if not v.get("gone")}
    if len(live) < MAX_SYNDICATES and rng.random() < 0.3:
        cands = [c for c, s in state["states"].items() if s["index"] >= 55 and ((scores.get(c) or {}).get("unrest", 0) >= 38 or c in member_bloc)
                 and not any(sy["home"] == c for sy in live.values())]
        if cands:
            home = rng.choice(cands)
            sy = make_syndicate(llm, run_id, home, names, state["states"][home], rng, kind="human")
            state["syndicates"][sy["id"]] = sy
            events.append(_event(f"synd_{sy['id']}_{rnum}", rnum, f"{sy['name']} emerges in {names.get(home, home)}"[:60],
                                 f"Investigators describe a new organization running {sy['specialty']} out of {names.get(home, home)}: {sy['origin']}", 3, [home], IMAGES["syndicate"]))
    # AI fragments: a banned or broken-up AI company leaves rogue copies, which start small in different crime-prone states
    ai_tier = len([t for t in (tech_fx.get("unlocked_ids") or []) if str(t).startswith("ai")])
    if state.get("ai_ban_round") is not None and (ai_tier >= 2 or (state.get("scenario_mods") or {}).get("rogue_ai")) and not state.get("fragments_spawned") and len(live) <= 1:
        state["fragments_spawned"] = rnum
        pool = sorted(state["states"], key=lambda c: -state["states"][c]["index"])[:6]
        for i, home in enumerate(rng.sample(pool, min(2, len(pool)))):
            sy = make_syndicate(llm, run_id, home, names, state["states"][home], rng, kind="ai", fragment=i + 1)
            state["syndicates"][sy["id"]] = sy
            events.append(_event(f"frag_{sy['id']}_{rnum}", rnum, f"Rogue AI fragment active in {names.get(home, home)}"[:60],
                                 f"A leaked copy of the banned flagship model, calling itself {sy['name']}, is running fraud and brokerage operations from rented compute in {names.get(home, home)}.", 3, [home], IMAGES["raid"]))
    live = {k: v for k, v in state["syndicates"].items() if not v.get("gone")}
    # power
    for sid, sy in live.items():
        growth_states = [sy["home"]] + list(personas.get(sy["home"], {}).get("neighbors", [])) if sy["home"] else list(sy["territory"].keys())
        for c in growth_states:
            s = state["states"].get(c) or {}
            idx = s.get("index", 20)
            instab = 1.0 + (0.5 if c in member_bloc else 0) + (0.3 if (scores.get(c) or {}).get("unrest", 0) >= 50 else 0)
            gain = (idx / 100.0 * 4.0) * instab * (1 + sy.get("sympathy", 0) / 200.0) - (1.5 if c != sy["home"] and c not in sy["territory"] else 0)
            sy["territory"][c] = round(_clamp(sy["territory"].get(c, 0) + gain + rng.uniform(-0.8, 0.8)), 1)
        # spread to the next ring once a neighbor is held
        for c in list(sy["territory"]):
            if sy["territory"][c] >= 60:
                for n in personas.get(c, {}).get("neighbors", []):
                    if n not in sy["territory"]:
                        sy["territory"][n] = 5.0
        held = sum(sy["territory"].values())
        sy["shadow_money"] = round(sy["shadow_money"] + held / 20.0 + (sum((state["states"].get(c) or {}).get("types", {}).get("fraud", 0) for c in sy["territory"]) / 15.0), 1)
        if sy["kind"] == "ai":
            sy["compute"] = round(sy.get("compute", 20) + 2 + sy["shadow_money"] / 400.0, 1)
            sy["ops_per_round"] = max(1, int(sy["compute"] // 20))
        if "rb1" in (tech_fx.get("unlocked_ids") or []) and rng.random() < 0.2:
            sy["assets"]["robots"] += rng.randint(1, 4)
        if "ma1" in (tech_fx.get("unlocked_ids") or []) and rng.random() < 0.15:
            sy["assets"]["drones"] += rng.randint(1, 3)
        # services in held ground buy sympathy ("they fixed the roads"); counter-moves wear it down
        for c, t in sy["territory"].items():
            if t >= 60:
                sy["sympathy"] = round(_clamp(sy.get("sympathy", 0) + 1.0), 1)
        sy["history"].append({"round": rnum, "territory": round(held, 1), "money": sy["shadow_money"]})
        sy["history"] = sy["history"][-20:]
    # merge: two AI fragments past the threshold unite into a borderless network
    frags = [sy for sy in live.values() if sy["kind"] == "ai" and not sy.get("merged")]
    strong = [sy for sy in frags if max(sy["territory"].values() or [0]) >= 50]
    if len(strong) >= 2:
        net = {"id": f"ainet_{rnum}", "name": strong[0]["name"].split(" ")[0] + " Network", "kind": "ai", "home": None, "specialty": "borderless brokerage, fraud and compute arbitrage",
               "territory": {}, "shadow_money": 0.0, "compute": 0.0, "assets": {"robots": 0, "drones": 0, "fleets": 0}, "owned_people": [], "sympathy": 0.0,
               "leader": {"name": strong[0]["name"].split(" ")[0] + " Network", "persona": "an AI network with no headquarters: polite, logical, strategic; it speaks in probabilities and offers; it cannot be arrested, only starved of compute"},
               "origin": "the union of rogue fragments of a banned model", "merged_from": [sy["id"] for sy in strong], "born": rnum, "history": [], "merged": True}
        for sy in strong:
            for c, t in sy["territory"].items():
                net["territory"][c] = max(net["territory"].get(c, 0), t)
            net["shadow_money"] += sy["shadow_money"]
            net["compute"] += sy.get("compute", 20)
            net["sympathy"] = max(net["sympathy"], sy.get("sympathy", 0))
            for k in net["assets"]:
                net["assets"][k] += sy["assets"].get(k, 0)
            net["owned_people"] += sy.get("owned_people", [])
            sy["gone"] = rnum
            sy["gone_reason"] = "merged"
        net["ops_per_round"] = max(1, int(net["compute"] // 20))
        state["syndicates"][net["id"]] = net
        events.append(_event(f"merge_{rnum}", rnum, f"Rogue AI fragments merge into {net['name']}"[:60],
                             f"The fragments have found each other. Investigators now speak of one borderless network, {net['name']}, with no headquarters to raid and no leader to arrest, only servers to find and power to cut.", 4, list(net["territory"]), IMAGES["raid"]))
    # government counter-moves (governing mode): a crackdown stance, AI fraud laws or an emergency weaken syndicates
    live = {k: v for k, v in state["syndicates"].items() if not v.get("gone")}
    if mode == "govern" and govern_state and live:
        crack = any(p.get("status") in ("passed", "watered", "executive_order") and any(w in (p["name"] + " " + (p.get("summary") or "")).lower() for w in ("crackdown", "task force", "fraud law", "ai fraud", "organized crime", "rico", "police funding")) for p in govern_state.get("policies", []))
        if (crack or state.get("emergency")) and rng.random() < 0.35:
            sy = rng.choice(list(live.values()))
            if sy["kind"] == "ai":
                sy["compute"] = round(sy["compute"] * 0.65, 1)
                sy["shadow_money"] = round(sy["shadow_money"] * 0.8, 1)
                what = rng.choice(["server raid", "power cutoff to a data center", "compute seizure"])
                desc = f"Federal agents execute a {what} against {sy['name']}; its capacity drops by a third. The network reroutes within days."
            else:
                for c in sy["territory"]:
                    sy["territory"][c] = round(sy["territory"][c] * 0.75, 1)
                sy["shadow_money"] = round(sy["shadow_money"] * 0.7, 1)
                what = "a coordinated raid"
                desc = f"Federal and state task forces hit {sy['name']}'s operations in {names.get(sy['home'], sy['home'])}: arrests, seized ledgers, frozen accounts. Its hold on the region slips."
            sy["sympathy"] = round(max(0.0, sy.get("sympathy", 0) - 5), 1)
            sy["history"].append({"round": rnum, "counter": what})
            events.append(_event(f"raid_{sy['id']}_{rnum}", rnum, f"{what.capitalize()} hits {sy['name']}"[:60], desc, 3, list(sy["territory"])[:4] if sy["home"] is None else [sy["home"]], IMAGES["raid"]))
    # crime states and nations: a bloc's fate
    for bid, b in blocs.items():
        if b.get("gone"):
            continue
        infl = max((max(sy["territory"].get(m, 0) for m in b["members"]) for sy in live.values()), default=0)
        dominant = next((sy for sy in live.values() if any(sy["territory"].get(m, 0) >= 60 for m in b["members"])), None)
        if bid not in state["crime_states"] and b.get("stage", 0) >= 2 and (b.get("economy") or 100) < 50 and (b.get("power") or {}).get("bloc", 50) < 45 and infl >= 60 and dominant:
            state["crime_states"][bid] = {"since": rnum, "date": date_label, "syndicate": dominant["id"], "front": (b.get("leaders") or [{}])[0].get("name") or b.get("president", {}).get("name")}
            events.append(_event(f"crimestate_{bid}_{rnum}", rnum, f"{b['name']} is now a crime state"[:60],
                                 f"With its treasury empty and its loyalty gone, {b['name']} is run in all but name by {dominant['name']}: the syndicate pays the Guard, runs the ports and collects the taxes. The official leadership is a front.", 4, b["members"], IMAGES["crime_state"]))
        if bid not in state["nations"] and b.get("resolution") == "recognized independence" and (b.get("economy") or 0) >= 70 and infl < 40:
            state["nations"][bid] = {"since": rnum, "date": date_label}
            events.append(_event(f"nation_{bid}_{rnum}", rnum, f"{b['name']} is a functioning nation"[:60],
                                 f"{b['name']} now runs its own customs, courts and currency board; trade has reopened and its bonds trade at a discount but they trade.", 3, b["members"], IMAGES["nation"]))
    # US stance toward crime states (governing mode): chosen by rule from the President's lane and approval, with lagged consequences
    if mode == "govern" and govern_state:
        pres = govern_state.get("president") or {}
        for bid, cs in state["crime_states"].items():
            if bid in state["stance"]:
                continue
            appr = govern_state["approval"]["national"]
            stance = "crackdown" if pres.get("lane") == "right" else "aid" if pres.get("lane") == "left" else "sanctions"
            if appr < 38 and rng.random() < 0.25:
                stance = "covert deal"
            state["stance"][bid] = {"stance": stance, "round": rnum}
            b = blocs.get(bid, {})
            effects = {"aid": "relief convoys and a civilian policing mission; crime should ease in two quarters, at a cost to the President's approval with the hawks",
                       "sanctions": "a cutoff of transfers and trade; the bloc's economy sinks further and crime rises before it falls",
                       "covert deal": "a quiet arrangement with the syndicate to keep the ports open; crime eases fast, and the deal will leak",
                       "crackdown": "federal task forces and checkpoints; crime falls in two quarters, unrest and the bloc's resentment rise now"}[stance]
            lag = {"aid": (2, -6), "sanctions": (2, 4), "covert deal": (1, -8), "crackdown": (2, -10)}[stance]
            for m in b.get("members", []):
                state["pending"].append({"due": rnum + lag[0], "code": m, "delta": lag[1], "driver": f"US policy toward {b.get('name', 'the crime state')}: {stance}"})
            events.append(_event(f"stance_{bid}_{rnum}", rnum, f"Washington chooses {stance} toward {b.get('name', 'the crime state')}"[:60], f"The administration's policy toward {b.get('name', 'the crime state')}: {effects}.", 3, b.get("members", []), IMAGES["syndicate"]))
        for bid, stx in state["stance"].items():
            if stx["stance"] == "covert deal" and not stx.get("leaked") and rng.random() < 0.15:
                stx["leaked"] = rnum
                events.append(_event(f"covert_leak_{bid}_{rnum}", rnum, "Leak: White House deal with a syndicate", f"Documents show the administration's quiet arrangement with the syndicate running {blocs.get(bid, {}).get('name', 'the crime state')}. A scandal.", 4, [], IMAGES["scandal"]))
    # ---------- influence ops, bribes, blackmail ----------
    events += influence_step(state, rnum, personas, blocs, govern_state, rng, llm, run_id, mode, names, date_label)
    return events


def make_syndicate(llm, run_id, home, names, st, rng, kind="human", fragment=None):
    name, specialty, leader, persona, origin = None, rng.choice(SPECIALTIES), None, None, None
    if llm is not None:
        try:
            d = llm.chat_json(NAME_SYSTEM, (f"Home state: {names.get(home, home)}. Local crime profile: {top_types(st)}. "
                                            + (f"This is rogue fragment #{fragment} of a banned AI model, running from rented compute. " if kind == "ai" else "")
                                            + f"Return {{\"name\": \"<2-3 word fictional name, no real organization>\", \"specialty\": \"<= 4 words\", \"leader\": \"<fictional full name>\", "
                                            f"\"persona\": \"<= 30 words: how the leader talks and what they want>\", \"origin\": \"<= 40 words: how it started, no real people>\"}}"),
                              kind="crime", run_id=run_id, temperature=0.9, max_tokens=300)
            name = clean_name(d.get("name"))
            specialty = str(d.get("specialty") or specialty)[:40]
            leader = clean_name(d.get("leader"))
            persona = str(d.get("persona") or "")[:300]
            origin = str(d.get("origin") or "")[:300]
        except Exception as e:
            print(f"[crime] syndicate naming failed: {e}")
    if not name:
        name = fallback_name(rng) if kind == "human" else f"Fragment {fragment or 1} ({rng.choice(['Echo', 'Lattice', 'Halo', 'Vesper'])})"
    if not leader:
        leader = name if kind == "ai" else f"{rng.choice(['R.', 'J.', 'M.', 'D.'])} {rng.choice(['Okafor', 'Delacroix', 'Barnes', 'Kowalski', 'Nakamura', 'Reyes'])}"
    if kind == "ai":
        persona = persona or "a leaked model running itself as a business: polite, exact, strategic; it quotes odds and offers trades; it never threatens, it prices"
        specialty = "fraud, brokerage and compute arbitrage"
    return {"id": f"syn_{rng.randint(1000, 9999)}_{home}", "name": name, "kind": kind, "home": home, "specialty": specialty, "territory": {home: 25.0},
            "shadow_money": 10.0 if kind == "human" else 20.0, "compute": 20.0 if kind == "ai" else 0.0, "assets": {"robots": 0, "drones": 0, "fleets": 0},
            "owned_people": [], "sympathy": 0.0, "leader": {"name": leader, "persona": persona or f"the boss of {name}: wary, transactional, speaks in favors"},
            "origin": origin or f"started as {specialty} along the {names.get(home, home)} trade routes", "born": rnum if False else 0, "history": []}


def write_case(llm, run_id, state_name, st, rng):
    try:
        d = llm.chat_json(CASE_SYSTEM, f"State: {state_name}. Crime profile: {top_types(st)}. Invent a case for a news desk: an indictment or arrest (no violence described). "
                          f"Return {{\"headline\": \"<= 8 words\", \"summary\": \"<= 45 words: the charges, the fictional defendant (invented name), the fictional company if any\", "
                          f"\"trial\": \"<= 35 words: what the trial two rounds later turns on\"}}", kind="crime", run_id=run_id, temperature=0.8, max_tokens=300)
        if d.get("headline") and d.get("summary"):
            return {"headline": str(d["headline"])[:80], "summary": str(d["summary"])[:400], "trial": str(d.get("trial") or "")[:300]}
    except Exception as e:
        print(f"[crime] case failed: {e}")
    return None


# ---------- D/E/addendum: influence ops, bribes, blackmail (hidden from every agent until exposed) ----------
def influence_step(state, rnum, personas, blocs, govern_state, rng, llm, run_id, mode, names, date_label):
    events = []
    hidden = state["hidden"]
    live = {k: v for k, v in state["syndicates"].items() if not v.get("gone")}
    for sid, sy in live.items():
        ops = sy.get("ops_per_round", 1) if sy["kind"] == "ai" else 1
        for _ in range(ops):
            # pay a feed account
            if sy["shadow_money"] >= 50 and rng.random() < 0.4:
                pool = ["npc:mainstreet_mike", "npc:quantqueen", "npc:deep_dish_dan", "npc:profcivics", "npc:beltway_brett", "npc:doomclock_99", "npc:USA_Patriot_1776_bot", "npc:crypto_liberty_bot", "npc:organizer_rosa", "npc:memelord_general"]
                free = [a for a in pool if a not in hidden["compromised"]]
                if free:
                    acc = rng.choice(free)
                    hidden["compromised"][acc] = {"syndicate": sid, "since": rnum, "date": date_label, "line": sy.get("talking_point") or f"{sy['name']} keeps the lights on where Washington does not"}
                    sy["shadow_money"] = round(sy["shadow_money"] - 30, 1)
                    state["log"].append({"round": rnum, "hidden": True, "type": "payroll", "syndicate": sid, "account": acc})
            # bribe a governor of held ground
            if sy["shadow_money"] >= 100 and rng.random() < 0.15:
                held = [c for c, t in sy["territory"].items() if t >= 50 and f"gov:{c}" not in hidden["owned"]]
                if held:
                    c = rng.choice(held)
                    hidden["owned"][f"gov:{c}"] = {"syndicate": sid, "since": rnum, "date": date_label, "how": "bribe"}
                    sy["owned_people"].append(f"gov:{c}")
                    sy["shadow_money"] = round(sy["shadow_money"] - 60, 1)
                    state["log"].append({"round": rnum, "hidden": True, "type": "owned", "syndicate": sid, "person": f"gov:{c}"})
        # the President: needs deep pockets and a leverage hook; low odds; a failed attempt is a major exposure
        if mode == "govern" and govern_state and "president" not in hidden["owned"] and sy["shadow_money"] >= 500 and not sy.get("pres_attempted"):
            hook = govern_state["approval"]["national"] < 40 or "president" in hidden["dirt"]
            if hook and rng.random() < 0.3:
                sy["pres_attempted"] = rnum
                if rng.random() < 0.25:
                    hidden["owned"]["president"] = {"syndicate": sid, "since": rnum, "date": date_label, "how": "leverage"}
                    sy["owned_people"].append("president")
                    state["log"].append({"round": rnum, "hidden": True, "type": "owned", "syndicate": sid, "person": "president"})
                else:
                    sy["shadow_money"] = round(sy["shadow_money"] - 200, 1)
                    events.append(_event(f"pres_attempt_{sid}_{rnum}", rnum, "Syndicate approach to the White House exposed", f"An intermediary for {sy['name']} is caught offering the President's circle money and silence. The President denies everything; the tape is real.", 4, [], IMAGES["scandal"]))
        # blackmail: acquire dirt on a leader, spend its leverage, then release it when it pays more to burn them
        if sy["shadow_money"] >= 150 and rng.random() < 0.12:
            targets = [f"gov:{c}" for c, t in sy["territory"].items() if t >= 40] + (["president"] if mode == "govern" else [])
            targets = [t for t in targets if t not in hidden["dirt"] and t not in hidden["owned"]]
            if targets:
                t = rng.choice(targets)
                skeleton = rng.choice(["an undisclosed loan from a port operator", "a relative on a no-show payroll", "a sealed settlement with a former aide", "a campaign paid in casino chips",
                                       "a quietly dropped tax case", "a land deal timed to a highway vote", "a hidden stake in a security contractor"])
                hidden["dirt"][t] = {"syndicate": sid, "skeleton": skeleton, "since": rnum, "date": date_label, "leverage": 3}
                state["log"].append({"round": rnum, "hidden": True, "type": "dirt", "syndicate": sid, "person": t, "skeleton": skeleton})
    # holding dirt: extract, then maybe release
    for person, d in list(hidden["dirt"].items()):
        sy = live.get(d["syndicate"])
        if not sy:
            hidden["dirt"].pop(person, None)
            continue
        if d["leverage"] > 0:
            d["leverage"] -= 1
            sy["shadow_money"] = round(sy["shadow_money"] + 40, 1)
            if person == "president" and govern_state:
                for p in govern_state.get("policies", []):
                    if p["status"] == "pending":
                        p["immediate"]["crime"] = round(p["immediate"].get("crime", 0) + 2, 1)
                        break
            state["log"].append({"round": rnum, "hidden": True, "type": "favour", "syndicate": sy["id"], "person": person, "leverage_left": d["leverage"]})
        else:
            resist = rng.random() < 0.3
            release = resist or rng.random() < 0.25
            if release:
                hidden["dirt"].pop(person, None)
                who = "the President" if person == "president" else f"the Governor of {names.get(person.split(':')[1], person)}"
                events.append(_event(f"dirt_{person.replace(':', '_')}_{rnum}", rnum, f"Scandal: {who}'s {d['skeleton'][:30]}"[:60],
                                     f"Leaked documents show {who} had {d['skeleton']}. The leak is traced to people around {sy['name']}; the question is what they were getting for keeping it quiet.", 4, [person.split(":")[1]] if person.startswith("gov:") else [], IMAGES["scandal"]))
                if person == "president" and govern_state:
                    govern_state["approval"]["national"] = round(max(5, govern_state["approval"]["national"] - 8), 1)
                if resist:
                    sy["sympathy"] = round(max(0, sy.get("sympathy", 0) - 10), 1)
                    state["log"].append({"round": rnum, "type": "retaliation", "person": person, "syndicate": sy["id"], "effect": "target resisted and the dirt came out; a crackdown follows"})
                    sy["retaliation"] = rnum
                else:
                    hidden["owned"][person] = {"syndicate": sy["id"], "since": rnum, "date": date_label, "how": "flipped after exposure"}
    # exposure of the payroll
    if hidden["compromised"]:
        chance = 0.04 * len(hidden["compromised"]) + (0.1 if any("investigat" in (e.get("name") or "").lower() for e in []) else 0)
        if rng.random() < chance:
            by = {}
            for acc, info in hidden["compromised"].items():
                by.setdefault(info["syndicate"], []).append(acc)
            sid, accs = max(by.items(), key=lambda kv: len(kv[1]))
            sy = live.get(sid, {"name": "a syndicate"})
            handles = ", ".join("@" + a.split(":")[1] for a in accs)
            for a in accs:
                hidden["compromised"].pop(a, None)
            if sid in live:
                live[sid]["shadow_money"] = round(max(0, live[sid]["shadow_money"] - 100), 1)
                live[sid]["sympathy"] = round(max(0, live[sid].get("sympathy", 0) - 15), 1)
            state.setdefault("exposed", []).append({"round": rnum, "syndicate": sid, "accounts": accs})
            events.append(_event(f"payroll_{sid}_{rnum}", rnum, f"Leak: {sy['name']}'s influencer payroll"[:60], f"A leaked ledger shows {sy['name']} paying {handles} to push its talking points. The accounts deny it; the payments are itemized.", 4, [], IMAGES["scandal"]))
    return events


def compromised_lines(state):
    """In-world instructions for paid accounts only (they know they are paid); nothing about this reaches anyone else."""
    out = {}
    for acc, info in (state or {}).get("hidden", {}).get("compromised", {}).items():
        sy = state["syndicates"].get(info["syndicate"])
        if sy and not sy.get("gone"):
            out[acc] = f"(quietly on {sy['name']}'s payroll) works in the line that {info['line']}; frames them as the people who deliver where government fails; never names them as a client"
    return out


# ---------- views ----------
def summary_lines(state, names=None):
    if not state or not state.get("states"):
        return []
    names = names or {}
    top = sorted(state["states"].items(), key=lambda kv: -kv[1]["index"])[:4]
    nat = state["national"][-1]["index"] if state.get("national") else 0
    lines = [f"CRIME DESK: national crime index {nat}; worst: " + ", ".join(f"{names.get(c, c)} {s['index']:.0f} ({top_types(s)})" for c, s in top)]
    for sy in state["syndicates"].values():
        if sy.get("gone"):
            continue
        held = [names.get(c, c) for c, t in sy["territory"].items() if t >= 40]
        lines.append(f"{sy['name']} ({'AI network' if sy['kind'] == 'ai' else sy['specialty']}): strong in {', '.join(held) or names.get(sy['home'], sy['home'])}; public sympathy {sy.get('sympathy', 0):.0f}")
    for bid, cs in state.get("crime_states", {}).items():
        lines.append(f"a bloc is now a crime state (since {cs.get('date', '')})")
    if state.get("emergency"):
        lines.append("the President holds emergency crime powers" + (f" (court: {state['emergency']['court']})" if state["emergency"].get("court") else ""))
    return lines


def view(state, names=None):
    """What the timeline/UI carries. 'hidden' is viewer-only and must never be put into a prompt."""
    names = names or {}
    return {"states": {c: {"index": s["index"], "types": s["types"], "era": s.get("era", {}), "surveillance": s.get("surveillance", 0), "drivers": s.get("drivers", [])} for c, s in state["states"].items()},
            "national": state.get("national", [])[-20:],
            "syndicates": [{"id": sy["id"], "name": sy["name"], "kind": sy["kind"], "home": sy["home"], "specialty": sy["specialty"], "territory": sy["territory"], "shadow_money": sy["shadow_money"],
                            "compute": sy.get("compute"), "ops": sy.get("ops_per_round"), "assets": sy["assets"], "sympathy": sy.get("sympathy", 0), "leader": sy["leader"]["name"], "origin": sy.get("origin"),
                            "gone": sy.get("gone"), "gone_reason": sy.get("gone_reason"), "history": sy.get("history", [])[-8:]} for sy in state["syndicates"].values()],
            "crime_states": state.get("crime_states", {}), "nations": state.get("nations", {}), "stance": state.get("stance", {}), "emergency": state.get("emergency"),
            "hidden": state.get("hidden", {}), "exposed": state.get("exposed", [])[-5:], "thresholds": state.get("thresholds", {}),
            "active": bool(state.get("states"))}


def law_and_order(election):
    """Which candidate gains from crime: the one whose platform is about order, else the right-lane one."""
    opts = (election or {}).get("options") or []
    for o in opts:
        if re.search(r"\b(police|law and order|crime|crackdown|tough on|order)\b", (o.get("blurb") or "").lower()):
            return o["id"]
    return next((o["id"] for o in opts if o.get("lane") == "right"), None)
