"""
FAULT LINES simulation runner.

Runs INSIDE MiroFish's venv (it needs camel-oasis). It drives MiroFish's own
OASIS Twitter runner (backend/scripts/run_twitter_simulation.py) one round at
a time and, after every round, dumps the new posts into
    <run_dir>/rounds/round_<n>.json
for the FAULT LINES backend to score. MiroFish's code is imported, never
modified.

Usage:
    python sim/runner.py --run-dir runs/<run_id>
Env:
    MIROFISH_BACKEND   path to mirofish/backend (default: ./mirofish/backend, else ../mirofish/backend)
    LLM_BASE_URL       where agents send chat completions (the backend's
                       logging proxy by default, set by the backend)
"""
import argparse
import asyncio
import json
import re
import os
import random
import sqlite3
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
FL_ROOT = os.path.dirname(HERE)
ROOT = os.path.dirname(FL_ROOT)
MIROFISH_BACKEND = os.environ.get("MIROFISH_BACKEND") or next((p for p in (os.path.join(FL_ROOT, "mirofish", "backend"), os.path.join(ROOT, "mirofish", "backend")) if os.path.isdir(p)), os.path.join(FL_ROOT, "mirofish", "backend"))

sys.path.insert(0, os.path.join(MIROFISH_BACKEND, "scripts"))
sys.path.insert(0, MIROFISH_BACKEND)
sys.path.insert(0, HERE)

from dotenv import load_dotenv  # noqa: E402
# MiroFish's .env supplies LLM_API_KEY etc.; our own env (proxy URL, model) wins because
# load_dotenv does not override variables that are already set.
load_dotenv(os.path.join(MIROFISH_BACKEND, "..", ".env"))

import run_twitter_simulation as mf  # noqa: E402  (MiroFish's runner, imported as a library)
import oasis  # noqa: E402
from oasis import ActionType, LLMAction, ManualAction, generate_twitter_agent_graph  # noqa: E402
from oasis.social_platform.platform import Platform  # noqa: E402
from oasis.social_platform.channel import Channel  # noqa: E402

import personas  # noqa: E402
import decision  # noqa: E402
import laya_decider  # noqa: E402


def _now():
    return datetime.now().isoformat(timespec="seconds")


