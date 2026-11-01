"""
CIVIL CONFLICT layer: breakaway blocs that stick, climb a ladder, win recognition and end in a settlement.

Everything that moves is a rule over the round's numbers; the LLM only writes the words (manifesto, leaders, bloc
candidates, the terms of a settlement). No violence is depicted anywhere: the "conflict" stage is a strategy map of
control, loyalty, blockades, sanctions and negotiations, covered by the news desk as politics and logistics.

Per bloc: {id, name, color, members, born, age, identity 0-100, stage, stage_round, manifesto, leaders, election,
           president, recognition {code: federal|bloc|neutral}, guard {code: federal-loyalty 0-100},
           commands {name: federal-loyalty}, foreign_support, control {code: bloc-control 0-100}, economy, trade,
           blockades [...], sanctions [...], front [codes], resolution, history [...]}
Persisted in runs/<id>/conflict.json; the timeline carries a slim view per round (entry["conflict"]).
"""
import random

STAGES = ["grievance", "declaration", "provisional", "election", "rival_president", "standoff", "conflict", "resolution"]
STAGE_LABEL = {"grievance": "Grievance bloc", "declaration": "Declaration", "provisional": "Provisional government",
               "election": "Bloc election", "rival_president": "Rival president", "standoff": "Standoff",
               "conflict": "Conflict", "resolution": "Resolution"}
# identity and unrest a bloc needs to climb to each stage (index = stage number), plus a trigger within the last 2 rounds
NEEDS = {1: (20, 38), 2: (35, 42), 3: (48, 45), 4: (55, 46), 5: (62, 48), 6: (72, 50)}
MIN_LIFESPAN = 4          # rounds before a bloc can dissolve
DISSOLVE_MARGIN = 15      # dissolve threshold = form threshold + margin (+ identity bonus)
IDENTITY_GAIN = 8         # per round the bloc exists with its members restless
TRIGGER_BONUS = 12        # identity jump when a trigger event lands
COMMANDS = {"Northern Command": {"Northeast", "Mid-Atlantic", "Great Lakes"}, "Southern Command": {"South", "Deep South", "Gulf"},
            "Central Command": {"Midwest", "Plains"}, "Western Command": {"Mountain West", "Southwest", "Pacific Northwest", "West Coast", "Pacific"}}
SECTOR_HITS = {"standoff": {"logistics": -3, "shipping": -3, "trucking": -3, "energy": 2, "defense": 3, "security": 3, "bank": -2, "finance": -2},
               "conflict": {"logistics": -6, "shipping": -6, "trucking": -5, "energy": 4, "defense": 5, "security": 4, "bank": -4, "finance": -4, "insurance": -3}}
IMAGES = {
    "declaration": "news photograph: a crowd in front of a state capitol at dusk, a new two-color flag raised on the pole, hand-painted banners, no readable text",
    "provisional": "news photograph: three officials at a podium in a borrowed press room, two flags behind them, reporters' hands raised, fluorescent light",
    "election": "news photograph: a polling place in a high-school gym, folding tables, a line of voters, a hand-lettered sign, morning light",
    "rival_president": "news photograph: an oath being taken on the steps of a state capitol, a small crowd, one flag, grey winter sky",
    "standoff": "news photograph: an empty interstate at dawn with concrete barriers and a checkpoint booth, two parked pickups, a long line of trucks in the distance",
    "conflict": "news photograph: a sandbagged entrance to a federal building, officials in suits talking on the steps, press vans, no weapons",
    "blockade": "aerial news photograph: a river port with barges tied up idle, cranes stopped, a line of trucks on the levee road",
    "resolution": "news photograph: a long negotiation table in a hotel ballroom, two delegations, flags of both sides, a handshake at the center",
}
DECLARE_SYSTEM = "You write the founding documents of a fictional breakaway bloc of US states in a simulation. No real people. Return JSON only."
RESOLVE_SYSTEM = "You write the terms of a fictional settlement between Washington and a breakaway bloc of states in a simulation. Return JSON only."


def _avg(xs):
    xs = list(xs)
    return sum(xs) / len(xs) if xs else 0.0


def _clamp(v, lo=0, hi=100):
    return max(lo, min(hi, v))


