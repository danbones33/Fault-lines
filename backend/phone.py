"""
THE PHONE: the player texts characters as an in-world insider. Characters answer in persona (one LLM call each) and
decide for themselves, at round end, whether to act on what they were told. Influence is earned: trust per character
starts low and moves with what the player's tips turn out to be worth. Every message, decision and effect is logged
so causality is traceable. Effects go through the sim's own systems (events, polls, cohesion, debate notes, bloc
identity), never around them. Meddling can be discovered and becomes a "mystery insider" scandal.

State: runs/<id>/phone.json = {contacts: {cid: {trust, thread, memories, unread, last_player_round}}, log: [], pending_events: [],
                              debate_notes: [], last_round}
"""
import random

REPLY_SYSTEM = ("You are a character in a fictional political simulation, texting with someone who claims to be an insider. Stay in character; "
                "you decide what to believe. Never reference real people or real-world years as the present. Plain text, English only. Return JSON only.")
DECIDE_SYSTEM = "You decide, in character, whether a fictional political figure acts on what an insider texted them. Return JSON only."
INBOUND_SYSTEM = "You write a short text message a fictional political figure sends to a trusted insider. Return JSON only."
ACTIONS = {"none": "do nothing", "leak_press": "leak the information to the press", "change_debate_line": "use it in the debate",
           "shift_policy": "shift a policy stance", "push_bloc_vote": "push the bloc toward action", "go_public": "go public and name the insider's claims", "warn": "warn allies privately"}
MAX_INBOUND_PER_ROUND = 2


def load(d):
    d = d or {}
    d.setdefault("contacts", {})
    d.setdefault("log", [])
    d.setdefault("pending_events", [])
    d.setdefault("debate_notes", [])
    d.setdefault("last_round", -1)
    return d


# ---------- contacts ----------
def build_contacts(tl, roster, personas, conflict_state, networks, run_id, crime_state=None):
    """Who the player can text: candidates, anchors, notable accounts, governors of the hottest and bloc states, bloc leaders."""
    out = []
    el = tl.get("election") or {}
    for o in el.get("options", []):
        ident = roster.get(f"npc:candidate_{o['id'].lower()}") or {}
        out.append({"id": f"cand:{o['id']}", "name": o["name"], "kind": "president" if o.get("incumbent") else "candidate", "avatar": o.get("portrait") or f"/api/avatars/candidate_{o['id'].lower()}.png",
                    "persona": ident.get("persona") or f"{o['name']}, {o.get('party', '')} candidate. Platform: {o.get('blurb', '')[:300]}", "tag": o.get("party", "")})
    for n in networks:
        out.append({"id": f"anchor:{n['id']}", "name": n["anchor"], "kind": "anchor", "avatar": f"/api/avatars/anchor_{n['id']}.png",
                    "persona": f"{n['anchor']}, lead anchor of {n['name']}. On air: {n.get('spin', '')}. Off air: a working journalist who trades in tips and protects sources, but checks them.", "tag": n["name"]})
    for v in roster.values():
        if v.get("kind") in ("influencer", "pundit", "organizer"):
            out.append({"id": v["id"], "name": v["display"], "kind": v["kind"], "avatar": v.get("avatar") or f"/api/avatars/{v['handle']}.png",
                        "persona": f"@{v['handle']}: {v.get('persona') or v.get('bio', '')}", "tag": "@" + v["handle"]})
    last = tl["rounds"][-1]["states"] if tl.get("rounds") else {}
    hot = [c for c, _ in sorted(last.items(), key=lambda kv: -kv[1]["unrest"])[:6]]
    bloc_states = []
    for b in (conflict_state or {}).get("blocs", {}).values():
        if not b.get("gone"):
            bloc_states += b["members"]
    for code in list(dict.fromkeys(hot + bloc_states))[:12]:
        pm = personas.get(code) or {}
        out.append({"id": f"gov:{code}", "name": f"Governor of {pm.get('name', code)}", "kind": "governor", "avatar": f"/api/avatars/{code.lower()}_voice.png",
                    "persona": f"The governor of {pm.get('name', code)}, speaking for the state. {(pm.get('persona') or '')[:500]}", "tag": code})
    for sy in (crime_state or {}).get("syndicates", {}).values():
        if sy.get("gone"):
            continue
        out.append({"id": f"syn:{sy['id']}", "name": sy["leader"]["name"], "kind": "AI network" if sy["kind"] == "ai" else "syndicate boss", "avatar": f"/api/avatars/{sy['id']}.png",
                    "persona": f"{sy['leader']['name']}, {'the voice of' if sy['kind'] == 'ai' else 'who runs'} {sy['name']} ({sy['specialty']}). {sy['leader'].get('persona', '')} Strong in: {', '.join(c for c, t in sy['territory'].items() if t >= 40) or sy['home']}. Knows a great deal; prices everything; never admits a crime in writing.",
                    "tag": sy["name"], "syndicate": sy["id"]})
    for b in (conflict_state or {}).get("blocs", {}).values():
        if b.get("gone") or b.get("stage", 0) < 2:
            continue
        for i, l in enumerate(b.get("leaders", [])[:3]):
            out.append({"id": f"bloc:{b['id']}:{i}", "name": l["name"], "kind": "bloc leader", "avatar": f"/api/avatars/{l.get('state', 'xx').lower()}_voice.png",
                        "persona": f"{l['name']}, {l['role']} of {b['name']} ({', '.join(b['members'])}). {l.get('bio', '')} Manifesto: {(b.get('manifesto') or '')[:300]}", "tag": b["name"], "bloc": b["id"]})
        if b.get("president"):
            out.append({"id": f"bloc:{b['id']}:pres", "name": b["president"]["name"], "kind": "bloc president", "avatar": f"/api/avatars/{b['members'][0].lower()}_voice.png",
                        "persona": f"{b['president']['name']}, elected President of {b['name']}. {b['president'].get('blurb', '')}", "tag": b["name"], "bloc": b["id"]})
    return out


