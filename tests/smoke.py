"""
End-to-end smoke test against a running server (python run.py).
Default: demo run (no LLM). Pass --live to run the real OASIS engine for 2 rounds.

    python -I tests/smoke.py [--live] [--base http://127.0.0.1:8008]

Asserts the map contract: every round scores all 50 states in range, stages
match thresholds, blocs are adjacency-connected and below threshold, and every
protest-or-higher state has (or is getting) a scene image.
"""
import argparse
import json
import sys
import time
import urllib.request

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://127.0.0.1:8008")
ap.add_argument("--live", action="store_true")
ap.add_argument("--rounds", type=int, default=2)
ap.add_argument("--agents", type=int, default=8)
args = ap.parse_args()


def get(path):
    with urllib.request.urlopen(args.base + path, timeout=60) as r:
        return json.load(r)


def post(path, body):
    req = urllib.request.Request(args.base + path, data=json.dumps(body).encode(), headers={"content-type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


cfg = get("/api/config")
states = get("/api/states")
assert len(states) == 50, len(states)
body = {"text": "BREAKING: Congress passes a 12% federal surcharge on rent and mortgage payments and cuts state transfers by 30%.",
        "rounds": args.rounds if args.live else 5, "demo": not args.live, "agents_per_round": args.agents, "seed": 11}
started = post("/api/runs", body)
rid = started.get("run_id")
if rid is None:   # setup runs as a job now: POST returns {job_id}; the run id arrives when the job is done
    for _ in range(300):
        time.sleep(1)
        j = get(f"/api/jobs/{started['job_id']}")
        assert j["status"] != "error", j
        if j["status"] == "done":
            rid = j["run_id"]
            break
print("run", rid, "live" if args.live else "demo")
t0 = time.time()
deadline = 3600 if args.live else 300
tl = None
while time.time() - t0 < deadline:
    tl = get(f"/api/runs/{rid}")
    print(f"  {tl['status']:8s} scored={len(tl['rounds'])}/{body['rounds']+1} engine={tl.get('engine',{}).get('status')} r{tl.get('engine',{}).get('round')}  {time.time()-t0:.0f}s")
    if tl["status"] in ("done", "error"):
        break
    time.sleep(3 if not args.live else 15)
assert tl["status"] == "done", (tl["status"], tl.get("error"), tl.get("errors"))
assert len(tl["rounds"]) == body["rounds"] + 1, len(tl["rounds"])
assert not tl.get("errors"), tl["errors"]

th = cfg["scoring"]["stage_thresholds"]
bt = cfg["scoring"]["bloc_cohesion_threshold"]
for rd in tl["rounds"]:
    assert set(rd["states"]) == set(states), "every state scored every round"
    for c, s in rd["states"].items():
        for k in ("unrest", "federal_cohesion", "neighbor_cohesion"):
            assert 0 <= s[k] <= 100, (c, k, s[k])
        base_stage = sum(1 for t in th if s["unrest"] >= t)
        assert base_stage <= s["stage"] <= min(4, base_stage + 1), (c, s["unrest"], s["stage"])
    for b in rd["blocs"]:
        assert len(b["members"]) >= cfg["scoring"]["bloc_min_states"]
        for m in b["members"]:
            assert rd["states"][m]["federal_cohesion"] < bt, (b["name"], m)
            assert any(n in b["members"] for n in states[m]["neighbors"]), f"{m} not adjacent to its bloc {b['name']}"
        assert b["name"] and "<svg" in b["flag_svg"]
last = tl["rounds"][-1]
hot = [c for c, s in last["states"].items() if s["stage"] >= 2]
time.sleep(2)
tl = get(f"/api/runs/{rid}")
if (cfg.get("media") or {}).get("images", True):   # a server with scene images off (a zero-cost test config) has none to check
    missing = [c for c in hot if not any(k.startswith(c + "_") for k in tl["images"])]
    assert not missing, f"no scene for protest+ states: {missing}"
if hot:
    d = get(f"/api/runs/{rid}/state/{hot[0]}")
    assert d["persona"]["persona"] and d["history"], "state detail endpoint"
if args.live:
    assert last["posts_count"] > 0, "live engine produced no posts in the last round"
    totals = get("/api/llm/totals")
    assert totals["calls"] > 0 and totals["prompt_tokens"] > 0, totals
    print("llm totals", totals)
print(f"SMOKE OK: {len(tl['rounds'])} rounds, {len(last['blocs'])} blocs in final round, "
      f"{len(hot)} protest+ states, {len(tl['images'])} scenes, {time.time()-t0:.0f}s")
