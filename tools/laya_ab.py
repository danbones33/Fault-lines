"""
A/B of the agent decision engines, local only and free: llm vs bank vs laya.

    python tools/laya_ab.py                 # runs all three, then writes reports/laya-ab.md
    python tools/laya_ab.py --report-only   # rebuild the report from runs/_ab/ab.json

Each arm runs the same scenario with the same seed for 6 rounds on a separate test server (port 8009) whose config is
derived from config.yaml with: every text kind on Ollama, no cloud kinds, media off (images, memes, avatars, voices,
video, broadcasts, rants, NPC reactions, episodes) and a $0 run budget. The paid providers' API keys are blanked in the
test server's environment, so nothing paid can even authenticate. After each run, every call-log row for that run is
checked: any provider other than ollama stops the A/B.
"""
import argparse
import difflib
import json
import os
import random
import re
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.request
from collections import Counter, defaultdict

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
AB_DIR = os.path.join(ROOT, "runs", "_ab")
PORT = 8009
BASE = f"http://127.0.0.1:{PORT}"
SCENARIO = ("BREAKING: The Colorado River Compact collapses. After a fourth straight failed snowpack, the Bureau of Reclamation cuts "
            "water allocations to Arizona, Nevada and California by 40 percent and orders farm districts in the Imperial Valley "
            "fallowed. The White House invokes emergency powers to seize state water rights 'for the duration', suspends the compact, "
            "and signs a deal selling desalination contracts to a single firm, Meridian Hydro. Las Vegas casinos get an exemption; "
            "lettuce farmers do not. Governors of Utah, Colorado and Wyoming say the upper basin will not release another acre-foot "
            "until the seizure is reversed.")
PAID_KEYS = ["OPENROUTER_API_KEY", "CARTESIA_API_KEY", "FISH_AUDIO_API_KEY", "FISH_API_KEY", "FAL_KEY", "ELEVENLABS_API_KEY"]
PRICE_MODEL = "deepseek/deepseek-v4.1-flash"


def local_config():
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    cfg["llm"].update({"provider": "ollama", "local_kinds": ["all"], "cloud_kinds": []})
    cfg["media"].update({"memes": False, "images": False, "avatars": False, "video": False, "broadcasts": False, "rants": False,
                         "memes_per_round": 0, "npc_reactions_per_round": 0})
    cfg["images"]["provider"] = "placeholder"
    cfg["video"]["provider"] = "none"
    cfg["broadcast"]["tts_provider"] = "none"
    cfg["spend"]["run_budget_usd"] = 0.0
    cfg.setdefault("episode", {})["auto_build"] = False
    cfg["server"]["port"] = PORT
    os.makedirs(AB_DIR, exist_ok=True)
    path = os.path.join(AB_DIR, "config.local.yaml")
    yaml.safe_dump(cfg, open(path, "w", encoding="utf-8"), sort_keys=False)
    return path


def http(path, body=None, timeout=60):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def start_server(cfg_path, engine):
    env = os.environ.copy()
    for k in PAID_KEYS:
        env[k] = ""                     # blank: the .env loader never overrides a variable that is already set
    env.update({"FL_CONFIG": cfg_path, "FL_DECIDER": engine, "RUN_BUDGET_USD": "0", "TTS_PROVIDER": "none",
                "VIDEO_PROVIDER": "none", "PYTHONIOENCODING": "utf-8"})
    log = open(os.path.join(AB_DIR, f"server_{engine}.log"), "w", encoding="utf-8")
    py = os.path.join(ROOT, ".venv", "Scripts", "python.exe") if os.name == "nt" else os.path.join(ROOT, ".venv", "bin", "python")
    proc = subprocess.Popen([py, "run.py"], cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0)
    for _ in range(60):
        time.sleep(1)
        try:
            c = http("/api/config", timeout=5)
            assert c["llm"]["provider"] in ("ollama",), c["llm"]
            return proc
        except Exception:
            continue
    proc.kill()
    raise RuntimeError("test server did not start; see " + log.name)


def stop_server(proc):
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True)
        else:
            proc.send_signal(signal.SIGTERM)
    except Exception:
        pass


def paid_rows(run_id):
    bad = []
    with open(os.path.join(ROOT, "logs", "llm_calls.jsonl"), encoding="utf-8") as f:
        for line in f:
            if run_id not in line:
                continue
            r = json.loads(line)
            if r.get("run_id") == run_id and (r.get("provider") or "ollama") not in ("ollama", "laya"):
                bad.append(r)
    return bad


