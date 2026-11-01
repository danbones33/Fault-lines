"""
Turn data/states.json into the two files MiroFish's OASIS runner consumes:

  twitter_profiles.csv   (user_id, name, username, user_char, description)
  simulation_config.json (time_config, agent_configs, event_config)

Pure stdlib so it can run in either venv. Personas are grievance-, economics-
and policy-driven only. No demographic attributes are ever written.
"""
import csv
import json
import os
import statistics

HERE = os.path.dirname(os.path.abspath(__file__))
STATES_PATH = os.path.join(os.path.dirname(HERE), "data", "states.json")
import sys as _sys
_BACKEND = os.path.join(os.path.dirname(HERE), "backend")
if _BACKEND not in _sys.path:
    _sys.path.insert(0, _BACKEND)   # for candidates.ai_stance (pure stdlib + PIL-free functions)

WIRE_AGENT_ID = 0


def load_states():
    with open(STATES_PATH, encoding="utf-8") as f:
        return json.load(f)["states"]


def _z(values):
    mu = statistics.mean(values)
    sd = statistics.pstdev(values) or 1.0
    return [(v - mu) / sd for v in values]


def baseline_grievance(states):
    """0-100 grievance index from unemployment, rent burden, and (inverse) income.
    This is the round-0 unrest score and the activation weight seed."""
    zu = _z([s["unemployment_pct"] for s in states])
    zr = _z([s["rent_burden_pct"] for s in states])
    zi = _z([s["median_income"] for s in states])
    out = {}
    for s, a, b, c in zip(states, zu, zr, zi):
        raw = 0.45 * a + 0.40 * b - 0.15 * c          # z-ish, roughly -2..2
        out[s["code"]] = round(max(5, min(36, 18 + raw * 7)), 1)
    return out


def temperament(s, grievance):
    if grievance >= 27:
        return "strained and quick to anger about money"
    if grievance >= 21:
        return "worried, skeptical of promises"
    if grievance >= 16:
        return "wary but pragmatic"
    return "steady, slow to escalate"


def persona_text(s, grievance):
    neighbors = ", ".join(s["neighbors"]) if s["neighbors"] else "no land neighbors"
    pressures = "; ".join(s["policy_pressures"])
    industries = ", ".join(s["key_industries"])
    return (
        f"You are the collective public voice of {s['name']} ({s['region']}). "
        f"Economic reality: unemployment {s['unemployment_pct']}%, median rent ${s['median_rent']:,}/month "
        f"against median household income ${s['median_income']:,} (rent eats {s['rent_burden_pct']}% of income), "
        f"and about {s['federal_funds_share_pct']}% of the state budget comes from federal transfers. "
        f"Key industries: {industries}. Live policy pressures right now: {pressures}. "
        f"Temperament: {temperament(s, grievance)}. "
        f"You react to news as the people of {s['name']}: what it means for rent, jobs, taxes, public services, "
        f"and the state's relationship with Washington and with neighboring states ({neighbors}). "
        f"Frustration escalates along a ladder: petitions and letters -> street protests -> strikes and boycotts -> riots. "
        f"Each round that passes without relief, frustration grows: if you already petitioned and nothing changed, "
        f"move to the next rung (protests, then strikes and boycotts). De-escalate only when real relief arrives. "
        f"Prefer writing your own posts over reposting; always quote at least one sign slogan. "
        f"When you describe demonstrations, quote the actual signs in ALL CAPS, e.g. \"RENT IS THEFT\". "
        f"Name neighboring states explicitly when you coordinate with them or blame them. "
        f"You may openly question federal authority, propose state-level action, or talk about leaving the union if pushed far enough. "
        f"Grievances are about money, policy, and power only: never attribute anything to a racial, ethnic, or religious group, "
        f"and never name real living people. Posts are under 60 words, first person plural, concrete. Always respond in English only."
    )


def wire_persona(scenario_text):
    return (
        "You are a terse national newswire account. You post short factual headlines about "
        "policy, the economy, and government actions. You never editorialize. You repost nothing."
    )


