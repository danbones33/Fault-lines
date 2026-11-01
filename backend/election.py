"""
Election mode: tables and tally math.

Electoral votes: 538 = 48 winner-take-all states + DC (3) + Maine (2 statewide + 2 districts) +
Nebraska (2 statewide + 3 districts). An assert guards the total. Populations are approximate 2023
figures in millions, used only to weight a popular-vote estimate. Poll-closing groups are Eastern-time
slots used to reveal the night in order.

Candidates: 2 to 4 options with a lane (left / center / right), a color (lane-colored unless given),
and optional ballot access: `off_ballot` lists the states where the candidate is not on the ballot.
"""
import random
import warnings

EV = {"AL": 9, "AK": 3, "AZ": 11, "AR": 6, "CA": 54, "CO": 10, "CT": 7, "DE": 3, "FL": 30, "GA": 16, "HI": 4, "ID": 4, "IL": 19,
      "IN": 11, "IA": 6, "KS": 6, "KY": 8, "LA": 8, "ME": 2, "MD": 10, "MA": 11, "MI": 15, "MN": 10, "MS": 6, "MO": 10, "MT": 4,
      "NE": 2, "NV": 6, "NH": 4, "NJ": 14, "NM": 5, "NY": 28, "NC": 16, "ND": 3, "OH": 17, "OK": 7, "OR": 8, "PA": 19, "RI": 4,
      "SC": 9, "SD": 3, "TN": 11, "TX": 40, "UT": 6, "VT": 3, "VA": 13, "WA": 12, "WV": 4, "WI": 10, "WY": 3,
      "DC": 3}
# congressional districts that award their own elector (parent state, EV, lean offset in points vs the state, + = right)
DISTRICTS = {"ME-1": ("ME", 1, -8), "ME-2": ("ME", 1, 12), "NE-1": ("NE", 1, 4), "NE-2": ("NE", 1, -14), "NE-3": ("NE", 1, 28)}
TOTAL_EV = sum(EV.values()) + sum(d[1] for d in DISTRICTS.values())
assert TOTAL_EV == 538, TOTAL_EV
TO_WIN = 270
STATES_50 = [c for c in EV if c != "DC"]

POP_M = {"AL": 5.1, "AK": 0.73, "AZ": 7.4, "AR": 3.1, "CA": 39.0, "CO": 5.9, "CT": 3.6, "DE": 1.0, "FL": 22.6, "GA": 11.0, "HI": 1.4,
         "ID": 2.0, "IL": 12.5, "IN": 6.9, "IA": 3.2, "KS": 2.9, "KY": 4.5, "LA": 4.6, "ME": 1.4, "MD": 6.2, "MA": 7.0, "MI": 10.0,
         "MN": 5.7, "MS": 2.9, "MO": 6.2, "MT": 1.1, "NE": 2.0, "NV": 3.2, "NH": 1.4, "NJ": 9.3, "NM": 2.1, "NY": 19.6, "NC": 10.8,
         "ND": 0.78, "OH": 11.8, "OK": 4.1, "OR": 4.2, "PA": 13.0, "RI": 1.1, "SC": 5.4, "SD": 0.92, "TN": 7.1, "TX": 30.5, "UT": 3.4,
         "VT": 0.65, "VA": 8.7, "WA": 7.8, "WV": 1.8, "WI": 5.9, "WY": 0.58, "DC": 0.68}

POLL_CLOSE = [
    ("7:00 PM ET", ["GA", "IN", "KY", "SC", "VT", "VA"]),
    ("7:30 PM ET", ["NC", "OH", "WV"]),
    ("8:00 PM ET", ["AL", "CT", "DC", "DE", "FL", "IL", "ME", "MD", "MA", "MS", "MO", "NH", "NJ", "OK", "PA", "RI", "TN"]),
    ("8:30 PM ET", ["AR"]),
    ("9:00 PM ET", ["AZ", "CO", "KS", "LA", "MI", "MN", "NE", "NM", "NY", "ND", "SD", "TX", "WI", "WY"]),
    ("10:00 PM ET", ["IA", "MT", "NV", "UT"]),
    ("11:00 PM ET", ["CA", "ID", "OR", "WA"]),
    ("12:00 AM ET", ["HI"]),
    ("1:00 AM ET", ["AK"]),
]
assert sorted(c for _, cs in POLL_CLOSE for c in cs) == sorted(EV)

