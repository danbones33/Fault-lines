"""
THE DIRECTOR: reads a finished run (timeline, feeds, market, debate, roster) and builds an EPISODE
SCRIPT: ordered acts of scenes with narration (voiced), durations, map states, feed posts, ticker
rounds and media. The player only ever plays the script and the script only ever references the
round it is on, so nothing can leak ahead. Election-night narration is generated from a per-tick
"revealed" summary that contains nothing beyond that tick.

episode.json:
{
  "run_id", "title", "built_at", "election": bool,
  "acts": [{"id", "title", "subtitle", "scenes": ["scene ids"], "music": "shock|campaign|debate|night|epilogue|cold"}],
  "scenes": {id: {"id","act","type","title","round","duration","narration":{"text","audio","voice","emotion"},
                  "chyron":{"head","sub"}, "anchor":{"network","image"}, "map":{...}, "feed":[items], "market":{...},
                  "media":{...}, "stinger": "breaking|call|none", "clock": "7:00 PM ET"|null, "night":{tick}|null}}
}
Scene types: title, anchor, montage, feed_burst, market, protest, polling, candidate, debate, night, call, victory,
contingent, epilogue, teaser.
"""
import json
import os
import random
import time
from datetime import datetime
import re


_CLOCK_RE = re.compile(r"^(?:(?:it's|it is|at)\s+)?\d{1,2}(?::\d{2})?\s*(?:a\.?m\.?|p\.?m\.?)\s*(?:et|eastern(?: time)?)?[,.:\s-]*", re.I)


def _strip_clock(text):
    """The clock is on screen: an election-night line never opens with the time."""
    out = _CLOCK_RE.sub("", text or "", count=1).lstrip()
    return (out[:1].upper() + out[1:]) if out else text

from election_night import build_night, revealed_summary
from providers import placeholder_png
from reporters import ROLE_EMOTION

NARRATOR_SYSTEM = ("You are the writers' room for a FICTIONAL news documentary about a simulated wave of US unrest and an election. "
                   "You write short, vivid, spoken narration for a news anchor, in present tense, concrete, with real numbers and "
                   "state names from the facts given. Never invent results that are not in the facts. No real people. Return JSON only.")

ACT_MUSIC = {"cold": "cold", "shock": "shock", "campaign": "campaign", "debate": "debate", "surprise": "shock",
             "night": "night", "epilogue": "epilogue"}


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _words(text):
    return len((text or "").split())