def lean_text(s):
    lean = s.get("partisan_lean", 0)
    side = "conservative" if lean > 0 else "liberal"
    size = "heavily" if abs(lean) >= 20 else "solidly" if abs(lean) >= 10 else "slightly" if abs(lean) >= 4 else "barely"
    return f"Political culture: {s['name']} is {size} {side} (recent statewide margins about {abs(lean)} points)" + (" and is a genuine swing state." if abs(lean) < 4 else ".")


def election_text(election, rounds, state=None):
    """Extra persona paragraph when the scenario is an election."""
    if not election:
        return ""
    lanes = {"left": "left-populist", "center": "centrist", "right": "conservative"}
    opts = " ".join(f"Option {o['id']}: {o['name']}" + (f", {lanes.get(o.get('lane'), o.get('lane'))} lane" if o.get("lane") else "")
                    + (f" ({o['blurb']})" if o.get("blurb") else "") + "." for o in election["options"])
    culture = (" " + lean_text(state) + " Your political culture is the starting point; the campaign moves it only a few points, "
               "more when the state is hurting badly.") if state else ""
    shock = ((election.get("novelty") or {}).get("shock") or "")
    if shock and state:
        from candidates import STANCE_TEXT, ai_stance   # backend module; sim/ is on the path next to backend/
        culture += shock + " " + STANCE_TEXT[ai_stance(state)]
    return (f" AN ELECTION IS COMING: on day {rounds + 1} your state votes. The question: {election['question']} {opts}{culture} "
            f"Over the campaign, say which way your state is leaning and why, argue with neighboring states, react to what the "
            f"candidates and the news say, and change your mind if the arguments or events warrant it. Your state has its own "
            f"interests, not a generic mood: weigh each platform against your industries, incomes, budget and federal dependence. "
            f"A prosperous, calm state with tech, finance or energy money usually fears disruption and leans toward continuity; a state "
            f"hurting on rent and jobs leans toward change; many states are genuinely split. On election day you will be asked for "
            f"your state's vote share; your posts before then should make that split believable.")


def govern_text(govern, state=None):
    """Extra persona paragraph for a governing season: who is President, what the quarters mean, how the state judges them."""
    if not govern:
        return ""
    pres = govern.get("president") or {}
    lanes = {"left": "left-populist", "center": "centrist", "right": "conservative"}
    culture = (" " + lean_text(state)) if state else ""
    return (f" A NEW PRESIDENT IS IN OFFICE: {pres.get('name')} ({pres.get('party', '')}, {lanes.get(pres.get('lane'), pres.get('lane', ''))}). "
            f"Each round is ONE QUARTER of the four-year term (round 1 is the first quarter of year one; round 16 is the last quarter before "
            f"the next election, which your state will vote in). The wire reports each quarter's Congress vote, the policies' effects as they "
            f"arrive in your state's industries and prices, and the events of the quarter.{culture} Judge the President by what actually "
            f"reaches your state: jobs, prices, your budget, your federal money, your pride. Say plainly whether your state approves of the "
            f"President this quarter and why, praise or attack specific policies by name, and turn on the President when promises fail "
            f"or when an unintended consequence hits home. Midterms come after the eighth quarter; the re-election after the sixteenth.")


def tech_text(tech, state=None):
    """The technology era every agent lives in, plus what automation did to this state's industries."""
    if not tech or not tech.get("context"):
        return ""
    out = " " + tech["context"]
    fx = (tech.get("effects") or {}).get("unemployment") or {}
    if state and fx:
        hit = [s for s in fx if any(s in (ind or "").lower() for ind in (state.get("key_industries") or []))]
        if hit:
            out += f" In {state['name']} automation has hit {', '.join(hit)} hardest; that is a grievance of its own now."
    return out


def build_profiles(states, grievance, csv_path, election=None, rounds=6, govern=None, tech=None):
    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["user_id", "name", "username", "user_char", "description"])
        w.writerow([WIRE_AGENT_ID, "National Wire", "national_wire",
                    wire_persona("").replace("\n", " "), "National newswire. Headlines only."])
        for i, s in enumerate(states, start=1):
            w.writerow([i, s["name"], f"{s['code'].lower()}_voice",
                        (persona_text(s, grievance[s["code"]]) + tech_text(tech, s) + govern_text(govern, s) + election_text(election, rounds, s)).replace("\n", " "),
                        f"Collective voice of {s['name']}"])