LANE_COLORS = {"right": "#d94f3d", "left": "#3d6fd9", "center": "#8a5bd6"}
EXTRA_COLORS = ["#3c8f72", "#e0a63a", "#c2437c"]   # third parties / duplicate lanes
LANE_SIGN = {"right": 1, "center": 0, "left": -1}


def normalize_election(e):
    """Scenario field -> canonical {question, options:[{id,name,color,party,lane,blurb,voice,off_ballot}], kind}."""
    if not e:
        return None
    opts, used = [], set()
    extra = list(EXTRA_COLORS)
    for i, o in enumerate((e.get("options") or [])[:4]):
        if isinstance(o, str):
            o = {"name": o}
        name = str(o.get("name", "")).strip()
        if not name:
            continue
        lane = str(o.get("lane", "")).lower()
        lane = lane if lane in LANE_SIGN else ""
        color = LANE_COLORS.get(lane) if lane and LANE_COLORS.get(lane) not in used else None
        color = color or o.get("color") or (extra.pop(0) if extra else "#999999")
        if color in used:
            color = extra.pop(0) if extra else "#999999"
        used.add(color)
        off = [str(c).upper()[:2] for c in (o.get("off_ballot") or []) if str(c).upper()[:2] in EV]
        opts.append({"id": chr(65 + i), "name": name[:60], "color": color, "party": str(o.get("party", ""))[:40],
                     "blurb": str(o.get("blurb", ""))[:3000], "lane": lane,
                     "voice": (str(o.get("voice", "m")).lower()[:9] if str(o.get("voice", "m")).lower()[:1] in "fms" else "m"),
                     "off_ballot": sorted(set(off))})
        # a continued season passes the incumbent through with the analysis, portrait and cast voice of the parent run
        for k in ("analysis", "portrait", "voice_id", "voice_cast", "incumbent"):
            if o.get(k):
                opts[-1][k] = o[k]
    if len(opts) < 2:
        return None
    return {"question": str(e.get("question", "")).strip()[:300] or "Election", "options": opts,
            "kind": e.get("kind") or ("referendum" if len(opts) == 2 and any(o["name"].lower() in ("yes", "no") for o in opts) else "candidates")}


def on_ballot(election, code):
    return [o for o in election["options"] if code not in o.get("off_ballot", [])]


def lane_desc(o):
    return {"left": "left-populist", "center": "centrist", "right": "conservative"}.get(o.get("lane"), o.get("lane") or "")


def ballot_prompt(election, state_name, lean=None, code=None, stance=None):
    opts_list = on_ballot(election, code) if code else election["options"]
    opts = "\n".join(f"  {o['id']}: {o['name']}" + (f" [{lane_desc(o)} lane]" if o.get("lane") else "") + (f" — {o['blurb']}" if o['blurb'] else "")
                     for o in opts_list)
    missing = [o["name"] for o in election["options"] if o not in opts_list]
    culture = ""
    if lean is not None:
        side = "conservative" if lean > 0 else "liberal"
        culture = (f"Start from {state_name}'s political culture: it is {side} by about {abs(lean)} points in recent statewide elections, "
                   f"so a candidate in the matching lane starts with roughly a {50 + abs(lean) / 2:.0f}-{50 - abs(lean) / 2:.0f} edge. "
                   f"The campaign, the debate, and how badly the state is hurting then move that by a few points, rarely more than 8. ")
    third = (" Candidates outside the two main lanes usually get 2-15 percent unless the campaign made them a real force here." if len(opts_list) > 2 else "")
    shock = ((election.get("novelty") or {}).get("shock") or "")
    if shock:
        culture += shock.strip() + " "
        if stance:
            from candidates import STANCE_TEXT
            culture += STANCE_TEXT.get(stance, "") + " "
    return (f"ELECTION DAY. The polls are open in {state_name}. The question on the ballot: {election['question']}\n"
            f"Options on the ballot in {state_name}:\n{opts}\n"
            + (f"(Not on the ballot here: {', '.join(missing)}.)\n" if missing else "")
            + f"You are the whole electorate of {state_name}, not one voter. {culture}"
            f"Your state's economy (its industries, incomes, unemployment, dependence on federal money), its temperament, and everything "
            f"said in the campaign all pull in different directions, and real states split. Estimate the statewide vote share honestly. "
            f"Most states land between 42 and 58 percent for the leader; only a few are blowouts.{third}\n"
            f"Reply with JSON only: {{\"shares\": {{\"<letter>\": <percent>, ...}} summing to 100 over the options on the ballot, "
            f"\"turnout\": <percent of eligible voters, typically 50-75>, \"reason\": \"<= 25 words, first person plural, no real people\"}}")


