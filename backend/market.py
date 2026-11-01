"""
The economy: a fictional market that reacts to the simulation.

At run start a Market Desk LLM call invents 6-8 tickers from the scenario (companies and sectors
that the news would actually move: an AI shock gets an AI platform, a trucking firm, a power utility,
a rent REIT...), each with a short "sensitivity" note. Every round the desk reads the round's facts
(unrest, strikes, riots, blocs, polls, debate, headlines) and returns a percent move per ticker and
two market headlines. A rule-based component (unrest delta, new strikes/riots/blocs) is blended in so
the tape still moves sensibly when the LLM is off (demo mode). Everything lands in runs/<id>/market.json.

Macro series are fixed: FLN 500 (index), 10Y yield (%), OIL ($/bbl), FEAR (volatility index).
"""
import math
import random

MACRO = [
    {"sym": "FLN500", "name": "FLN 500 index", "kind": "index", "price": 4820.0, "beta_unrest": -1.0, "beta_policy": -0.8},
    {"sym": "US10Y", "name": "10-year yield", "kind": "yield", "price": 4.35, "beta_unrest": 0.4, "beta_policy": 0.6},
    {"sym": "OIL", "name": "Crude oil", "kind": "commodity", "price": 78.4, "beta_unrest": 0.5, "beta_policy": 0.2},
    {"sym": "FEAR", "name": "Fear index", "kind": "vol", "price": 17.5, "beta_unrest": 3.0, "beta_policy": 1.5},
]

SETUP_SYSTEM = ("You are the markets desk of a fictional news network covering a simulated US crisis. Invent fictional, "
                "plausible public companies (never real ones; no real tickers) that this scenario would move. Return JSON only.")
DESK_SYSTEM = ("You are the markets desk of a fictional news network. Given what happened today in a simulated US crisis, "
               "decide how each fictional ticker moved today, in percent, and write two market headlines. Be specific and "
               "directional: strikes hurt logistics and retail, unrest raises fear and yields, a populist front-runner hits "
               "tech and finance, relief rallies happen when things calm. Return JSON only.")


def setup_prompt(scenario_text, election):
    el = ""
    if election:
        el = "\nThere is also an election: " + election["question"] + " Candidates: " + "; ".join(
            f"{o['id']} {o['name']} ({o.get('lane','')} lane: {o.get('blurb','')[:120]})" for o in election["options"])
    return (f"Scenario:\n{scenario_text[:1500]}{el}\n\nInvent 6 to 8 fictional public companies this scenario would move, across the "
            f"sectors it touches (for example: the technology driving the shock, logistics or employers losing jobs, utilities or "
            f"landlords collecting the bills, banks, defense or security, consumer retail, a hedge against chaos). For each: "
            f"'sym' (3-5 capital letters, not a real ticker), 'name', 'sector', 'price' (realistic share price 8-900), "
            f"'beta_unrest' (-3 to 3: how it reacts when unrest rises), 'beta_policy' (-3 to 3: how it reacts if the populist/relief "
            f"side gains), and 'note' (<= 15 words on why it moves). Return {{\"tickers\": [...]}}")


def desk_prompt(tickers, facts, prev_pct):
    tk = "\n".join(f"- {t['sym']} {t['name']} ({t['sector']}): {t['note']}; yesterday {prev_pct.get(t['sym'], 0):+.1f}%" for t in tickers)
    return (f"Today's facts:\n{facts}\n\nTickers:\n{tk}\n\nReturn {{\"moves\": {{\"SYM\": <percent change today, -12 to 12>, ...}}, "
            f"\"macro\": {{\"FLN500\": pct, \"US10Y\": <change in basis points, -40 to 40>, \"OIL\": pct, \"FEAR\": pct}}, "
            f"\"headlines\": [\"<= 14 words\", \"<= 14 words\"], \"rationale\": \"<= 30 words\"}}")