def ensure_contacts(state, contacts):
    for c in contacts:
        st = state["contacts"].setdefault(c["id"], {"trust": 20, "thread": [], "memories": [], "unread": 0, "last_player_round": None})
        st["name"], st["kind"], st["avatar"], st["persona"], st["tag"], st["bloc"] = c["name"], c["kind"], c["avatar"], c["persona"], c.get("tag", ""), c.get("bloc")
        st["syndicate"] = c.get("syndicate")
    return state


def summary(state):
    out = []
    for cid, c in state["contacts"].items():
        last = c["thread"][-1] if c["thread"] else None
        out.append({"id": cid, "name": c["name"], "kind": c["kind"], "avatar": c["avatar"], "tag": c.get("tag", ""), "trust": c["trust"], "unread": c.get("unread", 0),
                    "last": {"from": last["from"], "text": last["text"][:80], "date": last.get("date")} if last else None})
    order = {"president": 0, "candidate": 1, "bloc president": 2, "bloc leader": 3, "anchor": 4, "governor": 5}
    out.sort(key=lambda c: (-(c["unread"] > 0), order.get(c["kind"], 9), -c["trust"]))
    return out


# ---------- world state the characters know ----------
def world_lines(tl, date_label, tech_context, conflict_lines):
    lines = [f"Date: {date_label}."]
    if tech_context:
        lines.append(tech_context[:400])
    r = tl["rounds"][-1] if tl.get("rounds") else None
    if r:
        top = sorted(r["states"].items(), key=lambda kv: -kv[1]["unrest"])[:5]
        lines.append("Unrest leaders: " + ", ".join(f"{c} {s['unrest']:.0f} ({s['stage_name']})" for c, s in top) + ".")
        q = r.get("quarter")
        if q:
            lines.append(f"Quarter {q['label']}: approval {q['approval']['national']}%; events: {', '.join(q.get('events') or []) or 'none'}.")
        er = r.get("election") or {}
        if er.get("polls"):
            lead = {}
            for c, p in er["polls"].items():
                lead[p["lean"]] = lead.get(p["lean"], 0) + 1
            names = {o["id"]: o["name"] for o in (tl.get("election") or {}).get("options", [])}
            lines.append("Polls, states leaning: " + ", ".join(f"{names.get(k, k)} {v}" for k, v in lead.items()) + ".")
        if er.get("results"):
            names = {o["id"]: o["name"] for o in (tl.get("election") or {}).get("options", [])}
            lines.append("The election is over: " + ", ".join(f"{names.get(k, k)} {v} EV" for k, v in er["results"]["ev"].items()) + ".")
    lines += conflict_lines[:3]
    return lines


