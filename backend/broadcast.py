"""
Fault Lines Network: after each round a News Desk LLM writes short scripts for two networks with
opposite spin on the same facts. TTS (Cartesia) and anchor video (avatar provider) attach when keys
exist; otherwise the UI reads the script with the browser's speech synthesis.
"""
SYSTEM = ("You are a cable-news writers' room producing 20-40 second on-air scripts for a FICTIONAL network covering a "
          "simulated wave of US state unrest. Snappy, broadcast cadence, present tense, concrete numbers. "
          "Two networks cover the SAME facts with opposite framing. Never name real people; anchors and reporters are fictional. "
          "Return JSON only.")


def round_summary(tl, round_idx, feed_round, roster):
    cur = tl["rounds"][round_idx]
    prev = tl["rounds"][round_idx - 1] if round_idx > 0 else None
    esc = []
    for code, s in cur["states"].items():
        ps = prev["states"][code]["stage"] if prev else 0
        if s["stage"] > ps:
            esc.append(f"{roster['state:'+code]['display']} -> {s['stage_name']} (unrest {s['unrest']})")
    prev_ids = {b["id"]: b for b in (prev["blocs"] if prev else [])}
    cur_ids = {b["id"]: b for b in cur["blocs"]}
    formed = [f"{b['name']} ({', '.join(b['members'])})" for i, b in cur_ids.items() if i not in prev_ids]
    collapsed = [f"{b['name']}" for i, b in prev_ids.items() if i not in cur_ids]
    top = sorted(cur["states"].items(), key=lambda kv: -kv[1]["unrest"])[:5]
    viral = next((it for it in feed_round["items"] if it["id"] == feed_round.get("viral")), None)
    lines = [f"Round {cur['round']}.",
             "Escalations: " + ("; ".join(esc[:8]) or "none"),
             "Blocs formed: " + ("; ".join(formed) or "none"),
             "Blocs collapsed: " + ("; ".join(collapsed) or "none"),
             "Hottest states: " + ", ".join(f"{c} {s['unrest']} ({s['stage_name']})" for c, s in top),
             "Trending: " + ", ".join(t["tag"] for t in feed_round["trending"][:5]),
             "Viral post: " + (f"@{roster[viral['author']]['handle']}: {viral['text'][:200]}" if viral else "none")]
    return "\n".join(lines), {"escalations": esc, "formed": formed, "collapsed": collapsed}


def write_scripts(llm, run_id, summary_text, networks, seconds, field=None, sim_year=None):
    """field: {'name', 'state_name', 'code', 'stage', 'slogans'} for the on-the-ground reporter this round."""
    nets = "\n".join(f"- {n['id']}: {n['name']}, anchor {n['anchor']}, spin: {n['spin']}" for n in networks)
    fld = (f"Field reporter this round: {field['name']} reporting from {field['state_name']} ({field['code']}), where the "
           f"situation is '{field['stage']}'; signs there read: {', '.join(field['slogans'][:3]) or 'none'}. "
           f"The anchor hands off to the reporter by name at the end of the script ('...we go now to {field['name']} in {field['state_name']}')."
           if field else "No field reporter available; end on the anchor.")
    user = (f"Facts of the round:\n{summary_text}\n\n{fld}\n\nNetworks:\n{nets}\n\nFor each network write: 'headline' (<= 9 words, "
            f"chyron style), 'chyron' (lower-third, <= 60 chars), 'script' (~{seconds} seconds when read aloud, 55-95 words, "
            f"anchor voice, opens with the headline, ends with the handoff), and 'field_cutin' (2-3 sentences, 35-60 words, spoken by "
            f"the field reporter standing in that state, concrete and sensory: what they see and hear, one quote from a sign, then "
            f"'back to you'). Each network's reporter keeps the network's spin. "
            f"Return {{\"networks\": [{{\"id\": str, \"headline\": str, \"chyron\": str, \"script\": str, \"field_cutin\": str}}]}}")
    try:
        data = llm.chat_json(SYSTEM, user, kind="news", run_id=run_id, temperature=0.8, max_tokens=1200)
        out = []
        for n in networks:
            got = next((x for x in (data.get("networks") or []) if x.get("id") == n["id"]), None)
            if not got:
                continue
            out.append({"network": n["id"], "network_name": n["name"] + (" · BREAKING" if n.get("breaking") else ""), "anchor": n["anchor"],
                        "kind": "breaking" if n.get("breaking") else "news", "story": n.get("story"),
                        "headline": str(got.get("headline", ""))[:80], "chyron": str(got.get("chyron", ""))[:70],
                        "script": str(got.get("script", ""))[:1200], "field_cutin": str(got.get("field_cutin", ""))[:600],
                        "field_state": field["code"] if field else None, "field_reporter": field["name"] if field else None,
                        "field_state_name": field["state_name"] if field else None,
                        "anchor_image": None, "field_image": None, "audio": None, "field_audio": None, "video": None, "status": "script"})
        if sim_year:
            import yearfix
            for b in out:
                for k in ("headline", "chyron", "script", "field_cutin"):
                    b[k] = yearfix.fix_text(b.get(k) or "", sim_year)
        return out
    except Exception as e:
        print(f"[broadcast] scripts failed: {e}")
        return []