class Director:
    def __init__(self, run_dir, run_id, llm, tts, state_names, progress_cb=None):
        self.run_dir = run_dir
        self.run_id = run_id
        self.llm = llm
        self.tts = tts
        self.state_names = state_names
        self.progress_cb = progress_cb or (lambda *a: None)
        self.tl = _read(os.path.join(run_dir, "timeline.json"))
        self.roster = _read(os.path.join(run_dir, "roster.json"), {})
        self.market = _read(os.path.join(run_dir, "market.json"), {"history": [], "tickers": [], "macro": []})
        self.debate = _read(os.path.join(run_dir, "debate.json"))
        self.feeds = {}
        for r in self.tl["rounds"]:
            f = _read(os.path.join(run_dir, "feed", f"round_{r['round']}.json"))
            if f:
                self.feeds[r["round"]] = f
        self.election = self.tl.get("election")
        self.scenes = {}
        self.acts = []
        self.order = []
        self.rng = random.Random(self.tl["scenario"]["seed"])
        self.n_narr = 0
        self.unit_names = dict(state_names)
        self.unit_names.update({"DC": "the District of Columbia", "ME-1": "Maine's 1st district", "ME-2": "Maine's 2nd district",
                                "NE-1": "Nebraska's 1st district", "NE-2": "Nebraska's 2nd district", "NE-3": "Nebraska's 3rd district"})

    # ---------- helpers ----------
    def round(self, n):
        return next((r for r in self.tl["rounds"] if r["round"] == n), None)

    def media_dir(self):
        d = os.path.join(self.run_dir, "media", "episode")
        os.makedirs(d, exist_ok=True)
        return d

    def narrate(self, system_user, kind="episode"):
        """One LLM call -> {'text': ..., extra}. Falls back to a plain sentence when the LLM is off."""
        if self.llm is None:
            return None
        try:
            return self.llm.chat_json(NARRATOR_SYSTEM, system_user, kind=kind, run_id=self.run_id, temperature=0.7, max_tokens=700)
        except Exception as e:
            print(f"[director] narration failed: {e}")
            return None

    def voice(self, scene_id, text, role="anchor_a", emotion=None, voice_id=None):
        """TTS one narration; returns (url, seconds). Duration from mp3 size at 128 kbps, else words/2.6."""
        est = max(2.5, _words(text) / 2.6)
        if not text or self.tts is None or not self.tts.available:
            return None, est
        mp3 = self.tts.speak(text, role=role, kind="tts", run_id=self.run_id, voice_id=voice_id, emotion=emotion)
        if not mp3:
            return None, est
        fn = f"{scene_id}.mp3"
        with open(os.path.join(self.media_dir(), fn), "wb") as f:
            f.write(mp3)
        self.n_narr += 1
        secs = len(mp3) * 8 / 128000.0
        return f"/api/runs/{self.run_id}/media/episode/{fn}", max(1.5, secs)

    def add(self, act, scene):
        sid = scene["id"]
        scene["act"] = act
        self.scenes[sid] = scene
        self.order.append(sid)
        self.acts[-1]["scenes"].append(sid) if self.acts and self.acts[-1]["id"] == act else None
        return scene

    def new_act(self, aid, title, subtitle="", music=None):
        self.acts.append({"id": aid, "title": title, "subtitle": subtitle, "scenes": [], "music": music or ACT_MUSIC.get(aid, aid)})

    def anchor(self, network="fln_alarm"):
        img = f"/api/avatars/anchor_{network}.png"
        names = {"fln_alarm": ("FLN Red", "Dana Kessler", "anchor_a", ROLE_EMOTION["anchor_a"]),
                 "fln_calm": ("FLN Blue", "Marcus Oyelaran", "anchor_b", ROLE_EMOTION["anchor_b"]),
                 "fln_world": ("FLN World Service", "Clive Ashworth", "anchor_world", ROLE_EMOTION["anchor_world"])}
        nm, anchor, role, emo = names.get(network, names["fln_alarm"])
        return {"network": network, "network_name": nm, "anchor": anchor, "image": img, "role": role, "emotion": emo}

    def feed_items(self, rnum, kinds=None, n=6, authors=None):
        f = self.feeds.get(rnum)
        if not f:
            return []
        items = [it for it in f["items"] if (not kinds or it["kind"] in kinds) and (not authors or it["author"] in authors)]
        items.sort(key=lambda it: -(it.get("likes", 0) + 3 * it.get("reposts", 0)))
        return [self._slim(it) for it in items[:n]]

    def _slim(self, it):
        a = self.roster.get(it["author"], {})
        return {"id": it["id"], "round": it["round"], "handle": a.get("handle", it["author"]), "display": a.get("display", it["author"]), "avatar": a.get("avatar"),
                "verified": a.get("verified", "none"), "text": it.get("text", ""), "kind": it["kind"], "state": it.get("state"),
                "likes": it.get("likes", 0), "reposts": it.get("reposts", 0), "replies": it.get("replies", 0),
                "media": it.get("media"), "quote_text": it.get("quote_text")}

    def market_at(self, rnum):
        h = None
        for e in self.market.get("history", []):
            if e["round"] <= rnum:
                h = e
        return {"entry": h, "tickers": self.market.get("tickers", []), "macro": self.market.get("macro", [])} if h else None

    def round_facts(self, rnum):
        r = self.round(rnum)
        if not r:
            return ""
        st = r["states"]
        top = sorted(st.items(), key=lambda kv: -kv[1]["unrest"])[:6]
        prev = self.round(rnum - 1)
        esc = []
        if prev:
            for c, s in st.items():
                if s["stage"] > prev["states"][c]["stage"]:
                    esc.append(f"{self.state_names[c]} to {s['stage_name']}")
        stages = {}
        for s in st.values():
            stages[s["stage_name"]] = stages.get(s["stage_name"], 0) + 1
        lines = [f"Round {rnum}. Hottest: " + "; ".join(f"{self.state_names[c]} {s['stage_name']} (unrest {s['unrest']}, signs: {', '.join(s.get('slogans', [])[:2]) or 'none'})" for c, s in top),
                 "Stage counts: " + ", ".join(f"{k} {v}" for k, v in stages.items()),
                 "Escalations: " + ("; ".join(esc[:8]) or "none"),
                 "Blocs: " + ("; ".join(f"{b['name']} ({', '.join(b['members'])})" for b in r["blocs"]) or "none")]
        f = self.feeds.get(rnum)
        if f:
            lines.append("Trending: " + ", ".join(t["tag"] for t in f.get("trending", [])[:5]))
            if f.get("broadcasts"):
                lines.append(f["broadcasts"][0].get("network_name", "FLN") + " headline: " + f["broadcasts"][0]["headline"])
            m = f.get("market")
            if m:
                lines.append("Markets: " + ", ".join(f"{k} {v:+.1f}%" for k, v in list(m["pct"].items())[:5]) + ". " + (m.get("headlines") or [""])[0])
        er = r.get("election")
        if er and er.get("polls") and self.election:
            names = {o["id"]: o["name"] for o in self.election["options"]}
            lead = {}
            for p in er["polls"].values():
                lead[names.get(p["lean"], p["lean"])] = lead.get(names.get(p["lean"], p["lean"]), 0) + 1
            lines.append("Polling, states leaning: " + ", ".join(f"{k} {v}" for k, v in lead.items()))
        return "\n".join(lines)

    # ---------- build ----------
    def build(self):
        rounds = [r["round"] for r in self.tl["rounds"]]
        if not rounds:
            raise RuntimeError("run has no scored rounds")
        election = self.election
        night_round = election.get("ballot_round") if election else None
        results = None
        if election and night_round is not None:
            # re-tally from the raw ballot so the night always uses the current 538-unit math (DC, ME/NE districts)
            from election import tally
            rd = _read(os.path.join(self.run_dir, "rounds", f"round_{night_round}.json"))
            if rd and rd.get("ballot"):
                results = tally(rd["ballot"], election)
                election["results"] = results
            else:
                results = election.get("results")
        campaign_rounds = [r for r in rounds if r >= 1 and (night_round is None or r < night_round)]
        debate_round = election.get("debate_round") if election else None
        total_steps = 8 + len(campaign_rounds) + (12 if results else 0)
        self.progress_cb("writing", 0, total_steps)
        if self.tl.get("mode") == "govern":
            return self._build_govern(rounds, campaign_rounds, debate_round, night_round, results, total_steps)

        # ---- Cold open
        self.new_act("cold", "Cold Open", self.tl["scenario"]["title"], "cold")
        seed = self.round(0)["seed_post"] or self.tl["scenario"]["text"]
        d = self.narrate(f"Seed event:\n{seed}\n\nWrite 'text': 45-70 words of breaking-news narration for the anchor, opening cold, "
                         f"ending on what the nation is bracing for. Also 'chyron': <= 60 chars.")
        text = (d or {}).get("text") or f"Breaking tonight: {seed[:220]}"
        a = self.anchor("fln_alarm")
        s = self.add("cold", {"id": "s_cold", "type": "anchor", "title": "Breaking news", "round": 0, "anchor": a, "stinger": "breaking",
                              "chyron": {"head": "BREAKING NEWS", "sub": (d or {}).get("chyron") or self.tl["scenario"]["title"][:60]},
                              "map": {"mode": "unrest", "round": 0}, "feed": self.feed_items(0, n=1), "market": self.market_at(0)})
        s["narration"] = self._narr("s_cold", text, a["role"], "urgent, breathless")
        self.progress_cb("writing", 1, total_steps)

        # ---- Act 1: The Shock (first rounds)
        self.new_act("shock", "Act One", "The Shock", "shock")
        self.add("shock", {"id": "t_shock", "type": "title", "title": "Act One", "subtitle": "The Shock", "round": campaign_rounds[0] if campaign_rounds else 1, "duration": 4})
        # The Shock covers the first day or two; everything up to the debate is The Campaign; after it, The October Surprise
        n_first = 1 if (debate_round and debate_round <= 3) else 2
        first = campaign_rounds[:max(1, min(n_first, len(campaign_rounds)))] if campaign_rounds else rounds[1:2]
        for i, rnum in enumerate(first):
            self._montage(f"m_shock_{rnum}", rnum, "shock", network="fln_alarm" if i == 0 else "fln_calm")
            self._conflict_scenes("shock", rnum)
            self.add("shock", {"id": f"f_shock_{rnum}", "type": "feed_burst", "title": f"The Feed, day {rnum}", "round": rnum, "duration": 9,
                               "feed": self.feed_items(rnum, kinds=("post", "quote", "reply", "meme"), n=7), "map": {"mode": "unrest", "round": rnum},
                               "market": self.market_at(rnum)})
            self.progress_cb("writing", 2 + i, total_steps)
        self._protest_scene("p_shock", first[-1] if first else rounds[-1], "shock")
        self._market_scene("k_shock", first[-1] if first else rounds[-1], "shock")

        # ---- Act 2: The Campaign (or "The Split" for non-election runs)
        mid = [r for r in campaign_rounds[len(first):] if (debate_round is None or r < debate_round)]
        if election:
            self.new_act("campaign", "Act Two", "The Campaign", "campaign")
            self.add("campaign", {"id": "t_campaign", "type": "title", "title": "Act Two", "subtitle": "The Campaign", "round": mid[0] if mid else first[-1], "duration": 4})
            for o in election["options"]:
                self._candidate_scene(o, (mid or first)[0])
            for rnum in mid[:3]:
                self._montage(f"m_camp_{rnum}", rnum, "campaign", network="fln_calm")
                self._conflict_scenes("campaign", rnum)
                self.add("campaign", {"id": f"f_camp_{rnum}", "type": "feed_burst", "title": f"Campaign feed, day {rnum}", "round": rnum, "duration": 9,
                                      "feed": self.feed_items(rnum, n=7, authors=[k for k, v in self.roster.items() if v["kind"] in ("candidate", "influencer", "pundit", "meme_account", "state")]),
                                      "map": {"mode": "election", "round": rnum}, "market": self.market_at(rnum)})
                self._polling_scene(f"poll_{rnum}", rnum)
        else:
            self.new_act("campaign", "Act Two", "The Split", "campaign")
            self.add("campaign", {"id": "t_split", "type": "title", "title": "Act Two", "subtitle": "The Split", "round": mid[0] if mid else rounds[-1], "duration": 4})
            for rnum in mid[:4]:
                self._montage(f"m_split_{rnum}", rnum, "campaign", network="fln_calm" if rnum % 2 else "fln_alarm")
                self._conflict_scenes("campaign", rnum)
                self.add("campaign", {"id": f"f_split_{rnum}", "type": "feed_burst", "title": f"The Feed, day {rnum}", "round": rnum, "duration": 9,
                                      "feed": self.feed_items(rnum, n=7), "map": {"mode": "unrest", "round": rnum}, "market": self.market_at(rnum)})
                self._protest_scene(f"p_split_{rnum}", rnum, "campaign", only_new=True)
        self.progress_cb("writing", 3 + len(first), total_steps)

        # ---- Act 3: The Debate
        if election and self.debate and self.debate.get("lines"):
            self.new_act("debate", "Act Three", "The Debate", "debate")
            self.add("debate", {"id": "t_debate", "type": "title", "title": "Act Three", "subtitle": "The Debate", "round": debate_round, "duration": 4})
            self._debate_scene(debate_round)
        self.progress_cb("writing", 4 + len(first), total_steps)

        # ---- Act 4: The October Surprise (biggest mid-campaign event after the debate, before the vote)
        late = [r for r in campaign_rounds if (debate_round is None or r > debate_round)]
        if late:
            self.new_act("surprise", "Act Four", "The October Surprise", "shock")
            self.add("surprise", {"id": "t_surprise", "type": "title", "title": "Act Four", "subtitle": "The October Surprise", "round": late[0], "duration": 4})
            self._surprise_scene(late)
            for rnum in late:
                self._conflict_scenes("surprise", rnum)
        self.progress_cb("writing", 5 + len(first), total_steps)

        # ---- Act 5: Election Night
        if results:
            self.new_act("night", "Act Five", "Election Night", "night")
            self.add("night", {"id": "t_night", "type": "title", "title": "Act Five", "subtitle": "Election Night", "round": night_round, "duration": 4})
            self._election_night(results, night_round, total_steps)

        # ---- Epilogue
        self.new_act("epilogue", "Epilogue", "The Morning After", "epilogue")
        self.add("epilogue", {"id": "t_epi", "type": "title", "title": "Epilogue", "subtitle": "The Morning After", "round": rounds[-1], "duration": 4})
        self._market_scene("k_epi", rounds[-1], "epilogue", final=True)
        self._conflict_epilogue(rounds[-1])
        if results:
            self._victory_posts(night_round)
        self._teaser(rounds[-1])
        self.progress_cb("writing", total_steps, total_steps)

        episode = {"run_id": self.run_id, "title": self.tl["scenario"]["title"], "built_at": datetime.now().isoformat(timespec="seconds"),
                   "election": bool(election), "options": election["options"] if election else None, "year": self.tl.get("year"), "era": self.tl.get("era"),
                   "acts": self.acts, "scenes": self.scenes, "order": self.order,
                   "total_duration": round(sum(self.scenes[s].get("duration", 5) for s in self.order), 1), "narrations": self.n_narr}
        return episode

    # ---------- a governing season: inauguration, the term quarter by quarter, midterms, year four, the re-election ----------
    def _build_govern(self, rounds, quarters, debate_round, night_round, results, total_steps):
        election = self.election
        gv = self.tl.get("govern") or {}
        sc_gv = self.tl["scenario"].get("govern") or {}
        pres = next((o for o in election["options"] if o["id"] == "A"), election["options"][0])
        qinfo = {r["round"]: r.get("quarter") for r in self.tl["rounds"] if r.get("quarter")}
        season = gv.get("season") or self.tl.get("season") or 2

        # ---- Cold open: the inauguration, in the President's own voice
        self.new_act("cold", "Cold Open", f"Season {season}: The {pres['name'].title()} Presidency", "cold")
        speech = sc_gv.get("inauguration") or f"{pres['name']} takes the oath of office."
        handle = f"candidate_{pres['id'].lower()}"
        s = self.add("cold", {"id": "g_inaug", "type": "candidate", "title": "Inauguration Day", "round": 0, "candidate": pres, "stinger": "anthem",
                              "media": {"image": pres.get("portrait") or f"/api/avatars/{handle}.png"},
                              "chyron": {"head": (pres['name'] if pres['name'].lower().startswith("president") else f"PRESIDENT {pres['name']}").upper()[:60], "sub": "Inauguration Day · the oath of office"},
                              "feed": self.feed_items(0, n=3), "map": {"mode": "approval", "round": 0}, "market": self.market_at(0)})
        is_ai = (pres.get("analysis") or {}).get("entity_type") == "AI"
        s["narration"] = self._narr("g_inaug", "My fellow Americans. " + speech[:900], f"{pres['id']}_{'m' if is_ai else (pres.get('voice') or 'm')[:1]}",
                                    "calm, synthetic, evenly paced" if is_ai else "solemn, warm, rising", __import__("debate").voice_for(pres, "m") if is_ai else pres.get("voice_id"))
        tech = self.tl.get("tech") or {}
        if tech.get("segment"):
            a = self.anchor("fln_world")
            sc = self.add("cold", {"id": "g_tech", "type": "anchor", "title": f"{tech.get('year')} · {tech.get('era')}", "round": 0, "anchor": a, "stinger": "sting",
                                   "chyron": {"head": f"{tech.get('year')} · {str(tech.get('era', '')).upper()}"[:60], "sub": (tech.get("headlines") or [""])[0][:90]},
                                   "map": {"mode": "unrest", "round": 0}, "feed": self.feed_items(0, n=3), "market": self.market_at(0)})
            sc["narration"] = self._narr("g_tech", tech["segment"], a["role"], "dry, measured")
        self.progress_cb("writing", 1, total_steps)

        def quarter_scene(rnum, act, network="fln_calm"):
            q = qinfo.get(rnum) or {}
            facts = self.round_facts(rnum)
            d = self.narrate(f"{q.get('label', f'Quarter {rnum}')} of the presidency. The quarter's wire: {q.get('headline', '')}\nFacts:\n{facts}\n\n"
                             f"Write 'headline' (<= 8 words) and 'text': 45-65 words of anchor narration on what the quarter did to the country and to "
                             f"the President's approval ({(q.get('approval') or {}).get('national', '?')}%), naming the vote in Congress if there was one.")
            a = self.anchor(network)
            sid = f"g_q{rnum}"
            sc = self.add(act, {"id": sid, "type": "anchor", "title": q.get("label", f"Quarter {rnum}"), "round": rnum, "anchor": a,
                                "chyron": {"head": ((d or {}).get("headline") or q.get("label", "")).upper()[:60], "sub": (q.get("headline") or "")[:90]},
                                "map": {"mode": "approval", "round": rnum}, "feed": self.feed_items(rnum, n=5), "market": self.market_at(rnum)})
            sc["narration"] = self._narr(sid, (d or {}).get("text") or q.get("headline") or f"Quarter {rnum}.", a["role"], a["emotion"])
            # the event's picture (hurricane damage, the crowd, the ship) with the field voice, then the President's address, in her own voice
            for name, eid in (q.get("event_ids") or {}).items():
                rel = self.tl.get("images", {}).get(f"event_{eid}")
                if not rel or rel == "pending":
                    continue
                states = (q.get("event_states") or {}).get(name) or []
                where = ", ".join(self.state_names.get(c, c) for c in states[:3]) or "the country"
                dd = self.narrate(f"Event this quarter: {name}, hitting {where}. Write 'text': 30-45 words a field reporter says over footage of it: what it looks like on the ground, one number, no time stamps.")
                fs = self.add(act, {"id": f"g_ev{rnum}", "type": "candidate", "title": name, "round": rnum, "candidate": {"name": name},
                                    "media": {"image": f"/api/runs/{self.run_id}/media/{rel}"}, "stinger": "breaking",
                                    "chyron": {"head": name.upper()[:60], "sub": ("Hardest hit: " + where)[:90]}, "feed": self.feed_items(rnum, n=4),
                                    "map": {"mode": "unrest", "round": rnum, "focus": states[0] if states else None}, "market": self.market_at(rnum)})
                fs["narration"] = self._narr(f"g_ev{rnum}", (dd or {}).get("text") or f"{name}. {where} bears the brunt.", "field_m", "tense, speaking over wind")
                break
            addr = q.get("address")
            if addr and addr.get("audio"):
                ad = self.add(act, {"id": f"g_addr{rnum}", "type": "candidate", "title": "Address to the nation", "round": rnum, "candidate": pres,
                                    "media": {"image": pres.get("portrait") or f"/api/avatars/{handle}.png"}, "stinger": "anthem",
                                    "chyron": {"head": (pres['name'] if pres['name'].lower().startswith("president") else f"PRESIDENT {pres['name']}").upper()[:60], "sub": f"Address to the nation · {addr['event']}"[:90]},
                                    "feed": self.feed_items(rnum, n=3), "map": {"mode": "approval", "round": rnum}, "market": self.market_at(rnum),
                                    "duration": round(float(addr.get("seconds") or 20) + 1.0, 1)})
                ad["narration"] = {"text": addr["text"], "audio": addr["audio"], "voice": "president", "emotion": "solemn"}
            self._conflict_scenes(act, rnum)
            self.add(act, {"id": f"g_f{rnum}", "type": "feed_burst", "title": f"The Feed, {q.get('label', '')}", "round": rnum, "duration": 8,
                           "feed": self.feed_items(rnum, kinds=("post", "quote", "reply", "meme"), n=7), "map": {"mode": "approval", "round": rnum},
                           "market": self.market_at(rnum)})

        # ---- Act One: the first year
        def weight(r):
            q = qinfo.get(r) or {}
            return abs((q.get("approval") or {}).get("drift", 0)) + 3 * len(q.get("events") or []) + (2 if q.get("vote") else 0) + (10 if q.get("midterm") else 0) + (6 if q.get("address") else 0) + 8 * len(self._bloc_events(r))
        first = [r for r in quarters if r <= 4]
        self.new_act("shock", "Act One", "The First Year", "shock")
        self.add("shock", {"id": "t_y1", "type": "title", "title": "Act One", "subtitle": "The First Year", "round": first[0] if first else 1, "duration": 4})
        # the first quarter always, then the most eventful of the rest of year one (a hurricane in Q3 beats a quiet Q2)
        y1 = first[:1] + sorted(sorted(first[1:], key=weight, reverse=True)[:2]) if first else []
        for i, rnum in enumerate(y1):
            quarter_scene(rnum, "shock", "fln_alarm" if i == 0 else "fln_calm")
        if first:
            self._market_scene("k_y1", first[-1], "shock")
        self.progress_cb("writing", 3, total_steps)

        # ---- Act Two: the term (the quarters that moved approval or carried an event)
        mid = [r for r in quarters if 4 < r <= 12]
        self.new_act("campaign", "Act Two", "The Term", "campaign")
        self.add("campaign", {"id": "t_term", "type": "title", "title": "Act Two", "subtitle": "Governing", "round": mid[0] if mid else 5, "duration": 4})
        picks = sorted(sorted(mid, key=weight, reverse=True)[:3])
        for i, rnum in enumerate(picks):
            quarter_scene(rnum, "campaign", "fln_calm" if i % 2 else "fln_alarm")
            if rnum == 8 and (qinfo.get(8) or {}).get("midterm"):
                m = qinfo[8]["midterm"]["congress"]
                text = ("Midterm night. The new House: " + ", ".join(f"{l} {n}" for l, n in m["house"].items()) + ". The Senate: "
                        + ", ".join(f"{l} {n}" for l, n in m["senate"].items()) + f". A swing of {qinfo[8]['midterm']['swing']:+.0f} for the President's side.")
                a = self.anchor("fln_world")
                sc = self.add("campaign", {"id": "g_midterm", "type": "anchor", "title": "Midterms", "round": 8, "anchor": a, "stinger": "breaking",
                                           "chyron": {"head": "MIDTERM ELECTIONS", "sub": "Congress is redrawn"}, "map": {"mode": "approval", "round": 8},
                                           "feed": self.feed_items(8, n=4), "market": self.market_at(8)})
                sc["narration"] = self._narr("g_midterm", text, a["role"], a["emotion"])
            self._protest_scene(f"p_term_{rnum}", rnum, "campaign", only_new=True)
        self.progress_cb("writing", 6, total_steps)

        # ---- Act Three: year four and the debate
        late = [r for r in quarters if r > 12]
        self.new_act("debate", "Act Three", "Year Four", "debate")
        self.add("debate", {"id": "t_y4", "type": "title", "title": "Act Three", "subtitle": "Year Four", "round": late[0] if late else 13, "duration": 4})
        if late:
            quarter_scene(late[0], "debate", "fln_alarm")
            for o in election["options"]:
                if o["id"] != pres["id"]:
                    self._candidate_scene(o, late[0])
            self._polling_scene("poll_y4", late[-1])
        if self.debate and self.debate.get("lines"):
            self._debate_scene(debate_round)
        self.progress_cb("writing", 7, total_steps)

        # ---- Act Four: the re-election
        if results:
            self.new_act("night", "Act Four", "Election Night", "night")
            self.add("night", {"id": "t_night", "type": "title", "title": "Act Four", "subtitle": "The Re-election", "round": night_round, "duration": 4})
            self._election_night(results, night_round, total_steps)

        # ---- Epilogue: the season in one page
        self.new_act("epilogue", "Epilogue", "The Record", "epilogue")
        self.add("epilogue", {"id": "t_epi", "type": "title", "title": "Epilogue", "subtitle": "The Record", "round": rounds[-1], "duration": 4})
        rec = gv.get("recap") or {}
        verdict = ("re-elected" if rec.get("reelected") else "defeated" if rec.get("reelected") is False else "at the end of the term")
        d = self.narrate(f"Season recap. President {pres['name']} {verdict}. Approval {rec.get('approval_start')}% to {rec.get('approval_end')}%. "
                         f"Policies that worked: {', '.join(rec.get('policies_worked') or []) or 'none clearly'}. Backfired: {'; '.join(rec.get('policies_backfired') or []) or 'none'}. "
                         f"Biggest moments: {', '.join(rec.get('biggest_moments') or [])}.\nRecord:\n{rec.get('record', '')[:900]}\n\n"
                         f"Write 'text': 70-100 words of a world-service recap of the whole term, dry and exact, ending on what the next four years will test.")
        a = self.anchor("fln_world")
        sc = self.add("epilogue", {"id": "e_recap", "type": "anchor", "title": "The record", "round": rounds[-1], "anchor": a,
                                   "chyron": {"head": "THE RECORD", "sub": f"Approval {rec.get('approval_start', '?')}% → {rec.get('approval_end', '?')}%"},
                                   "map": {"mode": "approval", "round": rounds[-1]}, "feed": self.feed_items(rounds[-1], n=4), "market": self.market_at(rounds[-1])})
        sc["narration"] = self._narr("e_recap", (d or {}).get("text") or f"Four years of President {pres['name']}, {verdict}.", a["role"], a["emotion"])
        self._market_scene("k_epi", rounds[-1], "epilogue", final=True)
        self._conflict_epilogue(rounds[-1])
        if results:
            self._victory_posts(night_round)
        self._teaser(rounds[-1])
        self.progress_cb("writing", total_steps, total_steps)
        return {"run_id": self.run_id, "title": self.tl["scenario"]["title"], "built_at": datetime.now().isoformat(timespec="seconds"),
                "election": bool(election), "options": election["options"] if election else None, "season": season, "mode": "govern",
                "year": self.tl.get("year"), "era": self.tl.get("era"),
                "acts": self.acts, "scenes": self.scenes, "order": self.order,
                "total_duration": round(sum(self.scenes[s].get("duration", 5) for s in self.order), 1), "narrations": self.n_narr}

    # ---------- civil conflict: footage for each ladder step, the bloc's own election night, the war room, the settlement ----------
    def _bloc_events(self, rnum):
        rd = _read(os.path.join(self.run_dir, "rounds", f"round_{rnum}.json")) or {}
        return [e for e in (rd.get("events") or []) if e.get("kind") in ("bloc", "crime")]

    def _conflict_view(self, rnum):
        r = self.round(rnum)
        return (r or {}).get("conflict") or self.tl.get("conflict") or {}

    def _conflict_scenes(self, act, rnum):
        """Scenes for this round's bloc events (declaration, government, election, standoff, blockade, settlement) and, once the
        standoff starts, a war-room read of the map. Returns the number of scenes added."""
        n = 0
        evs = self._bloc_events(rnum)
        cf = self._conflict_view(rnum)
        blocs = {b["id"]: b for b in cf.get("blocs", [])}
        for ev in evs[:2]:
            b = blocs.get(ev.get("bloc")) or {}
            sid = f"cf_{rnum}_{ev['id'][-10:]}"
            if sid in self.scenes:
                continue
            if ev.get("stage") == "election" and b.get("election"):
                e = b["election"]
                cands = e["candidates"]
                sc = self.add(act, {"id": sid + "_bn", "type": "bloc_night", "title": f"{b['name']}: election night", "round": rnum, "duration": 14, "stinger": "breaking",
                                    "bloc_night": {"members": b["members"], "units": e["units"], "ticks": e["ticks"][:3], "candidates": cands},
                                    "chyron": {"head": f"{b['name'].upper()} VOTES"[:60], "sub": f"{cands[0]['name']} vs {cands[1]['name']}"[:90]},
                                    "map": {"mode": "conflict", "round": rnum}, "feed": self.feed_items(rnum, n=4), "market": self.market_at(rnum)})
                d = self.narrate(f"A breakaway bloc, {b['name']} ({', '.join(b['members'])}), holds its own presidential election between {cands[0]['name']} "
                                 f"(hardline) and {cands[1]['name']} (moderate). Polls have just closed; nothing is counted. Write 'text': 35-50 words of anchor "
                                 f"narration: what is at stake, Washington's position that the vote has no legal force, no results.")
                sc["narration"] = self._narr(sid + "_bn", (d or {}).get("text") or f"{b['name']} is voting for a president of its own.", "anchor_world", "dry, measured")
                w = next(c for c in cands if c["id"] == e["winner"])
                sc2 = self.add(act, {"id": sid + "_call", "type": "bloc_night", "title": f"{b['name']}: the call", "round": rnum, "duration": 8,
                                     "bloc_night": {"members": b["members"], "units": e["units"], "ticks": e["ticks"][-1:], "candidates": cands},
                                     "chyron": {"head": f"{b['name'].upper()}: {w['name'].upper()} WINS"[:60], "sub": f"{e['ev'][w['id']]} of {sum(e['ev'].values())} bloc electors"[:90]},
                                     "map": {"mode": "conflict", "round": rnum}, "feed": self.feed_items(rnum, n=4), "market": self.market_at(rnum)})
                d2 = self.narrate(f"{w['name']} ({w['lane']}) has won the bloc election of {b['name']} with {e['ev'][w['id']]} of {sum(e['ev'].values())} bloc electors. "
                                  f"Write 'text': 30-45 words calling it, and what it means for the two presidents problem.")
                sc2["narration"] = self._narr(sid + "_call", (d2 or {}).get("text") or f"{w['name']} wins the bloc's election.", "anchor_a", "urgent, excited")
                n += 2
                continue
            rel = self.tl.get("images", {}).get(ev.get("image_key") or f"event_{ev['id']}")
            image = f"/api/runs/{self.run_id}/media/{rel}" if rel and rel != "pending" else None
            net = "fln_world" if ev.get("stage") in ("declaration", "provisional", "resolution") else ("fln_calm" if ev.get("kind") == "crime" and int(ev.get("severity", 2)) < 3 else "fln_alarm")
            a = self.anchor(net)
            d = self.narrate((f"Crime desk, round {rnum}: {ev['name']}. {ev['description']}\nWrite 'text': 40-60 words of anchor narration as a crime segment: "
                              f"police briefings, courts, data, money; everyone named is fictional; no violence or victims described.") if ev.get("kind") == "crime" else
                             (f"Civil-conflict beat, round {rnum}: {ev['name']}. {ev['description']}\nWrite 'text': 40-60 words of anchor narration covering it as "
                              f"politics and logistics (money, loyalty, legitimacy, checkpoints, courts), never as combat; no casualties, no weapons."))
            scene = {"id": sid, "type": "candidate" if image else "anchor", "title": ev["name"], "round": rnum, "anchor": a, "stinger": "breaking",
                     "candidate": {"name": ev["name"]}, "media": {"image": image}, "chyron": {"head": ev["name"].upper()[:60], "sub": (b.get("slogan") or ", ".join(b.get("members", [])[:5]))[:90]},
                     "map": {"mode": "conflict", "round": rnum, "focus": (ev.get("affected") or {}).get("states", [None])[0]}, "feed": self.feed_items(rnum, n=4), "market": self.market_at(rnum)}
            if not image:
                scene.pop("media"); scene.pop("candidate")
            sc = self.add(act, scene)
            sc["narration"] = self._narr(sid, (d or {}).get("text") or ev["description"][:220], a["role"], "grave, measured" if net == "fln_world" else "urgent, tense")
            n += 1
        if cf.get("war_room") and not evs:
            b = max(cf["blocs"], key=lambda x: x["stage"])
            pw = b.get("power") or {}
            sid = f"war_{rnum}"
            if sid not in self.scenes:
                facts = (f"{b['name']} ({', '.join(b['members'])}), stage {b['stage_name']}. Recognition: {b.get('counts')}. Power bloc {pw.get('bloc')} vs federal {pw.get('federal')}; "
                         f"Guard with the bloc {pw.get('guard_bloc')}; commands with Washington {pw.get('military_federal')}; trade index {b.get('trade')}; bloc economy {b.get('economy')}; "
                         f"blockades {[x['state'] for x in b.get('blockades', [])]}; contested states {b.get('front')}; foreign support {b.get('foreign_support')}.")
                d = self.narrate(f"War-room briefing, round {rnum}. Facts:\n{facts}\n\nWrite 'text': 40-60 words a world-service anchor says over the strategy map: who holds "
                                 f"what, which way the loyalty and the money are moving, what the next week decides. Politics and logistics only, no combat.")
                a = self.anchor("fln_world")
                sc = self.add(act, {"id": sid, "type": "warroom", "title": "The war room", "round": rnum, "anchor": a,
                                    "chyron": {"head": "WAR ROOM · " + b["name"].upper()[:40], "sub": f"power {pw.get('bloc')} v {pw.get('federal')} · trade {b.get('trade')}"[:90]},
                                    "map": {"mode": "conflict", "round": rnum}, "feed": self.feed_items(rnum, n=4), "market": self.market_at(rnum)})
                sc["narration"] = self._narr(sid, (d or {}).get("text") or facts[:200], a["role"], "dry, measured")
                n += 1
        return n

    def _conflict_epilogue(self, last_round):
        cf = self.tl.get("conflict") or {}
        for b in cf.get("blocs", []):
            if not b.get("resolution"):
                continue
            sid = f"e_cf_{b['id']}"
            d = self.narrate(f"How the breakaway of {b['name']} ended: {b['resolution']}. Terms: {b.get('resolution_text', '')}\nWrite 'text': 45-65 words of epilogue narration "
                             f"on what the settlement means for those states and for the next four years.")
            a = self.anchor("fln_world")
            sc = self.add("epilogue", {"id": sid, "type": "warroom", "title": f"{b['name']}: {b['resolution']}", "round": last_round, "anchor": a,
                                       "chyron": {"head": b["resolution"].upper()[:60], "sub": b["name"][:90]}, "map": {"mode": "conflict", "round": last_round},
                                       "feed": self.feed_items(last_round, n=3), "market": self.market_at(last_round)})
            sc["narration"] = self._narr(sid, (d or {}).get("text") or b.get("resolution_text") or f"{b['name']}: {b['resolution']}.", a["role"], "dry, measured")

    # ---------- scene builders ----------
    def _narr(self, sid, text, role, emotion, voice_id=None):
        url, secs = self.voice(sid, text, role, emotion, voice_id)
        self.scenes[sid]["duration"] = round(secs + 1.0, 1)
        return {"text": text, "audio": url, "voice": role, "emotion": emotion}

    def _montage(self, sid, rnum, act, network="fln_calm"):
        facts = self.round_facts(rnum)
        d = self.narrate(f"Facts for day {rnum}:\n{facts}\n\nWrite 'text': 45-75 words of anchor narration covering what changed today "
                         f"(name states, stages, a sign slogan, a number). Also 'chyron': <= 60 chars.")
        text = (d or {}).get("text") or f"Day {rnum}. " + facts.split(chr(10))[0][:200]
        a = self.anchor(network)
        s = self.add(act, {"id": sid, "type": "montage", "title": f"Day {rnum}", "round": rnum, "anchor": a,
                           "chyron": {"head": f"DAY {rnum}", "sub": (d or {}).get("chyron") or "Unrest spreads"},
                           "map": {"mode": "unrest", "round": rnum}, "feed": self.feed_items(rnum, n=4), "market": self.market_at(rnum)})
        s["narration"] = self._narr(sid, text, a["role"], a["emotion"])

    def _protest_scene(self, sid, rnum, act, only_new=False):
        r = self.round(rnum)
        prev = self.round(rnum - 1)
        cands = []
        for c, s in r["states"].items():
            key = f"{c}_{s['stage']}"
            img = self.tl.get("images", {}).get(key)
            if s["stage"] >= 2 and img and img != "pending":
                if only_new and prev and prev["states"][c]["stage"] >= s["stage"]:
                    continue
                cands.append((s["unrest"], c, s, img))
        if not cands:
            return
        cands.sort(reverse=True)
        _, c, s, img = cands[0]
        rep = f"/api/avatars/reporter_{c.lower()}.png"
        size = {2: "a few hundred", 3: "thousands", 4: "a volatile crowd"}.get(s["stage"], "dozens")
        d = self.narrate(f"A field reporter stands at a {s['stage_name']} in {self.state_names[c]}: {size} of people; signs read: "
                         f"{', '.join(s.get('slogans', [])[:3]) or 'ENOUGH'}; grievance: {s['grievance']}. (Internal unrest index {s['unrest']} of 100, "
                         f"do not quote it.) Write 'text': 35-55 words of on-the-ground reporting, sensory, one quote from a sign, ending 'back to you'.")
        text = (d or {}).get("text") or f"Crowds in {self.state_names[c]} tonight, signs reading {', '.join(s.get('slogans', [])[:2])}. Back to you."
        sc = self.add(act, {"id": sid, "type": "protest", "title": f"{self.state_names[c]}: {s['stage_name']}", "round": rnum,
                            "state": c, "media": {"image": f"/api/runs/{self.run_id}/media/{img}", "reporter": rep},
                            "chyron": {"head": f"LIVE · {self.state_names[c].upper()}", "sub": f"{s['stage_name'].title()} · \"{(s.get('slogans') or ['ENOUGH'])[0]}\""},
                            "map": {"mode": "unrest", "round": rnum, "focus": c}, "market": self.market_at(rnum), "stinger": "breaking" if s["stage"] >= 4 else None})
        from reporters import FIELD_EMOTION, reporter as make_reporter
        rp = make_reporter(c, self.state_names[c])
        sc["narration"] = self._narr(sid, text, rp["voice_role"], FIELD_EMOTION.get(s["stage_name"], "steady"), rp.get("voice_id"))

    def _market_scene(self, sid, rnum, act, final=False):
        m = self.market_at(rnum)
        if not m or not m["entry"]:
            return
        e = m["entry"]
        hist = [h for h in self.market["history"] if h["round"] <= rnum]
        idx0, idx1 = hist[0]["prices"].get("FLN500", 0), e["prices"].get("FLN500", 0)
        moves = ", ".join(f"{sym} {e['pct'][sym]:+.1f}%" for sym in list(e["pct"])[:8])
        d = self.narrate(f"Market desk, {'final' if final else 'day ' + str(rnum)}: {moves}. FLN 500 {idx1:,.0f}, {((idx1/idx0-1)*100) if idx0 else 0:+.1f}% since the start. "
                         f"Headlines: {' | '.join(e.get('headlines', []))}. Rationale: {e.get('rationale', '')}. Write 'text': 35-55 words of calm "
                         f"markets-desk narration naming two tickers and the index.")
        text = (d or {}).get("text") or f"Markets: {moves}."
        a = self.anchor("fln_calm")
        s = self.add(act, {"id": sid, "type": "market", "title": "Markets", "round": rnum, "anchor": a,
                           "chyron": {"head": "MARKETS", "sub": (e.get("headlines") or ["Markets react"])[0][:60]}, "map": {"mode": "unrest", "round": rnum}, "market": m})
        s["narration"] = self._narr(sid, text, "anchor_b", "calm, measured")

    def _polling_scene(self, sid, rnum):
        r = self.round(rnum)
        er = r.get("election") or {}
        names = {o["id"]: o["name"] for o in self.election["options"]}
        lead = {}
        for p in (er.get("polls") or {}).values():
            lead[names.get(p["lean"], p["lean"])] = lead.get(names.get(p["lean"], p["lean"]), 0) + 1
        text_facts = "States leaning: " + (", ".join(f"{k} {v}" for k, v in lead.items()) or "too early") + f"; {50 - sum(lead.values())} undecided."
        d = self.narrate(f"Polling update day {rnum}: {text_facts} Write 'text': 25-40 words of horse-race narration (no results, just leans).")
        a = self.anchor("fln_calm")
        s = self.add("campaign", {"id": sid, "type": "polling", "title": "Polling", "round": rnum, "anchor": a,
                                  "chyron": {"head": "THE STATE OF THE RACE", "sub": text_facts[:60]}, "map": {"mode": "election", "round": rnum},
                                  "market": self.market_at(rnum)})
        s["narration"] = self._narr(sid, (d or {}).get("text") or text_facts, "anchor_b", "calm")

    def _candidate_scene(self, o, rnum):
        handle = f"candidate_{o['id'].lower()}"
        posts = self.feed_items(rnum, authors=[f"npc:{handle}"], n=2)
        d = self.narrate(f"Candidate: {o['name']} ({o.get('party','')}, {o.get('lane','')} lane). Platform: {o.get('blurb','')}. "
                         f"Their posts: {' | '.join(p['text'] for p in posts)}. Write 'text': 35-50 words introducing the candidate for a "
                         f"campaign profile segment, neutral but vivid.")
        text = (d or {}).get("text") or f"{o['name']}, running on: {o.get('blurb', '')[:150]}"
        sid = f"c_{o['id']}"
        s = self.add("campaign", {"id": sid, "type": "candidate", "title": o["name"], "round": rnum, "candidate": o,
                                  "media": {"image": o.get("portrait") or f"/api/avatars/{handle}.png"}, "chyron": {"head": o["name"].upper(), "sub": f"{o.get('party','')} · {((o.get('analysis') or {}).get('short_bio') or o.get('blurb',''))[:50]}"},
                                  "feed": posts, "map": {"mode": "election", "round": rnum}, "market": self.market_at(rnum)})
        s["narration"] = self._narr(sid, text, "anchor_b", "warm, measured")

    def _debate_scene(self, rnum):
        deb = self.debate
        sid = "d_debate"
        names = {o["id"]: o for o in self.election["options"]}
        lines = []
        for i, l in enumerate(deb["lines"]):
            lines.append({"i": i, "speaker": l["speaker"], "text": l["text"], "emotion": l.get("emotion"),
                          "audio": (deb.get("audio") or [None] * len(deb["lines"]))[i] if i < len(deb.get("audio") or []) else None,
                          "poll_shift_for": l.get("poll_shift_for"), "poll_shift": l.get("poll_shift", 0),
                          "seconds": max(2.0, _words(l["text"]) / 2.6)})
        reactions = []
        for r in deb.get("reactions", []):
            ident = next((v for v in self.roster.values() if v["handle"] == r["handle"]), None)
            reactions.append({"after_line": r["after_line"], "handle": r["handle"], "display": ident["display"] if ident else r["handle"],
                              "verified": ident["verified"] if ident else "none", "text": r["text"]})
        dur = sum(l["seconds"] for l in lines) + 3
        self.add("debate", {"id": sid, "type": "debate", "title": "FLN Presidential Debate", "round": rnum, "duration": round(dur, 1),
                            "debate": {"moderator": deb["moderator"], "topics": deb.get("topics", []), "lines": lines, "reactions": reactions,
                                       "candidates": deb.get("candidates", {}), "verdict": deb.get("verdict"), "stage_image": deb.get("stage_image")},
                            "chyron": {"head": "FLN PRESIDENTIAL DEBATE", "sub": f"Moderated by {deb['moderator']}"}, "map": {"mode": "election", "round": rnum},
                            "market": self.market_at(rnum)})
        v = deb.get("verdict") or {}
        if v.get("winner"):
            w = names[v["winner"]]
            d = self.narrate(f"Post-debate verdict: {w['name']} won; bump {v.get('bump', 0)} points; reason: {v.get('reason','')}. Sharpest exchange: "
                             f"{deb.get('recap','')}. Write 'text': 30-45 words of post-debate analysis for the anchor.")
            a = self.anchor("fln_calm")
            s = self.add("debate", {"id": "d_verdict", "type": "anchor", "title": "Who won", "round": rnum, "anchor": a,
                                    "chyron": {"head": f"INSTANT POLL: {w['name'].upper()} WON THE DEBATE", "sub": (v.get("reason") or "")[:60]},
                                    "map": {"mode": "election", "round": rnum}, "feed": self.feed_items(rnum, authors=[k for k, vv in self.roster.items() if vv["kind"] in ("pundit", "influencer")], n=4),
                                    "market": self.market_at(rnum)})
            s["narration"] = self._narr("d_verdict", (d or {}).get("text") or f"{w['name']} won the debate.", "anchor_b", "calm, analytical")

    def _surprise_scene(self, late):
        best = None
        # a scheduled event that fired in these rounds is the October surprise by definition
        self._surprise_event = None
        for rnum in late:
            rd = _read(os.path.join(self.run_dir, "rounds", f"round_{rnum}.json")) or {}
            evs = rd.get("events") or []
            if evs:
                self._surprise_event = (rnum, evs[0])
                break
        for rnum in late:
            r, prev = self.round(rnum), self.round(rnum - 1)
            if not prev:
                continue
            for c, s in r["states"].items():
                jump = s["unrest"] - prev["states"][c]["unrest"]
                score = jump + 12 * (s["stage"] - prev["states"][c]["stage"]) + (20 if s["stage"] >= 4 and prev["states"][c]["stage"] < 4 else 0)
                if best is None or score > best[0]:
                    best = (score, rnum, c, s)
            formed = [b for b in r["blocs"] if b["id"] not in {x["id"] for x in prev["blocs"]}]
            for b in formed:
                if best is None or 30 > best[0]:
                    best = (30, rnum, b["members"][0], None, b)
        rnum = best[1] if best else late[0]
        if self._surprise_event:
            rnum, ev = self._surprise_event
            best = (999, rnum, (ev["affected"].get("states") or [None])[0], None)
        facts = self.round_facts(rnum)
        if self._surprise_event:
            ev = self._surprise_event[1]
            facts = f"SCHEDULED EVENT FIRED: {ev['name']}. {ev['description']} Severity {ev['severity']}." + chr(10) + facts
        d = self.narrate(f"Late in the campaign, day {rnum}. Facts:\n{facts}\n\nWrite the 'October surprise' segment: 'headline' (<= 8 words), "
                         f"'text': 50-75 words of breaking narration about the single biggest shock of these days (a leak, a riot, a bloc breaking "
                         f"away, a market crash: whichever the facts support; invent only framing, not results).")
        a = self.anchor("fln_alarm")
        c = best[2] if best else None
        s = self.add("surprise", {"id": "x_surprise", "type": "anchor", "title": "October Surprise", "round": rnum, "anchor": a, "stinger": "breaking",
                                  "chyron": {"head": ((d or {}).get("headline") or "OCTOBER SURPRISE").upper()[:60], "sub": f"Day {rnum}"},
                                  "map": {"mode": "unrest", "round": rnum, "focus": c}, "feed": self.feed_items(rnum, n=5), "market": self.market_at(rnum)})
        s["narration"] = self._narr("x_surprise", (d or {}).get("text") or f"Day {rnum} brought the shock of the campaign.", "anchor_a", "urgent, alarmed")
        self._protest_scene("p_surprise", rnum, "surprise", only_new=True)

    def _election_night(self, results, night_round, total_steps):
        night = build_night(results, self.election, seed=self.tl["scenario"]["seed"])
        self.night_meta = {"contingent": night["contingent"], "winner_tick": night["winner_tick"]}
        ticks = night["ticks"]
        # open: the anchor sets the table knowing nothing but the map of leans
        a = self.anchor("fln_alarm")
        d = self.narrate("Election night opens. Polls are about to close in the East. Nothing has been counted. Write 'text': 35-50 words of "
                         "election-night opening narration (what to watch, which states close first: Georgia, Indiana, Kentucky, South Carolina, "
                         "Vermont, Virginia), no results.")
        s = self.add("night", {"id": "n_open", "type": "anchor", "title": "Election Night", "round": night_round, "anchor": a, "stinger": "breaking",
                               "chyron": {"head": "ELECTION NIGHT", "sub": "Polls close at 7:00 PM in the East"}, "clock": "6:58 PM ET",
                               "map": {"mode": "night", "tick": None}, "market": self.market_at(night_round - 1)})
        s["narration"] = self._narr("n_open", (d or {}).get("text") or "Election night in America. Polls close in minutes.", "anchor_a", "urgent, excited")
        # pacing: the night is told in beats, not ticks. Narrate the first poll close, the first calls, a leader change, a big jump
        # in the count, a too-close moment, and the projection; drop count-creep ticks that say nothing new; keep the rest as
        # two-second map updates. Narrations are short and never start with the time (the clock is on screen).
        step = 6 + 0
        n_narrated, last_ev, last_leader, first_calls_done = 0, 0, None, False
        MAX_NARR = 7
        for i, tk in enumerate(ticks):
            sid = f"n_{i:02d}"
            is_open = tk["label"].startswith("Polls close")
            total_called = sum(tk.get("ev", {}).values()) if isinstance(tk.get("ev"), dict) else 0
            leader = max(tk["ev"], key=tk["ev"].get) if isinstance(tk.get("ev"), dict) and any(tk["ev"].values()) else None
            why = None
            if tk.get("winner"):
                why = "winner"
            elif tk["new_calls"] and not first_calls_done:
                why = "first calls"
            elif tk["new_calls"] and leader and last_leader and leader != last_leader:
                why = "leader change"
            elif tk["new_calls"] and total_called - last_ev >= 60:
                why = "big jump"
            elif tk["too_close"] and tk["new_calls"]:
                why = "too close"
            elif is_open and i == 0:
                why = "polls close"
            if not (why or tk["new_calls"] or is_open or tk.get("winner")):
                continue          # a count-creep tick with nothing to say
            scene = {"id": sid, "type": "night", "title": tk["label"], "round": night_round, "clock": tk["time"], "night": tk,
                     "map": {"mode": "night", "tick": i}, "market": self.market_at(night_round - 1), "anchor": a,
                     "stinger": "call" if tk["new_calls"] else ("breaking" if tk.get("winner") else None),
                     "chyron": {"head": "ELECTION NIGHT · " + tk["time"], "sub": tk["label"][:60]}}
            self.add("night", scene)
            if why and (n_narrated < MAX_NARR or why == "winner"):
                revealed = revealed_summary(tk, self.election, self.unit_names)
                d = self.narrate(f"ELECTION NIGHT COMMENTARY ({why}). You know ONLY this (nothing has been counted beyond it):\n{revealed}\n\n"
                                 f"Write 'text': 18-32 words the anchor says right now. Do NOT state the time (it is on screen) and do not open with "
                                 f"'It's'. Only use states, numbers and calls in the facts. If a state is 'too close to call', say so. If there is a "
                                 f"PROJECTION line, announce the winner of the presidency.")
                text = (d or {}).get("text") or revealed[:160]
                text = _strip_clock(text)
                emo = "urgent, excited" if tk.get("winner") or tk["new_calls"] else "tense, measured"
                scene["narration"] = self._narr(sid, text, "anchor_a", emo)
                n_narrated += 1
                last_ev = total_called
                last_leader = leader or last_leader
                if tk["new_calls"]:
                    first_calls_done = True
            else:
                scene["duration"] = 2.5 if tk["new_calls"] else 1.8
            if i % 6 == 0:
                self.progress_cb("writing", 6 + step, total_steps)
            if tk.get("winner"):
                break
        last_i = i
        names = {o["id"]: o for o in self.election["options"]}
        if night["winner_tick"] is not None:
            w = names[ticks[night["winner_tick"]]["winner"]]
            d = self.narrate(f"PROJECTION: {w['name']} ({w.get('party','')}) has won the presidency with {night['final_ev'][w['id']]} electoral votes. "
                             f"Write 'text': 40-60 words of the victory announcement for the anchor, then a sentence on what it means for the states.")
            s = self.add("night", {"id": "n_victory", "type": "victory", "title": f"{w['name']} wins", "round": night_round, "anchor": a, "stinger": "breaking",
                                   "candidate": w, "media": {"image": w.get("portrait") or f"/api/avatars/candidate_{w['id'].lower()}.png"},
                                   "chyron": {"head": f"FLN PROJECTS: {w['name'].upper()} ELECTED PRESIDENT", "sub": f"{night['final_ev'][w['id']]} electoral votes"},
                                   "clock": ticks[night["winner_tick"]]["time"], "map": {"mode": "night", "tick": night["winner_tick"]}, "market": self.market_at(night_round - 1)})
            s["narration"] = self._narr("n_victory", (d or {}).get("text") or f"{w['name']} has been elected President.", "anchor_a", "excited, triumphant")
        else:
            hw = names.get(results.get("house_winner")) if results else None
            dl = results.get("delegations") or {} if results else {}
            d = self.narrate("No candidate reached 270 electoral votes. The election goes to the House of Representatives, each state delegation one vote. "
                             + (f"Delegations: {', '.join(names[k]['name'] + ' ' + str(v) for k, v in dl.items())}. The House elects {hw['name']}. " if hw else "")
                             + "Write 'text': 50-70 words for the anchor: the contingent election explained, then the House's choice announced as the next President, and one line on the legitimacy fight to come.")
            s = self.add("night", {"id": "n_contingent", "type": "contingent", "title": "Contingent election", "round": night_round, "anchor": a, "stinger": "breaking",
                                   "chyron": {"head": (f"HOUSE ELECTS {hw['name'].upper()}"[:60] if hw else "NO MAJORITY: ELECTION GOES TO THE HOUSE"), "sub": "No 270: each state delegation casts one vote"},
                                   "clock": ticks[-1]["time"], "map": {"mode": "night", "tick": len(ticks) - 1}, "market": self.market_at(night_round - 1)})
            s["narration"] = self._narr("n_contingent", (d or {}).get("text") or "No one has 270. The House will decide.", "anchor_a", "grave")
        self.night = night

    def _victory_posts(self, night_round):
        cands = [k for k, v in self.roster.items() if v["kind"] == "candidate"]
        posts = self.feed_items(night_round, authors=cands, n=4) + self.feed_items(night_round, kinds=("reply", "post"), n=4)
        if posts:
            self.add("epilogue", {"id": "e_posts", "type": "feed_burst", "title": "Victory and concession", "round": night_round, "duration": 10,
                                  "feed": posts, "map": {"mode": "night", "tick": -1}, "market": self.market_at(night_round)})

    def _teaser(self, last_round):
        r = self.round(last_round)
        facts = self.round_facts(last_round)
        res = self.election.get("results") if self.election else None
        names = {o["id"]: o["name"] for o in self.election["options"]} if self.election else {}
        rs = (f"The election was won by {names.get(res['winner'])} with {res['ev'][res['winner']]} electoral votes." if res and res.get("winner")
              else "No one reached 270; the House decided." if res else "There was no election.")
        d = self.narrate(f"Final state of the simulation:\n{facts}\n{rs}\n\nWrite 'text': 60-90 words of 'one year later' narration: a teaser of "
                         f"where this leads (states, blocs, prices, the new government), plausible from the facts, ending on a hook.")
        a = self.anchor("fln_world")
        s = self.add("epilogue", {"id": "e_teaser", "type": "teaser", "title": "One year later", "round": last_round, "anchor": a,
                                  "chyron": {"head": "ONE YEAR LATER", "sub": "Fault Lines will return"}, "map": {"mode": "unrest", "round": last_round},
                                  "market": self.market_at(last_round)})
        s["narration"] = self._narr("e_teaser", (d or {}).get("text") or "One year later, the country is still arguing.", "anchor_world", "dry, measured")


def build_episode(run_dir, run_id, llm, tts, state_names, progress_cb=None):
    d = Director(run_dir, run_id, llm, tts, state_names, progress_cb)
    ep = d.build()
    tmp = os.path.join(run_dir, "episode.json.tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(ep, f, ensure_ascii=False, indent=1)
    os.replace(tmp, os.path.join(run_dir, "episode.json"))
    return ep