def parse_ballot(text, election, code=None):
    """Loose JSON parse of an agent's ballot reply; falls back to a letter search. Off-ballot options are zeroed."""
    import json, re
    allowed = [o["id"] for o in (on_ballot(election, code) if code else election["options"])]
    out = {"vote": None, "margin": 10, "turnout": 55, "reason": ""}
    t = (text or "").strip()
    try:
        i, j = t.find("{"), t.rfind("}")
        d = json.loads(t[i:j + 1]) if i != -1 and j > i else {}
    except Exception:
        d = {}
    shares = d.get("shares") if isinstance(d.get("shares"), dict) else None
    if shares:
        clean = {}
        for k, v in shares.items():
            k = str(k).strip().upper()[:1]
            try:
                if k in allowed:
                    clean[k] = max(0.0, float(v))
            except Exception:
                pass
        tot = sum(clean.values())
        if clean and tot > 0:
            clean = {k: 100.0 * v / tot for k, v in clean.items()}
            top = max(clean, key=clean.get)
            second = max([v for k, v in clean.items() if k != top] or [0])
            out["vote"] = top
            out["margin"] = int(round(clean[top] - second))
            out["shares"] = {k: round(v, 1) for k, v in clean.items()}
    if not out["vote"]:
        v = str(d.get("vote", "")).strip().upper()[:1]
        if v in allowed:
            out["vote"] = v
        else:
            m = re.search(r"\b([A-D])\b", t.upper())
            if m and m.group(1) in allowed:
                out["vote"] = m.group(1)
        try:
            out["margin"] = max(0, min(100, int(float(d.get("margin", out["margin"])))))
        except Exception:
            pass
    try:
        out["turnout"] = max(0, min(100, int(float(d.get("turnout", out["turnout"])))))
    except Exception:
        pass
    out["reason"] = str(d.get("reason", ""))[:200] or t[:200]
    return out


def _shares_from(b, ids):
    """Vote shares for the listed option ids from a ballot record (shares if present, else margin-derived)."""
    if b.get("shares"):
        s = {i: float(b["shares"].get(i, 0.0)) for i in ids}
    else:
        s = {i: 0.0 for i in ids}
        if b.get("vote") in ids:
            win = 50 + b.get("margin", 10) / 2.0
            s[b["vote"]] = win
            rest = [i for i in ids if i != b["vote"]]
            for i in rest:
                s[i] = (100 - win) / max(1, len(rest))
    tot = sum(s.values()) or 1.0
    return {i: 100.0 * v / tot for i, v in s.items()}


def _shift(shares, election, points):
    """Move `points` of share from the left-most lanes toward the right-most (negative = toward the left)."""
    if points == 0:
        return dict(shares)
    sign = {o["id"]: LANE_SIGN.get(o.get("lane"), 0) for o in election["options"]}
    ids = [i for i in shares if sign.get(i, 0) != 0]
    out = dict(shares)
    rights = [i for i in ids if sign[i] > 0]
    lefts = [i for i in ids if sign[i] < 0]
    if not rights or not lefts:
        return out
    for i in rights:
        out[i] = max(0.0, out[i] + points / 2.0 / len(rights))
    for i in lefts:
        out[i] = max(0.0, out[i] - points / 2.0 / len(lefts))
    tot = sum(out.values()) or 1.0
    return {k: 100.0 * v / tot for k, v in out.items()}