def default_tickers(scenario_text):
    t = scenario_text.lower()
    out = [
        {"sym": "NXAI", "name": "Nexus Automation", "sector": "AI platforms", "price": 412.0, "beta_unrest": -1.2, "beta_policy": -2.5, "note": "the automation vendor; hated by the displaced"},
        {"sym": "HAUL", "name": "Haulwave Logistics", "sector": "Trucking", "price": 38.5, "beta_unrest": -2.2, "beta_policy": 0.5, "note": "strikes and blockades stop its trucks"},
        {"sym": "GRDC", "name": "Gridcore Power", "sector": "Utilities", "price": 71.2, "beta_unrest": -0.6, "beta_policy": -1.5, "note": "data-center demand, rate-cap risk"},
        {"sym": "RENT", "name": "Keystone Residential REIT", "sector": "Housing", "price": 27.9, "beta_unrest": -1.8, "beta_policy": -2.8, "note": "rent strikes and relief hit it directly"},
        {"sym": "FRST", "name": "First Meridian Bank", "sector": "Banks", "price": 54.3, "beta_unrest": -1.5, "beta_policy": -1.2, "note": "defaults rise with unrest"},
        {"sym": "SHLD", "name": "Shieldline Security", "sector": "Security", "price": 112.6, "beta_unrest": 2.6, "beta_policy": 0.3, "note": "the chaos trade"},
        {"sym": "BIGB", "name": "BigBasket Stores", "sector": "Retail", "price": 63.1, "beta_unrest": -1.4, "beta_policy": 0.8, "note": "boycotts and empty shelves"},
    ]
    if "rent" in t or "surcharge" in t or "mortgage" in t:
        out.append({"sym": "MTGE", "name": "Homestead Mortgage", "sector": "Mortgage finance", "price": 19.4, "beta_unrest": -1.6, "beta_policy": -2.0, "note": "surcharge politics"})
    return out[:8]


def _clean_tickers(raw):
    out, seen = [], set()
    for t in raw or []:
        sym = "".join(ch for ch in str(t.get("sym", "")).upper() if ch.isalpha())[:5]
        if len(sym) < 3 or sym in seen or sym in {m["sym"] for m in MACRO}:
            continue
        try:
            price = float(t.get("price", 50))
        except Exception:
            price = 50.0
        if not (1 <= price <= 5000):
            price = 50.0
        def b(k):
            try:
                return max(-3.0, min(3.0, float(t.get(k, 0))))
            except Exception:
                return 0.0
        seen.add(sym)
        out.append({"sym": sym, "name": str(t.get("name", sym))[:40], "sector": str(t.get("sector", ""))[:30], "price": round(price, 2),
                    "beta_unrest": b("beta_unrest"), "beta_policy": b("beta_policy"), "note": str(t.get("note", ""))[:90]})
    return out[:8]


def setup_market(llm, run_id, scenario_text, election):
    tickers = []
    if llm is not None:
        try:
            d = llm.chat_json(SETUP_SYSTEM, setup_prompt(scenario_text, election), kind="market", run_id=run_id, temperature=0.8, max_tokens=1200)
            tickers = _clean_tickers(d.get("tickers"))
        except Exception as e:
            print(f"[market] setup failed: {e}")
    if len(tickers) < 4:
        tickers = default_tickers(scenario_text)
    prices = {t["sym"]: t["price"] for t in tickers}
    prices.update({m["sym"]: m["price"] for m in MACRO})
    return {"tickers": tickers, "macro": [dict(m) for m in MACRO],
            "history": [{"round": 0, "prices": prices, "pct": {k: 0.0 for k in prices}, "headlines": ["Markets open steady ahead of the news"], "rationale": "baseline"}]}


def round_facts(tl_round, prev_round, blocs_formed, blocs_collapsed, polls_lead, extra_lines):
    cur = tl_round["states"]
    prev = prev_round["states"] if prev_round else {}
    avg = sum(s["unrest"] for s in cur.values()) / len(cur)
    pavg = (sum(s["unrest"] for s in prev.values()) / len(prev)) if prev else avg
    new_strikes = [c for c, s in cur.items() if s["stage"] >= 3 and (prev.get(c, {}).get("stage", 0) < 3)]
    new_riots = [c for c, s in cur.items() if s["stage"] >= 4 and (prev.get(c, {}).get("stage", 0) < 4)]
    strikes = sum(1 for s in cur.values() if s["stage"] >= 3)
    riots = sum(1 for s in cur.values() if s["stage"] >= 4)
    lines = [f"Average unrest {avg:.1f} (yesterday {pavg:.1f}). States on strike: {strikes}, rioting: {riots}.",
             f"New strikes today: {', '.join(new_strikes) or 'none'}. New riots: {', '.join(new_riots) or 'none'}.",
             f"Blocs formed: {', '.join(blocs_formed) or 'none'}. Blocs collapsed: {', '.join(blocs_collapsed) or 'none'}."]
    if polls_lead:
        lines.append("Election polling, states leaning: " + ", ".join(f"{k} {v}" for k, v in polls_lead.items()))
    lines += extra_lines
    return "\n".join(lines), {"avg": avg, "pavg": pavg, "new_strikes": len(new_strikes), "new_riots": len(new_riots),
                             "new_blocs": len(blocs_formed), "collapsed": len(blocs_collapsed), "strikes": strikes, "riots": riots}