def load_or_init(d):
    d = d or {}
    d.setdefault("blocs", {})
    d.setdefault("dissolved", [])
    d.setdefault("round", 0)
    return d


# ---------- 1. sticky blocs ----------
def dissolve_threshold(bloc, form_threshold):
    return form_threshold + DISSOLVE_MARGIN + bloc["identity"] * 0.15


def sticky_keep(state, scores, form_threshold):
    """Members of live blocs that must stay 'low' this round: hysteresis (below the bloc's dissolve threshold) or minimum lifespan."""
    keep = set()
    for b in state["blocs"].values():
        if b.get("resolution"):
            continue
        thr = dissolve_threshold(b, form_threshold)
        for m in b["members"]:
            s = scores.get(m)
            if not s:
                continue
            if s["federal_cohesion"] < thr or b["age"] < MIN_LIFESPAN:
                keep.add(m)
    return keep


def is_trigger(ev, members):
    """A trigger: a severe event anywhere, a political shock, or anything that lands on a member state."""
    sev = int(ev.get("severity", 0) or 0)
    name = (ev.get("name") or "").lower()
    hits = set((ev.get("affected") or {}).get("states") or ev.get("states") or [])
    political = any(w in name for w in ("court", "postpone", "relief", "ignored", "ruling", "emergency", "martial", "strike", "shutdown", "veto", "impeach"))
    return sev >= 4 or (sev >= 3 and political) or bool(hits & set(members)) or ev.get("kind") in ("aftermath",)


# ---------- the round ----------
def step(state, rnum, blocs, scores, personas, rd_events, rng, llm=None, run_id=None, approval=None, run_text="", tech=None):
    """Advance every bloc one round. Returns {"events": [...], "market_hits": {...}, "view": {...}}."""
    state = load_or_init(state)
    state["round"] = rnum
    events, market = [], {}
    live_ids = {b["id"] for b in blocs}
    names = {c: pm.get("name", c) for c, pm in personas.items()}
    # new blocs enter the registry; existing ones age and gain identity
    for b in blocs:
        cb = state["blocs"].get(b["id"])
        if not cb:
            cb = {"id": b["id"], "name": b["name"], "color": b["color"], "color2": b.get("color2"), "grievance": b.get("grievance"), "flag_svg": b.get("flag_svg"),
                  "members": list(b["members"]), "born": rnum, "age": 0, "identity": 10.0, "stage": 0, "stage_round": rnum, "manifesto": None,
                  "leaders": [], "election": None, "president": None, "recognition": {}, "guard": {}, "commands": {}, "foreign_support": 10.0,
                  "control": {}, "economy": 100.0, "trade": 100.0, "blockades": [], "sanctions": [], "front": [], "resolution": None,
                  "history": [{"round": rnum, "event": "formed"}], "power": None, "stalemate": 0, "streak": {"bloc": 0, "federal": 0}}
            state["blocs"][b["id"]] = cb
        else:
            cb["age"] += 1
            cb["members"] = sorted(set(cb["members"]) | set(b["members"])) if cb["stage"] >= 1 else list(b["members"])
        members = cb["members"]
        avg_u = _avg(scores[m]["unrest"] for m in members if m in scores)
        triggered = [ev for ev in rd_events if is_trigger(ev, members)]
        gain = IDENTITY_GAIN * (1.0 if avg_u >= 35 else 0.4) + (TRIGGER_BONUS if triggered else 0)
        cb["identity"] = round(_clamp(cb["identity"] + gain), 1)
        if triggered:
            cb["last_trigger"] = rnum
            cb["trigger_name"] = triggered[0]["name"]
        # ---------- 2. the ladder ----------
        nxt = cb["stage"] + 1
        if nxt in NEEDS and not cb.get("resolution"):
            need_id, need_u = NEEDS[nxt]
            # a shock within two rounds, or a bloc that has held a stage for three rounds with the numbers to climb (its own momentum)
            recent_trigger = rnum - cb.get("last_trigger", -99) <= 2 or (rnum - cb["stage_round"] >= 3 and cb["stage"] >= 1)
            if cb["identity"] >= need_id and avg_u >= need_u and recent_trigger and rnum - cb["stage_round"] >= 1:
                events.append(advance(cb, nxt, rnum, personas, scores, rng, llm, run_id, names))
        # ---------- 3. recognition and loyalty ----------
        if cb["stage"] >= 1:
            recognize(cb, scores, personas, rnum, rng)
        # ---------- 4/5. standoff and conflict: control, economy, resolution ----------
        if cb["stage"] >= 5 and not cb.get("resolution"):
            ev2, hits = contest(cb, scores, personas, rnum, rng, approval, rd_events, names, llm, run_id, tech or {})
            events += ev2
            for k, v in hits.items():
                market[k] = market.get(k, 0) + v
    # ---------- dissolution is an event with a cause, never silent ----------
    for bid, cb in list(state["blocs"].items()):
        if bid in live_ids or cb.get("resolution") in ("recognized independence", "frozen conflict"):
            continue
        if cb.get("gone"):
            continue
        cause = cb.get("resolution") or ("negotiated deal" if cb["stage"] >= 5 else "collapse")
        text = {"federal restoration": f"Washington has reasserted control: {cb['name']} is dissolved and its states return to federal administration.",
                "negotiated settlement": f"Under the settlement, {cb['name']} stands down as a political entity; its states keep the autonomy they bargained for.",
                "negotiated deal": f"A deal with Washington ends {cb['name']}: funds, waivers and seats at the table in exchange for standing down.",
                "collapse": f"{cb['name']} has collapsed: its member states drifted back as the pressure that made it eased."}.get(cause, f"{cb['name']} dissolves.")
        events.append({"id": f"bloc_{bid}_end_{rnum}", "round": rnum, "name": f"{cb['name']} dissolves ({cause})"[:90], "description": text, "severity": 3,
                       "affected": {"candidates": [], "companies": [], "states": list(cb["members"])}, "poll_hit": 0, "stock_hits": {}, "unrest_bump": 0,
                       "fired": True, "kind": "bloc", "image": IMAGES["resolution"] if cause != "collapse" else None})
        cb["gone"] = rnum
        cb["history"].append({"round": rnum, "event": f"dissolved: {cause}"})
        state["dissolved"].append({"id": bid, "name": cb["name"], "round": rnum, "cause": cause, "stage": cb["stage"]})
    return {"events": events, "market_hits": market, "view": view(state)}


