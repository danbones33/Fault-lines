"""
GOVERNING MODE (Season 2+): the winner of an election run takes office and a new run plays one term,
one round per quarter (Q1 Y1 .. Q4 Y4), ending in a re-election.

Everything that moves the world is a RULE applied to structured objects produced once by an LLM:
  * history book: a compressed summary of the parent season plus the hard numbers (not every post)
  * policies: the winner's platform turned into POLICY objects with immediate, lagged and unintended effects
  * Congress: House/Senate seats carried from the election; each policy is voted (pass / watered / fail),
    a failed one can return as an executive order (weaker, can be struck down later)
  * approval: national and per state, moved by policy effects (weighted by each state's lean and AI stance),
    events, unrest, and mean reversion
  * events: random quarterly draws (recession, scandal, disaster, foreign crisis, viral protest) plus the
    scheduled-event system; the president responds and that moves approval
  * midterms after Q8 (seats shift with approval and unrest); re-election after Q16 against challengers
    generated as opponents of the record
"""
import hashlib
import random

QUARTERS = 16
SECTORS = ["tech", "manufacturing", "logistics", "energy", "healthcare", "finance", "agriculture", "retail", "government", "housing"]
EFFECT_RANGES = {"approval": (-7, 7), "unrest": (-15, 15), "prices": (-6, 6), "crime": (-10, 10), "police": (-5, 5), "surveillance": (-10, 10)}
HONEYMOON = 0.5     # how much of a landslide carries into day-one approval (popular share above 50, halved)
REVERSION = 0.07    # share of the gap to 50 that approval gives back every quarter
GROUPS = ["enthusiast", "curious", "skeptical", "hostile", "left", "right", "center", "hurting", "prosperous"]

HISTORY_SYSTEM = ("You are the historian of a fictional simulated America. Write a compressed 'history book' chapter of the season just "
                  "finished: what happened, who won and why, what the public mood was, which companies rose or fell, which states broke "
                  "away. Dense, factual, no real people. Return JSON only.")
POLICY_SYSTEM = ("You turn a fictional president's platform into structured policy levers for a simulation. Numbers must be modest and "
                 "defensible; every policy has a cost, an immediate effect, a LAGGED effect that arrives later, and one plausible unintended "
                 "consequence. Return JSON only.")
RESPONSE_SYSTEM = ("You write short presidential statements for a fictional simulation, in the president's established voice. Return JSON only.")
CHALLENGER_SYSTEM = ("You invent fictional challengers for a re-election in a simulation: opponents of the incumbent's record, each with a "
                     "lane, party, full bio with a visual description, and platform. No real people. Return JSON only.")

EVENT_TABLE = [
    {"kind": "recession", "name": "Recession fears", "p": 0.10, "severity": 3, "effects": {"approval": -5, "unrest": 4, "prices": -1, "market": -6}, "targets": "all",
     "desc": "GDP contracts for a second quarter; layoffs spread beyond the AI shift.", "address": True,
     "image": "news photograph: a shuttered factory gate at dawn with a hand-lettered CLOSED sign, workers' pickups in a nearly empty lot, grey sky"},
    {"kind": "scandal", "name": "Cabinet scandal", "p": 0.10, "severity": 2, "effects": {"approval": -4, "unrest": 1, "market": 0}, "targets": "none",
     "desc": "A cabinet secretary resigns over undisclosed payments.", "address": False, "image": None},
    {"kind": "disaster", "name": "Hurricane landfall", "p": 0.12, "severity": 4, "effects": {"approval": -2, "unrest": 4, "market": -2}, "targets": "gulf",
     "desc": "A Category 4 hurricane makes landfall on the Gulf Coast; whole neighborhoods are under water and the federal response is judged in real time.",
     "address": True, "aftermath": {"name": "Relief funds stall", "p": 0.6, "desc": "Governors say federal disaster relief has not arrived; shelters are full and insurers are walking away.", "effects": {"approval": -6, "unrest": 6}},
     "image": "aerial news photograph after a hurricane: flooded streets of a Gulf Coast town, roofs torn off, a rescue boat between houses, brown water, overcast light"},
    {"kind": "foreign", "name": "Foreign crisis", "p": 0.10, "severity": 3, "effects": {"approval": 3, "unrest": 2, "market": -4}, "targets": "none",
     "desc": "A naval standoff abroad; markets flinch, the country rallies briefly.", "address": True,
     "image": "news photograph: a grey warship at sea at dusk, a helicopter lifting off its deck, long lens, no flags or insignia"},
    {"kind": "protest", "name": "Viral protest", "p": 0.14, "severity": 2, "effects": {"approval": -2, "unrest": 6, "market": -1}, "targets": "hot",
     "desc": "A livestreamed confrontation at a state capitol goes viral; copycat marches follow.", "address": False,
     "image": "news photograph: a crowd pressing against the steps of a state capitol at night, phone screens glowing, police line, no readable text"},
    {"kind": "boom", "name": "Productivity boom", "p": 0.08, "severity": 2, "effects": {"approval": 3, "unrest": -3, "prices": -1, "market": 5}, "targets": "prosperous",
     "desc": "Output jumps as automation gains are finally shared; hiring ticks up.", "address": False,
     "image": "news photograph: a bright new assembly hall with workers and robot arms side by side, a HIRING banner blurred in the background"},
    {"kind": "court", "name": "Supreme Court ruling", "p": 0.08, "severity": 2, "effects": {"approval": 0, "unrest": 2, "market": 0}, "targets": "none",
     "desc": "The Court rules on executive power; one executive order is at risk.", "address": False, "image": None},
]
GULF = ["LA", "TX", "FL", "MS", "AL", "GA", "SC", "NC"]