def rule_moves(market, nums, policy_signal, rng):
    """Deterministic-ish component: unrest delta, new strikes/riots/blocs, a policy signal (-1..1: populist side gaining)."""
    # bad > 0 means a worse day: unrest climbing, new strikes/riots/blocs; a collapsed bloc is relief
    bad = (0.12 * (nums["avg"] - nums["pavg"]) + 1.1 * nums["new_strikes"] + 2.0 * nums["new_riots"] + 1.4 * nums["new_blocs"]
           - 0.8 * nums["collapsed"])
    bad = max(-6.0, min(12.0, bad))
    out = {}
    for t in market["tickers"] + market["macro"]:
        if t["sym"] == "US10Y":
            # change in percentage points of yield: fear pushes yields up a little, populist gains more
            out[t["sym"]] = max(-0.4, min(0.4, 0.015 * bad * t["beta_unrest"] * 2 + policy_signal * 0.12 * t["beta_policy"] + rng.gauss(0, 0.03)))
            continue
        noise = rng.gauss(0, 0.7)
        out[t["sym"]] = bad * 0.5 * t["beta_unrest"] + policy_signal * 1.5 * t["beta_policy"] + noise
    return out


def step_market(llm, run_id, market, facts_text, nums, policy_signal, rnd, rng):
    prev = market["history"][-1]
    llm_moves, llm_macro, headlines, rationale = {}, {}, [], ""
    if llm is not None:
        try:
            d = llm.chat_json(DESK_SYSTEM, desk_prompt(market["tickers"], facts_text, prev["pct"]), kind="market", run_id=run_id,
                              temperature=0.6, max_tokens=700)
            llm_moves = {str(k).upper(): float(v) for k, v in (d.get("moves") or {}).items() if isinstance(v, (int, float))}
            llm_macro = {str(k).upper(): float(v) for k, v in (d.get("macro") or {}).items() if isinstance(v, (int, float))}
            headlines = [str(h)[:90] for h in (d.get("headlines") or [])][:2]
            rationale = str(d.get("rationale", ""))[:200]
        except Exception as e:
            print(f"[market] desk failed: {e}")
    rules = rule_moves(market, nums, policy_signal, rng)
    prices, pct = {}, {}
    for t in market["tickers"]:
        sym = t["sym"]
        m = rules[sym] if sym not in llm_moves else 0.5 * max(-12, min(12, llm_moves[sym])) + 0.5 * rules[sym]
        m = max(-15.0, min(15.0, m))
        prices[sym] = round(prev["prices"][sym] * (1 + m / 100), 2)
        pct[sym] = round(m, 2)
    for mac in market["macro"]:
        sym = mac["sym"]
        if sym == "US10Y":
            bp = llm_macro.get(sym)
            delta = (0.5 * max(-40, min(40, bp)) / 100 + 0.5 * rules[sym]) if bp is not None else rules[sym]
            prices[sym] = round(max(0.5, prev["prices"][sym] + delta), 2)
            pct[sym] = round(delta * 100, 1)   # basis points
        else:
            m = rules[sym] if sym not in llm_macro else 0.5 * max(-15, min(15, llm_macro[sym])) + 0.5 * rules[sym]
            if sym == "FEAR":
                m = max(-25.0, min(40.0, m))
            prices[sym] = round(prev["prices"][sym] * (1 + m / 100), 2)
            pct[sym] = round(m, 2)
    if not headlines:
        idx = pct.get("FLN500", 0)
        headlines = [f"FLN 500 {'slides' if idx < -1 else 'rallies' if idx > 1 else 'drifts'} {abs(idx):.1f}% as unrest {'builds' if nums['avg'] > nums['pavg'] else 'eases'}",
                     f"{'Security names bid' if nums['new_riots'] else 'Logistics under pressure' if nums['new_strikes'] else 'Volume thin ahead of the next round'}"]
    entry = {"round": rnd, "prices": prices, "pct": pct, "headlines": headlines, "rationale": rationale or "rule-based move",
             "inputs": {**{k: round(v, 2) for k, v in nums.items()}, "policy_signal": round(policy_signal, 2)}}
    market["history"].append(entry)
    return entry


def policy_signal_from(election, election_round):
    """-1..1: how much the populist / 'change' side (left lane, or option A) gained this round, from polls."""
    if not election or not election_round:
        return 0.0
    polls = election_round.get("polls") or {}
    if not polls:
        return 0.0
    lanes = {o["id"]: o.get("lane") for o in election["options"]}
    change_ids = [i for i, l in lanes.items() if l == "left"] or [election["options"][0]["id"]]
    n = sum(1 for p in polls.values() if p["lean"] in change_ids)
    return max(-1.0, min(1.0, (n - len(polls) / 2) / 25.0))