def run_arm(cfg_path, engine, rounds, agents, seed):
    proc = start_server(cfg_path, engine)
    try:
        job = http("/api/runs", {"text": SCENARIO, "rounds": rounds, "agents_per_round": agents, "seed": seed,
                                 "title": f"A/B {engine}", "start_year": 2036, "start_month": 6})
        run_id = None
        for _ in range(600):
            time.sleep(2)
            j = http(f"/api/jobs/{job['job_id']}")
            if j["status"] == "error":
                raise RuntimeError(j["error"])
            if j["status"] == "done":
                run_id = j["run_id"]
                break
        print(f"  {engine}: run {run_id}", flush=True)
        t0 = time.time()
        while time.time() - t0 < 3600:
            time.sleep(10)
            bad = paid_rows(run_id)
            if bad:
                http(f"/api/runs/{run_id}/stop", {})
                raise SystemExit(f"STOPPED: a paid call in {run_id}: {bad[0]}")
            r = http(f"/api/runs/{run_id}")
            if r.get("status") in ("done", "error"):
                break
        bad = paid_rows(run_id)
        if bad:
            raise SystemExit(f"STOPPED: a paid call in {run_id}: {bad[0]}")
        return run_id
    finally:
        stop_server(proc)


# ---------------- analysis ----------------
def runner_round_seconds(run_dir):
    """Wall seconds per round from the runner's running total: deciding (Laya, bank, writer) plus OASIS's step.
    Round 1 includes loading Laya once (about 15 s), so the mean over rounds 2+ is reported as well."""
    totals = []
    for line in open(os.path.join(run_dir, "runner.log"), encoding="utf-8", errors="replace"):
        m = re.search(r"round (\d+)/\d+: .*\(total (\d+)s\)", line)
        if m:
            totals.append(int(m.group(2)))
    return [b - a for a, b in zip([0] + totals, totals)]


def calls_for(run_id):
    by = defaultdict(lambda: [0, 0, 0])
    with open(os.path.join(ROOT, "logs", "llm_calls.jsonl"), encoding="utf-8") as f:
        for line in f:
            if run_id not in line:
                continue
            r = json.loads(line)
            if r.get("run_id") != run_id or r.get("purpose") in ("tts", "images", "memes", "avatars"):
                continue
            v = by[r.get("purpose") or "?"]
            v[0] += 1
            v[1] += int(r.get("prompt_tokens") or 0)
            v[2] += int(r.get("completion_tokens") or 0)
    return dict(by)


def action_mix(run_dir):
    con = sqlite3.connect(os.path.join(run_dir, "twitter_simulation.db"))
    rows = con.execute("select user_id, created_at, action from trace where user_id between 1 and 50").fetchall()
    keep = {"create_post": "post", "quote_post": "quote", "like_post": "like", "repost": "repost"}
    acts = Counter(keep[a] for _, _, a in rows if a in keep)
    turns = 0
    for fn in os.listdir(os.path.join(run_dir, "rounds")):
        rd = json.load(open(os.path.join(run_dir, "rounds", fn), encoding="utf-8"))
        if rd.get("round", 0) >= 1:
            turns += len(rd.get("active_states") or [])
    acting = len({(u, t) for u, t, a in rows if a in keep})
    acts["nothing"] = max(0, turns - acting)
    return acts, turns


def state_posts(run_dir):
    out = []
    for fn in sorted(os.listdir(os.path.join(run_dir, "rounds")), key=lambda x: int(x.split("_")[1].split(".")[0])):
        rd = json.load(open(os.path.join(run_dir, "rounds", fn), encoding="utf-8"))
        for p in rd.get("posts", []):
            if p.get("state"):
                t = (p.get("quote_content") or p.get("content") or "").strip()
                if t:
                    out.append((p["state"], t))
    return out


def dup_rate(texts, thresh=0.75):
    norm = [re.sub(r"[^a-z ]+", "", t.lower()) for t in texts]
    dup = 0
    for i, a in enumerate(norm):
        if any(difflib.SequenceMatcher(None, a, b).ratio() >= thresh for b in norm[:i]):
            dup += 1
    return dup / max(1, len(norm))


def laya_seconds(run_dir):
    d = os.path.join(run_dir, "log")
    vals = []
    for fn in os.listdir(d) if os.path.isdir(d) else []:
        if fn.startswith("decisions_r"):
            vals.append(json.load(open(os.path.join(d, fn), encoding="utf-8")).get("laya_seconds", 0))
    return vals