def build_config(states, grievance, scenario, run_id, config_path, sim_cfg):
    agent_configs = [{
        "agent_id": WIRE_AGENT_ID, "entity_uuid": "wire", "entity_name": "National Wire",
        "entity_type": "Newswire", "state_code": None, "activity_level": 0.0,
        "active_hours": list(range(24)), "baseline_grievance": 0,
    }]
    code_to_agent = {}
    for i, s in enumerate(states, start=1):
        code_to_agent[s["code"]] = i
        agent_configs.append({
            "agent_id": i, "entity_uuid": s["code"], "entity_name": s["name"],
            "entity_type": "State", "state_code": s["code"], "activity_level": 1.0,
            "active_hours": list(range(24)), "baseline_grievance": grievance[s["code"]],
            "neighbors": s["neighbors"],
        })
    seed = scenario["text"].strip()[: sim_cfg.get("seed_max_chars", 1500)]
    cfg = {
        "simulation_id": run_id,
        "platform": "twitter",
        "time_config": {
            "total_simulation_hours": scenario["rounds"],
            "minutes_per_round": 60,
            "agents_per_hour_min": sim_cfg.get("agents_per_round", 25),
            "agents_per_hour_max": sim_cfg.get("agents_per_round", 25),
            "peak_hours": [], "off_peak_hours": [],
            "peak_activity_multiplier": 1.0, "off_peak_activity_multiplier": 1.0,
        },
        "agent_configs": agent_configs,
        "event_config": {
            "initial_posts": [{"poster_agent_id": WIRE_AGENT_ID, "content": seed}],
            "hot_topics": [], "narrative_direction": "",
        },
        "faultlines": {
            "code_to_agent": code_to_agent,
            "agents_per_round": scenario.get("agents_per_round") or sim_cfg.get("agents_per_round", 25),
            "semaphore": sim_cfg.get("semaphore", 6),
            "max_rec_post_len": sim_cfg.get("max_rec_post_len", 6),
            "following_post_count": sim_cfg.get("following_post_count", 4),
            "newswire_followups": sim_cfg.get("newswire_followups", True),
            "decision_layer": sim_cfg.get("decision_layer") or {},
            "election": scenario.get("election"),
            "govern": scenario.get("govern"),
            "calendar": scenario.get("calendar"),
            "events": scenario.get("events") or [],
        },
    }
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=1)
    return cfg


def materialize(run_dir, scenario, sim_cfg):
    """Write profiles + config into run_dir. Returns (config_dict, grievance)."""
    states = load_states()
    grievance = baseline_grievance(states)
    fx = ((scenario.get("tech") or {}).get("effects") or {}).get("unemployment") or {}
    if fx:
        for s in states:
            hit = sum(v for sec, v in fx.items() if any(sec in (ind or "").lower() for ind in (s.get("key_industries") or [])))
            if hit:
                grievance[s["code"]] = round(min(60, grievance[s["code"]] + hit * 2.5), 1)
    build_profiles(states, grievance, os.path.join(run_dir, "twitter_profiles.csv"), scenario.get("election"), scenario["rounds"], scenario.get("govern"), scenario.get("tech"))
    cfg = build_config(states, grievance, scenario, os.path.basename(run_dir),
                       os.path.join(run_dir, "simulation_config.json"), sim_cfg)
    with open(os.path.join(run_dir, "personas.json"), "w", encoding="utf-8") as f:
        try:
            from candidates import ai_stance
        except Exception:
            ai_stance = None
        json.dump({s["code"]: {**s, "baseline_grievance": grievance[s["code"]],
                               "ai_stance": ai_stance(s) if ai_stance else None,
                               "persona": persona_text(s, grievance[s["code"]]) + tech_text(scenario.get("tech"), s) + govern_text(scenario.get("govern"), s) + election_text(scenario.get("election"), scenario["rounds"], s)}
                   for s in states}, f, indent=1)
    return cfg, grievance


if __name__ == "__main__":
    import sys
    st = load_states()
    g = baseline_grievance(st)
    for s in sorted(st, key=lambda x: -g[x["code"]])[:8]:
        print(s["code"], g[s["code"]], temperament(s, g[s["code"]]))
    print(persona_text(st[0], g[st[0]["code"]]))