def pick_targets(rule, rng, personas, round_scores):
    """Which states an event lands on: a Gulf hurricane, the hottest states, the prosperous ones, everyone, or no one."""
    if rule == "gulf":
        return sorted(rng.sample(GULF, rng.randint(3, 5)))
    if rule == "hot":
        return [c for c, _ in sorted(round_scores.items(), key=lambda kv: -kv[1]["unrest"])[:3]]
    if rule == "prosperous":
        return [c for c, _ in sorted(personas.items(), key=lambda kv: kv[1].get("baseline_grievance", 20))[:5]]
    return []


def quarter_label(q):
    return f"Q{(q - 1) % 4 + 1} Y{(q - 1) // 4 + 1}"


def _clamp(v, lo, hi):
    try:
        return max(lo, min(hi, float(v)))
    except Exception:
        return 0.0


# ---------- history book ----------
def history_prompt(parent):
    tl = parent["timeline"]
    el = tl.get("election") or {}
    res = el.get("results") or {}
    names = {o["id"]: o["name"] for o in el.get("options", [])}
    last = tl["rounds"][-1]["states"]
    top = sorted(last.items(), key=lambda kv: -kv[1]["unrest"])[:6]
    lines = [f"Scenario: {tl['scenario']['text'][:1500]}",
             f"Election: {el.get('question','')} Result: " + (", ".join(f"{names[k]} {v} EV" for k, v in res.get("ev", {}).items()) if res else "no election"),
             "Winner: " + ((names.get(res.get("winner")) if res.get("winner") else f"no one reached 270; the House of Representatives chose {names.get(res.get('house_winner'), 'n/a')} by state delegation") if res else "n/a"),
             "Final unrest leaders: " + "; ".join(f"{c} {s['unrest']} ({s['stage_name']})" for c, s in top),
             "Blocs at the end: " + ("; ".join(f"{b['name']} ({', '.join(b['members'])})" for b in tl["rounds"][-1]["blocs"]) or "none"),
             "Scheduled events that fired: " + ("; ".join(e["name"] for e in tl.get("events_fired", [])) or "none")]
    mk = parent.get("market") or {}
    if mk.get("history"):
        h0, h1 = mk["history"][0], mk["history"][-1]
        lines.append("Market: FLN 500 " + f"{h0['prices'].get('FLN500',0):,.0f} -> {h1['prices'].get('FLN500',0):,.0f}; tickers: " +
                     ", ".join(f"{t['sym']} ({t['name']}) {h0['prices'][t['sym']]:.2f}->{h1['prices'][t['sym']]:.2f}" for t in mk.get("tickers", [])))
    if parent.get("conflict"):
        lines.append("Civil conflict: " + " | ".join(parent["conflict"]))
    lines += parent.get("broadcast_headlines", [])[:12]
    return ("Season facts:\n" + "\n".join(lines) + "\n\nWrite {\"title\": \"<= 8 words\", \"summary\": \"<300-450 words, past tense, the whole "
            "season as a history chapter>\", \"key_facts\": [\"<= 12 one-line facts with numbers\"], \"open_threads\": [\"<= 5 unresolved threads "
            "the next season could pick up\"]}")


def history_book(llm, run_id, parent):
    if llm is not None:
        try:
            d = llm.chat_json(HISTORY_SYSTEM, history_prompt(parent), kind="season", run_id=run_id, temperature=0.5, max_tokens=1600)
            return {"title": str(d.get("title", "Season"))[:80], "summary": str(d.get("summary", ""))[:4000],
                    "key_facts": [str(x)[:200] for x in (d.get("key_facts") or [])][:12], "open_threads": [str(x)[:200] for x in (d.get("open_threads") or [])][:5]}
        except Exception as e:
            print(f"[govern] history failed: {e}")
    tl = parent["timeline"]
    return {"title": tl["scenario"]["title"][:80], "summary": tl["scenario"]["text"][:1500], "key_facts": [], "open_threads": []}