def report(arms):
    sys.path.insert(0, os.path.join(ROOT, "backend"))
    from spend import Pricing
    cfg = yaml.safe_load(open(os.path.join(ROOT, "config.yaml"), encoding="utf-8"))
    pricing = Pricing(cfg["llm"]["openrouter_base_url"])   # OpenRouter's public price list (no key, no charge)
    rows, quality = [], []
    for eng, run_id in arms.items():
        run_dir = os.path.join(ROOT, "runs", run_id)
        rounds = len(runner_round_seconds(run_dir)) or 1
        calls = calls_for(run_id)
        secs = runner_round_seconds(run_dir)
        tot_pt = sum(v[1] for v in calls.values())
        tot_ct = sum(v[2] for v in calls.values())
        agent_side = {k: v for k, v in calls.items() if k in ("agent", "bank", "writer")}
        a_pt = sum(v[1] for v in agent_side.values())
        a_ct = sum(v[2] for v in agent_side.values())
        usd_agent = pricing.chat_cost(PRICE_MODEL, a_pt, a_ct)
        usd_all = pricing.chat_cost(PRICE_MODEL, tot_pt, tot_ct)
        per_round = ", ".join(f"{k} {v[0] / rounds:.1f}" for k, v in sorted(calls.items(), key=lambda kv: -kv[1][0]))
        lay = laya_seconds(run_dir)
        rows.append((eng, run_id, per_round, f"{a_pt + a_ct:,}", f"{tot_pt + tot_ct:,}", f"{sum(secs) / max(1, len(secs)):.0f} (rounds 2+: {sum(secs[1:]) / max(1, len(secs) - 1):.0f})",
                     f"${usd_agent:.4f}", f"${usd_all:.4f}", f"{sum(lay) / len(lay):.2f}s" if lay else "-"))
        acts, turns = action_mix(run_dir)
        posts = state_posts(run_dir)
        texts = [t for _, t in posts]
        rng = random.Random(7)
        sample = rng.sample(posts, min(5, len(posts)))
        quality.append((eng, acts, turns, len(texts), dup_rate(texts), sample))
    lines = ["# Laya A/B: who decides what the states do", "",
             f"Same scenario (Colorado River seizure), same seed, 6 rounds, 25 active states per round, local qwen2.5:14b, media off. "
             f"Run on {time.strftime('%Y-%m-%d')}. Dollar columns are what the tokens would have cost on OpenRouter "
             f"{PRICE_MODEL} (backend Pricing class); the runs themselves cost $0.", "",
             "| engine | run | LLM calls per round, by purpose | agent-side tokens (agent+bank+writer) | all tokens (incl. judge) | sec / round | agent-side $ equiv | all $ equiv | Laya pass |",
             "|---|---|---|---|---|---|---|---|---|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    lines += ["", "## Quality sniff", ""]
    lines += ["| engine | turns | post | quote | like | repost | nothing | state posts | duplicate-text rate |", "|---|---|---|---|---|---|---|---|---|"]
    for eng, acts, turns, n, dr, _ in quality:
        pct = lambda k: f"{100 * acts.get(k, 0) / max(1, turns):.0f}%"
        lines.append(f"| {eng} | {turns} | {pct('post')} | {pct('quote')} | {pct('like')} | {pct('repost')} | {pct('nothing')} | {n} | {100 * dr:.0f}% |")
    lines += ["", "Percentages are actions per active-state turn (a bank turn can post and like in the same turn, so rows can pass 100%). "
              "Duplicate-text rate: share of state posts at least 75% similar to an earlier post in the same run.", ""]
    for eng, _, _, _, _, sample in quality:
        lines += [f"### Sample posts: {eng}", ""] + [f"- **{st}**: {t[:300]}" for st, t in sample] + [""]
    os.makedirs(os.path.join(ROOT, "reports"), exist_ok=True)
    out = os.path.join(ROOT, "reports", "laya-ab.md")
    open(out, "w", encoding="utf-8").write("\n".join(lines))
    print("\n".join(lines[:12]))
    print("wrote", out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rounds", type=int, default=6)
    ap.add_argument("--agents", type=int, default=25)
    ap.add_argument("--seed", type=int, default=11)
    ap.add_argument("--engines", default="llm,bank,laya")
    ap.add_argument("--report-only", action="store_true")
    ap.add_argument("--label", default=None, help="store the (single) arm under this name in the report")
    args = ap.parse_args()
    state_path = os.path.join(AB_DIR, "ab.json")
    arms = json.load(open(state_path)) if os.path.exists(state_path) else {}
    if not args.report_only:
        cfg_path = local_config()
        for eng in args.engines.split(","):
            print(f"== {eng}", flush=True)
            arms[args.label or eng] = run_arm(cfg_path, eng, args.rounds, args.agents, args.seed)
            json.dump(arms, open(state_path, "w"), indent=1)
    report(arms)


if __name__ == "__main__":
    main()