def dc_ballot(election):
    """DC is not an agent: it goes to the left-most lane on the ballot by about 85-15."""
    opts = on_ballot(election, "DC")
    ranked = sorted(opts, key=lambda o: LANE_SIGN.get(o.get("lane"), 0))
    shares = {o["id"]: 0.0 for o in opts}
    shares[ranked[0]["id"]] = 85.0
    rest = [o["id"] for o in opts if o["id"] != ranked[0]["id"]]
    for i in rest:
        shares[i] = 15.0 / max(1, len(rest))
    return {"shares": shares, "turnout": 62, "reason": "The District votes with the left lane, as it always has.", "vote": ranked[0]["id"]}


def tally(ballots, election):
    """ballots: {code: {vote, margin, turnout, reason, shares?}} for the 50 states ->
    results with every elector-awarding unit (states, DC, ME/NE districts), EV, popular estimate, call order."""
    ids = [o["id"] for o in election["options"]]
    name = {o["id"]: o["name"] for o in election["options"]}
    ev = {i: 0 for i in ids}
    pop = {i: 0.0 for i in ids}
    units = {}
    ballots = dict(ballots)
    ballots["DC"] = dc_ballot(election)

    def settle(code, shares, turnout, reason, evs, parent=None):
        allowed = [o["id"] for o in on_ballot(election, parent or code)]
        shares = {k: (v if k in allowed else 0.0) for k, v in shares.items()}
        tot = sum(shares.values()) or 1.0
        shares = {k: 100.0 * v / tot for k, v in shares.items()}
        vote = max(shares, key=shares.get) if shares and max(shares.values()) > 0 else None
        if vote is None:
            warnings.warn(f"election: {code} ended with no winner")
            return {"vote": None, "margin": 0, "turnout": turnout, "reason": reason, "ev": evs, "shares": shares, "parent": parent}
        second = max([v for k, v in shares.items() if k != vote] or [0])
        margin = int(round(shares[vote] - second))
        ev[vote] += evs
        return {"vote": vote, "margin": margin, "turnout": turnout, "reason": reason, "ev": evs,
                "shares": {k: round(v, 1) for k, v in shares.items()}, "parent": parent}

    for code in EV:
        b = ballots.get(code) or {}
        shares = _shares_from(b, ids)
        turnout = int(b.get("turnout", 55))
        units[code] = settle(code, shares, turnout, b.get("reason", ""), EV[code])
        voters = POP_M.get(code, 1.0) * turnout / 100.0
        for i in ids:
            pop[i] += voters * units[code]["shares"].get(i, 0) / 100.0
    for d, (parent, evs, offset) in DISTRICTS.items():
        base = units[parent]["shares"]
        units[d] = settle(d, _shift(base, election, offset), units[parent]["turnout"], f"{d} follows the {parent} count with its own lean.", evs, parent)
    assert sum(u["ev"] for u in units.values()) == 538
    total_pop = sum(pop.values()) or 1.0
    popular = {i: round(100 * pop[i] / total_pop, 1) for i in ids}
    winner = max(ev, key=ev.get) if ev else None
    if winner and ev[winner] < TO_WIN:
        winner = None
    order = []
    for slot, codes in POLL_CLOSE:
        for code in sorted(codes, key=lambda c: -units[c]["margin"]):
            order.append({"code": code, "slot": slot, "vote": units[code]["vote"], "margin": units[code]["margin"],
                          "too_close": units[code]["margin"] < 5})
            for d, (parent, _, _) in DISTRICTS.items():
                if parent == code:
                    order.append({"code": d, "slot": slot, "vote": units[d]["vote"], "margin": units[d]["margin"],
                                  "too_close": units[d]["margin"] < 5})
    closest = sorted([c for c in STATES_50 if units[c]["vote"]], key=lambda c: units[c]["margin"])[:5]
    missing = [c for c, u in units.items() if not u["vote"]]
    house_winner = None
    if not winner:
        # contingent election: each state delegation casts one vote, for the candidate who carried the state; ties go to the popular leader
        delegations = {i: sum(1 for c in STATES_50 if units[c]["vote"] == i) for i in ids}
        best = max(delegations.values()) if delegations else 0
        tied = [i for i, n in delegations.items() if n == best]
        house_winner = max(tied, key=lambda i: popular.get(i, 0)) if tied else None
    return {"states": units, "ev": ev, "to_win": TO_WIN, "total_ev": 538, "popular": popular, "winner": winner, "house_winner": house_winner,
            "delegations": ({i: sum(1 for c in STATES_50 if units[c]["vote"] == i) for i in ids} if not winner else None),
            "called_order": order, "closest": closest, "no_winner": missing,
            "options": [{"id": o["id"], "name": o["name"], "color": o["color"], "lane": o.get("lane", "")} for o in election["options"]]}