# ---------- policies ----------
def policy_prompt(winner, history, tickers):
    tk = "; ".join(f"{t['sym']} ({t['name']}, {t.get('sector','')})" for t in tickers)
    a = winner.get("analysis") or {}
    return (f"President-elect: {winner['name']} ({winner.get('party','')}, {winner.get('lane','')} lane). {a.get('short_bio','')}\n"
            f"Platform:\n{winner.get('blurb','')}\n\nHistory of the season just ended:\n{history['summary'][:1500]}\n\nTickers: {tk}\n\n"
            f"Convert the platform into 4 to 6 POLICY objects. Each: 'name', 'summary' (<= 25 words), 'cost_bn' (billions per year), "
            f"'immediate' effects, 'lag_quarters' (1-6), 'lagged' effects, and 'unintended': {{'after_quarters': 2-8, 'description': <= 25 words, "
            f"'effects': {{...}}}}. Effects objects use only these keys, all optional: 'approval' (-12..12 national points), 'unrest' (-15..15), "
            f"'prices' (-6..6 percent), 'jobs' ({{sector: -8..8}} for sectors in {SECTORS}), 'market' ({{SYM: -30..30 percent}} for listed "
            f"tickers), 'groups' ({{group: -10..10 approval}} for groups in {GROUPS}: enthusiast/curious/skeptical/hostile are AI stances, "
            f"left/right/center political cultures, hurting/prosperous economies), 'crime' (-10..10 points of the national crime index, arriving "
            f"with the lag), 'police' (-5..5 police and justice funding), 'surveillance' (-10..10). Crime levers you may use if they fit the platform: "
            f"police funding, sentencing reform, prison policy, AI fraud laws, robot policing, drug policy; each with an unintended consequence. "
            f"Return {{\"policies\": [...]}}")


def _clean_effects(e, syms):
    out = {}
    if not isinstance(e, dict):
        return out
    for k in ("approval", "unrest", "prices", "crime", "police", "surveillance"):
        if k in e:
            out[k] = _clamp(e[k], *EFFECT_RANGES[k])
    if isinstance(e.get("jobs"), dict):
        out["jobs"] = {str(s)[:20]: _clamp(v, -8, 8) for s, v in e["jobs"].items() if str(s) in SECTORS}
    if isinstance(e.get("market"), dict):
        out["market"] = {str(s).upper(): _clamp(v, -30, 30) for s, v in e["market"].items() if str(s).upper() in syms}
    if isinstance(e.get("groups"), dict):
        out["groups"] = {str(g): _clamp(v, -10, 10) for g, v in e["groups"].items() if str(g) in GROUPS}
    return out


def build_policies(llm, run_id, winner, history, tickers):
    syms = {t["sym"] for t in tickers}
    raw = []
    if llm is not None:
        try:
            d = llm.chat_json(POLICY_SYSTEM, policy_prompt(winner, history, tickers), kind="season", run_id=run_id, temperature=0.5, max_tokens=2500)
            raw = [p for p in (d.get("policies") or []) if isinstance(p, dict)]
        except Exception as e:
            print(f"[govern] policies failed: {e}")
    out = []
    for i, p in enumerate(raw[:6], 1):
        try:
            lag = max(1, min(6, int(p.get("lag_quarters", 2))))
        except Exception:
            lag = 2
        un = p.get("unintended") if isinstance(p.get("unintended"), dict) else {}
        try:
            after = max(2, min(8, int(un.get("after_quarters", 4))))
        except Exception:
            after = 4
        try:
            cost = max(0.0, float(p.get("cost_bn", 50)))
        except Exception:
            cost = 50.0
        out.append({"id": f"p{i}", "name": str(p.get("name", f"Policy {i}"))[:60], "summary": str(p.get("summary", ""))[:200], "cost_bn": cost,
                    "immediate": _clean_effects(p.get("immediate"), syms), "lag_quarters": lag, "lagged": _clean_effects(p.get("lagged"), syms),
                    "unintended": {"after_quarters": after, "description": str(un.get("description", ""))[:200], "effects": _clean_effects(un.get("effects"), syms)},
                    "status": "pending", "vote_quarter": i + 1, "history": []})
    if not out:
        out = [{"id": "p1", "name": "Transition relief package", "summary": "Direct aid to displaced workers", "cost_bn": 120,
                "immediate": {"approval": 3, "unrest": -4, "prices": 1}, "lag_quarters": 3, "lagged": {"approval": 2, "unrest": -2},
                "unintended": {"after_quarters": 4, "description": "Deficit hawks revolt; borrowing costs rise", "effects": {"approval": -3, "prices": 1}},
                "status": "pending", "vote_quarter": 2, "history": []}]
    return out


