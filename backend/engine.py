"""
RunManager: creates runs, launches MiroFish's OASIS engine (via sim/runner.py in MiroFish's venv),
scores each round into timeline.json, builds the Feed for the round, and queues media (memes,
scenes, avatars, broadcasts, clips) so the sim never blocks on an image.

Per-run layout:
  rounds/round_N.json   raw posts from the engine
  timeline.json         scores, blocs, images index, spend snapshot
  feed/round_N.json     X-style items, trending, broadcasts for that round
  media/{memes,scenes,broadcast,video}/...
  spend.json            the ledger
Global:
  media/avatars/<handle>.png   cached forever, shared across runs
"""
import hashlib
import json
import os
import random
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime

from blocs import detect_blocs
import conflict as conflict_mod
import tech as tech_mod
import calendar_sim as cal
import phone as phone_mod
import yearfix
import crime as crime_mod
from broadcast import round_stories, plan_slots, round_summary, write_scripts
from election import demo_ballots, election_summary, normalize_election, tally
from candidates import STANCE_MARGIN, analyze_candidates, generate_portraits, novelty, portrait_key, shock_context
from debate import voice_for, write_debate
from govern import QUARTERS
from director import build_episode
from events import events_for_round, parse_events, unfired
from market import policy_signal_from, round_facts as market_facts, setup_market, step_market
from feed import build_round_feed, item_id
from identities import build_roster
from memes import FORMAT_BY_ID, image_prompt as meme_image_prompt, plan_memes
from providers import watermark_png, initials_avatar, parse_json_loose, placeholder_png, stamp_caption
from reporters import FIELD_EMOTION, ROLE_EMOTION, reporter as make_reporter
from scenes import build_prompt as scene_prompt, headline as scene_headline
from scoring import blend, judge_prompt, JUDGE_SYSTEM, normalize_judge, rule_score

FL_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(FL_ROOT, "sim"))
import personas  # noqa: E402

RUNS_DIR = os.path.join(FL_ROOT, "runs")
AVATAR_DIR = os.path.join(FL_ROOT, "media", "avatars")


def _atomic_json(path, obj):
    tmp = path + f".{threading.get_ident()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    for attempt in range(40):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.05 * (attempt + 1))
    os.replace(tmp, path)


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write_bytes(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)


def find_mirofish():
    """MIROFISH_BACKEND if set, else mirofish/backend inside this folder (where the installer puts it), else next to it."""
    env = os.environ.get("MIROFISH_BACKEND")
    if env:
        return env
    inside = os.path.join(FL_ROOT, "mirofish", "backend")
    beside = os.path.join(os.path.dirname(FL_ROOT), "mirofish", "backend")
    return inside if os.path.isdir(inside) or not os.path.isdir(beside) else beside


def mirofish_python(mirofish_backend):
    for c in (os.path.join(mirofish_backend, ".venv", "Scripts", "python.exe"),
              os.path.join(mirofish_backend, ".venv", "bin", "python")):
        if os.path.exists(c):
            return c
    return None


HONORIFICS = r"^(?:senator|sen\.?|governor|gov\.?|representative|rep\.?|congress(?:man|woman)|dr\.?|doctor|mayor|judge|general|gen\.?|admiral|adm\.?|pastor|rev\.?|reverend|secretary|sec\.?|president|mr\.?|mrs\.?|ms\.?)\s+"


def president_name(name):
    """'Senator Elias Vance' -> 'President Elias Vance' once they hold the office (a title that stays wrong for four years grates)."""
    import re
    base = re.sub(HONORIFICS, "", (name or "").strip(), flags=re.I).strip()
    return f"President {base or name}"