# ---------- pacing: one broadcast per round, the network that suits the top story, rotated; a second BREAKING slot only for big shocks ----------
HUGE = 8.0        # top-story impact at or above this is "something huge": the World Service gets the slot
QUIET = 2.0       # below this nothing much happened: the partisan desks just alternate


def round_stories(tl, idx, rd, er, election, market_last):
    """Scored stories of the round: [(impact, kind, text, suits)] with suits in alarm|calm|world|breaking|alternate."""
    cur = tl["rounds"][idx]["states"]
    prev = tl["rounds"][idx - 1]["states"] if idx > 0 else {}
    st = []
    for ev in rd.get("events") or []:
        sev = int(ev.get("severity", 3))
        st.append((6 + 2 * sev, "event", f"{ev['name']}: {ev.get('description', '')[:160]}", "breaking"))
    if er and er.get("results"):
        st.append((100, "election_night", "Election night results are in", "breaking"))
    if election and election.get("debate_round") == rd["round"]:
        st.append((7, "debate", "The televised debate", "world"))
    ups = sorted(((cur[c]["stage"] - prev.get(c, {}).get("stage", 0), c) for c in cur if c in prev), reverse=True)
    if ups and ups[0][0] > 0:
        jump, code = ups[0]
        stage = cur[code]["stage"]
        score = {4: 9, 3: 5, 2: 3}.get(stage, 1.5) + (jump - 1)
        st.append((score, "escalation", f"{cur[code].get('name', code)} moved to {cur[code]['stage_name']}", "alarm"))
    downs = [c for c in cur if c in prev and cur[c]["stage"] < prev[c]["stage"]]
    if downs:
        st.append((min(4.0, 1.5 + 0.3 * len(downs)), "de-escalation", f"{len(downs)} states calmed down", "calm"))
    blocs_now = {b["name"] for b in tl["rounds"][idx].get("blocs", [])}
    blocs_prev = {b["name"] for b in tl["rounds"][idx - 1].get("blocs", [])} if idx > 0 else set()
    for b in blocs_now - blocs_prev:
        st.append((8.5, "bloc_formed", f"A bloc of states, {b}, has formed", "world"))
    for b in blocs_prev - blocs_now:
        st.append((5, "bloc_collapsed", f"The {b} bloc has collapsed", "calm"))
    if market_last:
        mv = float((market_last.get("pct") or {}).get("FLN500", 0))
        if mv <= -5:
            st.append((7 + min(4, (-mv - 5)), "crash", f"Markets fell {mv:.1f}%", "alarm"))
        elif mv >= 3:
            st.append((3, "rally", f"Markets rose {mv:+.1f}%", "calm"))
    if er and er.get("polls") and not er.get("results"):
        st.append((2.5, "polls", "The campaign polls", "alternate"))
    if not st:
        st.append((1, "quiet", "A quiet day", "alternate"))
    st.sort(key=lambda t: -t[0])
    return st


def plan_slots(networks, stories, last_network):
    """The network dicts to write this round: one, or two when the second is a BREAKING cut-in. Each is tagged with its story."""
    by_id = {n["id"]: n for n in networks}
    partisan = [n["id"] for n in networks if n["id"] != "fln_world"] or [n["id"] for n in networks]
    world = by_id.get("fln_world")
    impact, kind, text, suits = stories[0]

    def other(nid):
        return next((p for p in partisan if p != nid), nid)

    if impact >= HUGE and world and last_network != "fln_world":
        main = "fln_world"
    elif suits == "alarm" and "fln_alarm" in by_id:
        main = "fln_alarm"
    elif suits == "calm" and "fln_calm" in by_id:
        main = "fln_calm"
    else:
        main = other(last_network) if last_network in partisan else partisan[0]
    if impact < HUGE - 2 and main == last_network and main in partisan:
        main = other(main)   # the same desk two rounds running only when the story clearly belongs to it
    slots = [dict(by_id[main], story=text, breaking=False)]
    shock = [s for s in stories if s[1] in ("event", "election_night") or (s[1] in ("escalation", "crash") and s[0] >= 9)]
    if shock:
        bnet = "fln_world" if (kind == "election_night" and world and main != "fln_world") else (other(main) if main in partisan else partisan[0])
        if bnet != main:
            slots.append(dict(by_id[bnet], story=shock[0][2], breaking=True,
                              spin=by_id[bnet]["spin"] + "; this is a BREAKING NEWS cut-in: urgent, only this one story, no small talk"))
    return slots