# ---------- congress ----------
def congress_from_election(election, results, rng):
    """Seats by lane from the popular vote (plus noise); the president's lane is what matters for votes."""
    opts = election["options"]
    pop = results.get("popular") or {o["id"]: 100 / len(opts) for o in opts}
    lanes = {}
    for o in opts:
        lanes[o.get("lane") or "other"] = lanes.get(o.get("lane") or "other", 0) + pop.get(o["id"], 0)
    tot = sum(lanes.values()) or 1
    house, senate = {}, {}
    for lane, share in lanes.items():
        house[lane] = int(round(435 * share / tot + rng.uniform(-12, 12)))
        senate[lane] = int(round(100 * share / tot + rng.uniform(-4, 4)))
    for chamber, n in ((house, 435), (senate, 100)):
        diff = n - sum(chamber.values())
        top = max(chamber, key=chamber.get)
        chamber[top] += diff
    return {"house": house, "senate": senate}


def president_share(congress, lane):
    h = congress["house"].get(lane, 0) / 435
    s = congress["senate"].get(lane, 0) / 100
    return (h + s) / 2


def vote(policy, congress, lane, approval, rng):
    """Pass probability from the president's seat share, approval and cost. Returns outcome + strength multiplier."""
    share = president_share(congress, lane)
    p = 0.15 + share * 0.9 + (approval - 50) / 150 - min(0.25, policy["cost_bn"] / 1200)
    roll = rng.random()
    if roll < p - 0.18:
        return "passed", 1.0
    if roll < p + 0.12:
        return "watered", 0.5
    return "failed", 0.0


# ---------- approval ----------
def init_approval(election, results, winner_id, personas):
    """Day-one approval: a vote share is not approval. Half the margin above 50 carries over (a 70% landslide starts near 60)."""
    pop = float((results.get("popular") or {}).get(winner_id, 50.0))
    nat = 50 + (pop - 50) * HONEYMOON
    states = {}
    for code, pm in personas.items():
        u = (results.get("states") or {}).get(code) or {}
        share = float((u.get("shares") or {}).get(winner_id, pop)) if u.get("shares") else pop
        states[code] = round(_clamp(50 + (share - 50) * HONEYMOON, 15, 85), 1)
    return {"national": round(nat, 1), "states": states}


def _national(state):
    st = state["approval"]["states"]
    state["approval"]["national"] = round(sum(st.values()) / max(1, len(st)), 1)


def group_weight(pm, winner):
    """How much a state belongs to each group (for group-targeted effects)."""
    w = {}
    st = pm.get("ai_stance")
    if st:
        w[st] = 1.0
    lean = pm.get("partisan_lean", 0)
    w["right" if lean > 4 else "left" if lean < -4 else "center"] = 1.0
    w["hurting" if pm.get("baseline_grievance", 20) >= 24 else "prosperous"] = 1.0
    return w


def apply_effects(effects, strength, state, personas, winner, rng, tag, targets=None):
    """Applies an effects object to the governing state in place; returns a log line. Rules only.
    With targets, the hit lands mostly on those states (approval x2.2 there, x0.35 elsewhere; unrest mostly there)."""
    log = {"tag": tag, "strength": strength, "applied": {}, "targets": list(targets or [])}
    a = effects.get("approval", 0) * strength
    if a:
        log["applied"]["approval"] = round(a, 1)
    groups = effects.get("groups") or {}
    for code, pm in personas.items():
        delta = a * ((2.2 if code in targets else 0.35) if targets else 1.0)
        for g, v in groups.items():
            delta += v * strength * group_weight(pm, winner).get(g, 0)
        if delta:
            state["approval"]["states"][code] = round(_clamp(state["approval"]["states"][code] + delta + rng.uniform(-0.6, 0.6), 2, 98), 1)
    _national(state)
    if effects.get("unrest"):
        u = effects["unrest"] * strength
        if targets:
            state.setdefault("pending_unrest_states", {})
            for code in targets:
                state["pending_unrest_states"][code] = state["pending_unrest_states"].get(code, 0) + u * 1.6
            state["pending_unrest"] = state.get("pending_unrest", 0) + u * 0.3
        else:
            state["pending_unrest"] = state.get("pending_unrest", 0) + u
        log["applied"]["unrest"] = round(u, 1)
    if effects.get("prices"):
        state["prices_index"] = round(state.get("prices_index", 100) * (1 + effects["prices"] * strength / 100), 2)
        log["applied"]["prices"] = round(effects["prices"] * strength, 1)
    for s, v in (effects.get("jobs") or {}).items():
        state["jobs"][s] = round(state["jobs"].get(s, 0) + v * strength, 1)
    for sym, v in (effects.get("market") or {}).items():
        state["pending_market"][sym] = state["pending_market"].get(sym, 0) + v * strength
    if effects.get("jobs"):
        log["applied"]["jobs"] = {s: round(v * strength, 1) for s, v in effects["jobs"].items()}
    if effects.get("market"):
        log["applied"]["market"] = {s: round(v * strength, 1) for s, v in effects["market"].items()}
    return log


# ---------- quarter step ----------
def init_state(election, results, winner, personas, policies, congress, history, twist_events):
    return {"quarter": 0, "winner": winner["id"], "lane": winner.get("lane") or "center", "approval": init_approval(election, results, winner["id"], personas),
            "congress": congress, "policies": policies, "pending": [], "jobs": {s: 0.0 for s in SECTORS}, "prices_index": 100.0,
            "pending_market": {}, "pending_unrest": 0.0, "events": [], "midterm": None, "history": history, "approval_series": [],
            "executive_orders": [], "twist_events": twist_events or []}