class RunManager:
    def __init__(self, cfg, llm, imagegen, tts, video, avatar, spend, logger, media, proxy_base_url):
        self.cfg = cfg
        self.llm = llm
        self.imagegen = imagegen
        self.tts = tts
        self.video = video
        self.avatar = avatar
        self.spend = spend
        self.logger = logger
        self.media = media
        self.proxy_base_url = proxy_base_url
        self.procs, self.threads, self.timelines, self.locks = {}, {}, {}, {}
        self.jobs = {}   # job_id -> {status, note, run_id, error, started}
        self.activity = {}   # run_id -> {"current": str, "t": epoch, "log": [{t, ts, text}]}: what the engine is doing, in plain words
        self._act_lock = threading.Lock()
        self.states = {s["code"]: s for s in personas.load_states()}
        self.state_names = {c: s["name"] for c, s in self.states.items()}
        self.roster = build_roster(self.states)
        self.rosters = {}   # run_id -> roster (election runs add candidate accounts)
        self.mirofish_backend = find_mirofish()
        os.makedirs(RUNS_DIR, exist_ok=True)
        os.makedirs(AVATAR_DIR, exist_ok=True)
        for rid in self.list_runs():
            tl = _read_json(os.path.join(RUNS_DIR, rid, "timeline.json"), {})
            if tl.get("status") in ("running", "starting", "scoring"):
                self._start_scorer(rid)

    def act(self, run_id, text):
        """One line of what is happening right now; the live bar shows the latest, the Activity tab the last 80."""
        with self._act_lock:
            a = self.activity.get(run_id)
            if a is None:
                a = self.activity[run_id] = _read_json(os.path.join(RUNS_DIR, run_id, "activity.json"), None) or {"current": "", "t": 0, "log": []}
            now = time.time()
            a["current"], a["t"] = text, now
            a["log"].append({"t": now, "ts": time.strftime("%H:%M:%S"), "text": text})
            del a["log"][:-80]
            try:
                _atomic_json(os.path.join(RUNS_DIR, run_id, "activity.json"), a)
            except Exception:
                pass

    def activity_of(self, run_id):
        with self._act_lock:
            a = self.activity.get(run_id)
            if a is None:
                a = self.activity[run_id] = _read_json(os.path.join(RUNS_DIR, run_id, "activity.json"), None) or {"current": "", "t": 0, "log": []}
            return {"current": a["current"], "t": a["t"], "log": list(a["log"])}

    # ---------- jobs: a run is set up in a thread so the UI can show what is happening ----------
    def start_job(self, kind, fn):
        jid = uuid.uuid4().hex[:8]
        self.jobs[jid] = {"id": jid, "kind": kind, "status": "working", "note": "starting", "run_id": None, "error": None, "started": time.time()}

        def work():
            try:
                run_id = fn(jid)
                self.jobs[jid].update({"status": "done", "run_id": run_id, "note": "started"})
            except Exception as e:
                import traceback; traceback.print_exc()
                self.jobs[jid].update({"status": "error", "error": f"{type(e).__name__}: {e}"[:300], "note": "failed"})
        threading.Thread(target=work, daemon=True).start()
        return jid

    def note(self, job, text):
        if job and job in self.jobs:
            self.jobs[job]["note"] = text
            self.jobs[job]["since"] = time.time()
        print(f"[setup] {text}")

    # ---------- runs ----------
    def list_runs(self):
        return [d for d in sorted(os.listdir(RUNS_DIR), reverse=True)
                if os.path.isdir(os.path.join(RUNS_DIR, d)) and os.path.exists(os.path.join(RUNS_DIR, d, "scenario.json"))]

    def summary(self, run_id):
        d = os.path.join(RUNS_DIR, run_id)
        sc = _read_json(os.path.join(d, "scenario.json"), {})
        tl = _read_json(os.path.join(d, "timeline.json"), {})
        pr = _read_json(os.path.join(d, "progress.json"), {})
        return {"run_id": run_id, "title": sc.get("title") or sc.get("text", "")[:80], "rounds": sc.get("rounds"),
                "demo": sc.get("demo", False), "status": tl.get("status", "unknown"), "scored_rounds": len(tl.get("rounds", [])),
                "engine": pr, "created_at": sc.get("created_at"), "spend": tl.get("spend", {}).get("total_usd", 0),
                "mode": tl.get("mode", "campaign"), "season": tl.get("season", 1), "parent_run_id": tl.get("parent_run_id"),
                "winner": (lambda el: next((o["name"] for o in el.get("options", []) if o["id"] == ((el.get("results") or {}).get("winner") or (el.get("results") or {}).get("house_winner"))), None))(tl.get("election") or {})}

    def _lock(self, run_id):
        return self.locks.setdefault(run_id, threading.Lock())

    def _save(self, run_id, tl):
        tl["spend"] = self.spend.status(run_id)
        tl["media_pending"] = self.media.pending()
        _atomic_json(os.path.join(RUNS_DIR, run_id, "timeline.json"), tl)

    def timeline(self, run_id):
        tl = self.timelines.get(run_id)
        if tl is not None:
            with self._lock(run_id):
                tl["spend"] = self.spend.status(run_id)
                tl["media_pending"] = self.media.pending()
                return json.loads(json.dumps(tl))
        return _read_json(os.path.join(RUNS_DIR, run_id, "timeline.json"))

    def feed_round(self, run_id, n):
        return _read_json(os.path.join(RUNS_DIR, run_id, "feed", f"round_{n}.json"))

    def run_roster(self, run_id):
        if run_id not in self.rosters:
            self.rosters[run_id] = _read_json(os.path.join(RUNS_DIR, run_id, "roster.json")) or self.roster
        return self.rosters[run_id]

    def create_run(self, text, rounds, agents_per_round=None, demo=False, title=None, seed=None, election=None, explicit=None, govern=None, start=None, job=None):
        caps = self.cfg["caps"]
        rounds = max(1, min(int(rounds), caps["max_rounds"]))
        run_id = datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:4]
        run_dir = os.path.join(RUNS_DIR, run_id)
        for sub in ("rounds", "feed", "media/memes", "media/scenes", "media/broadcast", "media/video"):
            os.makedirs(os.path.join(run_dir, sub), exist_ok=True)
        # the technology clock: a continued season arrives with its rolled-forward state; a new run starts at the seed year
        # the calendar: a campaign counts days from the chosen start; a season counts quarters from the January after the election
        world = self.cfg.get("world", {})
        calendar = (govern or {}).pop("calendar", None) if govern else None
        if calendar is None:
            y, m = (start or (world.get("start_year", 2026), world.get("start_month", 1)))
            calendar = cal.make(int(y), int(m or 1), "day", step_days=cal.pacing(rounds, bool(election)))
        tech_state = (govern or {}).pop("tech_state", None) if govern else None
        if tech_state is None:
            tcfg = self.cfg.get("tech", {})
            tech_state = tech_mod.initial(tcfg.get("pace", "realistic"), year=int(calendar["start"][:4]), flags=tcfg.get("flags") or [])
        tech_fx = tech_mod.effects(tech_state)
        date_line = cal.prompt_line(calendar, 1)
        election = normalize_election(election)
        llm_ok = self.llm.available()
        if election:
            election["debate_round"] = max(1, rounds - 1) if self.cfg["broadcast"].get("debate", True) and rounds >= 2 else None
            # CANDIDATE ANALYSIS: every candidate reads its own bio (entity type, appearance, novelty) before anything else uses it
            self.note(job, "Reading the candidates' bios and casting their voices")
            analyze_candidates(self.llm if llm_ok else None, run_id, election, text)
            for o in election["options"]:
                o["portrait"] = o.get("portrait") or f"/api/runs/{run_id}/media/candidates/{portrait_key(o)}"
            nov, who = novelty(election)
            election["novelty"] = {"score": nov, "candidate": who["id"], "shock": shock_context(election)}
        # the economy first, so scheduled events can name this run's tickers
        self.note(job, "Setting up the market: tickers for this scenario")
        try:
            mk = setup_market(self.llm if llm_ok else None, run_id, text, election)
        except Exception as e:
            print(f"[market] setup error: {e}")
            mk = setup_market(None, run_id, text, election)
        # the technology clock's companies trade on this run's market
        have = {t["sym"] for t in mk["tickers"]}
        for c in tech_fx.get("companies", []):
            if c["sym"] not in have:
                mk["tickers"].append({"sym": c["sym"], "name": c["name"], "sector": c["sector"], "price": float(c["price"]), "beta_unrest": -0.6, "beta_policy": 0.4,
                                      "note": f"{c['name']}: a company of the {tech_state['era']}."})
                mk["history"][0]["prices"][c["sym"]] = float(c["price"]) if mk.get("history") else None
                if mk.get("history"):
                    mk["history"][0]["pct"][c["sym"]] = 0.0
                have.add(c["sym"])
        _atomic_json(os.path.join(run_dir, "market.json"), mk)
        _atomic_json(os.path.join(run_dir, "tech.json"), tech_state)
        # the crime layer reads the scenario's conditions once (unemployment, police funding, bans, leaked models)
        try:
            crime_state = crime_mod.load({})
            crime_state["scenario_mods"] = crime_mod.scenario_mods(self.llm if llm_ok else None, run_id, text)
            print(f"[crime] scenario mods: {crime_state['scenario_mods']}")
            _atomic_json(os.path.join(run_dir, "crime.json"), crime_state)
        except Exception as e:
            print(f"[crime] scenario mods error: {e}")
        self.note(job, "Scheduling the scenario's events")
        events = parse_events(self.llm if llm_ok else None, run_id, text, election, mk["tickers"], rounds, explicit, FL_ROOT)
        scenario = {"text": text, "rounds": rounds, "agents_per_round": agents_per_round, "demo": bool(demo), "calendar": calendar,
                    "tech": {"year": tech_state["year"], "era": tech_state["era"], "pace": tech_state["pace"],
                             "context": date_line + " " + (tech_mod.context_text(tech_state) if tech_state.get("log") else f"THE YEAR IS {calendar['start'][:4]}. Speak and argue as people living in {calendar['start'][:4]}; earlier years are the past."),
                             "effects": tech_fx, "timeline": tech_mod.timeline_view(tech_state)},
                    "title": title or text.strip().split("\n")[0][:80], "seed": seed if seed is not None else random.randint(1, 10**6),
                    "election": election, "events": events, "created_at": datetime.now().isoformat(timespec="seconds"),
                    "govern": govern}
        _atomic_json(os.path.join(run_dir, "scenario.json"), scenario)
        for ev in events:
            print(f"[events] scheduled: round {ev['round']} · {ev['name']} (severity {ev['severity']}, poll hit {ev['poll_hit']}, stocks {ev['stock_hits']})")
        self.note(job, "Writing the fifty state personas")
        cfg, _ = personas.materialize(run_dir, scenario, dict(self.cfg["sim"]))
        assert len(cfg["agent_configs"]) <= caps["max_agents"], "max_agents cap exceeded"
        roster = build_roster(self.states, election)
        self.rosters[run_id] = roster
        _atomic_json(os.path.join(run_dir, "roster.json"), roster)
        if election and self.imagegen.provider != "placeholder":
            # bio-driven portraits, generated now so round 1 already has them; the same file is used everywhere
            self.media.submit(lambda: generate_portraits(self.imagegen, run_dir, run_id, election), f"portraits {run_id}")
        tl = {"run_id": run_id, "scenario": scenario, "status": "starting", "rounds": [], "bloc_registry": {},
              "images": {}, "image_notes": {}, "config": self.cfg["scoring"], "judge_enabled": (not demo) and llm_ok,
              "llm_enabled": llm_ok, "llm_provider": self.llm.provider, "agent_model": self.llm.agent_model,
              "image_provider": self.imagegen.provider, "media_flags": self.cfg["media"], "created_at": scenario["created_at"],
              "riot_seen": [], "spend": self.spend.status(run_id),
              "mode": "govern" if govern else "campaign", "season": (govern or {}).get("season", 1), "parent_run_id": (govern or {}).get("parent_run_id"),
              "year": tech_state["year"], "era": tech_state["era"], "tech": tech_mod.timeline_view(tech_state),
              "election": ({**election, "results": None, "ballot_round": rounds + 1} if election else None)}
        self.timelines[run_id] = tl
        self._save(run_id, tl)
        self.note(job, "Launching the agents; the first posts take about a minute")
        if demo:
            threading.Thread(target=self._demo_producer, args=(run_id,), daemon=True).start()
        else:
            self._spawn_engine(run_id, run_dir)
        self._start_scorer(run_id)
        return run_id

    # ---------- CONTINUE: a governing season (one term, a round per quarter) grown from a finished election run ----------
    def continue_run(self, parent_id, twist="", quarters=QUARTERS, agents_per_round=None, job=None):
        from govern import (history_book, build_policies, congress_from_election, init_state, generate_challengers, inauguration)
        ptl = self.timeline(parent_id)
        if not ptl or ptl.get("status") not in ("done", "error"):
            raise ValueError("the parent run has not finished")
        el = ptl.get("election") or {}
        res = el.get("results") or {}
        wid = res.get("winner") or res.get("house_winner")
        if not wid:
            raise ValueError("the parent run has no elected winner to inaugurate")
        winner = next(o for o in el["options"] if o["id"] == wid)
        by_house = not res.get("winner")
        pdir = os.path.join(RUNS_DIR, parent_id)
        pmk = _read_json(os.path.join(pdir, "market.json")) or {}
        headlines = []
        for r in ptl["rounds"]:
            f = self.feed_round(parent_id, r["round"]) or {}
            headlines += [f"{b.get('network_name', 'FLN')}: {b.get('headline', '')}" for b in (f.get("broadcasts") or [])]
        llm = self.llm if self.llm.available() else None
        season = int(ptl.get("season") or 1) + 1
        self.note(job, "Writing the history book of the season just played")
        hist = history_book(llm, parent_id, {"timeline": ptl, "market": pmk, "broadcast_headlines": headlines,
                                             "conflict": conflict_mod.summary_lines(_read_json(os.path.join(pdir, "conflict.json")) or {})})
        hist["season"] = season - 1
        # the technology clock: four years pass; milestones unlock by rule, the LLM writes the headlines
        tech_state = _read_json(os.path.join(pdir, "tech.json")) or tech_mod.initial(self.cfg.get("tech", {}).get("pace", "realistic"))
        self.note(job, "Rolling the technology clock forward four years")
        new_tiers = tech_mod.advance(tech_state, 4, parent={"timeline": ptl}, pace=self.cfg.get("tech", {}).get("pace"))
        heads, segment = tech_mod.flavor(llm, parent_id, tech_state, new_tiers, 4)
        tech_state["headlines"], tech_state["segment"] = heads, segment
        hist["tech"] = {"year": tech_state["year"], "era": tech_state["era"], "unlocked": [n["name"] for n in new_tiers], "headlines": heads}
        self.note(job, "Turning the winner's platform into policies")
        policies = build_policies(llm, parent_id, winner, hist, pmk.get("tickers") or [])
        used = []
        pid = parent_id
        for _ in range(12):   # every candidate name back through the chain of seasons
            t = self.timeline(pid) or {}
            used += [o["name"] for o in ((t.get("election") or {}).get("options") or [])]
            pid = t.get("parent_run_id")
            if not pid:
                break
        self.note(job, "Inventing the challengers")
        challengers = generate_challengers(llm, parent_id, winner, hist, None, used_names=sorted(set(used)))
        self.note(job, "Writing the inauguration speech")
        speech = inauguration(llm, parent_id, winner, hist, policies)
        inc = {k: winner.get(k) for k in ("name", "party", "lane", "blurb", "voice", "analysis", "portrait", "voice_id", "voice_cast")}
        inc["incumbent"] = True
        pres_name = president_name(winner["name"])
        inc["name"] = pres_name
        inc["blurb"] = (f"INCUMBENT PRESIDENT, running for a second term on the record of the first. Original platform: {winner.get('blurb', '')}")[:3000]
        election2 = {"question": f"Should {pres_name} get a second term?", "options": [inc] + challengers}
        pol_lines = "; ".join(f"{p['name']} ({p['summary']})" for p in policies)
        text = (f"SEASON {season}: THE {pres_name.upper()} PRESIDENCY. IT IS JANUARY {tech_state['year']} ({tech_state['era']}); each round is one quarter from here.\n\n"
                + tech_mod.context_text(tech_state) + "\nSince last season: " + " ".join(heads) + f"\n\nWHAT HAPPENED LAST SEASON: {hist['summary']}\n\nKEY FACTS: "
                + "; ".join(hist["key_facts"]) + f"\n\n{winner['name']} ({winner.get('party', '')}), from now on {pres_name}, has been inaugurated as President of the United "
                f"States after " + (f"a contingent election: no one reached 270 electoral votes ({winner['name']} had {res['ev'].get(winner['id'], '?')}) and the House of Representatives, voting by state delegation, chose them, so the presidency begins under a cloud of illegitimacy" if by_house else f"winning {res['ev'].get(winner['id'], '?')} electoral votes") + f". Each round of this simulation is ONE QUARTER of the "
                f"presidential term (round 1 = Q1 of year 1, round 16 = Q4 of year 4), and the term ends with the next presidential election. "
                f"The President's agenda: {pol_lines}. Congress votes on one policy a quarter; the wire reports each quarter's vote, the "
                f"policies' effects as they arrive, and the events of the quarter.\n\nINAUGURATION SPEECH (excerpt): {speech[:700]}"
                + (f"\n\nTWIST FOR THIS SEASON: {twist}" if twist else ""))
        pcal = ptl["scenario"].get("calendar") or cal.make(int(tech_state["year"]) - 4, 1, "day")
        calendar2 = cal.next_season_start(pcal, ptl["scenario"]["rounds"] + 1)
        tech_state["year"] = int(calendar2["start"][:4])
        govern = {"season": season, "parent_run_id": parent_id, "calendar": calendar2, "president": {"id": "A", "name": pres_name, "party": winner.get("party", ""),
                  "lane": winner.get("lane") or "center", "portrait": winner.get("portrait"), "voice_id": winner.get("voice_id"), "voice": winner.get("voice")},
                  "history": hist, "inauguration": speech, "approval_start": round(float((res.get("popular") or {}).get(winner["id"], 50)), 1), "by_house": by_house,
                  "tech_state": tech_state}
        # milestones unlocked over the four years become round-1 events: banner, wire, feed, a desk lead
        explicit = [{"round": 1, "name": f"Milestone: {n['name']}"[:60], "description": f"{n['track']}: {n['name']} arrived in {n['year']}. " + (heads[i] if i < len(heads) else ""),
                     "severity": 2, "poll_hit": 0, "stock_hits": {c["sym"]: 6.0 for c in n.get("companies", [])}} for i, n in enumerate(new_tiers[:3])] or None
        run_id = self.create_run(text, quarters, agents_per_round, False, f"Season {season}: {pres_name.title()}", None, election2,
                                 explicit, govern=govern, job=job)
        run_dir = os.path.join(RUNS_DIR, run_id)
        personas_map = _read_json(os.path.join(run_dir, "personas.json")) or {}
        rng = random.Random(hash(run_id) & 0xffff)
        congress = congress_from_election(el, res, rng)
        tl = self.timelines[run_id]
        winner2 = next(o for o in tl["election"]["options"] if o["id"] == "A")
        winner2["analysis"] = winner2.get("analysis") or winner.get("analysis") or {}
        state = init_state(el, res, winner2, personas_map, policies, congress, hist, [])
        if by_house:
            for code in state["approval"]["states"]:
                state["approval"]["states"][code] = round(max(5, state["approval"]["states"][code] - 6), 1)
            state["approval"]["national"] = round(sum(state["approval"]["states"].values()) / max(1, len(state["approval"]["states"])), 1)
        state["president"] = govern["president"]
        state["season"] = season
        _atomic_json(os.path.join(run_dir, "govern.json"), state)
        with self._lock(run_id):
            tl["govern"] = self._govern_view(state)
            tl["history"] = hist
            self._save(run_id, tl)
        print(f"[govern] season {season} started as {run_id}: {len(policies)} policies, congress {congress}")
        return run_id

    def _govern_view(self, st):
        """The compact governing state the UI reads from the timeline."""
        return {"season": st.get("season"), "president": st.get("president"), "quarter": st["quarter"], "approval": st["approval"],
                "approval_series": st["approval_series"], "congress": st["congress"],
                "policies": [{k: p[k] for k in ("id", "name", "summary", "cost_bn", "status", "vote_quarter", "history", "lag_quarters")} | {"unintended": p["unintended"]["description"]} for p in st["policies"]],
                "quarters": st.get("quarters", []), "midterm": st.get("midterm"), "executive_orders": st.get("executive_orders", []),
                "jobs": st.get("jobs"), "prices_index": st.get("prices_index"), "recap": st.get("recap")}

    def _govern_quarter(self, run_id, run_dir, tl, rd, states, prev, personas_map):
        """One quarter of governing: policies voted and applied, events drawn, approval moved; the results feed the round."""
        from govern import step_quarter, quarter_label, respond, update_challengers, apply_effects
        self.act(run_id, f"quarter {rd['round']}: Congress votes, policies land, approval moves")
        st = _read_json(os.path.join(run_dir, "govern.json"))
        if not st:
            return None
        rnum = rd["round"]
        if rnum < 1 or rnum > QUARTERS or st.get("quarter", 0) >= rnum:
            return None
        pres = next((o for o in tl["election"]["options"] if o["id"] == "A"), None) or {"name": st["president"]["name"]}
        rng = random.Random(tl["scenario"]["seed"] * 31 + rnum)
        sched = []
        for e in rd.get("events") or []:
            sev = int(e.get("severity", 3))
            sched.append({"id": e["id"], "name": e["name"], "description": e.get("description", ""), "severity": sev, "kind": "scheduled",
                          "effects": {"approval": -sev, "unrest": 2 * sev, "market": -2 * sev}, "states": list((e.get("affected") or {}).get("states") or []),
                          "address": sev >= 3, "image": (f"news photograph of {e['name']}: {e.get('description', '')[:160]}" if sev >= 3 else None)})
        out = step_quarter(st, rnum, states, prev, personas_map, pres, rng, scheduled_events=sched)
        crv = (tl.get("crime") or {}).get("national") or []
        if len(crv) >= 4 and crv[-1]["index"] - crv[-4]["index"] >= 6:
            out["effects"].append(apply_effects({"approval": -1.5}, 1.0, st, personas_map, pres, rng, "crime trend: rising (the incumbent is blamed)"))
        llm = self.llm if tl.get("llm_enabled") else None
        # the President answers the quarter's biggest event (one LLM call), in character
        if out["events"]:
            ev = max(out["events"], key=lambda e: e.get("severity", 0))
            ev["response"] = respond(llm, run_id, pres, ev, st)
        # unrest: the quarter's net effect lands on every state (policies calm or inflame, events inflame), harder on the states an event hit
        scfg = self.cfg["scoring"]
        per_state = out.get("unrest_states") or {}
        if out["unrest_delta"] or per_state:
            for code, s in states.items():
                s["unrest"] = round(max(0, min(100, s["unrest"] + out["unrest_delta"] * 0.6 + per_state.get(code, 0))), 1)
                s["stage"] = min(4, sum(1 for t in scfg["stage_thresholds"] if s["unrest"] >= t))
        # the quarter's events and vote become this round's events: banner, wire, feed burst, broadcast lead, market hits all follow
        mk = _read_json(os.path.join(run_dir, "market.json")) or {}
        syms = [t["sym"] for t in mk.get("tickers", [])]
        hits = {}
        for sym, pct in (out["market_hits"] or {}).items():
            if sym == "FLN500":
                for t in syms:
                    hits[t] = round(hits.get(t, 0) + pct * 0.7, 1)
            elif sym in syms:
                hits[sym] = round(hits.get(sym, 0) + pct, 1)
        rd.setdefault("events", [])
        existing = {e["id"]: e for e in rd["events"]}
        for i, ev in enumerate(out["events"]):
            eid = ev.get("id") or f"q{rnum}_{i}"
            desc = (ev.get("description", "") + (f" The President responded: \"{ev['response']['statement']}\"" if ev.get("response") else ""))[:700]
            if eid in existing:   # a scheduled event the runner already posted: keep its wire text, add the response and the picture
                existing[eid]["description"] = desc
                existing[eid]["image_key"] = f"event_{eid}" if ev.get("image") else None
                existing[eid]["quarter"] = out["label"]
            else:
                rd["events"].append({"id": eid, "round": rnum, "name": ev["name"], "severity": ev.get("severity", 2), "description": desc,
                                     "affected": {"candidates": [], "companies": [], "states": list(ev.get("states") or [])}, "poll_hit": 0, "stock_hits": hits if i == 0 else {},
                                     "unrest_bump": 0, "fired": True, "quarter": out["label"], "image_key": f"event_{eid}" if ev.get("image") else None})
            # the picture: damage, the crowd, the ship. Then the address to the nation, in the President's own voice.
            if ev.get("image") and self.cfg["media"].get("images") and self.imagegen.provider != "placeholder":
                tl["images"][f"event_{eid}"] = "pending"
                self.media.submit(lambda e=dict(ev), r=rnum, k=eid: self._event_scene_job(run_id, run_dir, r, k, e), f"event picture {eid}")
            if ev.get("address") and ev.get("response") and self.tts.available:
                self.media.submit(lambda e=dict(ev), r=rnum, k=eid: self._address_job(run_id, run_dir, r, k, e, pres), f"address {eid}")
        if out["vote"]:
            v = out["vote"]
            verb = {"passed": "passes", "watered": "passes a watered-down", "failed": "rejects", "executive_order": "rejects"}.get(v["result"], v["result"])
            label = {"passed": "passed", "watered": "passed, watered down", "failed": "rejected", "executive_order": "rejected; signed as an executive order"}.get(v["result"], v["result"])
            rd["events"].append({"id": f"q{rnum}_vote", "round": rnum, "name": f"{v['name']}: {label}"[:90], "severity": 2,
                                 "description": (f"{out['label']}: Congress {verb} {v['name']} (${v['cost_bn']:.0f}bn a year). "
                                                 + ("The President signs it as an executive order instead." if v["result"] == "executive_order" else ""))[:500],
                                 "affected": {"candidates": [], "companies": [], "states": []}, "poll_hit": 0, "stock_hits": {} if out["events"] else hits,
                                 "unrest_bump": 0, "fired": True, "quarter": out["label"], "vote": v})
        if out.get("midterm"):
            c = out["midterm"]["congress"]
            rd["events"].append({"id": f"q{rnum}_midterm", "round": rnum, "name": "Midterm elections", "severity": 3,
                                 "description": f"MIDTERMS: the new House is " + ", ".join(f"{l} {n}" for l, n in c["house"].items()) + "; the Senate " + ", ".join(f"{l} {n}" for l, n in c["senate"].items()) + f". Swing {out['midterm']['swing']:+.0f} for the President's side.",
                                 "affected": {"candidates": [], "companies": [], "states": []}, "poll_hit": 0, "stock_hits": {}, "unrest_bump": 0, "fired": True, "quarter": out["label"]})
        out["headline"] = cal.label(tl["scenario"].get("calendar") or cal.make(2026, 1, "quarter"), rnum) + ". " + out["headline"]
        st.setdefault("quarters", []).append({"quarter": rnum, "label": out["label"], "headline": out["headline"], "approval": out["approval"]["national"],
                                              "vote": out["vote"], "events": [{"name": e["name"], "response": (e.get("response") or {}).get("statement")} for e in out["events"]],
                                              "effects": [{"tag": e["tag"], "applied": e["applied"]} for e in out["effects"]], "struck_down": out["struck_down"]})
        # year 4 opens: the challengers' platforms are rewritten against the record (the ballot reads scenario.json at election day)
        if rnum == 13 and llm is not None:
            try:
                update_challengers(llm, run_id, tl["election"], pres, st)
                sc = _read_json(os.path.join(run_dir, "scenario.json")) or {}
                sc["election"] = tl["election"]
                _atomic_json(os.path.join(run_dir, "scenario.json"), sc)
            except Exception as e:
                print(f"[govern] challenger update failed: {e}")
        _atomic_json(os.path.join(run_dir, "govern.json"), st)
        tl["govern"] = self._govern_view(st)
        return out

    def _event_scene_job(self, run_id, run_dir, rnum, eid, ev):
        prompt = f"{ev['image']}. Photojournalism, natural light, no text, no logos, no real people."
        png = self.imagegen.generate(prompt, kind="images", run_id=run_id, aspect=self.cfg["images"]["aspect_scene"])
        note = self.imagegen.provider
        if not png:
            png = placeholder_png(ev["name"], (1024, 576), seed=hash(eid), kind="scene")
            note = "placeholder"
        if ev.get("watermark") and self.cfg["images"].get("watermark"):   # off by default; set images.watermark to stamp crime pictures
            png = watermark_png(png, str(self.cfg["images"]["watermark"]))
        rel = f"events/{eid}.png"
        _write_bytes(os.path.join(run_dir, "media", rel), png)
        tl = self.timelines.get(run_id)
        with self._lock(run_id):
            tl["images"][f"event_{eid}"] = rel
            tl["image_notes"][f"event_{eid}"] = note
            self._save(run_id, tl)
        states = ev.get("states") or []
        author = f"news:{states[0]}" if states and f"news:{states[0]}" in self.run_roster(run_id) else "wire"
        self._append_item(run_id, rnum, {"id": item_id(rnum, "ev", eid), "round": rnum, "author": author, "kind": "scene",
                                         "text": f"BREAKING: {ev['name']}. {ev.get('description', '')[:220]}", "state": states[0] if states else None,
                                         "likes": random.randint(20000, 120000), "reposts": random.randint(6000, 40000), "replies": random.randint(2000, 15000),
                                         "ts": cal.ts(self.timelines[run_id]["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum, 11),
                                         "media": {"type": "image", "url": self._media_url(run_id, rel), "status": "ready", "note": note},
                                         "quote_of": None, "reply_to": None, "thread": []})

    def _address_job(self, run_id, run_dir, rnum, eid, ev, pres):
        """The President addresses the nation about an event: voiced in the cast voice, posted to the Feed, kept for the episode."""
        text = ev["response"]["statement"]
        is_ai = (pres.get("analysis") or {}).get("entity_type") == "AI"
        intro = f"My fellow Americans. " if not text.lower().startswith("my fellow") else ""
        mp3 = self.tts.speak(intro + text, role=f"{pres['id']}_{'m' if is_ai else (pres.get('voice') or 'm')[:1]}", kind="tts", run_id=run_id,
                             voice_id=voice_for(pres, "m" if is_ai else pres.get("voice"), self.tts.provider),
                             emotion="calm, synthetic, evenly paced" if is_ai else "solemn, steady, resolute")
        if not mp3:
            return
        rel = f"address/{eid}.mp3"
        _write_bytes(os.path.join(run_dir, "media", rel), mp3)
        url = self._media_url(run_id, rel)
        secs = round(len(mp3) * 8 / 128000.0, 1)
        self._append_item(run_id, rnum, {"id": item_id(rnum, "addr", eid), "round": rnum, "author": "npc:candidate_a", "kind": "address",
                                         "text": f"Address to the nation · {ev['name']}: \"{text[:300]}\"", "state": None,
                                         "likes": random.randint(80000, 400000), "reposts": random.randint(20000, 90000), "replies": random.randint(8000, 50000),
                                         "ts": cal.ts(self.timelines[run_id]["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum, 20),
                                         "media": {"type": "audio", "url": url, "status": "ready", "poster": pres.get("portrait") or "/api/avatars/candidate_a.png"},
                                         "quote_of": None, "reply_to": None, "thread": []})
        tl = self.timelines.get(run_id)
        # the quarter is written to govern.json and the timeline after this job was queued: wait for both before recording the address
        t0 = time.time()
        while time.time() - t0 < 240:
            st = _read_json(os.path.join(run_dir, "govern.json")) or {}
            if any(q["quarter"] == rnum for q in st.get("quarters", [])) and any(r["round"] == rnum and r.get("quarter") for r in tl["rounds"]):
                break
            time.sleep(1.5)
        with self._lock(run_id):
            st = _read_json(os.path.join(run_dir, "govern.json")) or {}
            for q in st.get("quarters", []):
                if q["quarter"] == rnum:
                    q["address"] = {"event": ev["name"], "text": text, "audio": url, "seconds": secs}
            _atomic_json(os.path.join(run_dir, "govern.json"), st)
            for r in tl["rounds"]:
                if r["round"] == rnum and r.get("quarter"):
                    r["quarter"]["address"] = {"event": ev["name"], "text": text, "audio": url, "seconds": secs}
            tl["govern"] = self._govern_view(st)
            self._save(run_id, tl)

    # ---------- THE PHONE ----------
    def _phone_state(self, run_id, run_dir, tl):
        ppath = os.path.join(run_dir, "phone.json")
        st = phone_mod.load(_read_json(ppath))
        cst = _read_json(os.path.join(run_dir, "conflict.json")) or {}
        pm = _read_json(os.path.join(run_dir, "personas.json")) or {}
        phone_mod.ensure_contacts(st, phone_mod.build_contacts(tl, self.run_roster(run_id), pm, cst, self.cfg["broadcast"]["networks"], run_id,
                                                               crime_state=_read_json(os.path.join(run_dir, "crime.json")) or {}))
        return st, ppath

    def _phone_world(self, run_id, run_dir, tl):
        rnum = tl["rounds"][-1]["round"] if tl.get("rounds") else 0
        calx = tl["scenario"].get("calendar") or cal.make(2026, 1, "day")
        lines = conflict_mod.summary_lines(_read_json(os.path.join(run_dir, "conflict.json")) or {}) if (tl.get("conflict") or {}).get("active") else []
        return phone_mod.world_lines(tl, cal.label(calx, rnum), (tl["scenario"].get("tech") or {}).get("context"), lines), cal.label(calx, rnum).rsplit(" ", 1)[0].rstrip(",").split(" ")[0] + " " + cal.label(calx, rnum).rsplit(" ", 1)[-1], rnum

    def phone_summary(self, run_id):
        tl = self.timeline(run_id)
        if not tl:
            return None
        run_dir = os.path.join(RUNS_DIR, run_id)
        with self._lock(run_id):
            st, ppath = self._phone_state(run_id, run_dir, tl)
            _atomic_json(ppath, st)
        return {"contacts": phone_mod.summary(st), "unread": sum(c.get("unread", 0) for c in st["contacts"].values()), "log": st["log"][-30:]}

    def phone_thread(self, run_id, cid, mark_read=True):
        tl = self.timeline(run_id)
        run_dir = os.path.join(RUNS_DIR, run_id)
        with self._lock(run_id):
            st, ppath = self._phone_state(run_id, run_dir, tl)
            c = st["contacts"].get(cid)
            if not c:
                return None
            if mark_read:
                c["unread"] = 0
            _atomic_json(ppath, st)
        return {"id": cid, "name": c["name"], "kind": c["kind"], "avatar": c["avatar"], "tag": c.get("tag"), "trust": c["trust"], "thread": c["thread"][-60:]}

    def phone_send(self, run_id, cid, text):
        tl = self.timeline(run_id)
        run_dir = os.path.join(RUNS_DIR, run_id)
        with self._lock(run_id):
            st, ppath = self._phone_state(run_id, run_dir, tl)
            if cid not in st["contacts"]:
                return None
            world, month_label, rnum = self._phone_world(run_id, run_dir, tl)
        llm = self.llm if tl.get("llm_enabled") else None
        out = phone_mod.reply(llm, run_id, st, cid, text, rnum, month_label, world, random.Random())
        with self._lock(run_id):
            _atomic_json(ppath, st)
        c = st["contacts"][cid]
        return {"reply": out["reply"], "mood": out["mood"], "trust": c["trust"], "thread": c["thread"][-60:], "date": month_label}

    def _phone_round_end(self, run_id, run_dir, tl, rd, feed):
        ppath = os.path.join(run_dir, "phone.json")
        if not os.path.exists(ppath):
            return
        with self._lock(run_id):
            st, _ = self._phone_state(run_id, run_dir, tl)
        if not any(c["thread"] for c in st["contacts"].values()):
            return
        world, month_label, rnum = self._phone_world(run_id, run_dir, tl)
        gpath, cpath = os.path.join(run_dir, "govern.json"), os.path.join(run_dir, "conflict.json")
        gst, cst = _read_json(gpath), _read_json(cpath)
        llm = self.llm if tl.get("llm_enabled") else None
        res = phone_mod.round_end(llm, run_id, st, tl, rd["round"], month_label, world, rd.get("events") or [], random.Random(tl["scenario"]["seed"] * 13 + rd["round"]),
                                  tl.get("mode"), govern_state=gst, conflict_state=cst)
        with self._lock(run_id):
            _atomic_json(ppath, st)
            if gst is not None:
                _atomic_json(gpath, gst)
            if cst is not None:
                _atomic_json(cpath, cst)
            tl["phone"] = {"unread": sum(c.get("unread", 0) for c in st["contacts"].values()), "log_tail": st["log"][-8:]}
        for d in res["decisions"]:
            if d.get("action") not in (None, "none"):
                print(f"[phone] round {rd['round']}: {d['contact']} -> {d['action']}: {d.get('reason', '')[:80]}")
        if res["exposed"]:
            print(f"[phone] round {rd['round']}: the insider's meddling was discovered")

    def _govern_finish(self, run_id, run_dir, tl):
        from govern import season_recap
        st = _read_json(os.path.join(run_dir, "govern.json"))
        if not st or st.get("recap"):
            return
        pres = next((o for o in tl["election"]["options"] if o["id"] == "A"), {"name": st["president"]["name"]})
        st["recap"] = season_recap(st, pres, _read_json(os.path.join(run_dir, "market.json")))
        res = (tl.get("election") or {}).get("results") or {}
        st["recap"]["reelected"] = res.get("winner") == "A" if res else None
        _atomic_json(os.path.join(run_dir, "govern.json"), st)
        tl["govern"] = self._govern_view(st)

    def stop_run(self, run_id):
        open(os.path.join(RUNS_DIR, run_id, "STOP"), "w").close()
        p = self.procs.get(run_id)
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
        return True

    # ---------- engine subprocess ----------
    def _spawn_engine(self, run_id, run_dir):
        py = mirofish_python(self.mirofish_backend)
        if not py:
            raise RuntimeError(f"MiroFish venv not found under {self.mirofish_backend}; run `uv sync` there")
        base = self.proxy_base_url.replace("/llm/v1", f"/llm/{run_id}/v1")
        env = os.environ.copy()
        env.update({"MIROFISH_BACKEND": self.mirofish_backend, "LLM_API_KEY": "proxy", "OPENAI_API_KEY": "proxy",
                    "LLM_BASE_URL": base, "OPENAI_API_BASE_URL": base, "LLM_MODEL_NAME": self.llm.agent_model,
                    "MODEL_TIMEOUT": str(self.cfg["llm"]["model_timeout_s"]), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        log = open(os.path.join(run_dir, "runner.log"), "w", encoding="utf-8")
        self.procs[run_id] = subprocess.Popen([py, os.path.join(FL_ROOT, "sim", "runner.py"), "--run-dir", run_dir],
                                              cwd=run_dir, stdout=log, stderr=subprocess.STDOUT, env=env)
        self.act(run_id, "agent process launched: loading the model and the fifty state agents")

    # ---------- scoring loop ----------
    def _start_scorer(self, run_id):
        if run_id in self.threads and self.threads[run_id].is_alive():
            return
        t = threading.Thread(target=self._score_loop, args=(run_id,), daemon=True)
        self.threads[run_id] = t
        t.start()

    def _score_loop(self, run_id):
        run_dir = os.path.join(RUNS_DIR, run_id)
        if run_id not in self.timelines:
            self.timelines[run_id] = _read_json(os.path.join(run_dir, "timeline.json"))
        tl = self.timelines[run_id]
        lock = self._lock(run_id)
        personas_map = _read_json(os.path.join(run_dir, "personas.json"))
        next_round, idle = len(tl["rounds"]), 0
        while True:
            rp = os.path.join(run_dir, "rounds", f"round_{next_round}.json")
            if os.path.exists(rp):
                rd = _read_json(rp)
                if rd is None:
                    time.sleep(0.5)
                    continue
                self.act(run_id, f"round {next_round}: the posts are in ({rd.get('posts_count', len(rd.get('posts', [])))}); scoring the states" if next_round else "seed round: setting the baseline")
                with lock:
                    try:
                        self._score_round(run_id, run_dir, tl, personas_map, rd)
                    except Exception as e:
                        import traceback; traceback.print_exc()
                        tl.setdefault("errors", []).append(f"round {next_round}: {type(e).__name__}: {e}")
                        self.act(run_id, f"round {next_round}: scoring failed ({type(e).__name__}); moving on")
                    tl["status"] = "running"
                    tl["llm_totals"] = self.logger.totals()
                    self._save(run_id, tl)
                self.act(run_id, f"round {next_round} scored; building the feed, the memes and tonight's broadcast")
                try:
                    self._after_round(run_id, run_dir, tl, rd)
                except Exception as e:
                    import traceback; traceback.print_exc()
                    with lock:
                        tl.setdefault("errors", []).append(f"feed round {next_round}: {type(e).__name__}: {e}")
                        self._save(run_id, tl)
                    self.act(run_id, f"round {next_round}: the feed step failed ({type(e).__name__})")
                self.act(run_id, f"round {next_round} is on screen; the agents are writing round {next_round + 1}" if next_round < int(tl["scenario"].get("rounds", 0)) else f"round {next_round} is on screen")
                next_round += 1
                idle = 0
                continue
            pr = _read_json(os.path.join(run_dir, "progress.json"), {})
            proc = self.procs.get(run_id)
            engine_dead = proc is not None and proc.poll() is not None
            if proc is None and pr.get("status") not in ("done", "error") and pr.get("updated_at"):
                # no runner process in this server (a restart or a reboot): progress that has not moved in 15 minutes is a dead runner
                try:
                    age = (datetime.now() - datetime.fromisoformat(pr["updated_at"])).total_seconds()
                    engine_dead = age > 900
                except Exception:
                    pass
            stopped = os.path.exists(os.path.join(run_dir, "STOP"))
            if pr.get("status") in ("done", "error") or (engine_dead and idle > 10) or stopped:
                with lock:
                    if pr.get("status") == "error":
                        tl["status"], tl["error"] = "error", pr.get("note")
                        self.act(run_id, f"the agent process failed: {pr.get('note')}")
                    elif engine_dead and pr.get("status") != "done" and not stopped:
                        tl["status"], tl["error"] = "error", "engine process exited early; see runner.log"
                        self.act(run_id, "the agent process exited early; see runner.log")
                    else:
                        tl["status"] = "done"
                        self.act(run_id, "stopped by you" if stopped else f"finished: {len(tl['rounds']) - 1} rounds scored")
                    # every scheduled event must have fired
                    fired = {e["id"] for e in tl.get("events_fired", [])}
                    missed = [e for e in (tl["scenario"].get("events") or []) if e["id"] not in fired]
                    if missed:
                        msg = "EVENTS NEVER FIRED: " + "; ".join(f"{e['name']} (round {e['round']})" for e in missed)
                        print("[events] WARNING " + msg)
                        tl.setdefault("warnings", []).append(msg)
                    tl["llm_totals"] = self.logger.totals()
                    self._save(run_id, tl)
                if tl["status"] == "done":
                    try:
                        cpath = os.path.join(run_dir, "conflict.json")
                        cst = _read_json(cpath)
                        if cst and conflict_mod.freeze_unresolved(cst, len(tl["rounds"])):
                            _atomic_json(cpath, cst)
                            with lock:
                                tl["conflict"] = conflict_mod.view(cst)
                                self._save(run_id, tl)
                    except Exception as e:
                        import traceback; traceback.print_exc()
                if tl["status"] == "done" and tl.get("mode") == "govern":
                    try:
                        with lock:
                            self._govern_finish(run_id, run_dir, tl)
                            self._save(run_id, tl)
                    except Exception as e:
                        import traceback; traceback.print_exc()
                if tl["status"] == "done" and not stopped and self.cfg.get("episode", {}).get("auto_build", True) and not tl["scenario"].get("demo"):   # a run you stopped gets no episode (it would spend on voices)
                    self.act(run_id, "writing the episode script (it builds in the background; the Episode button lights up when it is ready)")
                    self.build_episode(run_id)
                return
            idle += 1
            time.sleep(1.0)

    def _score_round(self, run_id, run_dir, tl, personas_map, rd):
        scfg = self.cfg["scoring"]
        rnum = rd["round"]
        prev = tl["rounds"][-1]["states"] if tl["rounds"] else {}
        by_state, all_by_state = {}, {}
        sim_year = int((tl["scenario"].get("calendar") or {}).get("start", "2026")[:4]) if tl["scenario"].get("calendar") else None
        if sim_year:
            try:
                sim_year = cal.round_date(tl["scenario"]["calendar"], rnum).year
            except Exception:
                pass
            yearfix.fix_items(rd["posts"], sim_year, "content")
        for p in rd["posts"]:
            if p.get("state"):
                all_by_state.setdefault(p["state"], []).append(p)
                if (p.get("content") or "").strip() or (p.get("quote_content") or "").strip():
                    by_state.setdefault(p["state"], []).append(p)
        engagement = rd.get("engagement", {})
        rules = {c: rule_score(ps, engagement, self.state_names, personas_map[c]["neighbors"]) for c, ps in by_state.items()}
        election = tl.get("election")
        judges = self._judge(run_id, by_state, election) if (tl.get("judge_enabled") and by_state) else {}
        states = {}
        for code, pm in personas_map.items():
            base = {"unrest": pm["baseline_grievance"], "federal_cohesion": 55 + pm["federal_funds_share_pct"] * 0.5, "neighbor_cohesion": 60.0}
            s = blend(prev.get(code), rules.get(code), judges.get(code), base, scfg)
            esc_mult = float(((tl["scenario"].get("tech") or {}).get("effects") or {}).get("escalation", 1.0))
            if esc_mult != 1.0 and prev.get(code) and s["unrest"] > prev[code]["unrest"]:
                s["unrest"] = round(min(100, prev[code]["unrest"] + (s["unrest"] - prev[code]["unrest"]) * esc_mult), 1)
                s["stage"] = min(4, sum(1 for t in scfg["stage_thresholds"] if s["unrest"] >= t))
            s["posts_this_round"] = len(all_by_state.get(code, []))
            s["top_posts"] = sorted(by_state.get(code, []), key=lambda p: -(p.get("num_likes", 0) + 2 * p.get("num_shares", 0)))[:3]
            states[code] = s
        ppath = os.path.join(run_dir, "phone.json")
        pstate = phone_mod.load(_read_json(ppath))
        if pstate.get("pending_events") and rnum >= 1:
            for ev in phone_mod.take_pending_events(pstate, rnum):
                rd.setdefault("events", []).append(ev)
            _atomic_json(ppath, pstate)
        quarter = None
        if tl.get("mode") == "govern":
            try:
                quarter = self._govern_quarter(run_id, run_dir, tl, rd, states, prev, personas_map)
            except Exception as e:
                import traceback; traceback.print_exc()
                tl.setdefault("errors", []).append(f"govern round {rnum}: {type(e).__name__}: {e}")
        # CRIME: a per-state index driven by the economy, unrest, events, blocs, policy and the era; syndicates, influence, blackmail
        crime_view = None
        if rnum >= 1:
            try:
                crpath = os.path.join(run_dir, "crime.json")
                crstate = crime_mod.load(_read_json(crpath))
                gpath = os.path.join(run_dir, "govern.json")
                gst = _read_json(gpath) if tl.get("mode") == "govern" else None
                calx = tl["scenario"].get("calendar") or cal.make(2026, 1, "day")
                cres = crime_mod.step(crstate, rnum, states, personas_map, rd.get("events") or [], tl.get("conflict") or {},
                                      (tl["scenario"].get("tech") or {}).get("effects") or {}, gst, random.Random(tl["scenario"]["seed"] * 17 + rnum),
                                      self.llm if tl.get("llm_enabled") else None, run_id, tl.get("mode"), cal.label(calx, rnum))
                for ev in cres["events"]:
                    ev["states"] = list(ev["affected"]["states"])
                    rd.setdefault("events", []).append(ev)
                    if ev.get("image") and self.cfg["media"].get("images") and self.imagegen.provider != "placeholder":
                        tl["images"][f"event_{ev['id']}"] = "pending"
                        ev["image_key"] = f"event_{ev['id']}"
                        self.media.submit(lambda e=dict(ev), r=rnum, k=ev["id"]: self._event_scene_job(run_id, run_dir, r, k, e), f"crime picture {ev['id']}")
                if cres["market_hits"]:
                    mh = rd.setdefault("market_hits", {})
                    for k, v in cres["market_hits"].items():
                        mh[k] = round(mh.get(k, 0) + v, 2)
                # syndicate ground props up a bloc (protection money, services) and chips at Washington's hold
                cst_local = conflict_mod.load_or_init(_read_json(os.path.join(run_dir, "conflict.json")))
                touched = False
                for sy in crstate["syndicates"].values():
                    if sy.get("gone"):
                        continue
                    for b in cst_local.get("blocs", {}).values():
                        if b.get("gone") or b.get("stage", 0) < 2:
                            continue
                        if any(sy["territory"].get(m, 0) >= 50 for m in b["members"]):
                            b["economy"] = round(min(100, b.get("economy", 100) + 4), 1)
                            b["identity"] = round(min(100, b["identity"] + 2), 1)
                            touched = True
                if touched:
                    _atomic_json(os.path.join(run_dir, "conflict.json"), cst_local)
                if gst is not None:
                    _atomic_json(gpath, gst)
                _atomic_json(crpath, crstate)
                crime_view = cres["view"]
                tl["crime"] = crime_view
            except Exception as e:
                import traceback; traceback.print_exc()
                tl.setdefault("errors", []).append(f"crime round {rnum}: {type(e).__name__}: {e}")
        # scheduled events: immediate rule-based unrest shock (affected states, or everywhere)
        for ev in rd.get("events") or []:
            targets = ev["affected"].get("states") or list(states)
            for code in targets:
                if code in states:
                    states[code]["unrest"] = round(min(100, states[code]["unrest"] + ev["unrest_bump"]), 1)
                    states[code]["stage"] = max(states[code]["stage"], min(4, sum(1 for t in scfg["stage_thresholds"] if states[code]["unrest"] >= t)))
                    # a shock aimed at named states also costs Washington their trust (the judge's reading of cohesion lags the anger)
                    if ev["affected"].get("states"):
                        states[code]["federal_cohesion"] = round(max(5, states[code]["federal_cohesion"] - ev["unrest_bump"] * 0.8), 1)
            print(f"[events] EVENT FIRED: {ev['name']} at round {rnum} (unrest +{ev['unrest_bump']} in {len(targets)} states)")
            tl.setdefault("events_fired", []).append({"id": ev["id"], "round": rnum, "name": ev["name"]})
        cpath = os.path.join(run_dir, "conflict.json")
        cstate = conflict_mod.load_or_init(_read_json(cpath))
        blocs = detect_blocs(states, personas_map, scfg["bloc_cohesion_threshold"], scfg["bloc_min_states"], tl["bloc_registry"],
                             prev_blocs=tl["rounds"][-1]["blocs"] if tl["rounds"] else None,
                             keep=conflict_mod.sticky_keep(cstate, states, scfg["bloc_cohesion_threshold"]), max_states=int(scfg.get("bloc_max_states", 10)))
        bloc_of = {m: b["id"] for b in blocs for m in b["members"]}
        for code in states:
            states[code]["bloc"] = bloc_of.get(code)
        # the civil-conflict layer: sticky blocs climb a ladder, win recognition, contest control, and end in a settlement
        cview = None
        if rnum >= 1:
            try:
                approval = ((tl.get("govern") or {}).get("approval") or {}).get("national") if tl.get("mode") == "govern" else None
                res = conflict_mod.step(cstate, rnum, blocs, states, personas_map, rd.get("events") or [], random.Random(tl["scenario"]["seed"] * 7 + rnum),
                                        self.llm if tl.get("llm_enabled") else None, run_id, approval=approval, run_text=tl["scenario"]["text"],
                                        tech=(tl["scenario"].get("tech") or {}).get("effects") or {})
                for ev in res["events"]:
                    ev["states"] = list(ev["affected"]["states"])
                    rd.setdefault("events", []).append(ev)
                    if ev.get("image") and self.cfg["media"].get("images") and self.imagegen.provider != "placeholder":
                        tl["images"][f"event_{ev['id']}"] = "pending"
                        ev["image_key"] = f"event_{ev['id']}"
                        self.media.submit(lambda e=dict(ev), r=rnum, k=ev["id"]: self._event_scene_job(run_id, run_dir, r, k, e), f"event picture {ev['id']}")
                if res["market_hits"]:
                    rd["market_hits"] = res["market_hits"]
                cview = res["view"]
                _atomic_json(cpath, cstate)
                tl["conflict"] = cview
            except Exception as e:
                import traceback; traceback.print_exc()
                tl.setdefault("errors", []).append(f"conflict round {rnum}: {type(e).__name__}: {e}")
        calx = tl["scenario"].get("calendar") or cal.make(2026, 1, "day")
        entry = {"round": rnum, "written_at": rd.get("written_at"), "active_states": rd.get("active_states", []),
                 "date": cal.round_date(calx, rnum).isoformat(), "date_label": cal.label(calx, rnum),
                 "posts_count": len(rd["posts"]), "states": states, "blocs": blocs,
                 "seed_post": next((p["content"] for p in rd["posts"] if p.get("agent_id") == personas.WIRE_AGENT_ID), None)}
        if election:
            rd["_states_for_polls"] = states
            entry["election"] = self._election_round(tl, rd, judges, prev_entry=tl["rounds"][-1] if tl["rounds"] else None)
            rd.pop("_states_for_polls", None)
            if crime_view and entry["election"].get("polls"):
                lo = crime_mod.law_and_order(election)
                inc = next((o["id"] for o in election["options"] if o.get("incumbent")), None)
                nat = crime_view["national"]
                rising = len(nat) >= 4 and nat[-1]["index"] - nat[-4]["index"] >= 6
                for code, p in entry["election"]["polls"].items():
                    idx = (crime_view["states"].get(code) or {}).get("index", 0)
                    if idx >= 60 and lo:
                        if p["lean"] == lo:
                            p["margin"] = min(60, p["margin"] + 2)
                        else:
                            p["margin"] = max(1, p["margin"] - 2)
                            if p["margin"] <= 1:
                                entry["election"]["polls"][code] = {"lean": lo, "margin": 1}
                    if rising and inc and p["lean"] == inc and idx >= 45:
                        p["margin"] = max(1, p["margin"] - 2)
        if cview is not None:
            entry["conflict"] = cview
        if crime_view is not None:
            entry["crime"] = {k: v for k, v in crime_view.items() if k != "hidden"}
        if quarter:
            entry["quarter"] = {"label": quarter["label"], "headline": quarter["headline"], "vote": quarter["vote"], "midterm": quarter["midterm"],
                                "approval": quarter["approval"], "events": [e["name"] for e in quarter["events"]],
                                "event_states": {e["name"]: e.get("states") or [] for e in quarter["events"]},
                                "event_ids": {e["name"]: e.get("id") for e in quarter["events"]}}
        tl["rounds"].append(entry)

    def _election_round(self, tl, rd, judges, prev_entry):
        """Polls carry forward and update from the judge's lean; Election Night tallies the ballot."""
        election = tl["election"]
        polls = dict((prev_entry or {}).get("election", {}).get("polls", {}))
        for code, j in judges.items():
            if j.get("lean") and j["lean"] != "undecided":
                old = polls.get(code, {"lean": None, "margin": 0})
                # a state that just flipped shows a small margin; the same lean again firms up
                margin = j["lean_margin"] if old.get("lean") != j["lean"] else int(0.5 * old["margin"] + 0.5 * j["lean_margin"] + 5)
                polls[code] = {"lean": j["lean"], "margin": max(1, min(60, margin))}
            elif j.get("lean") == "undecided" and code in polls:
                polls[code] = {"lean": polls[code]["lean"], "margin": max(1, int(polls[code]["margin"] * 0.6))}
        # no judge read: a state turning against Washington drifts to the challenger, one at peace with it to the incumbent
        inc = next((o["id"] for o in election["options"] if o.get("incumbent") or "incumbent" in (o.get("blurb") or "").lower()[:200]), None)
        if inc:
            lanes = {o["id"]: o.get("lane") for o in election["options"]}
            chal = next((o["id"] for o in election["options"] if o["id"] != inc and lanes[o["id"]] != lanes[inc]), next((o["id"] for o in election["options"] if o["id"] != inc), None))
            prev_states = (prev_entry or {}).get("states") or {}
            cur_states = rd.get("_states_for_polls") or {}
            for code, st in cur_states.items():
                j = judges.get(code) or {}
                if j.get("lean") and j["lean"] != "undecided":
                    continue
                coh = float(st.get("federal_cohesion", 60)); u = float(st.get("unrest", 20))
                pcoh = float((prev_states.get(code) or {}).get("federal_cohesion", coh))
                push = 0
                if coh < 45 or u >= 50:
                    push = 3 if coh < 35 or u >= 65 else 2
                    side = chal
                elif coh > 65 and u < 30:
                    push = 1
                    side = inc
                else:
                    side = None
                if pcoh - coh >= 6 and chal:
                    side, push = chal, max(push, 2)
                if not side or not push:
                    continue
                old = polls.get(code)
                if old and old["lean"] == side:
                    old["margin"] = min(60, old["margin"] + push)
                elif old:
                    old["margin"] -= push
                    if old["margin"] <= 0:
                        polls[code] = {"lean": side, "margin": 1}
                else:
                    polls[code] = {"lean": side, "margin": push}
        # a prior for states the judge has not placed yet: the state's political culture, lightly held
        pm_all = _read_json(os.path.join(RUNS_DIR, tl["run_id"], "personas.json")) or {}
        by_lane = {o.get("lane"): o["id"] for o in election["options"] if o.get("lane")}
        for code, pm in pm_all.items():
            if code in polls:
                continue
            lean = float(pm.get("partisan_lean", 0) or 0)
            side = "right" if lean > 4 else "left" if lean < -4 else None
            if side and side in by_lane:
                polls[code] = {"lean": by_lane[side], "margin": int(min(12, abs(lean) / 2))}
        # the morning after the debate: the verdict's bump moves every state's lean margin toward the winner
        deb = _read_json(os.path.join(RUNS_DIR, tl["run_id"], "debate.json")) or {}
        if election.get("debate_round") and rd["round"] == election["debate_round"] + 1 and (deb.get("verdict") or {}).get("winner"):
            w, bump = deb["verdict"]["winner"], float(deb["verdict"].get("bump", 0))
            for code, p in list(polls.items()):
                if p["lean"] == w:
                    p["margin"] = min(60, int(p["margin"] + bump * 2))
                else:
                    p["margin"] = max(1, int(p["margin"] - bump * 2))
                    if p["margin"] <= 2 and bump >= 3:
                        polls[code] = {"lean": w, "margin": 2}
        # novelty polarizes: each state's AI stance weights its lean toward or against the AI candidate every round
        ai = next((o for o in election["options"] if (o.get("analysis") or {}).get("entity_type") == "AI"), None)
        if ai:
            pmap = _read_json(os.path.join(RUNS_DIR, tl["run_id"], "personas.json")) or {}
            for code, p in list(polls.items()):
                st = (pmap.get(code) or {}).get("ai_stance")
                w = STANCE_MARGIN.get(st, 0)
                if not w:
                    continue
                if p["lean"] == ai["id"]:
                    p["margin"] = max(1, min(60, int(p["margin"] + w)))
                    if p["margin"] <= 1 and w < 0:
                        # a hostile state that only barely leaned to the machine flips to its political-culture side
                        others = [o for o in election["options"] if o["id"] != ai["id"]]
                        lean_pts = (pmap.get(code) or {}).get("partisan_lean", 0)
                        pick = max(others, key=lambda o: {"right": 1, "center": 0, "left": -1}.get(o.get("lane"), 0) * (1 if lean_pts >= 0 else -1))
                        polls[code] = {"lean": pick["id"], "margin": 2}
                else:
                    p["margin"] = max(1, min(60, int(p["margin"] - w // 2)))
        # scheduled events: polling hit to the affected candidates, scaled by severity
        for ev in rd.get("events") or []:
            for cid in ev["affected"].get("candidates") or []:
                hit0 = float(ev.get("poll_hit", 0))
                aff = set(ev["affected"].get("states") or [])
                for code, p in list(polls.items()):
                    # the full hit lands where the event landed; elsewhere half of it, so one leak does not flip fifty states at once
                    hit = hit0 if (not aff or code in aff) and aff else hit0 * 0.5
                    if p["lean"] == cid:
                        p["margin"] = int(p["margin"] - hit)
                        if p["margin"] < 1:
                            others = [o["id"] for o in election["options"] if o["id"] != cid]
                            polls[code] = {"lean": others[0] if others else cid, "margin": max(1, -p["margin"])}
                    else:
                        p["margin"] = min(60, int(p["margin"] + hit / 2))
                print(f"[events] poll hit {hit} to {cid} from {ev['name']}")
        out = {"polls": polls, "election_night": bool(rd.get("election_night")), "results": None,
               "events": [{"id": e["id"], "name": e["name"], "severity": e["severity"]} for e in (rd.get("events") or [])]}
        if rd.get("election_night") and rd.get("ballot"):
            results = tally(rd["ballot"], election)
            out["results"] = results
            election["results"] = results
        return out

    def _judge(self, run_id, by_state, election=None):
        out = {}
        items = [(c, self.state_names[c], [(p.get("content") or "") + (f' [quoting: {p["quote_content"]}]' if p.get("quote_content") else "")
                                           for p in ps]) for c, ps in sorted(by_state.items())]
        bs = self.cfg["llm"]["judge_batch_size"]
        batches = [items[i:i + bs] for i in range(0, len(items), bs)]
        self.act(run_id, f"the judge reads {len(items)} states' posts ({len(batches)} batches)")

        def one(batch):
            try:
                txt = self.llm.chat([{"role": "system", "content": JUDGE_SYSTEM}, {"role": "user", "content": judge_prompt(batch, election)}],
                                    kind="judge", run_id=run_id, temperature=self.cfg["llm"]["judge_temperature"])
                data = parse_json_loose(txt)
                return normalize_judge(data.get("states") if isinstance(data, dict) else data, election)
            except Exception as e:
                print(f"[judge] batch failed: {e}")
                return {}
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=int(self.cfg["sim"].get("judge_concurrency", 4))) as ex:
            for res in ex.map(one, batches):
                out.update(res)
        return out

    # ---------- feed + media (runs outside the timeline lock, except for brief patches) ----------
    def _after_round(self, run_id, run_dir, tl, rd):
        mcfg = self.cfg["media"]
        if not mcfg.get("feed", True):
            return
        rnum = rd["round"]
        idx = next(i for i, r in enumerate(tl["rounds"]) if r["round"] == rnum)
        cur = tl["rounds"][idx]["states"]
        prev = tl["rounds"][idx - 1]["states"] if idx > 0 else None
        prev_feed = self.feed_round(run_id, rnum - 1) if rnum > 0 else None
        fcfg = {**mcfg, "feed": bool(tl.get("llm_enabled")), "calendar": tl["scenario"].get("calendar")}
        roster = self.run_roster(run_id)
        election = tl.get("election")
        feed = build_round_feed(self.llm, run_id, rd, cur, prev, roster, fcfg, prev_feed["trending"] if prev_feed else None,
                                tl["scenario"]["seed"], election=election, election_round=tl["rounds"][idx].get("election"),
                                events=rd.get("events") or [],
                                hidden=crime_mod.compromised_lines(_read_json(os.path.join(run_dir, "crime.json")) or {}),
                                context=[cal.prompt_line(tl["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum)]
                                        + crime_mod.summary_lines(_read_json(os.path.join(run_dir, "crime.json")) or {}, self.state_names)[:3]
                                        + ([(tl["scenario"].get("tech") or {}).get("context")] if (tl["scenario"].get("tech") or {}).get("context") else [])
                                        + (conflict_mod.summary_lines(_read_json(os.path.join(run_dir, "conflict.json")) or {}) if (tl.get("conflict") or {}).get("active") else []) or None)
        try:
            sim_year = cal.round_date(tl["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum).year
            n_fixed = yearfix.fix_items(feed["items"], sim_year, "text")
            if n_fixed:
                print(f"[yearfix] round {rnum}: rewrote stray years in {n_fixed} feed items to {sim_year}")
        except Exception as e:
            print(f"[yearfix] {e}")
        feed["media_pending"] = 0
        feed["events"] = rd.get("events") or []
        feed["market_hits"] = rd.get("market_hits") or {}
        feed["conflict"] = conflict_mod.summary_lines(_read_json(os.path.join(run_dir, "conflict.json")) or {}) if tl.get("conflict", {}).get("active") else []
        if tl.get("mode") == "govern":
            gv = tl["scenario"].get("govern") or {}
            pres_author = "npc:candidate_a"
            if rnum == 0 and gv.get("inauguration") and pres_author in roster:
                feed["items"].insert(0, {"id": item_id(0, "g", "inaug"), "round": 0, "author": pres_author, "kind": "post",
                                         "text": "My fellow Americans. " + gv["inauguration"][:600], "reply_to": None, "quote_of": None, "state": None,
                                         "ts": cal.ts(tl["scenario"].get("calendar") or cal.make(2026, 1, "quarter"), 0, 12), "likes": random.randint(90000, 400000), "reposts": random.randint(20000, 90000),
                                         "replies": random.randint(9000, 40000), "thread": [], "pinned": True})
            for ev in rd.get("events") or []:
                if "The President responded:" in ev.get("description", "") and pres_author in roster:
                    quote = ev["description"].split("The President responded:", 1)[1].strip().strip('"')
                    feed["items"].insert(0, {"id": item_id(rnum, "g", ev["id"]), "round": rnum, "author": pres_author, "kind": "post", "text": quote[:600],
                                             "reply_to": None, "quote_of": None, "state": None, "ts": cal.ts(tl["scenario"].get("calendar") or cal.make(2026, 1, "quarter"), rnum, 9, 30),
                                             "likes": random.randint(30000, 200000), "reposts": random.randint(5000, 60000), "replies": random.randint(3000, 30000), "thread": []})
        self._save_feed(run_id, feed)

        # THE PHONE: contacted characters decide whether to act on the insider's texts; trusted ones may text first
        try:
            self._phone_round_end(run_id, run_dir, tl, rd, feed)
        except Exception as e:
            import traceback; traceback.print_exc()
        # avatars for everyone who appears this round (cached forever; placeholders are drawn on the fly by the API)
        if mcfg.get("avatars") and self.imagegen.provider != "placeholder":
            for aid in {it["author"] for it in feed["items"]}:
                if aid in roster:
                    self._ensure_avatar(run_id, roster[aid])

        # markets: one desk call per round, blended with the rule-based move
        if rnum >= 1:
            try:
                self._market_round(run_id, run_dir, tl, idx, feed)
            except Exception as e:
                import traceback; traceback.print_exc()

        # debate night (election runs): transcript now, stage portraits + voices async
        if election and election.get("debate_round") == rnum and tl.get("llm_enabled") and mcfg.get("broadcasts"):
            self._debate(run_id, run_dir, tl, idx, feed, roster)

        # scenes: first time a state reaches a stage >= scene_min_stage
        if mcfg.get("images"):
            for code, s in cur.items():
                key = f"{code}_{s['stage']}"
                if s["stage"] >= mcfg["scene_min_stage"] and key not in tl["images"]:
                    tl["images"][key] = "pending"
                    self.media.submit(lambda c=code, st=dict(s), r=rnum: self._scene_job(run_id, run_dir, c, st, r), f"scene {key}")

        # memes
        if mcfg.get("memes") and rnum >= 1 and tl.get("llm_enabled"):
            n = int(os.environ.get("MEMES_PER_ROUND") or mcfg["memes_per_round"])
            if n > 0:
                self._plan_and_queue_memes(run_id, run_dir, tl, feed, cur, n)

        # broadcasts (scripts now, audio/video async)
        if mcfg.get("broadcasts") and rnum >= 1 and tl.get("llm_enabled"):
            summary, facts = round_summary(tl, idx, feed, roster)
            mk = _read_json(os.path.join(run_dir, "market.json"))
            if mk and mk["history"] and mk["history"][-1]["round"] == rnum:
                h = mk["history"][-1]
                summary += chr(10) + "Markets today: " + ", ".join(f"{sym} {h['pct'][sym]:+.1f}%" for sym in list(h["pct"])[:6]) + f". {h['headlines'][0] if h['headlines'] else ''}"
            networks = self.cfg["broadcast"]["networks"]
            er = tl["rounds"][idx].get("election")
            lead = ""
            for ev in rd.get("events") or []:
                lead += f"BREAKING NEWS THIS ROUND (lead with it, every network): {ev['name']}. {ev['description']} "
            lead += cal.prompt_line(tl["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum) + " "
            if (tl["scenario"].get("tech") or {}).get("context"):
                lead += tl["scenario"]["tech"]["context"] + " "
            for line in crime_mod.summary_lines(_read_json(os.path.join(run_dir, "crime.json")) or {}, self.state_names)[:2]:
                lead += line + " "
            if feed.get("conflict"):
                lead += "CIVIL CONFLICT SITUATION (cover it as politics and logistics, never as combat): " + " | ".join(feed["conflict"]) + " "
            if election and (election.get("novelty") or {}).get("score", 0) >= 7 and rnum <= 2:
                lead += "LEAD STORY: " + election["novelty"]["shock"].replace("SHOCK CONTEXT:", "").strip() + " Open the broadcast on this. "
            elif election and (election.get("novelty") or {}).get("score", 0) >= 7:
                lead += "Recurring theme: a non-human candidate is on the ballot; keep returning to it. "
            if election:
                # partisan networks: Red backs option A, Blue backs option B, the World Service stays an outsider
                names = {o["id"]: o["name"] for o in election["options"]}
                # FLN Red backs the right-lane candidate, FLN Blue the left-lane one (fallback: slot order)
                opts = election["options"]
                red = next((o for o in opts if o.get("lane") == "right"), opts[0])
                blue = next((o for o in opts if o.get("lane") == "left" and o is not red), next((o for o in opts if o is not red), opts[-1]))
                networks = [dict(n, spin=n["spin"] + (f"; openly and unapologetically backs {red['name']} and frames every fact to help that side"
                                                       if n["id"] == "fln_alarm" else
                                                       f"; openly and unapologetically backs {blue['name']} and frames every fact to help that side"
                                                       if n["id"] == "fln_calm" else "; neutral on the election, mildly amused by both sides"))
                            for n in networks]
                if er and er.get("results"):
                    summary = election_summary(er["results"], election) + "\n" + summary
                    facts["election"] = {"winner": names.get(er["results"]["winner"]) or "no majority", "ev": er["results"]["ev"]}
                elif er and er.get("polls"):
                    leaning = {}
                    for c, p in er["polls"].items():
                        leaning[p["lean"]] = leaning.get(p["lean"], 0) + 1
                    summary += "\nCampaign polling, states leaning: " + ", ".join(f"{names.get(k, k)} {v}" for k, v in leaning.items())
            # the field reporter stands in the state that moved the most this round (else the hottest, or the closest race on election night)
            if er and er.get("results") and er["results"]["closest"]:
                fcode = er["results"]["closest"][0]
            else:
                esc = [(c, cur[c]["stage"] - (prev or {}).get(c, {}).get("stage", 0), cur[c]["unrest"]) for c in cur]
                fcode = max(esc, key=lambda e: (e[1], e[2]))[0]
            rep = make_reporter(fcode, self.state_names[fcode])
            field = {**rep, "stage": cur[fcode]["stage_name"], "slogans": cur[fcode].get("slogans", [])}
            # pacing: one broadcast a round (the desk that suits the top story, red/blue alternating, the World Service only for something
            # huge), plus a BREAKING cut-in only for scheduled events, election-night calls and severe shocks
            stories = round_stories(tl, idx, rd, er, election, mk["history"][-1] if (mk and mk["history"] and mk["history"][-1]["round"] == rnum) else None)
            slots = plan_slots(networks, stories, tl.get("last_network"))
            tl["last_network"] = slots[0]["id"]
            self.act(run_id, f"round {rnum}: writing the {slots[0]['id']} broadcast" + (f" and a BREAKING cut-in" if len(slots) > 1 else ""))
            facts["stories"] = [{"impact": round(sc_, 1), "kind": k, "text": t} for sc_, k, t, _ in stories[:4]]
            scripts = write_scripts(self.llm, run_id, (lead + chr(10) if lead else "") + summary + chr(10) + "Top story this round: " + stories[0][2],
                                    slots, self.cfg["broadcast"]["script_seconds"], field,
                                    sim_year=cal.round_date(tl["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum).year)
            self._patch_feed(run_id, rnum, lambda f: f.update(broadcasts=scripts, facts=facts))
            for i, sc in enumerate(scripts):
                self.media.submit(lambda s=sc, r=rnum, i=i, rp=rep: self._broadcast_media_job(run_id, run_dir, r, s, i, rp), f"broadcast {sc['network']}")
            if self.video.available and mcfg.get("video"):
                self._queue_video_triggers(run_id, run_dir, tl, idx, feed, facts)
            if self.tts.available and mcfg.get("rants", True):
                self._queue_influencer_rant(run_id, run_dir, rnum, feed)

    # ---------- feed file helpers ----------
    def _feed_path(self, run_id, rnum):
        return os.path.join(RUNS_DIR, run_id, "feed", f"round_{rnum}.json")

    def _save_feed(self, run_id, feed):
        with self._lock(run_id):
            _atomic_json(self._feed_path(run_id, feed["round"]), feed)

    def _patch_feed(self, run_id, rnum, fn):
        with self._lock(run_id):
            f = _read_json(self._feed_path(run_id, rnum))
            if f is None:
                return
            fn(f)
            _atomic_json(self._feed_path(run_id, rnum), f)

    def _append_item(self, run_id, rnum, item):
        self._wait_feed(run_id, rnum)
        self._patch_feed(run_id, rnum, lambda f: f["items"].append(item))

    def _wait_feed(self, run_id, rnum, timeout=240):
        """A media job can finish before the round's feed file is written; wait for it rather than lose the item."""
        t0 = time.time()
        while not os.path.exists(self._feed_path(run_id, rnum)) and time.time() - t0 < timeout:
            time.sleep(1.0)

    def _media_url(self, run_id, rel):
        return f"/api/runs/{run_id}/media/{rel}"

    # ---------- jobs ----------
    def _ensure_avatar(self, run_id, ident):
        path = os.path.join(AVATAR_DIR, f"{ident['handle']}.png")
        if os.path.exists(path) or not ident.get("avatar_prompt"):
            return
        def job():
            if os.path.exists(path):
                return
            png = self.imagegen.generate(ident["avatar_prompt"] + " Square portrait crop, no text.", kind="avatars", run_id=run_id,
                                         aspect=self.cfg["images"]["aspect_avatar"])
            if png:
                _write_bytes(path, png)
        self.media.submit(job, f"avatar {ident['handle']}")

    def _scene_job(self, run_id, run_dir, code, s, rnum):
        key = f"{code}_{s['stage']}"
        name = self.state_names[code]
        prompt = scene_prompt(name, s["stage"], s["slogans"], s["grievance"])
        png = self.imagegen.generate(prompt, kind="images", run_id=run_id, aspect=self.cfg["images"]["aspect_scene"])
        note = self.imagegen.provider
        if not png:
            png = placeholder_png(f"{name} · {s['stage_name']} · " + " / ".join(s["slogans"][:2] or ["ENOUGH"]), (1024, 576), seed=hash(key), kind="scene")
            note = "placeholder"
        rel = f"scenes/{key}.png"
        _write_bytes(os.path.join(run_dir, "media", rel), png)
        with open(os.path.join(run_dir, "media", f"scenes/{key}.prompt.txt"), "w", encoding="utf-8") as f:
            f.write(prompt)
        tl = self.timelines.get(run_id)
        with self._lock(run_id):
            tl["images"][key] = rel
            tl["image_notes"][key] = note
            self._save(run_id, tl)
        news = self.run_roster(run_id)[f"news:{code}"]
        self._append_item(run_id, rnum, {"id": item_id(rnum, "s", key), "round": rnum, "author": news["id"], "kind": "scene",
                                         "text": "BREAKING: " + scene_headline(name, s["stage_name"], s["slogans"]), "state": code,
                                         "likes": 400 + s["stage"] * 900, "reposts": 120 + s["stage"] * 300, "replies": 60 + s["stage"] * 80,
                                         "ts": cal.ts(self.timelines[run_id]["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum, 12),
                                         "media": {"type": "image", "url": self._media_url(run_id, rel), "status": "ready", "note": note},
                                         "quote_of": None, "reply_to": None, "thread": []})

    def _plan_and_queue_memes(self, run_id, run_dir, tl, feed, cur, n):
        hot = sorted(cur.items(), key=lambda kv: -kv[1]["unrest"])[:5]
        ctx = "\n".join(f"- {self.state_names[c]}: {s['stage_name']} (unrest {s['unrest']}), {s.get('summary','')}; slogans: {', '.join(s.get('slogans',[])[:2])}"
                        for c, s in hot)
        ctx += "\nTrending: " + ", ".join(t["tag"] for t in feed["trending"][:5])
        viral = next((it for it in feed["items"] if it["id"] == feed.get("viral")), None)
        if viral:
            ctx += f"\nViral post: @{self.run_roster(run_id)[viral['author']]['handle']}: {viral['text'][:200]}"
        authors = [v for v in self.run_roster(run_id).values() if v["kind"] in ("meme_account", "influencer", "troll", "doomposter", "pundit")]
        authors += [self.run_roster(run_id)[f"state:{c}"] for c, _ in hot[:3]]
        pres = None
        if tl.get("mode") == "govern":
            pres = next((o for o in (tl.get("election") or {}).get("options", []) if o["id"] == "A"), None)
            if pres:
                ctx += (f"\nThe President is {pres['name']} ({pres.get('party', '')}); approval {((tl.get('govern') or {}).get('approval') or {}).get('national', '?')}%. "
                        f"Satirical cartoons of the President (format prez_cartoon) are the meme of the season: at least one of the {n} memes uses it.")
        plans = plan_memes(self.llm, ctx, authors, n, run_id)
        rnum = feed["round"]
        for i, m in enumerate(plans):
            if m["format_id"] == "prez_cartoon":
                if not pres:
                    m["format_id"] = "two_panel_reaction"
                else:
                    m["subject"] = (pres.get("analysis") or {}).get("appearance", "")[:400]
            rel = f"memes/r{rnum}_{i}.png"
            item = {"id": item_id(rnum, "m", i), "round": rnum, "author": m["author"], "kind": "meme", "text": m["post_text"],
                    "state": self.run_roster(run_id)[m["author"]].get("state"), "likes": random.randint(800, 40000), "reposts": random.randint(200, 9000),
                    "replies": random.randint(40, 2000), "ts": cal.ts(tl["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum, 13),
                    "media": {"type": "image", "url": self._media_url(run_id, rel), "status": "pending", "alt": f"{m['top']} / {m['bottom']}",
                              "format": FORMAT_BY_ID[m["format_id"]]["name"]}, "quote_of": None, "reply_to": None, "thread": []}
            self._append_item(run_id, rnum, item)
            self.media.submit(lambda m=m, rel=rel, iid=item["id"], r=rnum: self._meme_job(run_id, run_dir, r, iid, m, rel), f"meme {item['id']}")

    def _meme_job(self, run_id, run_dir, rnum, iid, m, rel):
        base = self.imagegen.generate(meme_image_prompt(m["format_id"], m.get("subject")), kind="memes", run_id=run_id, aspect=self.cfg["images"]["aspect_meme"])
        note = self.imagegen.provider
        if not base:
            base = placeholder_png(FORMAT_BY_ID[m["format_id"]]["name"], (768, 768), seed=hash(iid), kind="meme")
            note = "placeholder"
        png = stamp_caption(base, m["top"], m["bottom"])
        _write_bytes(os.path.join(run_dir, "media", rel), png)
        def patch(f):
            for it in f["items"]:
                if it["id"] == iid:
                    it["media"]["status"] = "ready"
                    it["media"]["note"] = note
        self._patch_feed(run_id, rnum, patch)

    def _broadcast_media_job(self, run_id, run_dir, rnum, sc, i, rep):
        """Anchor portrait + voiced script, then the state reporter's portrait + voiced cut-in. No avatar provider needed."""
        net = next((n for n in self.cfg["broadcast"]["networks"] if n["id"] == sc["network"]), {})
        role = net.get("voice") or ("anchor_a" if i == 0 else "anchor_b")
        anchor_png = self._anchor_photo(run_id, sc)
        field_png = self._reporter_photo(run_id, rep)
        rel = f"broadcast/r{rnum}_{sc['network']}.mp3"
        mp3 = self.tts.speak(sc["script"], role=role, kind="tts", run_id=run_id, emotion=net.get("emotion") or ROLE_EMOTION.get(role))
        if mp3:
            _write_bytes(os.path.join(run_dir, "media", rel), mp3)
        frel = f"broadcast/r{rnum}_{sc['network']}_field.mp3"
        stage_name = (self.timelines[run_id]["rounds"][-1]["states"].get(rep["code"]) or {}).get("stage_name", "calm")
        fmp3 = self.tts.speak(sc["field_cutin"], role=rep["voice_role"], kind="tts", run_id=run_id, voice_id=rep.get("voice_id"),
                              emotion=FIELD_EMOTION.get(stage_name, "steady")) if sc.get("field_cutin") else None
        if fmp3:
            _write_bytes(os.path.join(run_dir, "media", frel), fmp3)
        video_rel = None
        if self.avatar.available and mp3 and anchor_png:
            mp4 = self.avatar.animate(anchor_png, mp3, kind="avatar_video", run_id=run_id)
            if mp4:
                video_rel = f"broadcast/r{rnum}_{sc['network']}.mp4"
                _write_bytes(os.path.join(run_dir, "media", video_rel), mp4)
        def patch(f):
            for b in f.get("broadcasts", []):
                if b["network"] == sc["network"]:
                    b["anchor_image"] = f"/api/avatars/anchor_{sc['network']}.png"
                    b["field_image"] = f"/api/avatars/{rep['handle']}.png"
                    b["audio"] = self._media_url(run_id, rel) if mp3 else None
                    b["field_audio"] = self._media_url(run_id, frel) if fmp3 else None
                    b["video"] = self._media_url(run_id, video_rel) if video_rel else None
                    b["status"] = "video" if video_rel else ("audio" if mp3 else "script")
        self._patch_feed(run_id, rnum, patch)

    # ---------- episode (the show) ----------
    def recast_voices(self, run_id):
        """Re-cast every human candidate's voice from the bio (casting.cast) and re-voice the debate with the new voices."""
        from candidates import cast_voices, voice_profile_llm
        tl = self.timeline(run_id)
        election = (tl or {}).get("election")
        if not election:
            return None
        for o in election["options"]:
            a = o.setdefault("analysis", {})
            if a.get("entity_type") != "AI" and not (a.get("voice_profile") or {}).get("tone"):
                a["voice_profile"] = voice_profile_llm(self.llm if tl.get("llm_enabled") else None, run_id, o)
            o.pop("voice_id", None)
        cast_voices(election)
        with self._lock(run_id):
            self._save(run_id, tl)
        run_dir = os.path.join(RUNS_DIR, run_id)
        sc = _read_json(os.path.join(run_dir, "scenario.json"))
        if sc and sc.get("election"):
            sc["election"] = election
            _atomic_json(os.path.join(run_dir, "scenario.json"), sc)
        deb = _read_json(os.path.join(run_dir, "debate.json"))
        if deb and election.get("debate_round") is not None and self.tts.available:
            rnum = election["debate_round"]
            self.media.submit(lambda: self._debate_media_job(run_id, run_dir, rnum, election, deb), "debate voices (recast)")
        return {o["id"]: {"name": o["name"], "voice_id": o.get("voice_id"), "cast": o.get("voice_cast")} for o in election["options"]}

    def build_episode(self, run_id, force=False):
        """Queue the Director after the media queue drains, so scenes can use every image and voice. Returns True if queued."""
        run_dir = os.path.join(RUNS_DIR, run_id)
        if os.path.exists(os.path.join(run_dir, "episode.json")) and not force:
            return False
        pr_path = os.path.join(run_dir, "episode_progress.json")
        pr = _read_json(pr_path, {})
        if pr.get("status") in ("queued", "writing") and not force:
            return False
        _atomic_json(pr_path, {"status": "queued", "step": 0, "total": 1, "note": "waiting for media to finish"})

        def prog(status, step, total, note=""):
            _atomic_json(pr_path, {"status": status, "step": step, "total": total, "note": note})

        def job():
            for _ in range(600):
                if self.media.pending_other("episode") == 0:   # wait for pictures and voices, not for other episode builds
                    break
                time.sleep(2)
            try:
                tl = self.timeline(run_id)
                llm = self.llm if tl.get("llm_enabled") else None
                build_episode(run_dir, run_id, llm, self.tts, self.state_names, prog)
                prog("ready", 1, 1, "episode ready")
            except Exception as e:
                import traceback; traceback.print_exc()
                prog("error", 0, 1, f"{type(e).__name__}: {e}")
        self.media.submit(job, f"episode {run_id}")
        return True

    # ---------- markets ----------
    def _market_round(self, run_id, run_dir, tl, idx, feed):
        mpath = os.path.join(run_dir, "market.json")
        mk = _read_json(mpath)
        if not mk:
            return
        rnum = tl["rounds"][idx]["round"]
        if any(h["round"] == rnum for h in mk["history"]):
            return
        cur, prev = tl["rounds"][idx], tl["rounds"][idx - 1] if idx > 0 else None
        prev_ids = {b["id"]: b for b in (prev["blocs"] if prev else [])}
        cur_ids = {b["id"]: b for b in cur["blocs"]}
        formed = [b["name"] for i, b in cur_ids.items() if i not in prev_ids]
        collapsed = [b["name"] for i, b in prev_ids.items() if i not in cur_ids]
        election = tl.get("election")
        er = cur.get("election") or {}
        lead = {}
        if election and er.get("polls"):
            names = {o["id"]: o["name"] for o in election["options"]}
            for p in er["polls"].values():
                lead[names.get(p["lean"], p["lean"])] = lead.get(names.get(p["lean"], p["lean"]), 0) + 1
        extra = ["Top headlines: " + "; ".join(b["headline"] for b in feed.get("broadcasts", [])[:2])] if feed.get("broadcasts") else []
        if er.get("results"):
            names = {o["id"]: o["name"] for o in election["options"]}
            extra.append(f"ELECTION RESULT: {names.get(er['results']['winner']) or 'no majority'} wins, EV " + ", ".join(f"{names[k]} {v}" for k, v in er["results"]["ev"].items()))
        if feed.get("debate"):
            extra.append("Debate night: " + feed["debate"].get("recap", "")[:200])
        rd_events = feed.get("events") or (_read_json(os.path.join(run_dir, "rounds", f"round_{rnum}.json")) or {}).get("events") or []
        for ev in rd_events:
            extra.append(f"BREAKING EVENT: {ev['name']}. {ev['description']} Affected companies: {', '.join(ev['affected'].get('companies') or []) or 'n/a'}.")
        facts, nums = market_facts(cur, prev, formed, collapsed, lead, extra)
        signal = policy_signal_from(election, er)
        if er.get("results") and election:
            lanes = {o["id"]: o.get("lane") for o in election["options"]}
            w = er["results"]["winner"]
            signal = 1.0 if (w and lanes.get(w) == "left") else -1.0 if (w and lanes.get(w) == "right") else 0.0
        rng = random.Random(tl["scenario"]["seed"] * 7 + rnum)
        entry = step_market(self.llm if tl.get("llm_enabled") else None, run_id, mk, facts, nums, signal, rnum, rng)
        # a civil conflict moves whole sectors: shipping, energy, defense, banks (keys 'sector:<word>' match ticker sector or name)
        for key, pct in (feed.get("market_hits") or {}).items():
            word = key.split(":", 1)[-1].lower()
            for t in mk["tickers"]:
                if word in (str(t.get("sector", "")) + " " + str(t.get("name", ""))).lower() and t["sym"] in entry["prices"]:
                    entry["prices"][t["sym"]] = round(entry["prices"][t["sym"]] * (1 + pct / 100), 2)
                    entry["pct"][t["sym"]] = round(entry["pct"][t["sym"]] + pct, 2)
        # scheduled events: hard stock hits on the affected tickers, applied on top of the desk's move
        for ev in rd_events:
            for sym, pct in (ev.get("stock_hits") or {}).items():
                if sym in entry["prices"]:
                    entry["prices"][sym] = round(entry["prices"][sym] * (1 + pct / 100), 2)
                    entry["pct"][sym] = round(entry["pct"][sym] + pct, 2)
                    print(f"[events] stock hit {sym} {pct:+.0f}% from {ev['name']}")
            entry.setdefault("headlines", []).insert(0, f"{ev['name']}: " + ", ".join(f"{s} {p:+.0f}%" for s, p in (ev.get("stock_hits") or {}).items())[:80])
        _atomic_json(mpath, mk)
        self._patch_feed(run_id, rnum, lambda f: f.update(market={**entry, "tickers": mk["tickers"], "macro": mk["macro"]}))

    # ---------- debate ----------
    def _debate(self, run_id, run_dir, tl, idx, feed, roster):
        election = tl["election"]
        cur = tl["rounds"][idx]["states"]
        er = tl["rounds"][idx].get("election") or {}
        names = {o["id"]: o["name"] for o in election["options"]}
        hot = sorted(cur.items(), key=lambda kv: -kv[1]["unrest"])[:6]
        lead = {}
        for c, p in (er.get("polls") or {}).items():
            lead.setdefault(p["lean"], []).append(c)
        ctx = ["Hottest states: " + "; ".join(f"{self.state_names[c]} ({s['stage_name']}, unrest {s['unrest']}, signs: {', '.join(s.get('slogans', [])[:2]) or 'none'})" for c, s in hot),
               "Polls, states leaning: " + ("; ".join(f"{names.get(k, k)}: {', '.join(v[:12])}" for k, v in lead.items()) or "too early"),
               (tl["scenario"].get("tech") or {}).get("context") or "",
               ("Lines the candidates have privately decided to use: " + " | ".join((_read_json(os.path.join(run_dir, "phone.json")) or {}).get("debate_notes") or [])) if (_read_json(os.path.join(run_dir, "phone.json")) or {}).get("debate_notes") else "",
               ("A MARS QUESTION is live: there is a Mars outpost and funding a colony is contested; one debate question must be about it." if ((tl["scenario"].get("tech") or {}).get("effects") or {}).get("mars_vote") else ""),
               "Trending: " + ", ".join(t["tag"] for t in feed["trending"][:6])]
        viral = next((it for it in feed["items"] if it["id"] == feed.get("viral")), None)
        if viral:
            ctx.append(f"Viral post: @{roster[viral['author']]['handle']}: {viral['text'][:200]}")
        blocs = tl["rounds"][idx]["blocs"]
        if blocs:
            ctx.append("Blocs pulling away from Washington: " + "; ".join(f"{b['name']} ({', '.join(b['members'])})" for b in blocs))
        handles = [v["handle"] for v in roster.values() if v["kind"] in ("influencer", "pundit", "troll", "bot", "doomposter", "organizer", "meme_account", "reply_guy")]
        deb = write_debate(self.llm, run_id, election, "\n".join(ctx), self.cfg["broadcast"].get("debate_questions", 5), handles)
        if not deb:
            return
        deb["round"] = feed["round"]
        deb["candidates"] = {o["id"]: {"name": o["name"], "party": o.get("party", ""), "color": o["color"],
                                       "image": o.get("portrait") or f"/api/avatars/candidate_{o['id'].lower()}.png",
                                       "stage_image": o.get("portrait")} for o in election["options"]}
        _atomic_json(os.path.join(run_dir, "debate.json"), deb)
        self._patch_feed(run_id, feed["round"], lambda f: f.update(debate=deb))
        self.media.submit(lambda: self._debate_media_job(run_id, run_dir, feed["round"], election, deb), "debate media")

    def revoice(self, run_id, episode=True, everything=False):
        """Re-voice a finished run with today's voices: every World Service report, the debate moderator's lines (the
        moderator's voice now follows the name), then the episode. Candidate voices are left as they are."""
        import glob
        from debate import MODERATORS, moderator_voice
        run_dir = os.path.join(RUNS_DIR, run_id)
        stamp = str(int(time.time()))
        done = {"world": 0, "moderator": 0}
        for fp in sorted(glob.glob(os.path.join(run_dir, "feed", "round_*.json"))):
            f = _read_json(fp) or {}
            rnum = f.get("round")
            if rnum is None:
                continue
            for i, b in enumerate(f.get("broadcasts") or []):
                if not b.get("script") or (b.get("network") != "fln_world" and not everything):
                    continue
                netid = b["network"]
                net = next((n for n in self.cfg["broadcast"]["networks"] if n["id"] == netid), {})
                role = net.get("voice") or ("anchor_a" if i == 0 else "anchor_b")
                mp3 = self.tts.speak(b["script"], role=role, kind="tts", run_id=run_id, emotion=net.get("emotion") or ROLE_EMOTION.get(role))
                if mp3:
                    rel = f"broadcast/r{rnum}_{netid}.mp3"
                    _write_bytes(os.path.join(run_dir, "media", rel), mp3)
                    url = self._media_url(run_id, rel) + "?v=" + stamp
                    self._patch_feed(run_id, rnum, lambda ff, url=url, netid=netid: [x.update(audio=url) for x in ff.get("broadcasts", []) if x.get("network") == netid])
                    done["world"] += 1
                if everything and b.get("field_cutin") and b.get("field_state"):
                    rep = make_reporter(b["field_state"], self.state_names.get(b["field_state"], b["field_state"]))
                    fmp3 = self.tts.speak(b["field_cutin"], role=rep["voice_role"], kind="tts", run_id=run_id, voice_id=rep.get("voice_id"), emotion="tense, raised voice")
                    if fmp3:
                        frel = f"broadcast/r{rnum}_{netid}_field.mp3"
                        _write_bytes(os.path.join(run_dir, "media", frel), fmp3)
                        furl = self._media_url(run_id, frel) + "?v=" + stamp
                        self._patch_feed(run_id, rnum, lambda ff, furl=furl, netid=netid: [x.update(field_audio=furl) for x in ff.get("broadcasts", []) if x.get("network") == netid])
            deb = f.get("debate")
            if deb and deb.get("lines"):
                if not deb.get("moderator_gender"):
                    deb["moderator_gender"] = dict(MODERATORS).get(deb.get("moderator")) or "m"
                vid = moderator_voice(deb)
                audio = list(deb.get("audio") or [None] * len(deb["lines"]))
                audio += [None] * (len(deb["lines"]) - len(audio))
                election = (self.timeline(run_id) or {}).get("election") or {}
                for i, line in enumerate(deb["lines"]):
                    if line["speaker"] != "mod":
                        if not everything:
                            continue
                        opt = next((o for o in election.get("options", []) if o["id"] == line["speaker"]), None)
                        if not opt:
                            continue
                        ai = (opt.get("analysis") or {}).get("entity_type") == "AI"
                        mp3 = self.tts.speak(line["text"], role=f"{opt['id']}_{'m' if ai else (opt.get('voice') or 'm')[:1]}", kind="tts", run_id=run_id,
                                             voice_id=voice_for(opt, "m" if ai else opt.get("voice"), self.tts.provider),
                                             emotion="calm, synthetic, evenly paced" if ai else line.get("emotion"))
                    else:
                        mp3 = self.tts.speak(line["text"], role="moderator", kind="tts", run_id=run_id,
                                             emotion=line.get("emotion") or ROLE_EMOTION["moderator"], voice_id=vid)
                    if mp3:
                        rel = f"debate/line_{i:02d}.mp3"
                        _write_bytes(os.path.join(run_dir, "media", rel), mp3)
                        audio[i] = self._media_url(run_id, rel) + "?v=" + stamp
                        done["moderator"] += 1
                gender = deb["moderator_gender"]
                self._patch_feed(run_id, rnum, lambda ff, audio=audio, gender=gender: ff.get("debate") and ff["debate"].update(audio=audio, moderator_gender=gender))
        print(f"[revoice] {run_id}: {done}")
        if episode:
            self.build_episode(run_id, force=True)
        return done

    def _debate_media_job(self, run_id, run_dir, rnum, election, deb):
        # the stage uses the one bio-driven portrait per candidate (generated at run start, same file everywhere)
        images = {o["id"]: o["portrait"] for o in election["options"] if o.get("portrait")}
        # ...and one wide shot of the whole stage, so the debate looks like a debate rather than three rooms
        stage_url = None
        if self.imagegen.provider != "placeholder" and self.cfg["media"].get("images"):
            from candidates import stage_prompt
            key = hashlib.sha1("|".join((o.get("analysis") or {}).get("appearance", o["name"]) for o in election["options"]).encode()).hexdigest()[:8]
            png = self._cached_portrait(run_id, f"stage_{key}.png", stage_prompt(election), aspect=self.cfg["images"]["aspect_scene"])
            if png:
                _write_bytes(os.path.join(run_dir, "media", "debate", "stage.png"), png)
                stage_url = self._media_url(run_id, "debate/stage.png")
        audio = []
        if self.tts.available:
            for i, line in enumerate(deb["lines"]):
                prov = self.tts.provider
                if line["speaker"] == "mod":
                    mp3 = self.tts.speak(line["text"], role="moderator", kind="tts", run_id=run_id,
                                         emotion=line.get("emotion") or ROLE_EMOTION["moderator"],
                                         voice_id=__import__("debate").moderator_voice(deb))   # a Cartesia uuid, so name and voice always agree
                else:
                    opt = next(o for o in election["options"] if o["id"] == line["speaker"])
                    is_ai = (opt.get("analysis") or {}).get("entity_type") == "AI"
                    emo = ("calm, synthetic, evenly paced" if is_ai else line.get("emotion"))
                    mp3 = self.tts.speak(line["text"], role=f"{opt['id']}_{'m' if is_ai else (opt.get('voice') or 'm')[:1]}", kind="tts", run_id=run_id,
                                         voice_id=voice_for(opt, "m" if is_ai else opt.get("voice"), prov), emotion=emo)
                if mp3:
                    rel = f"debate/line_{i:02d}.mp3"
                    _write_bytes(os.path.join(run_dir, "media", rel), mp3)
                    audio.append(self._media_url(run_id, rel))
                else:
                    audio.append(None)
        def patch(f):
            d = f.get("debate")
            if d:
                d["audio"] = audio
                d["stage_image"] = stage_url
                for k, url in images.items():
                    d["candidates"][k]["stage_image"] = url
                d["status"] = "voiced" if audio and any(audio) else "script"
        self._patch_feed(run_id, rnum, patch)
        deb_path = os.path.join(run_dir, "debate.json")
        d = _read_json(deb_path) or deb
        d["audio"] = audio
        d["stage_image"] = stage_url
        for k, url in images.items():
            d["candidates"][k]["stage_image"] = url
        d["status"] = "voiced" if audio and any(audio) else "script"
        _atomic_json(deb_path, d)

    def _cached_portrait(self, run_id, filename, prompt, aspect=None):
        path = os.path.join(AVATAR_DIR, filename)
        if not os.path.exists(path):
            png = self.imagegen.generate(prompt, kind="avatars", run_id=run_id, aspect=aspect or self.cfg["images"]["aspect_avatar"])
            if not png:
                return None
            _write_bytes(path, png)
        with open(path, "rb") as f:
            return f.read()

    def _anchor_photo(self, run_id, sc):
        # 16:9 to match the player; medium shot with headroom so nothing is cropped
        net = next((n for n in self.cfg["broadcast"]["networks"] if n["id"] == sc["network"]), {})
        studio = net.get("studio") or "blue and silver"
        return self._cached_portrait(run_id, f"anchor_{sc['network']}.png",
                                     f"photorealistic 16:9 broadcast frame of a fictional TV news anchor named {sc['anchor']} seated at a glossy "
                                     f"news desk, {studio} studio, "
                                     f"medium shot from the chest up with clear headroom above the head, centered, dramatic studio lighting, "
                                     f"looking into the camera. No text, no logos, no real people.", aspect="16:9")

    def _reporter_photo(self, run_id, rep):
        return self._cached_portrait(run_id, rep["cache"], rep["portrait_prompt"] + " 16:9 frame, chest-up with clear headroom, centered.",
                                     aspect="16:9")

    def _queue_video_triggers(self, run_id, run_dir, tl, idx, feed, facts):
        rnum = tl["rounds"][idx]["round"]
        cur = tl["rounds"][idx]["states"]
        triggers = self.cfg["video"]["triggers"]
        events = []
        if "riot" in triggers:
            for code, s in cur.items():
                if s["stage"] >= 4 and code not in tl["riot_seen"]:
                    tl["riot_seen"].append(code)
                    events.append(("riot", code, f"{self.state_names[code]} erupts: riot overnight"))
        lead = max(cur.items(), key=lambda kv: kv[1]["unrest"])[0]
        if "bloc_formed" in triggers:
            for b in facts["formed"]:
                events.append(("bloc_formed", lead, f"BREAKING: {b} forms"))
        if "bloc_collapsed" in triggers:
            for b in facts["collapsed"]:
                events.append(("bloc_collapsed", lead, f"{b} collapses"))
        for kind, code, title in events[:2]:
            self.media.submit(lambda k=kind, c=code, t=title, r=rnum: self._video_job(run_id, run_dir, r, k, c, t), f"video {kind} {code}")

    def _video_job(self, run_id, run_dir, rnum, kind, code, title):
        tl = self.timelines[run_id]
        s = tl["rounds"][-1]["states"][code]
        key = f"{code}_{max(2, s['stage'])}"
        png_path = os.path.join(run_dir, "media", f"scenes/{key}.png")
        if not os.path.exists(png_path):
            png = self.imagegen.generate(scene_prompt(self.state_names[code], max(2, s["stage"]), s["slogans"], s["grievance"]),
                                         kind="images", run_id=run_id, aspect=self.cfg["images"]["aspect_scene"])
            if not png:
                return
            _write_bytes(png_path, png)
        with open(png_path, "rb") as f:
            png = f.read()
        mp4 = self.video.animate(png, f"handheld news footage, {title}, crowd moving, smoke drifting, camera shake, 6 seconds", kind="video", run_id=run_id)
        if not mp4:
            return
        rel = f"video/r{rnum}_{kind}_{code}.mp4"
        _write_bytes(os.path.join(run_dir, "media", rel), mp4)
        news = self.run_roster(run_id)[f"news:{code}"]
        self._append_item(run_id, rnum, {"id": item_id(rnum, "v", f"{kind}_{code}"), "round": rnum, "author": news["id"], "kind": "video",
                                         "text": title, "state": code, "likes": 9000, "reposts": 3000, "replies": 800,
                                         "ts": cal.ts(self.timelines[run_id]["scenario"].get("calendar") or cal.make(2026, 1, "day"), rnum, 14),
                                         "media": {"type": "video", "url": self._media_url(run_id, rel), "status": "ready"},
                                         "quote_of": None, "reply_to": None, "thread": []})

    def _queue_influencer_rant(self, run_id, run_dir, rnum, feed):
        cands = [it for it in feed["items"] if self.run_roster(run_id)[it["author"]]["kind"] == "influencer" and it.get("text")]
        if not cands:
            return
        top = max(cands, key=lambda it: it["likes"] + 3 * it["reposts"])
        ident = self.run_roster(run_id)[top["author"]]
        def job():
            mp3 = self.tts.speak(top["text"], role="influencer", kind="tts", run_id=run_id, emotion=ROLE_EMOTION["influencer"])
            if not mp3:
                return
            rel = f"video/r{rnum}_rant_{ident['handle']}.mp3"
            _write_bytes(os.path.join(run_dir, "media", rel), mp3)
            media = {"type": "audio", "url": self._media_url(run_id, rel), "status": "ready", "poster": f"/api/avatars/{ident['handle']}.png"}
            if self.avatar.available:
                photo = self._cached_portrait(run_id, f"{ident['handle']}.png", ident["avatar_prompt"])
                mp4 = self.avatar.animate(photo, mp3, kind="avatar_video", run_id=run_id) if photo else None
                if mp4:
                    vrel = f"video/r{rnum}_rant_{ident['handle']}.mp4"
                    _write_bytes(os.path.join(run_dir, "media", vrel), mp4)
                    media = {"type": "video", "url": self._media_url(run_id, vrel), "status": "ready"}
            self._append_item(run_id, rnum, {"id": item_id(rnum, "rant", ident["handle"]), "round": rnum, "author": ident["id"], "kind": "rant",
                                             "text": "🎙 " + top["text"][:200], "state": None, "likes": top["likes"] * 2, "reposts": top["reposts"] * 2,
                                             "replies": top["replies"], "ts": top["ts"], "media": media, "quote_of": None, "reply_to": None, "thread": []})
        self.media.submit(job, f"rant {ident['handle']}")

    # ---------- demo producer (no LLM) ----------
    def _demo_producer(self, run_id):
        from demo_bank import make_round_posts
        run_dir = os.path.join(RUNS_DIR, run_id)
        sc = _read_json(os.path.join(run_dir, "scenario.json"))
        pm = _read_json(os.path.join(run_dir, "personas.json"))
        rng = random.Random(sc["seed"])
        total = sc["rounds"]
        pid = 1
        rounds_dir = os.path.join(run_dir, "rounds")
        _atomic_json(os.path.join(rounds_dir, "round_0.json"),
                     {"round": 0, "written_at": datetime.now().isoformat(timespec="seconds"), "active_states": [],
                      "posts": [{"post_id": 0, "agent_id": 0, "state": None, "kind": "post", "content": sc["text"][:1500],
                                 "quote_content": None, "original_post_id": None, "num_likes": 0, "num_shares": 0}],
                      "engagement": {}, "action_counts_cumulative": {}})
        _atomic_json(os.path.join(run_dir, "progress.json"), {"status": "running", "round": 0, "total_rounds": total})
        for r in range(1, total + 1):
            time.sleep(0.6)
            posts, pid = make_round_posts(r, total, pm, rng, pid, sc.get("agents_per_round") or self.cfg["sim"]["agents_per_round"])
            _atomic_json(os.path.join(rounds_dir, f"round_{r}.json"),
                         {"round": r, "written_at": datetime.now().isoformat(timespec="seconds"),
                          "active_states": sorted({p["state"] for p in posts}), "posts": posts,
                          "engagement": {str(p["post_id"]): {"likes": p["num_likes"], "shares": p["num_shares"]} for p in posts},
                          "action_counts_cumulative": {"CREATE_POST": pid}})
            _atomic_json(os.path.join(run_dir, "progress.json"), {"status": "running", "round": r, "total_rounds": total})
            if os.path.exists(os.path.join(run_dir, "STOP")):
                break
        election = sc.get("election")
        if election:
            time.sleep(0.6)
            ballots = demo_ballots(pm, election, sc["seed"])
            names = {o["id"]: o["name"] for o in election["options"]}
            posts = [{"post_id": pid + i, "agent_id": None, "state": c, "kind": "post",
                      "content": f"{pm[c]['name']} has voted: {names[b['vote']]}. {b['reason']}", "quote_content": None,
                      "original_post_id": None, "num_likes": rng.randint(0, 20), "num_shares": rng.randint(0, 6)}
                     for i, (c, b) in enumerate(ballots.items())]
            _atomic_json(os.path.join(rounds_dir, f"round_{total + 1}.json"),
                         {"round": total + 1, "written_at": datetime.now().isoformat(timespec="seconds"), "election_night": True,
                          "ballot": ballots, "active_states": list(ballots), "posts": posts, "engagement": {}, "action_counts_cumulative": {}})
            total += 1
        _atomic_json(os.path.join(run_dir, "progress.json"), {"status": "done", "round": total, "total_rounds": total})