def advance(cb, nxt, rnum, personas, scores, rng, llm, run_id, names):
    cb["stage"] = nxt
    cb["stage_round"] = rnum
    stage = STAGES[nxt]
    cb["history"].append({"round": rnum, "event": f"-> {STAGE_LABEL[stage]} (after {cb.get('trigger_name', 'events')})"})
    members_txt = ", ".join(names.get(m, m) for m in cb["members"])
    if stage == "declaration":
        words = write_declaration(llm, run_id, cb, members_txt)
        cb["manifesto"] = words["manifesto"]
        cb["slogan"] = words["slogan"]
        desc = f"{cb['name']} ({members_txt}) declares itself a political entity. Its manifesto: {cb['manifesto'][:260]}"
    elif stage == "provisional":
        cb["leaders"] = write_leaders(llm, run_id, cb, members_txt)
        desc = f"{cb['name']} names a provisional government: " + "; ".join(f"{l['name']}, {l['role']}" for l in cb["leaders"][:3]) + ". Washington calls it illegitimate."
    elif stage == "election":
        cb["election"] = bloc_election(cb, personas, scores, rng, llm, run_id, names)
        c = cb["election"]["candidates"]
        desc = f"{cb['name']} holds its own presidential election: {c[0]['name']} against {c[1]['name']}. Washington says the vote has no legal force."
    elif stage == "rival_president":
        e = cb["election"]
        w = next(c for c in e["candidates"] if c["id"] == e["winner"])
        cb["president"] = w
        desc = f"{w['name']} takes an oath as President of {cb['name']} after winning {e['ev'][w['id']]} of its {sum(e['ev'].values())} electors. Two presidents now claim the loyalty of {len(cb['members'])} states."
    elif stage == "standoff":
        desc = (f"Standoff. Federal agencies stop transfers to {cb['name']}; the bloc stops remitting federal taxes. National Guard units in "
                f"{members_txt} are told to choose. Checkpoints appear on the interstates. No shots; everyone is waiting.")
    else:   # conflict
        desc = (f"The standoff hardens into an open contest for control of {members_txt} and the states around them: blockades, sanctions, court orders, "
                f"and competing orders to Guard and federal commands. It is fought with money, loyalty and legitimacy.")
    return {"id": f"bloc_{cb['id']}_{stage}_{rnum}", "round": rnum, "name": f"{cb['name']}: {STAGE_LABEL[stage]}"[:90], "description": desc[:700], "severity": 3 + min(2, nxt // 3),
            "affected": {"candidates": [], "companies": [], "states": list(cb["members"])}, "poll_hit": 0, "stock_hits": {}, "unrest_bump": 2 if nxt >= 5 else 0,
            "fired": True, "kind": "bloc", "image": IMAGES.get(stage), "bloc": cb["id"], "stage": stage}


# ---------- words (LLM, with fallbacks) ----------
def write_declaration(llm, run_id, cb, members_txt):
    fb = {"manifesto": f"We, the states of {members_txt}, declare that Washington no longer governs with our consent. We will keep our taxes, run our own relief, and answer to our own people.", "slogan": "OUR STATES, OUR TERMS"}
    if llm is None:
        return fb
    try:
        d = llm.chat_json(DECLARE_SYSTEM, f"Bloc: {cb['name']}. Member states: {members_txt}. Main grievance: {cb.get('grievance')}. "
                          f"Write {{\"manifesto\": \"<60-90 words, first person plural, concrete demands, no real people>\", \"slogan\": \"<= 5 words, all caps>\"}}",
                          kind="conflict", run_id=run_id, temperature=0.8, max_tokens=400)
        return {"manifesto": str(d.get("manifesto") or fb["manifesto"])[:800], "slogan": str(d.get("slogan") or fb["slogan"])[:40]}
    except Exception as e:
        print(f"[conflict] declaration failed: {e}")
        return fb


def write_leaders(llm, run_id, cb, members_txt):
    fb = [{"name": "Chair Dana Whitlock", "role": "Chair of the Provisional Council", "state": cb["members"][0], "bio": "a former state budget director"},
          {"name": "Gen. (ret.) Marcus Hale", "role": "Coordinator for Public Safety", "state": cb["members"][-1], "bio": "a retired Guard adjutant general"},
          {"name": "Ruth Okafor", "role": "Treasurer", "state": cb["members"][len(cb["members"]) // 2], "bio": "a credit-union executive"}]
    if llm is None:
        return fb
    try:
        d = llm.chat_json(DECLARE_SYSTEM, f"Bloc: {cb['name']} ({members_txt}). Manifesto: {cb.get('manifesto', '')[:400]}. "
                          f"Invent 3 leaders of its provisional government. Each: 'name', 'role', 'state' (two-letter code from the members), 'bio' (<= 25 words). "
                          f"No real people, no stereotypes. Return {{\"leaders\": [...]}}", kind="conflict", run_id=run_id, temperature=0.8, max_tokens=500)
        out = [{"name": str(l.get("name", ""))[:60], "role": str(l.get("role", ""))[:60], "state": str(l.get("state", ""))[:2].upper(), "bio": str(l.get("bio", ""))[:200]}
               for l in (d.get("leaders") or []) if l.get("name")][:3]
        return out or fb
    except Exception as e:
        print(f"[conflict] leaders failed: {e}")
        return fb


def bloc_election(cb, personas, scores, rng, llm, run_id, names):
    """Two bloc candidates; each member state votes by rule (unrest, lean, a little noise). Spoiler-safe ticks for the episode."""
    cands = [{"id": "X", "name": (cb["leaders"][0]["name"] if cb["leaders"] else "Dana Whitlock"), "lane": "hardline", "color": cb["color"]},
             {"id": "Y", "name": "Sen. Lydia Marsh", "lane": "moderate", "color": cb.get("color2") or "#8893a2"}]
    if llm is not None:
        try:
            d = llm.chat_json(DECLARE_SYSTEM, f"Bloc: {cb['name']}. Leaders: {', '.join(l['name'] + ' (' + l['role'] + ')' for l in cb['leaders'])}. "
                              f"Invent the 2 candidates in the bloc's first presidential election: one hardliner (independence now), one moderate "
                              f"(autonomy, negotiate with Washington). Each: 'name', 'lane' (hardline|moderate), 'blurb' (<= 40 words). No real people. "
                              f"Return {{\"candidates\": [...]}}", kind="conflict", run_id=run_id, temperature=0.8, max_tokens=400)
            got = [c for c in (d.get("candidates") or []) if c.get("name")][:2]
            if len(got) == 2:
                cands = [{"id": "X", "name": str(got[0]["name"])[:60], "lane": "hardline", "blurb": str(got[0].get("blurb", ""))[:300], "color": cb["color"]},
                         {"id": "Y", "name": str(got[1]["name"])[:60], "lane": "moderate", "blurb": str(got[1].get("blurb", ""))[:300], "color": cb.get("color2") or "#8893a2"}]
        except Exception as e:
            print(f"[conflict] bloc candidates failed: {e}")
    from election import EV
    units, ev = {}, {"X": 0, "Y": 0}
    for m in cb["members"]:
        s = scores.get(m, {})
        hard = 50 + (s.get("unrest", 30) - 45) * 0.6 + rng.uniform(-9, 9)
        hard = _clamp(hard, 20, 80)
        vote = "X" if hard >= 50 else "Y"
        units[m] = {"vote": vote, "margin": int(abs(hard - 50) * 2), "shares": {"X": round(hard, 1), "Y": round(100 - hard, 1)}, "ev": EV.get(m, 3)}
        ev[vote] += EV.get(m, 3)
    winner = "X" if ev["X"] >= ev["Y"] else "Y"
    order = sorted(cb["members"], key=lambda m: -units[m]["margin"])
    n = len(order)
    ticks = [{"label": "Polls close across the bloc", "called": []},
             {"label": "First calls", "called": order[: max(1, n // 3)]},
             {"label": "Most of the bloc counted", "called": order[: max(1, (2 * n) // 3)]},
             {"label": "Final calls", "called": order}]
    return {"candidates": cands, "units": units, "ev": ev, "winner": winner, "ticks": ticks, "round": None}


# ---------- 3. recognition and loyalty ----------
def recognize(cb, scores, personas, rnum, rng):
    members = set(cb["members"])
    lean_bloc = _avg(personas[m].get("partisan_lean", 0) for m in members if m in personas)
    for code, pm in personas.items():
        if code in members:
            cb["recognition"][code] = "bloc"
            continue
        s = scores.get(code, {})
        near = any(n in members for n in pm.get("neighbors", []))
        same_side = 1.0 if (pm.get("partisan_lean", 0) * lean_bloc > 0 and abs(pm.get("partisan_lean", 0)) > 4) else 0.0
        score = (0.35 if near else 0) + s.get("unrest", 20) / 100.0 * 0.5 + 0.2 * same_side + (0.15 if s.get("federal_cohesion", 60) < 50 else 0) + cb["identity"] / 400.0
        prev = cb["recognition"].get(code, "federal")
        # recognition is sticky: a state needs a clear margin to switch
        if prev == "federal":
            cb["recognition"][code] = "bloc" if score >= 0.85 else "neutral" if score >= 0.62 else "federal"
        elif prev == "neutral":
            cb["recognition"][code] = "bloc" if score >= 0.8 else "federal" if score < 0.5 else "neutral"
        else:
            cb["recognition"][code] = "federal" if score < 0.45 else "neutral" if score < 0.65 else "bloc"
    # National Guard loyalty to Washington follows the state's alignment with a lag; member Guards drift to the bloc
    for code in personas:
        cur = cb["guard"].get(code, 85.0)
        target = {"bloc": 20.0, "neutral": 55.0, "federal": 90.0}[cb["recognition"].get(code, "federal")]
        if code in members and cb["stage"] >= 2:
            target = 15.0
        cb["guard"][code] = round(cur + (target - cur) * 0.35 + rng.uniform(-3, 3), 1)
    # regional military commands: loyal to Washington unless their region has turned
    for cmd, regions in COMMANDS.items():
        codes = [c for c, pm in personas.items() if pm.get("region") in regions]
        if not codes:
            continue
        share_bloc = sum(1 for c in codes if cb["recognition"].get(c) == "bloc") / len(codes)
        cur = cb["commands"].get(cmd, 95.0)
        target = _clamp(95 - share_bloc * 110 - (10 if cb["stage"] >= 5 else 0), 25, 98)
        cb["commands"][cmd] = round(cur + (target - cur) * 0.3, 1)
    # foreign support grows slowly once there is a government to talk to, faster if Washington looks illegitimate
    if cb["stage"] >= 2:
        cb["foreign_support"] = round(_clamp(cb["foreign_support"] + 3 + (3 if cb["stage"] >= 4 else 0) + rng.uniform(-1, 2)), 1)


# ---------- 4/5/6. the contest ----------
def legitimacy(cb, approval, rd_events, rnum):
    fed = (approval if approval is not None else 50.0)
    for ev in rd_events:
        n = (ev.get("name") or "").lower()
        if any(w in n for w in ("postpone", "advisory", "ignore", "defy", "emergency", "martial")):
            fed -= 12
    bloc = 30 + (25 if cb["stage"] >= 4 else 10 if cb["stage"] >= 2 else 0) + cb["identity"] * 0.3
    return _clamp(fed), _clamp(bloc)


def contest(cb, scores, personas, rnum, rng, approval, rd_events, names, llm, run_id, tech=None):
    events, hits = [], {}
    members = cb["members"]
    if not cb["control"]:
        for c in personas:
            rec = cb["recognition"].get(c, "federal")
            cb["control"][c] = 100.0 if c in members else 50.0 if rec == "bloc" else 30.0 if rec == "neutral" else 0.0
    fed_legit, bloc_legit = legitimacy(cb, approval, rd_events, rnum)
    guard_bloc = 100 - _avg(cb["guard"].get(m, 85) for m in members)
    mil_fed = _avg(cb["commands"].values()) if cb["commands"] else 90
    bloc_power = 0.35 * guard_bloc + 0.25 * cb["economy"] + 0.2 * bloc_legit + 0.2 * cb["foreign_support"]
    fed_power = 0.35 * mil_fed + 0.25 * cb["trade"] + 0.2 * fed_legit + 0.2 * (100 - cb["foreign_support"])
    # the technology era: autonomous federal forces tilt the balance to Washington; a bloc that holds robot factories gets leverage
    tech = tech or {}
    fed_power += 20 * float(tech.get("federal_force", 0))
    if tech.get("bloc_factories"):
        fac = sum(1 for m in members if any(w in " ".join(personas.get(m, {}).get("key_industries") or []).lower() for w in ("manufactur", "robot", "automotive", "aerospace")))
        bloc_power += 10 * float(tech["bloc_factories"]) * fac / max(1, len(members))
    cb["tech_edge"] = {"federal_force": round(20 * float(tech.get("federal_force", 0)), 1), "factories": round(bloc_power - (0.35 * guard_bloc + 0.25 * cb["economy"] + 0.2 * bloc_legit + 0.2 * cb["foreign_support"]), 1)}
    adv = bloc_power - fed_power
    cb["power"] = {"bloc": round(bloc_power, 1), "federal": round(fed_power, 1), "advantage": round(adv, 1), "guard_bloc": round(guard_bloc, 1),
                   "military_federal": round(mil_fed, 1), "legitimacy": {"federal": round(fed_legit, 1), "bloc": round(bloc_legit, 1)}}
    # the economy: trade between the two sides falls every round of standoff, faster in conflict; blockades and sanctions are events
    rate = 4 if cb["stage"] == 5 else 7
    cb["trade"] = round(_clamp(cb["trade"] - rate), 1)
    cb["economy"] = round(_clamp(cb["economy"] - rate * 0.8 + cb["foreign_support"] * 0.04), 1)
    for sector, pct in SECTOR_HITS["conflict" if cb["stage"] >= 6 else "standoff"].items():
        hits[f"sector:{sector}"] = pct
    if cb["stage"] >= 6:
        # control of contested ground moves with the advantage; member states are the bloc's unless the advantage turns hard against it
        for c in cb["control"]:
            if c in members:
                cb["control"][c] = round(_clamp(cb["control"][c] + min(0, adv) * 0.12 + rng.uniform(-1, 1)), 1)
            elif cb["recognition"].get(c) in ("bloc", "neutral"):
                cb["control"][c] = round(_clamp(cb["control"][c] + adv * 0.15 + rng.uniform(-2, 2)), 1)
        if rng.random() < 0.45 and len(cb["blockades"]) < 4:
            port = rng.choice(members)
            cb["blockades"].append({"round": rnum, "state": port, "by": "federal"})
            cb["economy"] = round(_clamp(cb["economy"] - 6), 1)
            hits["sector:logistics"] = hits.get("sector:logistics", 0) - 5
            events.append({"id": f"bloc_{cb['id']}_blockade_{rnum}", "round": rnum, "name": f"Federal blockade of {names.get(port, port)}"[:90],
                           "description": f"Federal authorities close river and rail traffic into {names.get(port, port)} and freeze the bloc's accounts in federal banks. Shelves thin; the bloc answers with its own permits and a fuel levy.",
                           "severity": 3, "affected": {"candidates": [], "companies": [], "states": [port]}, "poll_hit": 0, "stock_hits": {}, "unrest_bump": 3,
                           "fired": True, "kind": "bloc", "image": IMAGES["blockade"], "bloc": cb["id"]})
        elif rng.random() < 0.35 and len(cb["sanctions"]) < 3:
            cb["sanctions"].append({"round": rnum, "what": "banking", "by": "federal"})
            hits["sector:bank"] = hits.get("sector:bank", 0) - 3
        # resolution
        if adv <= -12:
            cb["streak"]["federal"] += 1
            cb["streak"]["bloc"] = 0
        elif adv >= 12:
            cb["streak"]["bloc"] += 1
            cb["streak"]["federal"] = 0
        else:
            cb["streak"] = {"bloc": 0, "federal": 0}
            cb["stalemate"] += 1
        outcome = None
        if cb["streak"]["federal"] >= 2:
            outcome = "federal restoration"
        elif cb["streak"]["bloc"] >= 2 and cb["foreign_support"] >= 50:
            outcome = "recognized independence"
        elif cb["stalemate"] >= 4 or (cb["streak"]["bloc"] >= 2 and cb["foreign_support"] < 50 and cb["stalemate"] >= 2):
            outcome = "negotiated settlement"
        if outcome:
            events.append(resolve(cb, outcome, rnum, names, llm, run_id))
    # front line: the member/aligned states the bloc holds against the ones it does not
    cb["front"] = sorted([c for c, v in cb["control"].items() if 35 <= v <= 65])
    return events, hits


def resolve(cb, outcome, rnum, names, llm, run_id):
    cb["resolution"] = outcome
    cb["stage"] = 7
    cb["stage_round"] = rnum
    members_txt = ", ".join(names.get(m, m) for m in cb["members"])
    fb = {"federal restoration": f"With the Guard back under federal orders and the bloc's accounts frozen, the provisional government of {cb['name']} resigns. Washington promises no prosecutions and a relief package for {members_txt}.",
          "recognized independence": f"Washington and {cb['name']} sign an armistice of recognition: the bloc keeps its government and its taxes, trade reopens under a customs treaty, and foreign governments begin exchanging envoys.",
          "negotiated settlement": f"The settlement: {cb['name']} stands down its claim of independence; in return its states get block grants, a veto over federal preemption, and seats on a joint commission. Both presidents call it a win."}
    text = fb[outcome]
    if llm is not None:
        try:
            d = llm.chat_json(RESOLVE_SYSTEM, f"Outcome: {outcome}. Bloc: {cb['name']} ({members_txt}), stage reached: {STAGE_LABEL[STAGES[min(6, cb['stage'])]]}. "
                              f"Power at the end: {cb.get('power')}. Write {{\"terms\": \"<60-90 words: the concrete terms of this outcome, as a wire would report them, no real people>\"}}",
                              kind="conflict", run_id=run_id, temperature=0.6, max_tokens=400)
            text = str(d.get("terms") or text)[:800]
        except Exception as e:
            print(f"[conflict] resolution text failed: {e}")
    cb["resolution_text"] = text
    cb["history"].append({"round": rnum, "event": f"resolution: {outcome}"})
    return {"id": f"bloc_{cb['id']}_resolution_{rnum}", "round": rnum, "name": f"{cb['name']}: {outcome}"[:90], "description": text[:700], "severity": 4,
            "affected": {"candidates": [], "companies": [], "states": list(cb["members"])}, "poll_hit": 0, "stock_hits": {}, "unrest_bump": -6,
            "fired": True, "kind": "bloc", "image": IMAGES["resolution"], "bloc": cb["id"], "stage": "resolution", "outcome": outcome}


def freeze_unresolved(state, rnum):
    """A term or campaign ends with a bloc still in conflict: a frozen conflict, carried into the next season."""
    out = []
    for cb in (state or {}).get("blocs", {}).values():
        if cb["stage"] == 6 and not cb.get("resolution"):
            cb["resolution"] = "frozen conflict"
            cb["history"].append({"round": rnum, "event": "resolution: frozen conflict"})
            cb["resolution_text"] = f"No settlement: {cb['name']} holds its ground, Washington holds the rest, and the checkpoints become permanent. The conflict is frozen."
            out.append(cb)
    return out


# ---------- the slim view the timeline and the UI carry ----------
def view(state):
    out = {"blocs": []}
    for cb in state["blocs"].values():
        if cb.get("gone") and not cb.get("resolution"):
            continue
        rec = cb.get("recognition") or {}
        out["blocs"].append({"id": cb["id"], "name": cb["name"], "color": cb["color"], "color2": cb.get("color2"), "members": cb["members"], "age": cb["age"],
                             "identity": cb["identity"], "stage": cb["stage"], "stage_name": STAGE_LABEL[STAGES[cb["stage"]]], "manifesto": cb.get("manifesto"),
                             "slogan": cb.get("slogan"), "leaders": cb.get("leaders", []), "president": cb.get("president"),
                             "election": ({k: cb["election"][k] for k in ("candidates", "ev", "winner", "ticks")} | {"units": {c: {"vote": u["vote"], "margin": u["margin"]} for c, u in cb["election"]["units"].items()}}) if cb.get("election") else None,
                             "recognition": rec, "counts": {k: sum(1 for v in rec.values() if v == k) for k in ("federal", "bloc", "neutral")},
                             "guard": cb.get("guard", {}), "commands": cb.get("commands", {}), "foreign_support": cb.get("foreign_support", 0),
                             "control": cb.get("control", {}), "front": cb.get("front", []), "economy": cb.get("economy"), "trade": cb.get("trade"),
                             "blockades": cb.get("blockades", []), "sanctions": cb.get("sanctions", []), "power": cb.get("power"),
                             "resolution": cb.get("resolution"), "resolution_text": cb.get("resolution_text"), "gone": cb.get("gone"), "history": cb.get("history", [])[-8:]})
    out["dissolved"] = state.get("dissolved", [])
    out["active"] = any(b["stage"] >= 1 and not b.get("gone") for b in out["blocs"])
    out["war_room"] = any(b["stage"] >= 5 and not b.get("gone") for b in out["blocs"])
    return out


def summary_lines(state):
    """For the news desk, the feed and the history book: one line per live bloc."""
    lines = []
    for cb in (state or {}).get("blocs", {}).values():
        if cb.get("gone"):
            continue
        s = f"{cb['name']} ({', '.join(cb['members'])}): {STAGE_LABEL[STAGES[cb['stage']]]}, identity {cb['identity']:.0f}"
        if cb["stage"] >= 1:
            c = {k: sum(1 for v in cb["recognition"].values() if v == k) for k in ("bloc", "neutral")}
            s += f"; {c['bloc']} states recognize it, {c['neutral']} neutral"
        if cb.get("president"):
            s += f"; its president is {cb['president']['name']}"
        if cb.get("power"):
            s += f"; power bloc {cb['power']['bloc']} vs federal {cb['power']['federal']}, trade index {cb['trade']}, {len(cb['blockades'])} blockades"
        if cb.get("resolution"):
            s += f"; RESOLVED: {cb['resolution']}"
        lines.append(s)
    for d in (state or {}).get("dissolved", [])[-3:]:
        lines.append(f"{d['name']} dissolved in round {d['round']} ({d['cause']})")
    return lines