def step_quarter(state, q, round_scores, prev_scores, personas, winner, rng, scheduled_events=None):
    """Advance the governing state by one quarter. Returns the quarter outcome (what the wire and the UI show)."""
    state["quarter"] = q
    state["pending_unrest"] = 0.0
    state["pending_unrest_states"] = {}
    state["pending_market"] = {}
    outcome = {"quarter": q, "label": quarter_label(q), "vote": None, "effects": [], "events": [], "struck_down": [], "midterm": None}
    # 1. the policy on the agenda this quarter goes to Congress
    for pol in state["policies"]:
        if pol["status"] == "pending" and pol["vote_quarter"] == q:
            res, strength = vote(pol, state["congress"], state["lane"], state["approval"]["national"], rng)
            pol["status"] = res
            pol["history"].append({"quarter": q, "event": f"Congress: {res}"})
            if res == "failed" and rng.random() < 0.6:
                pol["status"] = "executive_order"
                strength = 0.4
                state["executive_orders"].append({"policy": pol["id"], "quarter": q})
                pol["history"].append({"quarter": q, "event": "signed as an executive order"})
            outcome["vote"] = {"policy": pol["id"], "name": pol["name"], "result": pol["status"], "strength": strength, "cost_bn": pol["cost_bn"]}
            if strength > 0:
                outcome["effects"].append(apply_effects(pol["immediate"], strength, state, personas, winner, rng, f"{pol['name']}: immediate"))
                state["pending"].append({"due": q + pol["lag_quarters"], "policy": pol["id"], "kind": "lagged", "effects": pol["lagged"], "strength": strength})
                state["pending"].append({"due": q + pol["unintended"]["after_quarters"], "policy": pol["id"], "kind": "unintended",
                                         "effects": pol["unintended"]["effects"], "strength": strength, "description": pol["unintended"]["description"]})
            break
    # 2. lagged + unintended effects that come due (an event's aftermath comes due as a new event, handled in step 4)
    due = [p for p in state["pending"] if p["due"] == q]
    state["pending"] = [p for p in state["pending"] if p["due"] != q]
    names = {p["id"]: p["name"] for p in state["policies"]}
    aftermath = [p for p in due if p.get("kind") == "aftermath"]
    for p in due:
        if p.get("kind") == "aftermath":
            continue
        tag = f"{names.get(p['policy'], p['policy'])}: {p['kind']}" + (f" ({p['description']})" if p.get("description") else "")
        outcome["effects"].append(apply_effects(p["effects"], p["strength"], state, personas, winner, rng, tag))
        for pol in state["policies"]:
            if pol["id"] == p["policy"]:
                pol["history"].append({"quarter": q, "event": f"{p['kind']} effect arrived" + (f": {p['description']}" if p.get("description") else "")})
    # 3. executive orders can be struck down (court event or 12% a quarter after 2 quarters)
    for eo in list(state["executive_orders"]):
        if q - eo["quarter"] >= 2 and rng.random() < 0.12:
            state["executive_orders"].remove(eo)
            for pol in state["policies"]:
                if pol["id"] == eo["policy"]:
                    pol["status"] = "struck_down"
                    pol["history"].append({"quarter": q, "event": "executive order struck down by the courts"})
                    outcome["struck_down"].append(pol["name"])
                    outcome["effects"].append(apply_effects({k: -v for k, v in pol["immediate"].items() if isinstance(v, (int, float))}, 0.4, state, personas, winner, rng, f"{pol['name']}: struck down (reversal)"))
    # 4. events: scheduled (twist / seed) and random draws
    fired = list(scheduled_events or [])
    state_names = {c: pm.get("name", c) for c, pm in personas.items()}
    for p in aftermath:
        fired.append({"id": f"aft_{q}_{p['event']}", "name": p["name"], "description": p["description"] + " Hardest hit: " + ", ".join(state_names.get(c, c) for c in p["states"]) + ".",
                      "severity": 2, "kind": "aftermath", "effects": dict(p["effects"]), "states": list(p["states"]), "address": True, "image": None})
    def _kind(e):   # a scheduled "Hurricane Delia" counts as a disaster for the cooldown
        n = (e.get("name") or "").lower()
        return "disaster" if any(w in n for w in ("hurricane", "flood", "storm", "wildfire", "earthquake")) else e.get("kind")
    recent = {}
    for qe in state["events"]:
        for e in qe["events"]:
            k = _kind(e)
            if k:
                recent[k] = max(recent.get(k, -99), e["quarter"])
    for e in fired:
        if _kind(e):
            recent[_kind(e)] = q
    table = list(EVENT_TABLE)
    rng.shuffle(table)
    for ev in table:
        if q - recent.get(ev["kind"], -99) < (6 if ev["kind"] == "disaster" else 4):
            continue
        if rng.random() < ev["p"] * (1.3 if state["approval"]["national"] < 42 else 1.0):
            targets = pick_targets(ev.get("targets"), rng, personas, round_scores)
            desc = ev["desc"] + (" Hardest hit: " + ", ".join(state_names.get(c, c) for c in targets) + "." if targets else "")
            fired.append({"id": f"rnd_{q}_{ev['kind']}", "name": ev["name"], "description": desc, "severity": ev["severity"], "kind": ev["kind"],
                          "effects": dict(ev["effects"]), "random": True, "states": targets, "address": ev.get("address", False), "image": ev.get("image")})
            if ev.get("aftermath") and targets and rng.random() < ev["aftermath"]["p"]:
                state["pending"].append({"due": q + 1, "kind": "aftermath", "event": ev["kind"], "name": ev["aftermath"]["name"],
                                         "description": ev["aftermath"]["desc"], "states": targets, "effects": dict(ev["aftermath"]["effects"])})
            break
    for ev in fired:
        # any severe event that lands on named states can come back next quarter as a relief-funds fight
        if ev.get("states") and ev.get("severity", 0) >= 3 and _kind(ev) == "disaster" and not any(p.get("kind") == "aftermath" and p["due"] == q + 1 for p in state["pending"]) and rng.random() < 0.5:
            state["pending"].append({"due": q + 1, "kind": "aftermath", "event": ev.get("kind", "event"), "name": "Relief funds stall",
                                     "description": f"Governors say federal relief after {ev['name'].lower()} has not arrived; shelters are full and insurers are walking away.",
                                     "states": list(ev["states"]), "effects": {"approval": -6, "unrest": 6}})
        eff = dict(ev.get("effects") or {"approval": -ev.get("severity", 2), "unrest": ev.get("severity", 2) * 2})
        mk = eff.pop("market", 0)
        if mk:
            eff["market"] = {"FLN500": mk}
        targets = ev.get("states") or []
        outcome["effects"].append(apply_effects(eff, 1.0, state, personas, winner, rng, f"event: {ev['name']}", targets=targets))
        outcome["events"].append({"id": ev.get("id"), "name": ev["name"], "description": ev.get("description", ""), "severity": ev.get("severity", 2),
                                  "kind": ev.get("kind", "scheduled"), "quarter": q, "response": None, "states": targets,
                                  "address": bool(ev.get("address", ev.get("severity", 2) >= 3)), "image": ev.get("image")})
    # 5. unrest feedback and mean reversion on approval
    avg = sum(s["unrest"] for s in round_scores.values()) / max(1, len(round_scores))
    pavg = (sum(s["unrest"] for s in prev_scores.values()) / max(1, len(prev_scores))) if prev_scores else avg
    # gravity: unrest costs approval, the honeymoon fades, big spending bills cost the President with the prosperous
    spent = sum(p["cost_bn"] for p in state["policies"] if p["status"] in ("passed", "watered", "executive_order"))
    drift = -(avg - pavg) / 6.0 - max(0, avg - 30) * 0.05 + (50 - state["approval"]["national"]) * REVERSION - min(2.0, spent / 400.0) + rng.uniform(-1.2, 1.2)
    for code in state["approval"]["states"]:
        state["approval"]["states"][code] = round(_clamp(state["approval"]["states"][code] + drift + rng.uniform(-1.2, 1.2), 2, 98), 1)
    _national(state)
    state["approval_series"].append({"quarter": q, "label": quarter_label(q), "national": state["approval"]["national"]})
    outcome["approval"] = {"national": state["approval"]["national"], "states": dict(state["approval"]["states"]), "drift": round(drift, 1)}
    # 6. midterms after Q8: seats swing with approval and unrest
    if q == 8:
        swing = (state["approval"]["national"] - 50) * 1.6 - (avg - 25) * 0.4
        lane = state["lane"]
        for chamber, n, k in (("house", 435, 0.6), ("senate", 100, 0.14)):
            ch = state["congress"][chamber]
            delta = int(round(swing * k))
            others = [l for l in ch if l != lane]
            ch[lane] = max(0, min(n, ch.get(lane, 0) + delta))
            rest = n - ch[lane]
            tot_o = sum(ch[l] for l in others) or 1
            for l in others:
                ch[l] = int(round(rest * ch[l] / tot_o))
            diff = n - sum(ch.values())
            if others:
                ch[others[0]] += diff
        state["midterm"] = {"quarter": q, "swing": round(swing, 1), "congress": {k: dict(v) for k, v in state["congress"].items()}}
        outcome["midterm"] = state["midterm"]
    outcome["congress"] = {k: dict(v) for k, v in state["congress"].items()}
    outcome["unrest_delta"] = round(state["pending_unrest"], 1)
    outcome["unrest_states"] = {c: round(v, 1) for c, v in (state.get("pending_unrest_states") or {}).items()}
    outcome["market_hits"] = dict(state["pending_market"])
    outcome["prices_index"] = state["prices_index"]
    outcome["jobs"] = dict(state["jobs"])
    outcome["headline"] = headline(outcome, state)
    state["events"].append({"quarter": q, "events": outcome["events"]})
    return outcome


