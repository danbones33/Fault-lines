"""
Scheduled events that actually fire.

Sources: (1) parsed out of the seed text by an LLM ("Mid-campaign event (round 4): a leaked Helix memo..."),
(2) an explicit list passed with the run (`events` in the POST body), (3) data/events.json applied to every run.
Each event: {id, round, name, description, affected: {candidates: [ids], companies: [names], states: [codes]},
severity 1-5, poll_hit (points), stock_hits: {SYM: pct}, unrest_bump}.

When the round arrives the runner posts it as breaking news from the wire (every agent sees it), the engine
applies the rule-based shock (polls, market, unrest), the news desk leads with it, the Feed gets a burst, the
timeline logs EVENT FIRED and the UI shows a banner. At the end of the run every scheduled event must have
fired; a loud warning is logged otherwise.
"""
import json
import os
import re

PARSE_SYSTEM = ("You extract scheduled events from a fictional scenario for a simulation. Only extract events that are explicitly "
                "scheduled for a future round/day (e.g. 'Mid-campaign event (round 4): ...', 'On day 3 ...'); the opening situation is not "
                "an event. Return JSON only.")


def parse_prompt(scenario_text, election, tickers, rounds):
    cands = "; ".join(f"{o['id']} = {o['name']}" for o in (election or {}).get("options", [])) or "none"
    tk = "; ".join(f"{t['sym']} = {t['name']} ({t.get('sector','')})" for t in tickers) or "none"
    body = scenario_text if len(scenario_text) <= 3000 else scenario_text[:1500] + "\n[...]\n" + scenario_text[-1500:]
    return (f"Scenario (the simulation has {rounds} campaign rounds, round 1 = day 1):\n{body}\n\n"
            f"Candidates: {cands}\nTickers in this run's market: {tk}\n\n"
            f"Return {{\"events\": [{{\"round\": int, \"name\": \"<= 8 words\", \"description\": \"<= 60 words, as breaking news\", "
            f"\"affected_candidates\": [candidate ids], \"affected_companies\": [names], \"affected_tickers\": [symbols from the list, "
            f"or [] if none matches], \"affected_states\": [two-letter codes or []], \"severity\": 1-5, "
            f"\"poll_hit\": <0-12 points of polling damage to the affected candidates>, \"stock_hits\": {{\"SYM\": <negative or positive percent>}}}}]}} "
            f"(empty list if nothing is scheduled).")


def _clean(ev, i, election, tickers, rounds):
    try:
        rnd = int(ev.get("round", 0))
    except Exception:
        rnd = 0
    if rnd < 1:
        return None
    rnd = min(rnd, rounds)
    ids = {o["id"] for o in (election or {}).get("options", [])}
    syms = {t["sym"] for t in tickers}
    try:
        sev = max(1, min(5, int(ev.get("severity", 3))))
    except Exception:
        sev = 3
    try:
        poll_hit = max(0.0, min(12.0, float(ev.get("poll_hit", sev * 2))))
    except Exception:
        poll_hit = sev * 2.0
    hits = {}
    for k, v in (ev.get("stock_hits") or {}).items():
        k = str(k).upper()
        if k in syms:
            try:
                hits[k] = max(-40.0, min(40.0, float(v)))
            except Exception:
                pass
    for s in ev.get("affected_tickers") or []:
        s = str(s).upper()
        if s in syms and s not in hits:
            hits[s] = -(10 + 4 * sev)
    # a scandal-grade event moves its company hard: floor negative hits at -(5 x severity), e.g. severity 3 -> at least -15%
    for s, pct in list(hits.items()):
        if pct < 0:
            hits[s] = min(pct, -5.0 * sev)
    return {"id": f"ev{i}", "round": rnd, "name": str(ev.get("name", f"Event {i}"))[:60], "description": str(ev.get("description", ""))[:500],
            "affected": {"candidates": [c for c in (ev.get("affected_candidates") or []) if c in ids],
                         "companies": [str(c)[:40] for c in (ev.get("affected_companies") or [])][:5],
                         "states": [str(s).upper()[:2] for s in (ev.get("affected_states") or [])][:10]},
            "severity": sev, "poll_hit": poll_hit, "stock_hits": hits, "unrest_bump": 3 * sev, "fired": False}


def parse_events(llm, run_id, scenario_text, election, tickers, rounds, explicit=None, fl_root=None):
    events = []
    if llm is not None and re.search(r"\b(round|day)\s*\d+", scenario_text, re.I):
        try:
            d = llm.chat_json(PARSE_SYSTEM, parse_prompt(scenario_text, election, tickers, rounds), kind="events", run_id=run_id,
                              temperature=0.2, max_tokens=900)
            events += [e for e in (d.get("events") or []) if isinstance(e, dict)]
        except Exception as e:
            print(f"[events] parse failed: {e}")
    events += list(explicit or [])
    if fl_root:
        p = os.path.join(fl_root, "data", "events.json")
        if os.path.exists(p):
            try:
                with open(p, encoding="utf-8") as f:
                    events += json.load(f)
            except Exception as e:
                print(f"[events] data/events.json unreadable: {e}")
    out, seen = [], set()
    for i, ev in enumerate(events, 1):
        c = _clean(ev, i, election, tickers, rounds)
        if c and (c["round"], c["name"].lower()) not in seen:
            seen.add((c["round"], c["name"].lower()))
            out.append(c)
    out.sort(key=lambda e: e["round"])
    return out


def events_for_round(events, rnd):
    return [e for e in events if e["round"] == rnd]


def breaking_text(ev):
    return f"BREAKING: {ev['name']}. {ev['description']}"


def unfired(events):
    return [e for e in events if not e.get("fired")]