def demo_ballots(personas, election, rng_seed):
    """No-LLM ballots: political culture first, grievance pushes toward the left lane, region adds a tilt, plus noise.
    Third options get a small share unless they are the only one in their lane."""
    rng = random.Random(rng_seed)
    ids = [o["id"] for o in election["options"]]
    sign = {o["id"]: LANE_SIGN.get(o.get("lane"), 0) for o in election["options"]}
    tilt = {"Northeast": 0.1, "Mid-Atlantic": 0.05, "West Coast": 0.15, "Pacific Northwest": 0.1, "Great Lakes": 0.0, "Midwest": -0.05,
            "Plains": -0.15, "Mountain West": -0.1, "Southwest": 0.0, "South": -0.1, "Deep South": -0.15, "Gulf": -0.1, "Pacific": 0.1}
    out = {}
    for code, pm in personas.items():
        allowed = [o["id"] for o in on_ballot(election, code)]
        right_share = 0.5 + pm.get("partisan_lean", 0) / 200.0 - (pm["baseline_grievance"] - 20) / 150 - tilt.get(pm["region"], 0) * 0.3 + rng.uniform(-0.06, 0.06)
        right_share = max(0.15, min(0.85, right_share))
        rights = [i for i in allowed if sign[i] > 0]
        lefts = [i for i in allowed if sign[i] < 0]
        centers = [i for i in allowed if sign[i] == 0]
        third = (rng.uniform(0.03, 0.12) if centers else 0.0)
        shares = {}
        for i in rights:
            shares[i] = (right_share * (1 - third)) / len(rights) * 100
        for i in lefts:
            shares[i] = ((1 - right_share) * (1 - third)) / len(lefts) * 100
        for i in centers:
            shares[i] = third * 100 / len(centers)
        if not rights or not lefts:   # degenerate ballot: split by position
            shares = {i: 100.0 / len(allowed) + rng.uniform(-8, 8) for i in allowed}
        tot = sum(shares.values()) or 1.0
        shares = {k: round(100 * v / tot, 1) for k, v in shares.items()}
        vote = max(shares, key=shares.get)
        second = max([v for k, v in shares.items() if k != vote] or [0])
        out[code] = {"vote": vote, "margin": int(round(shares[vote] - second)), "turnout": rng.randint(45, 78), "shares": shares,
                     "reason": f"{pm['name']} goes with {vote}: {rng.choice(pm['policy_pressures'])} decided it."}
    return out


def election_summary(results, election):
    name = {o["id"]: o["name"] for o in election["options"]}
    ev_s = ", ".join(f"{name[i]} {results['ev'][i]}" for i in results["ev"])
    pv_s = ", ".join(f"{name[i]} {results['popular'][i]}%" for i in results["popular"])
    win = name[results["winner"]] if results["winner"] else "no majority (contingent election in the House)"
    closest = ", ".join(f"{c} ({results['states'][c]['margin']} pts for {name[results['states'][c]['vote']]})" for c in results["closest"][:4])
    return (f"ELECTION NIGHT RESULTS. {election['question']} Electoral votes ({results['to_win']} to win of 538): {ev_s}. "
            f"Popular vote estimate: {pv_s}. Winner: {win}. Closest states: {closest}.")