def headline(outcome, state):
    parts = [f"{outcome['label']}."]
    v = outcome.get("vote")
    if v:
        parts.append({"passed": f"Congress passes {v['name']}.", "watered": f"Congress passes a watered-down {v['name']}.",
                      "failed": f"Congress rejects {v['name']}.", "executive_order": f"Congress rejects {v['name']}; the President signs it as an executive order."}.get(v["result"], f"{v['name']}: {v['result']}."))
    for e in outcome["effects"]:
        if "lagged" in e["tag"] or "unintended" in e["tag"]:
            parts.append(e["tag"].split(":")[0] + ": " + ("the delayed effects arrive." if "lagged" in e["tag"] else "unintended consequence: " + e["tag"].split("(")[-1].rstrip(")") + "."))
    for ev in outcome["events"]:
        parts.append(f"{ev['name']}: {ev['description']}")
    for s in outcome["struck_down"]:
        parts.append(f"Courts strike down the executive order on {s}.")
    parts.append(f"Presidential approval {outcome['approval']['national']}%.")
    if outcome.get("midterm"):
        c = outcome["midterm"]["congress"]
        parts.append("MIDTERMS: House " + ", ".join(f"{l} {n}" for l, n in c["house"].items()) + "; Senate " + ", ".join(f"{l} {n}" for l, n in c["senate"].items()) + ".")
    return " ".join(parts)[:900]