class FaultLinesRunner(mf.TwitterSimulationRunner):
    """MiroFish's Twitter runner, stepped round-by-round with a per-round dump."""

    def __init__(self, run_dir: str):
        self.run_dir = os.path.abspath(run_dir)
        with open(os.path.join(self.run_dir, "scenario.json"), encoding="utf-8") as f:
            self.scenario = json.load(f)
        super().__init__(os.path.join(self.run_dir, "simulation_config.json"), wait_for_commands=False)
        fl = self.config["faultlines"]
        self.code_to_agent = fl["code_to_agent"]
        self.agent_to_code = {v: k for k, v in self.code_to_agent.items()}
        self.agents_per_round = int(fl["agents_per_round"])
        self.semaphore = int(fl["semaphore"])
        self.max_rec_post_len = int(fl["max_rec_post_len"])
        self.following_post_count = int(fl["following_post_count"])
        self.followups = bool(fl.get("newswire_followups", True))
        self.state_agents = [a for a in self.config["agent_configs"] if a.get("state_code")]
        self.baseline = {a["state_code"]: a["baseline_grievance"] for a in self.state_agents}
        self.last_post_id = 0
        self.rounds_dir = os.path.join(self.run_dir, "rounds")
        os.makedirs(self.rounds_dir, exist_ok=True)
        self.rng = random.Random(self.scenario.get("seed", 42))

    # ---------- files ----------
    def progress(self, status, round_num, total, note=""):
        with open(os.path.join(self.run_dir, "progress.json"), "w", encoding="utf-8") as f:
            json.dump({"status": status, "round": round_num, "total_rounds": total,
                       "note": note, "updated_at": _now()}, f)

    def latest_states(self):
        """The backend's latest scored states (unrest, stage, grievance, bloc), or a calm baseline."""
        p = os.path.join(self.run_dir, "timeline.json")
        try:
            with open(p, encoding="utf-8") as f:
                tl = json.load(f)
            if tl.get("rounds"):
                return tl["rounds"][-1]["states"], tl["rounds"][-1].get("date_label"), tl["rounds"][-1]["round"]
        except Exception:
            pass
        return {c: {"unrest": u, "stage_name": "calm", "grievance": "other", "bloc": None} for c, u in self.baseline.items()}, None, -1

    def recent_posts_struct(self, n=24):
        """Last round's posts with ids, so picks can quote or like a real post."""
        try:
            files = sorted(os.listdir(self.rounds_dir), key=lambda x: int(x.split("_")[1].split(".")[0]) if x.endswith(".json") else -1)
            with open(os.path.join(self.rounds_dir, files[-1]), encoding="utf-8") as f:
                rd = json.load(f)
            out = [{"post_id": p["post_id"], "state": p.get("state"), "content": (p.get("quote_content") or p.get("content") or "")[:200]}
                   for p in rd["posts"] if (p.get("content") or p.get("quote_content"))]
            return out[-n:]
        except Exception:
            return []

    def decision_actions(self, r, active, headline, seed):
        """Who decides this round: engine llm (every active state is a full LLM agent), bank (a slice stays on the LLM agent,
        the rest pick from one round-wide option bank) or laya (one Laya pass decides everyone; see laya_actions)."""
        dl = self.config["faultlines"].get("decision_layer") or {}
        engine = laya_decider.engine_from(dl)
        if engine == "llm":
            return None
        if engine == "laya":
            acts = self.laya_actions(r, active, headline, seed, dl)
            if acts is not None:
                return acts
        # engine == "bank" (or Laya unavailable): today's path, unchanged
        states, date_label, _ = self.latest_states()
        pmap = self._personas()
        frac = float(dl.get("llm_agent_fraction", 0.25))
        ordered = sorted(active, key=lambda aid: -(states.get(self.agent_to_code.get(aid), {}) or {}).get("unrest", 0))
        n_llm = int(round(len(ordered) * frac))
        llm_ids, bank_ids = ordered[:n_llm], ordered[n_llm:]
        # states the story touches: named in the scenario, hit by this round's events, or next door to one that is
        text_low = (self.config["event_config"]["initial_posts"][0]["content"] + " " + (headline or "")).lower()
        hit = set()
        for code, pm in pmap.items():
            if re.search(r"\b" + re.escape(pm.get("name", code).lower()) + r"\b", text_low) or re.search(r"\b" + code.lower() + r"\b", text_low):
                hit.add(code)
        for ev in getattr(self, "_round_events", []) or []:
            hit |= set((ev.get("affected") or {}).get("states") or ev.get("affected_states") or [])
        hit |= {n for c in list(hit) for n in pmap.get(c, {}).get("neighbors", [])}
        keys = {}
        for aid in bank_ids:
            code = self.agent_to_code.get(aid)
            keys[aid] = decision.cluster_key(states.get(code), pmap.get(code, {}), hit=code in hit)
        clusters = sorted(set(keys.values()))
        if len(clusters) > 10:   # merge leans when the day is too varied (keep hit/watch)
            keys = {aid: "|".join(k.split("|")[:3]) + "|any|" + k.split("|")[4] for aid, k in keys.items()}
            clusters = sorted(set(keys.values()))
        context = self.context_lines(date_label)
        base = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        model = os.environ.get("LLM_MODEL_NAME", "qwen2.5:14b")
        t0 = time.time()
        try:
            self.progress("running", r - 1, getattr(self, "total", r), f"round {r}: one local call writes the option bank ({len(clusters)} situations, {len(bank_ids)} states)")
            bank = decision.build_bank(base, model, clusters, context, headline or seed[:300], self.recent_state_posts(6), int(dl.get("options_per_cluster", 6)),
                                       avoid=getattr(self, "_last_bank_texts", []))
        except Exception as e:
            print(f"  decision layer: bank call failed ({str(e)[:80]}); all agents on the LLM this round")
            return None
        if not bank:
            print("  decision layer: empty bank; all agents on the LLM this round")
            return None
        try:   # the bank is kept for inspection: runs/<id>/log/bank_r<N>.json
            os.makedirs(os.path.join(self.run_dir, "log"), exist_ok=True)
            with open(os.path.join(self.run_dir, "log", f"bank_r{r}.json"), "w", encoding="utf-8") as f:
                json.dump({"clusters": {aid: k for aid, k in keys.items()}, "bank": bank, "headline": headline_for_bank if 'headline_for_bank' in dir() else headline}, f, indent=1)
        except Exception:
            pass
        self._last_bank_texts = [o["text"] for v in bank.values() for o in v]
        picker = decision.Picker(self.rng, n_total=len(bank_ids))
        targets = self.recent_posts_struct()
        actions = {}
        n_quote = n_like = 0
        for aid in bank_ids:
            code = self.agent_to_code.get(aid)
            pm = pmap.get(code, {})
            pick = picker.pick(bank, keys[aid], pm.get("name", code), [pmap.get(n, {}).get("name", n) for n in pm.get("neighbors", [])],
                               [t for t in targets if t["state"] in pm.get("neighbors", []) or t["state"] is None], issues=pm.get("policy_pressures") or [])
            if not pick:
                continue
            agent = self.env.agent_graph.get_agent(aid)
            acts = []
            if pick["kind"] in ("quote", "reply") and pick["target"]:
                acts.append(ManualAction(action_type=ActionType.QUOTE_POST, action_args={"post_id": pick["target"]["post_id"], "quote_content": pick["text"][:600]}))
                n_quote += 1
            else:
                acts.append(ManualAction(action_type=ActionType.CREATE_POST, action_args={"content": pick["text"][:600]}))
            # engagement: like or repost something a neighbor said, like real accounts do
            nb = [t for t in targets if t["state"] in pm.get("neighbors", [])]
            if nb and self.rng.random() < 0.6:
                t = self.rng.choice(nb)
                acts.append(ManualAction(action_type=ActionType.REPOST if self.rng.random() < 0.25 else ActionType.LIKE_POST, action_args={"post_id": t["post_id"]}))
                n_like += 1
            actions[agent] = acts
        for aid in llm_ids:
            actions[self.env.agent_graph.get_agent(aid)] = LLMAction()
        print(f"  decision layer: {len(bank_ids)} states from a bank of {sum(len(v) for v in bank.values())} options in {len(bank)} clusters "
              f"({n_quote} quotes, {n_like} likes/reposts), {len(llm_ids)} full-LLM agents; bank call {time.time()-t0:.0f}s")
        return actions

    def round_posts_full(self):
        """Last round's posts with author, likes and ids, for Laya's feed view."""
        try:
            files = sorted((x for x in os.listdir(self.rounds_dir) if x.endswith(".json")), key=lambda x: int(x.split("_")[1].split(".")[0]))
            with open(os.path.join(self.rounds_dir, files[-1]), encoding="utf-8") as f:
                rd = json.load(f)
        except Exception:
            return []
        pmap = self._personas()
        out = []
        for p in rd.get("posts", []):
            txt = p.get("quote_content") or p.get("content") or ""
            if not txt.strip():
                continue
            st = p.get("state")
            out.append({"post_id": p["post_id"], "state": st, "author": (pmap.get(st, {}).get("name") or st) if st else "national_wire",
                        "likes": int(p.get("num_likes") or 0), "content": txt})
        return out

    def laya_actions(self, r, active, headline, seed, dl):
        """One Laya pass decides every active state; likes and reposts need no LLM; the angriest writers get one batched
        local LLM call; the other writers take option-bank text. Returns None (the caller uses the bank) if Laya is down."""
        if getattr(self, "_laya", None) is None:
            self._laya = laya_decider.LayaDecider(dl)
        if not self._laya.ready():
            return None
        t0 = time.time()
        states, date_label, _ = self.latest_states()
        pmap = self._personas()
        era = ""
        try:
            with open(os.path.join(self.run_dir, "scenario.json"), encoding="utf-8") as f:
                era = ((json.load(f).get("tech") or {}).get("era")) or ""
        except Exception:
            pass
        posts = self.round_posts_full()
        issue = "the main story this round: " + laya_decider._clip(headline or seed, 160)
        agents = []
        for aid in active:
            code = self.agent_to_code.get(aid)
            pm = pmap.get(code, {})
            nbrs = pm.get("neighbors") or []
            sc = dict(states.get(code) or {})
            lean = float(pm.get("partisan_lean") or 0)
            sc["lean"] = "right" if lean > 4 else "left" if lean < -4 else "swing"
            feed = laya_decider.feed_for(code, nbrs, posts)
            agents.append({"aid": aid, "code": code, "neighbors": nbrs, "posts": feed,
                           "text": laya_decider.state_text(pm.get("name", code), pm.get("persona", ""), sc, date_label, era, feed)})
        self.progress("running", r - 1, getattr(self, "total", r), f"round {r}: Laya decides what {len(agents)} states do")
        try:
            decisions = self._laya.decide(agents, issue, neighbor_bias=float(dl.get("neighbor_bias", 1.25)))
        except Exception as e:
            print(f"  laya: predict failed ({type(e).__name__}: {str(e)[:100]}); option bank this round")
            return None
        t_laya = time.time() - t0
        for d in decisions:
            d["laya_choice"] = d["action"]          # Laya's own top answer, kept in the log
        mix = dl.get("action_mix", laya_decider.DEFAULT_MIX)
        if mix:
            laya_decider.assign_mix(decisions, mix)
        unrest = {c: float((states.get(c) or {}).get("unrest", 0)) for c in self.agent_to_code.values()}
        items = laya_decider.plan(decisions, agents, unrest, max_writers=int(dl.get("max_llm_writers", 6)),
                                  quote_cap=float(dl.get("max_quote_share", 0.35)),
                                  low_conf_fallback=bool(dl.get("low_confidence_llm_fallback", False)),
                                  conf_min=float(dl.get("low_confidence_min", 0.55)))
        by_aid = {a["aid"]: a for a in agents}
        base = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
        model = os.environ.get("LLM_MODEL_NAME", "qwen2.5:14b")
        context = self.context_lines(date_label)
        # the angriest writers: one batched local call
        llm_w = [it for it in items if it["mode"] == "write_llm"]
        texts = {}
        if llm_w:
            self.progress("running", r - 1, getattr(self, "total", r), f"round {r}: one local call writes {len(llm_w)} posts")
            texts = laya_decider.write_texts(base, model, [
                {"id": by_aid[it["aid"]]["code"], "state": pmap.get(by_aid[it["aid"]]["code"], {}).get("name", by_aid[it["aid"]]["code"]),
                 "persona": pmap.get(by_aid[it["aid"]]["code"], {}).get("persona", ""),
                 "stage": (states.get(by_aid[it["aid"]]["code"]) or {}).get("stage_name", "calm"),
                 "stance": laya_decider.STANCE[max(0, min(4, int(round(it["decision"]["stance"]))))],
                 "kind": it["kind"], "target": (it["target"] or {}).get("content")} for it in llm_w], context, story=headline or seed)
        for it in llm_w:   # a writer the call did not cover falls back to the bank
            if by_aid[it["aid"]]["code"] not in texts:
                it["mode"] = "write_bank"
        # the rest of the writers: option-bank text (one bank call, only when needed)
        bank_w = [it for it in items if it["mode"] == "write_bank"]
        bank, keys = {}, {}
        if bank_w:
            for it in bank_w:
                code = by_aid[it["aid"]]["code"]
                keys[it["aid"]] = decision.cluster_key(states.get(code), pmap.get(code, {}), hit=False)
            clusters = sorted(set(keys.values()))
            if len(clusters) > 10:
                keys = {aid: "|".join(k.split("|")[:3]) + "|any|" + k.split("|")[4] for aid, k in keys.items()}
                clusters = sorted(set(keys.values()))
            try:
                bank = decision.build_bank(base, model, clusters, context, headline or seed[:300], self.recent_state_posts(6),
                                           int(dl.get("options_per_cluster", 6)), avoid=getattr(self, "_last_bank_texts", []))
                self._last_bank_texts = [o["text"] for v in bank.values() for o in v]
            except Exception as e:
                print(f"  laya: bank call failed ({str(e)[:80]}); those writers fall back to the LLM agent")
        picker = decision.Picker(self.rng, n_total=len(items))
        actions, counts, log = {}, {}, []
        for it in items:
            a = by_aid[it["aid"]]
            code = a["code"]
            pm = pmap.get(code, {})
            agent = self.env.agent_graph.get_agent(it["aid"])
            mode, text, source = it["mode"], None, None
            if mode == "write_llm":
                text, source = texts[code], "llm_writer"
            elif mode == "write_bank":
                pick = picker.pick(bank, keys[it["aid"]], pm.get("name", code), [pmap.get(n, {}).get("name", n) for n in pm.get("neighbors", [])],
                                   [], issues=pm.get("policy_pressures") or []) if bank else None
                if pick:
                    text, source = pick["text"], "bank"
                else:
                    mode = "llm_agent"
            kind = it["kind"]
            if mode == "llm_agent":
                actions[agent] = LLMAction()
            elif mode in ("like", "repost"):
                actions[agent] = ManualAction(action_type=ActionType.LIKE_POST if mode == "like" else ActionType.REPOST,
                                              action_args={"post_id": it["target"]["post_id"]})
            elif text and kind == "quote" and it["target"]:
                actions[agent] = ManualAction(action_type=ActionType.QUOTE_POST, action_args={"post_id": it["target"]["post_id"], "quote_content": text[:600]})
            elif text:
                kind = "post"
                actions[agent] = ManualAction(action_type=ActionType.CREATE_POST, action_args={"content": text[:600]})
            label = mode if mode in ("none", "like", "repost", "llm_agent") else kind
            counts[label] = counts.get(label, 0) + 1
            d = it["decision"]
            log.append({"state": code, "action": d["action"], "laya_choice": d.get("laya_choice"), "action_probs": d["action_probs"], "confidence": round(d["confidence"], 4),
                        "target": (it["target"] or {}).get("post_id"), "target_probs": {k: round(v, 4) for k, v in d["target_probs"].items()},
                        "stance": round(d["stance"], 3), "stance_probs": d["stance_probs"], "executed": label, "text_source": source,
                        "text": text, "feed": [p["post_id"] for p in a["posts"]]})
        try:
            os.makedirs(os.path.join(self.run_dir, "log"), exist_ok=True)
            with open(os.path.join(self.run_dir, "log", f"decisions_r{r}.json"), "w", encoding="utf-8") as f:
                json.dump({"engine": "laya", "device": self._laya.device, "issue": issue, "laya_seconds": round(t_laya, 2),
                           "decisions": log}, f, indent=1)
        except Exception:
            pass
        print(f"  laya: {len(items)} states decided in {t_laya:.1f}s: " + ", ".join(f"{k} {v}" for k, v in sorted(counts.items()))
              + f"; {len(texts)} LLM-written, {sum(1 for x in log if x['text_source'] == 'bank')} from the bank")
        return actions

    def _personas(self):
        if getattr(self, "_pmap", None) is None:
            try:
                with open(os.path.join(self.run_dir, "personas.json"), encoding="utf-8") as f:
                    self._pmap = json.load(f)
            except Exception:
                self._pmap = {}
        return self._pmap

    def context_lines(self, date_label):
        """The date, the era and the live situation, read from the scenario and the backend's files."""
        lines = []
        try:
            with open(os.path.join(self.run_dir, "scenario.json"), encoding="utf-8") as f:
                sc = json.load(f)
            if date_label:
                lines.append(f"TODAY'S DATE: {date_label}. Use only this year as the present.")
            t = (sc.get("tech") or {}).get("context")
            if t:
                lines.append(t[:500])
            gv = sc.get("govern") or {}
            if gv.get("president"):
                lines.append(f"The President is {gv['president'].get('name')}.")
        except Exception:
            pass
        try:
            with open(os.path.join(self.run_dir, "conflict.json"), encoding="utf-8") as f:
                cf = json.load(f)
            for b in list(cf.get("blocs", {}).values())[:3]:
                if not b.get("gone") and b.get("stage", 0) >= 1:
                    lines.append(f"Breakaway bloc {b['name']} ({', '.join(b['members'])}) is at stage '{b.get('stage')}' with identity {b.get('identity')}.")
        except Exception:
            pass
        return "\n".join(lines)

    def latest_unrest(self):
        """Read the backend's timeline (if it has scored anything) to weight activation."""
        p = os.path.join(self.run_dir, "timeline.json")
        try:
            with open(p, encoding="utf-8") as f:
                tl = json.load(f)
            if tl.get("rounds"):
                return {c: v["unrest"] for c, v in tl["rounds"][-1]["states"].items()}
        except Exception:
            pass
        return dict(self.baseline)

    def pick_active(self):
        """Weighted sample without replacement: angrier states post more (Efraimidis-Spirakis)."""
        unrest = self.latest_unrest()
        n = min(self.agents_per_round, len(self.state_agents))
        keyed = []
        for a in self.state_agents:
            w = 1.0 + unrest.get(a["state_code"], 20) / 40.0
            keyed.append((self.rng.random() ** (1.0 / w), a["agent_id"]))
        keyed.sort(reverse=True)
        return [aid for _, aid in keyed[:n]]

    def collect_round(self, round_num, active_ids):
        db = self._get_db_path()
        conn = sqlite3.connect(db)
        cur = conn.cursor()
        cur.execute("SELECT post_id, user_id, original_post_id, content, quote_content, num_likes, num_shares "
                    "FROM post WHERE post_id > ? ORDER BY post_id", (self.last_post_id,))
        new_posts = []
        for pid, uid, orig, content, quote, likes, shares in cur.fetchall():
            kind = "post" if orig is None else ("quote" if quote else "repost")
            new_posts.append({
                "post_id": pid, "agent_id": uid, "state": self.agent_to_code.get(uid),
                "kind": kind, "content": content or "", "quote_content": quote,
                "original_post_id": orig, "num_likes": likes, "num_shares": shares,
            })
            self.last_post_id = max(self.last_post_id, pid)
        cur.execute("SELECT post_id, num_likes, num_shares FROM post")
        engagement = {str(pid): {"likes": l, "shares": s} for pid, l, s in cur.fetchall()}
        cur.execute("SELECT action, COUNT(*) FROM trace GROUP BY action")
        action_counts = {a: c for a, c in cur.fetchall()}
        conn.close()
        out = {
            "round": round_num, "written_at": _now(), "events": list(getattr(self, "_round_events", []) or []),
            "active_states": [self.agent_to_code.get(i) for i in active_ids],
            "posts": new_posts, "engagement": engagement, "action_counts_cumulative": action_counts,
        }
        tmp = os.path.join(self.rounds_dir, f"round_{round_num}.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.rounds_dir, f"round_{round_num}.json"))
        return len(new_posts)

    # ---------- main loop ----------
    async def run(self, max_rounds=None):  # noqa: C901
        total = int(self.scenario["rounds"])
        print(f"FAULT LINES runner: {self.run_dir}  rounds={total}  agents/round={self.agents_per_round}")
        self.progress("starting", 0, total, "loading model + agents")

        model = self._create_model()  # MiroFish's: reads LLM_* from env
        self.agent_graph = await generate_twitter_agent_graph(
            profile_path=self._get_profile_path(), model=model, available_actions=self.AVAILABLE_ACTIONS)

        db_path = self._get_db_path()
        if os.path.exists(db_path):
            os.remove(db_path)
        channel = Channel()
        # "random" recsys: no HuggingFace model download, everything stays local.
        platform = Platform(db_path=db_path, channel=channel, recsys_type="random",
                            refresh_rec_post_count=3, max_rec_post_len=self.max_rec_post_len,
                            following_post_count=self.following_post_count)
        self.env = oasis.make(agent_graph=self.agent_graph, platform=platform,
                              database_path=db_path, semaphore=self.semaphore)
        await self.env.reset()

        # Geography: every state follows its land neighbors and the newswire.
        follows = {}
        for a in self.state_agents:
            agent = self.env.agent_graph.get_agent(a["agent_id"])
            targets = [self.code_to_agent[n] for n in a.get("neighbors", []) if n in self.code_to_agent]
            targets.append(personas.WIRE_AGENT_ID)
            follows[agent] = [ManualAction(action_type=ActionType.FOLLOW, action_args={"followee_id": t})
                              for t in targets]
        await self.env.step(follows)
        print(f"  follow graph built ({sum(len(v) for v in follows.values())} edges)")

        # Seed: the newswire posts the scenario.
        wire = self.env.agent_graph.get_agent(personas.WIRE_AGENT_ID)
        seed = self.config["event_config"]["initial_posts"][0]["content"]
        await self.env.step({wire: ManualAction(action_type=ActionType.CREATE_POST,
                                                action_args={"content": seed})})
        self.collect_round(0, [])
        self.progress("running", 0, total, "seed posted")

        t0 = time.time()
        self.total = total
        govern_mode = bool((self.config["faultlines"].get("govern") or {}))
        for r in range(1, total + 1):
            if govern_mode and r > 1:
                await self.wait_for_quarter(r - 1)
            active = self.pick_active()
            actions = None
            headline_for_bank = None
            fired = [ev for ev in (self.config["faultlines"].get("events") or []) if int(ev.get("round", 0)) == r]
            for ev in fired:
                print(f"  EVENT FIRED: {ev['name']} at round {r}")
            self._round_events = fired
            if r > 1 and self.followups and not fired:
                headline = self.govern_headline(r) or self.debate_recap(r) or self.newswire_followup(r, seed)
                if headline:
                    headline_for_bank = headline
            if fired:
                headline_for_bank = "; ".join(f"BREAKING: {ev['name']}. {ev['description']}" for ev in fired)[:600]
            actions = self.decision_actions(r, active, headline_for_bank, seed) or {self.env.agent_graph.get_agent(aid): LLMAction() for aid in active}
            if fired:
                actions[wire] = [ManualAction(action_type=ActionType.CREATE_POST,
                                              action_args={"content": "".join(ch for ch in f"BREAKING: {ev['name']}. {ev['description']}" if 32 <= ord(ch) < 127)[:600]})
                                 for ev in fired]
            elif headline_for_bank:
                actions[wire] = ManualAction(action_type=ActionType.CREATE_POST, action_args={"content": headline_for_bank})
            tr = time.time()
            self.progress("running", r - 1, total, f"round {r}: {len(active)} states are writing their posts")
            try:
                await self.env.step(actions)
            except Exception as e:  # keep going; a single bad round shouldn't kill the run
                print(f"  round {r}: step error: {e}")
            n = self.collect_round(r, active)
            print(f"  round {r}/{total}: {len(active)} active, {n} new posts, {time.time()-tr:.0f}s "
                  f"(total {time.time()-t0:.0f}s)")
            self.progress("running", r, total, f"round {r}: {n} posts written in {time.time()-tr:.0f}s; the backend is scoring them")
            if self._stop_requested():
                print("  stop requested")
                break

        election = self.config["faultlines"].get("election")
        if govern_mode and not self._stop_requested():
            await self.wait_for_quarter(total)   # the record is complete before the country votes on it
        try:  # a governing season rewrites the challengers against the record in year 4: the ballot uses the latest scenario
            with open(os.path.join(self.run_dir, "scenario.json"), encoding="utf-8") as f:
                election = json.load(f).get("election") or election
        except Exception:
            pass
        if election and not self._stop_requested():
            self.progress("running", total, total + 1, "election day: states are voting")
            await self.election_day(total + 1, election)
            self.progress("done", total + 1, total + 1, f"finished in {time.time()-t0:.0f}s")
        else:
            self.progress("done", min(r, total), total, f"finished in {time.time()-t0:.0f}s")
        await self.env.close()

    # ---------- election day ----------
    async def election_day(self, round_num, election):
        """Interview every state agent with the ballot (a real in-character decision), then write an
        Election Night round whose posts are each state's reason, plus the ballot block."""
        sys.path.insert(0, os.path.join(FL_ROOT, "backend"))
        from election import ballot_prompt, parse_ballot
        with open(os.path.join(self.run_dir, "personas.json"), encoding="utf-8") as f:
            pmap = json.load(f)
        actions = {}
        for a in self.state_agents:
            agent = self.env.agent_graph.get_agent(a["agent_id"])
            lean = (pmap.get(a["state_code"]) or {}).get("partisan_lean")
            stance = (pmap.get(a["state_code"]) or {}).get("ai_stance")
            actions[agent] = ManualAction(action_type=ActionType.INTERVIEW,
                                          action_args={"prompt": ballot_prompt(election, a["entity_name"], lean, a["state_code"], stance)})
        t0 = time.time()
        try:
            await self.env.step(actions)
        except Exception as e:
            print(f"  election step error: {e}")
        # read the interview responses back from the trace table
        conn = sqlite3.connect(self._get_db_path())
        cur = conn.cursor()
        cur.execute("SELECT user_id, info FROM trace WHERE action = ? ORDER BY created_at", (ActionType.INTERVIEW.value,))
        latest = {}
        for uid, info in cur.fetchall():
            try:
                d = json.loads(info) if info else {}
                latest[uid] = d.get("response", d) if isinstance(d, dict) else str(d)
            except Exception:
                latest[uid] = info
        conn.close()
        ballots, posts = {}, []
        pid = self.last_post_id + 1
        for a in self.state_agents:
            code = a["state_code"]
            raw = latest.get(a["agent_id"])
            raw = raw if isinstance(raw, str) else json.dumps(raw)
            b = parse_ballot(raw, election, code)
            ballots[code] = b
            name = {o["id"]: o["name"] for o in election["options"]}.get(b["vote"], "nobody")
            posts.append({"post_id": pid, "agent_id": a["agent_id"], "state": code, "kind": "post",
                          "content": f"{a['entity_name']} has voted: {name}. {b['reason']}", "quote_content": None,
                          "original_post_id": None, "num_likes": 0, "num_shares": 0})
            pid += 1
        voted = sum(1 for b in ballots.values() if b["vote"])
        print(f"  election day: {voted}/50 ballots parsed in {time.time()-t0:.0f}s")
        out = {"round": round_num, "written_at": _now(), "election_night": True, "ballot": ballots,
               "active_states": [a["state_code"] for a in self.state_agents], "posts": posts, "engagement": {},
               "action_counts_cumulative": {}}
        tmp = os.path.join(self.rounds_dir, f"round_{round_num}.json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=1)
        os.replace(tmp, os.path.join(self.rounds_dir, f"round_{round_num}.json"))

    def _stop_requested(self):
        return os.path.exists(os.path.join(self.run_dir, "STOP"))

    # ---------- newswire follow-ups ----------
    def recent_state_posts(self, n=12):
        """A sample of last round's state posts, for the newswire to react to."""
        try:
            files = sorted(os.listdir(self.rounds_dir), key=lambda x: int(x.split("_")[1].split(".")[0]) if x.endswith(".json") else -1)
            with open(os.path.join(self.rounds_dir, files[-1]), encoding="utf-8") as f:
                rd = json.load(f)
            out = []
            for p in rd["posts"]:
                if p.get("state"):
                    txt = p.get("quote_content") or p.get("content") or ""
                    if txt.strip():
                        out.append(f"[{p['state']}] {txt[:200]}")
            return out[:n]
        except Exception:
            return []

    async def wait_for_quarter(self, q, timeout=420):
        """Block until govern.json carries quarter q (the backend scores and steps one quarter per round), or the timeout."""
        import asyncio
        t0 = time.time()
        self.progress("running", q, getattr(self, "total", q), f"waiting for quarter {q} to be scored before the next one starts")
        while time.time() - t0 < timeout and not self._stop_requested():
            try:
                with open(os.path.join(self.run_dir, "govern.json"), encoding="utf-8") as f:
                    if any(x["quarter"] == q for x in json.load(f).get("quarters", [])):
                        return True
            except Exception:
                pass
            await asyncio.sleep(2)
        print(f"  quarter {q}: outcome not ready after {int(time.time() - t0)}s; continuing")
        return False

    def govern_headline(self, round_num):
        """Governing seasons: the wire opens each quarter with the previous quarter's outcome written by the rule engine."""
        try:
            with open(os.path.join(self.run_dir, "govern.json"), encoding="utf-8") as f:
                st = json.load(f)
        except Exception:
            return None
        q = next((x for x in st.get("quarters", []) if x["quarter"] == round_num - 1), None)
        if not q:
            return None
        txt = "".join(ch for ch in q["headline"] if 32 <= ord(ch) < 127)
        print(f"  wire (quarter): {txt[:120]}")
        return txt[:600]

    def debate_recap(self, round_num):
        """The morning after debate night the wire leads with the debate. Waits briefly for the
        backend to finish writing debate.json (it is one LLM call, usually seconds)."""
        election = self.config["faultlines"].get("election") or {}
        dr = election.get("debate_round")
        if not dr or round_num != dr + 1:
            return None
        path = os.path.join(self.run_dir, "debate.json")
        for _ in range(60):
            if os.path.exists(path):
                break
            time.sleep(1)
        try:
            with open(path, encoding="utf-8") as f:
                d = json.load(f)
            recap = "".join(ch for ch in d.get("recap", "") if 32 <= ord(ch) < 127).strip()
            names = {k: v["name"] for k, v in d.get("candidates", {}).items()}
            if recap:
                head = f"{self.date_label(round_num)}: DEBATE NIGHT RECAP. {names.get('A','A')} and {names.get('B','B')} clashed on live TV. {recap}"
                print(f"  wire (debate): {head[:120]}")
                return head[:500]
        except Exception as e:
            print(f"  debate recap unavailable ({e})")
        return None

    def date_label(self, round_num):
        try:
            sys.path.insert(0, os.path.join(FL_ROOT, "backend"))
            import calendar_sim as cal
            calx = self.config["faultlines"].get("calendar")
            return cal.label(calx, round_num) if calx else f"Day {round_num}"
        except Exception:
            return f"Day {round_num}"

    def newswire_followup(self, round_num, seed):
        """One short factual headline per round so the story moves. LLM via the (logged) proxy,
        template fallback. The federal side holds course: the sim is about the states' reaction."""
        day = self.date_label(round_num)
        gv = self.config["faultlines"].get("govern") or {}
        if gv:
            pres = (gv.get("president") or {}).get("name", "the President")
            return (f"Quarter {round_num}: the {pres} administration begins the quarter; Congress takes up the next item on the agenda "
                    f"while states weigh the last quarter's results.")
        fallback = (f"{day}: The measures take effect as scheduled. Federal officials say no waivers or delays "
                    f"are planned; state requests for consultation remain unanswered.")
        try:
            import urllib.request
            base = os.environ.get("LLM_BASE_URL", "http://localhost:11434/v1").rstrip("/")
            model = os.environ.get("LLM_MODEL_NAME", "qwen2.5:14b")
            sample = "\n".join(self.recent_state_posts())
            body = json.dumps({
                "model": model, "temperature": 0.7, "max_tokens": 120, "stream": False,
                "messages": [
                    {"role": "system", "content": "You are a terse national newswire. One factual headline, max 40 words, "
                                                  "no editorializing, no real people's names. The federal government keeps "
                                                  "the policy in place this round. Always respond in English only."},
                    {"role": "user", "content": f"Original bulletin: {seed}\n\nIt is now {day}. Reactions from states "
                                                f"yesterday:\n{sample}\n\nWrite today's wire headline about how the policy "
                                                f"is being implemented and what officials said in response to the states."},
                ]}).encode()
            req = urllib.request.Request(f"{base}/chat/completions", data=body,
                                         headers={"Content-Type": "application/json", "Authorization": "Bearer ollama", "X-FL-Purpose": "wire"})
            with urllib.request.urlopen(req, timeout=120) as resp:
                txt = json.load(resp)["choices"][0]["message"]["content"].strip().strip('"')
            # qwen occasionally drifts into CJK mid-sentence; keep the wire in plain printable ASCII
            txt = "".join(ch for ch in txt if 32 <= ord(ch) < 127).strip()
            if len(txt) < 20:
                return fallback
            headline = f"{day}: {txt}" if not txt.lower().startswith(("day", day.split(" ")[0].lower())) else txt
            print(f"  wire: {headline[:120]}")
            return headline[:400]
        except Exception as e:
            print(f"  wire fallback ({e})")
            return fallback


async def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-dir", required=True)
    args = ap.parse_args()
    run_dir = os.path.abspath(args.run_dir)
    mf.setup_oasis_logging(os.path.join(run_dir, "log"))
    runner = FaultLinesRunner(run_dir)
    try:
        await runner.run()
    except Exception as e:
        runner.progress("error", 0, int(runner.scenario["rounds"]), f"{type(e).__name__}: {e}")
        raise


if __name__ == "__main__":
    if sys.platform == "win32":
        os.environ.setdefault("PYTHONUTF8", "1")
    asyncio.run(main())