# ---------- replies ----------
def reply(llm, run_id, state, cid, player_text, rnum, date_label, world, rng):
    c = state["contacts"][cid]
    c["thread"].append({"from": "player", "text": player_text[:800], "round": rnum, "date": date_label})
    c["last_player_round"] = rnum
    trust = c["trust"]
    thread = "\n".join(f"{'INSIDER' if m['from'] == 'player' else c['name']}: {m['text']}" for m in c["thread"][-12:])
    mood = ("You barely know this person and suspect a setup: be curt, suspicious, or ignore them (empty reply)." if trust < 30 else
            "You have some reason to trust this person: answer, but guard what matters." if trust < 60 else
            "You trust this person: be candid, ask for favours, share what you would never say on air.")
    prompt = (f"Who you are: {c['persona'][:900]}\nTrust in the insider: {trust}/100. {mood}\n"
              f"What you remember about them: {'; '.join(c['memories'][-6:]) or 'nothing yet'}\n"
              f"The world right now:\n" + "\n".join(world) + f"\n\nThe conversation so far:\n{thread}\n\n"
              f"Write your reply as a text message (0-60 words; empty string if you ignore them). You may lie, deflect or refuse. "
              f"Return {{\"reply\": str, \"mood\": \"<2-3 words>\", \"trust_delta\": -5..5, \"memory\": \"<= 20 words you will remember, or ''\", "
              f"\"intent\": \"none|will_consider|will_act\"}}")
    out = {"reply": "", "mood": "silent", "trust_delta": 0, "memory": "", "intent": "none"}
    if llm is not None:
        try:
            d = llm.chat_json(REPLY_SYSTEM, prompt, kind="phone", run_id=run_id, temperature=0.8, max_tokens=300)
            out = {"reply": str(d.get("reply") or "")[:600], "mood": str(d.get("mood", ""))[:30], "trust_delta": max(-5, min(5, int(float(d.get("trust_delta", 0) or 0)))),
                   "memory": str(d.get("memory", ""))[:160], "intent": str(d.get("intent", "none"))[:16]}
        except Exception as e:
            print(f"[phone] reply failed: {e}")
            out["reply"] = "..." if trust >= 30 else ""
    if out["reply"]:
        c["thread"].append({"from": "char", "text": out["reply"], "round": rnum, "date": date_label, "mood": out["mood"]})
    else:
        c["thread"].append({"from": "char", "text": "", "round": rnum, "date": date_label, "mood": "left on read"})
    c["trust"] = max(0, min(100, c["trust"] + out["trust_delta"]))
    if out["memory"]:
        c["memories"].append(out["memory"])
    state["log"].append({"round": rnum, "type": "message", "contact": cid, "player": player_text[:200], "reply": out["reply"][:200], "mood": out["mood"],
                         "trust": c["trust"], "intent": out["intent"]})
    return out