def next_agenda(state, q):
    for pol in state["policies"]:
        if pol["status"] == "pending" and pol["vote_quarter"] == q + 1:
            return f"Next quarter: {pol['name']} goes to a vote in Congress."
    return ""


# ---------- presidential response + re-election ----------
def response_prompt(winner, event, state):
    a = winner.get("analysis") or {}
    return (f"President {winner['name']} ({winner.get('party','')}). Voice: {a.get('short_bio','')} Approval {state['approval']['national']}%.\n"
            f"Event: {event['name']}: {event['description']} (severity {event['severity']}).\n"
            f"Write {{\"statement\": \"<45-70 words the President says publicly, in character>\", \"pivot\": \"<= 15 words: any policy pivot, or 'none'>\"}}")


def challenger_prompt(winner, history, state, used_names=()):
    rec = record_text(state, winner) if state else "No record yet (the term is just starting): run against the platform and the season's history."
    return (f"Incumbent: {winner['name']} ({winner.get('party','')}, {winner.get('lane','')} lane). Record:\n{rec}\n\nHistory:\n{history['summary'][:1200]}\n\n"
            + (f"Names already used in this saga (do not reuse them or their surnames, and do not write a 'Thorne'): {', '.join(used_names)}. "
               f"One challenger may be a returning figure from the history, named exactly as before, only if the history makes that rematch compelling.\n\n" if used_names else "")
            + f"Invent 2 challengers who oppose this record from different directions (one from the opposite lane, one insurgent). Each: 'name', "
            f"'party', 'lane' (left|center|right), 'voice' (f|m), 'blurb' (120-220 words: who they are, a precise visual description for a "
            f"portrait, and a platform that attacks the record point by point). No real people. Return {{\"challengers\": [...]}}")


def record_text(state, winner):
    lines = [f"Approval now {state['approval']['national']}% (started {state['approval_series'][0]['national'] if state['approval_series'] else '?'}%)."]
    for p in state["policies"]:
        lines.append(f"{p['name']}: {p['status']}" + ("; " + "; ".join(h["event"] for h in p["history"][-2:]) if p["history"] else ""))
    lines.append("Jobs by sector (points): " + ", ".join(f"{s} {v:+.1f}" for s, v in state["jobs"].items() if v))
    lines.append(f"Prices index {state['prices_index']:.1f} (100 at inauguration).")
    if state.get("midterm"):
        lines.append("Midterms: " + "; ".join(f"{c}: " + ", ".join(f"{l} {n}" for l, n in v.items()) for c, v in state["midterm"]["congress"].items()))
    evs = [e["name"] for qe in state["events"] for e in qe["events"]]
    if evs:
        lines.append("Events: " + ", ".join(evs[:10]))
    return "\n".join(lines)


def generate_challengers(llm, run_id, winner, history, state, used_names=()):
    out = []
    if llm is not None:
        try:
            d = llm.chat_json(CHALLENGER_SYSTEM, challenger_prompt(winner, history, state, used_names), kind="season", run_id=run_id, temperature=0.8, max_tokens=1800)
            for c in (d.get("challengers") or [])[:2]:
                if c.get("name"):
                    out.append({"name": str(c["name"])[:60], "party": str(c.get("party", ""))[:40], "lane": str(c.get("lane", "center")).lower(),
                                "voice": str(c.get("voice", "m"))[:1], "blurb": str(c.get("blurb", ""))[:3000]})
        except Exception as e:
            print(f"[govern] challengers failed: {e}")
    if not out:
        opp = "right" if (winner.get("lane") == "left") else "left"
        out = [{"name": "Sen. Dana Whitfield", "party": "Opposition", "lane": opp, "voice": "f",
                "blurb": "A two-term senator, 61, silver hair, charcoal suit, runs against the record: reverse the costliest policy, restore order, cut prices."}]
    return out


def season_recap(state, winner, market):
    series = state["approval_series"]
    worked = [p["name"] for p in state["policies"] if p["status"] in ("passed", "watered", "executive_order") and (p["immediate"].get("approval", 0) + p["lagged"].get("approval", 0)) > 0]
    backfired = [p["name"] + (": " + p["unintended"]["description"] if p["unintended"].get("description") else "") for p in state["policies"] if p["status"] in ("passed", "watered", "executive_order") and p["unintended"]["effects"].get("approval", 0) < -2]
    mk = {}
    if market and market.get("history"):
        h0, h1 = market["history"][0], market["history"][-1]
        mk = {sym: round((h1["prices"][sym] / h0["prices"][sym] - 1) * 100, 1) for sym in h0["prices"] if h0["prices"][sym]}
    biggest = sorted([e for qe in state["events"] for e in qe["events"]], key=lambda e: -e.get("severity", 0))[:5]
    return {"approval_series": series, "approval_start": series[0]["national"] if series else None, "approval_end": series[-1]["national"] if series else None,
            "policies_worked": worked, "policies_backfired": backfired, "market_performance": mk, "biggest_moments": [e["name"] for e in biggest],
            "congress_end": state["congress"], "record": record_text(state, winner)}


# ---------- LLM one-offs: inauguration, presidential responses, challengers rewritten against the record ----------
def inauguration(llm, run_id, winner, history, policies):
    a = winner.get("analysis") or {}
    agenda = "; ".join(f"{p['name']}: {p['summary']}" for p in policies)
    fallback = (f"Today the work begins. I asked for your trust and you gave it; I will spend it on {policies[0]['name'] if policies else 'the work ahead'} "
                f"first, and on every promise after. The country is tired of being told to wait. We will not wait.")
    if llm is None:
        return fallback
    try:
        d = llm.chat_json(RESPONSE_SYSTEM, (f"President {winner['name']} ({winner.get('party', '')}). Who they are: {a.get('short_bio', '')} "
                                            f"Their platform: {winner.get('blurb', '')[:1200]}\n\nWhat happened in the campaign season: {history['summary'][:1200]}\n\n"
                                            f"Agenda for the term: {agenda}\n\nWrite {{\"speech\": \"<110-160 words of the inauguration address, in this "
                                            f"President's own voice, naming the season's wounds and the first policies; no real people>\"}}"),
                          kind="season", run_id=run_id, temperature=0.7, max_tokens=500)
        return str(d.get("speech") or fallback)[:1500]
    except Exception as e:
        print(f"[govern] inauguration failed: {e}")
        return fallback


def respond(llm, run_id, pres, event, state):
    fallback = {"statement": f"On {event['name'].lower()}: my administration is acting today, and the people affected will not be left to face it alone.", "pivot": "none"}
    if llm is None:
        return fallback
    try:
        d = llm.chat_json(RESPONSE_SYSTEM, response_prompt(pres, event, state), kind="season", run_id=run_id, temperature=0.7, max_tokens=300)
        return {"statement": str(d.get("statement") or fallback["statement"])[:600], "pivot": str(d.get("pivot", "none"))[:120]}
    except Exception as e:
        print(f"[govern] response failed: {e}")
        return fallback


def update_challengers(llm, run_id, election, pres, state):
    """Year 4: each challenger's blurb gains an attack on the actual record, so the re-election is fought on what happened."""
    rec = record_text(state, pres)
    for o in election["options"]:
        if o["id"] == "A" or o.get("incumbent"):
            continue
        d = llm.chat_json(CHALLENGER_SYSTEM, (f"Challenger: {o['name']} ({o.get('party', '')}, {o.get('lane', '')} lane). Bio: {o.get('blurb', '')[:1200]}\n\n"
                                              f"The incumbent's record:\n{rec}\n\nWrite {{\"attack\": \"<60-110 words: this challenger's closing case against the "
                                              f"record, naming specific policies, numbers and failures from it>\"}}"), kind="season", run_id=run_id, temperature=0.7, max_tokens=400)
        attack = str(d.get("attack", ""))[:800]
        if attack:
            o["blurb"] = (o["blurb"].split("\nCASE AGAINST THE RECORD:")[0] + "\nCASE AGAINST THE RECORD: " + attack)[:3000]
    return election