# ---------- round end: decisions, effects, exposure, inbound ----------
def round_end(llm, run_id, state, tl, rnum, date_label, world, rd_events, rng, mode, govern_state=None, conflict_state=None):
    """Each contacted character decides whether to act; effects become pending events / notes; exposure may trigger a scandal;
    trusted characters may text first. Returns {"decisions": [...], "inbound": n, "exposed": bool}."""
    out = {"decisions": [], "inbound": 0, "exposed": False}
    acted = []
    names = {o["id"]: o["name"] for o in (tl.get("election") or {}).get("options", [])}
    for cid, c in state["contacts"].items():
        recent = [m for m in c["thread"] if m["from"] == "player" and m["round"] is not None and rnum - m["round"] <= 1]
        window = [m for m in c["thread"] if m["from"] == "player" and m["round"] is not None and rnum - m["round"] <= 3 and not m.get("credited")]
        # a tip proves useful when it named something that then happened (within three rounds)
        for m in window:
            txt = m["text"].lower()
            if any((ev.get("name") or "").lower().split(":")[0][:14] in txt or any(s.lower() in txt for s in (ev.get("affected") or {}).get("states") or []) for ev in rd_events):
                m["credited"] = True
                c["trust"] = min(100, c["trust"] + 8)
                state["log"].append({"round": rnum, "type": "trust", "contact": cid, "delta": 8, "why": "a tip matched what happened"})
                break
        if not recent or c.get("decided_round") == rnum:
            continue
        c["decided_round"] = rnum
        for m in []:
            txt = m["text"].lower()
            if any((ev.get("name") or "").lower().split(":")[0][:14] in txt or any(s.lower() in txt for s in (ev.get("affected") or {}).get("states") or []) for ev in rd_events):
                c["trust"] = min(100, c["trust"] + 8)
                state["log"].append({"round": rnum, "type": "trust", "contact": cid, "delta": 8, "why": "a tip matched what happened"})
                break
        if c["trust"] < 40:
            state["log"].append({"round": rnum, "type": "decision", "contact": cid, "action": "none", "reason": f"trust {c['trust']} too low to act on an unknown insider"})
            out["decisions"].append({"contact": cid, "action": "none"})
            continue
        allowed = ["none", "warn", "leak_press", "go_public"]
        if cid.startswith("cand:"):
            allowed += ["change_debate_line", "shift_policy"]
        if cid.startswith("bloc:") or cid.startswith("gov:"):
            allowed += ["push_bloc_vote"]
        prompt = (f"Who you are: {c['persona'][:600]}\nTrust in the insider: {c['trust']}/100.\nThe world:\n" + "\n".join(world[:5]) +
                  f"\n\nWhat the insider texted you recently:\n" + "\n".join("- " + m["text"][:300] for m in recent) +
                  f"\n\nDecide whether to act. Options: {', '.join(f'{a} ({ACTIONS[a]})' for a in allowed)}. Acting on bad intel can burn you. "
                  f"Return {{\"action\": one of the options, \"reason\": \"<= 30 words\", \"payload\": \"<= 40 words: what you leak, say or push, in your words\"}}")
        d = {"action": "none", "reason": "no call made", "payload": ""}
        if llm is not None:
            try:
                g = llm.chat_json(DECIDE_SYSTEM, prompt, kind="phone", run_id=run_id, temperature=0.6, max_tokens=250)
                a = str(g.get("action", "none"))
                d = {"action": a if a in allowed else "none", "reason": str(g.get("reason", ""))[:200], "payload": str(g.get("payload", ""))[:300]}
            except Exception as e:
                print(f"[phone] decision failed: {e}")
        state["log"].append({"round": rnum, "type": "decision", "contact": cid, **d})
        out["decisions"].append({"contact": cid, **d})
        if d["action"] != "none":
            acted.append((cid, c, d))
    # effects through the sim's own systems
    for cid, c, d in acted:
        a = d["action"]
        who = c["name"]
        if a == "leak_press":
            target = None
            if cid.startswith("cand:"):
                others = [k for k in names if k != cid.split(":")[1]]
                target = rng.choice(others) if others else None
            state["pending_events"].append({"id": f"ph_leak_{rnum}_{len(state['log'])}", "name": f"Leak: {d['payload'][:50]}"[:60], "description": f"A leak, sourced to someone close to {who}: {d['payload']}",
                                            "severity": 2, "poll_hit": 3.0 if target else 0.0, "stock_hits": {}, "affected_candidates": [target] if target else [], "affected_states": []})
            state["log"].append({"round": rnum, "type": "effect", "contact": cid, "effect": f"leak scheduled as next round's event" + (f"; poll hit 3 to {names.get(target, target)}" if target else "")})
        elif a == "change_debate_line":
            state["debate_notes"].append(f"{who} will say: {d['payload']}")
            state["log"].append({"round": rnum, "type": "effect", "contact": cid, "effect": "debate line queued for the writers' room"})
        elif a == "shift_policy" and mode == "govern" and govern_state:
            pend = [p for p in govern_state.get("policies", []) if p["status"] == "pending"]
            if pend:
                p = pend[0]
                p["immediate"]["approval"] = round(p["immediate"].get("approval", 0) + 1.5, 1)
                p["history"].append({"quarter": rnum, "event": f"stance shifted after a private tip: {d['payload'][:80]}"})
                state["log"].append({"round": rnum, "type": "effect", "contact": cid, "effect": f"{p['name']}: immediate approval +1.5"})
        elif a == "push_bloc_vote" and conflict_state:
            bid = c.get("bloc") or next((b["id"] for b in conflict_state.get("blocs", {}).values() if cid.split(":")[1] in b.get("members", [])), None)
            b = conflict_state.get("blocs", {}).get(bid) if bid else None
            if b and not b.get("gone"):
                b["identity"] = round(min(100, b["identity"] + 6), 1)
                b["history"].append({"round": rnum, "event": f"pushed by {who} after an insider's tip"})
                state["log"].append({"round": rnum, "type": "effect", "contact": cid, "effect": f"{b['name']} identity +6"})
        elif a == "go_public":
            state["pending_events"].append({"id": f"ph_public_{rnum}_{len(state['log'])}", "name": f"{who} goes public"[:60], "description": f"{who} goes public with claims from an unnamed insider: {d['payload']}",
                                            "severity": 3, "poll_hit": 0.0, "stock_hits": {}, "affected_candidates": [], "affected_states": []})
            c["trust"] = max(0, c["trust"] - 20)
            state["log"].append({"round": rnum, "type": "effect", "contact": cid, "effect": "went public; trust -20"})
        elif a == "warn":
            state["log"].append({"round": rnum, "type": "effect", "contact": cid, "effect": "warned allies privately (no public effect)"})
    # exposure: the more hands the tip passed through, the likelier someone talks
    meddling = [x for x in acted if x[2]["action"] in ("leak_press", "change_debate_line", "shift_policy", "push_bloc_vote")]
    syndicate_talk = [c for cid, c in state["contacts"].items() if c.get("syndicate") and any(m["from"] == "player" and m["round"] is not None and rnum - m["round"] <= 1 for m in c["thread"])]
    if meddling or syndicate_talk:
        chance = min(0.6, 0.08 * len(meddling) * (1 + 0.2 * len(acted))) + (0.25 if syndicate_talk else 0)
        if rng.random() < chance:
            out["exposed"] = True
            pres = names.get("A")
            if syndicate_talk:
                state["pending_events"].append({"id": f"ph_exposed_{rnum}", "name": "Insider caught talking to a syndicate", "description": f"Investigators intercept messages between an unnamed insider and {', '.join(c['name'] for c in syndicate_talk)} of {', '.join(c.get('tag', '') for c in syndicate_talk)}. A major scandal: who in the circle of power is dealing with organized crime?",
                                                "severity": 4, "poll_hit": 4.0, "stock_hits": {}, "affected_candidates": ["A"] if pres else [], "affected_states": []})
            else:
                state["pending_events"].append({"id": f"ph_exposed_{rnum}", "name": "Mystery insider scandal", "description": f"Reporters trace a string of leaks and sudden pivots to a single unnamed insider texting {', '.join(c['name'] for _, c, _ in meddling)}. Who is pulling strings?",
                                                "severity": 3, "poll_hit": 2.0, "stock_hits": {}, "affected_candidates": ["A"] if pres else [], "affected_states": []})
            for cid2, c2 in state["contacts"].items():
                c2["trust"] = max(0, c2["trust"] - (15 if any(cid2 == x[0] for x in meddling) else 5))
            state["log"].append({"round": rnum, "type": "exposure", "chance": round(chance, 2), "effect": "mystery insider scandal next round; trust -15 for those involved, -5 for everyone"})
    # inbound: trusted characters text first when something big hits them
    n_in = 0
    for cid, c in state["contacts"].items():
        if n_in >= MAX_INBOUND_PER_ROUND or c["trust"] < 50 or llm is None:
            continue
        hit = None
        for ev in rd_events:
            aff = ev.get("affected") or {}
            if (cid.startswith("cand:") and cid.split(":")[1] in (aff.get("candidates") or [])) or (cid.startswith("gov:") and cid.split(":")[1] in (aff.get("states") or [])) \
               or (cid.startswith("bloc:") and ev.get("kind") == "bloc") or int(ev.get("severity", 0)) >= 4:
                hit = ev
                break
        if not hit or c.get("inbound_round") == rnum:
            continue
        try:
            g = llm.chat_json(INBOUND_SYSTEM, (f"Who you are: {c['persona'][:500]}\nYou trust this insider ({c['trust']}/100). What just happened: {hit['name']}. {hit.get('description', '')[:300]}\n"
                                               f"Date: {date_label}. Write {{\"text\": \"<15-50 words: a favour, a tip, venting, or a warning, as a text message>\"}}"),
                              kind="phone", run_id=run_id, temperature=0.8, max_tokens=150)
            txt = str(g.get("text") or "").strip()
            if txt:
                c["thread"].append({"from": "char", "text": txt[:400], "round": rnum, "date": date_label, "mood": "inbound"})
                c["unread"] = c.get("unread", 0) + 1
                c["inbound_round"] = rnum
                n_in += 1
                state["log"].append({"round": rnum, "type": "inbound", "contact": cid, "about": hit["name"], "text": txt[:120]})
        except Exception as e:
            print(f"[phone] inbound failed: {e}")
    out["inbound"] = n_in
    state["last_round"] = rnum
    return out


def take_pending_events(state, rnum):
    """Events queued by decisions fire in the next round (banner, wire context, feed, desk) through the normal event path."""
    evs = state.get("pending_events") or []
    state["pending_events"] = []
    out = []
    for e in evs:
        out.append({"id": e["id"], "round": rnum, "name": e["name"], "description": e["description"], "severity": e["severity"], "poll_hit": e.get("poll_hit", 0),
                    "stock_hits": e.get("stock_hits") or {}, "affected": {"candidates": e.get("affected_candidates") or [], "companies": [], "states": e.get("affected_states") or []},
                    "unrest_bump": 2 * e["severity"] if e.get("affected_states") else 0, "fired": True, "kind": "phone"})
    return out
