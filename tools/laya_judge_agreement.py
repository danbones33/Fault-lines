"""
How well would Laya agree with the LLM judge? Offline, on finished runs; nothing in a run changes.

    <mirofish venv python> tools/laya_judge_agreement.py --runs 20261023-183155-2683 20261023-143351-3504

For every (state, round) the LLM judge scored, Laya reads the state's own posts that round and answers one score
question on the same five-step ladder (calm, petition, protest, strike, riot). Reports exact and within-one-step
agreement with the judge's stage, and the rank correlation between Laya's expected score and the judge's unrest number.
"""
import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LADDER = ["calm: no complaint, business as usual", "petition: complaints, letters, petitions and calls to officials",
          "protest: marches, rallies and crowds in the streets", "strike: walkouts, shutdowns and boycotts",
          "riot: violence, clashes and property destroyed"]


def spearman(a, b):
    def ranks(x):
        order = sorted(range(len(x)), key=lambda i: x[i])
        r = [0.0] * len(x)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and x[order[j + 1]] == x[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r
    ra, rb = ranks(a), ranks(b)
    n = len(a)
    ma, mb = sum(ra) / n, sum(rb) / n
    cov = sum((x - ma) * (y - mb) for x, y in zip(ra, rb))
    va = sum((x - ma) ** 2 for x in ra) ** 0.5
    vb = sum((y - mb) ** 2 for y in rb) ** 0.5
    return cov / (va * vb) if va and vb else 0.0


def samples(run_id):
    run_dir = os.path.join(ROOT, "runs", run_id)
    tl = json.load(open(os.path.join(run_dir, "timeline.json"), encoding="utf-8"))
    names = {c: p.get("name", c) for c, p in json.load(open(os.path.join(run_dir, "personas.json"), encoding="utf-8")).items()}
    out = []
    for r in tl["rounds"]:
        rp = os.path.join(run_dir, "rounds", f"round_{r['round']}.json")
        if r["round"] < 1 or not os.path.exists(rp):
            continue
        posts = {}
        for p in json.load(open(rp, encoding="utf-8")).get("posts", []):
            if p.get("state"):
                posts.setdefault(p["state"], []).append(p.get("quote_content") or p.get("content") or "")
        for code, st in r["states"].items():
            j = st.get("judge") or {}
            if code in posts and j.get("stage") is not None:
                text = f"State: {names.get(code, code)}. What its public account posted this round:\n" + "\n".join(f"- {t[:300]}" for t in posts[code][:4])
                out.append({"text": text, "stage": int(j["stage"]), "unrest": float(j.get("unrest", 0))})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", nargs="+", required=True)
    args = ap.parse_args()
    import torch
    from laya import Router
    router = Router(device="cuda" if torch.cuda.is_available() else "cpu", max_loaded=1)
    q = {"stage": {"type": "score", "instructions": "How far has unrest in this state gone, judging only by what it posted?",
                   "criteria": LADDER}}
    rows = []
    for run_id in args.runs:
        s = samples(run_id)
        res = router.predict_batch([{"state": x["text"], "questions": q, "model": "english"} for x in s], batch_size=16)
        for x, r in zip(s, res):
            a = r["answers"]["stage"]
            probs = {int(k): v for k, v in (a.get("probabilities") or {}).items()}
            x["laya_stage"] = max(probs, key=probs.get) if probs else round(a.get("score", 0))
            x["laya_score"] = float(a.get("score", 0))
            x["run"] = run_id
        rows += s
    n = len(rows)
    exact = sum(1 for x in rows if x["laya_stage"] == x["stage"]) / n
    near = sum(1 for x in rows if abs(x["laya_stage"] - x["stage"]) <= 1) / n
    rho = spearman([x["laya_score"] for x in rows], [x["unrest"] for x in rows])
    base = max(set(x["stage"] for x in rows), key=lambda s: sum(1 for x in rows if x["stage"] == s))
    always = sum(1 for x in rows if x["stage"] == base) / n
    print(f"{n} state-rounds from {len(args.runs)} runs")
    print(f"exact stage agreement {100 * exact:.0f}%  within one step {100 * near:.0f}%  "
          f"(always guessing the judge's most common stage, {base}, scores {100 * always:.0f}%)")
    print(f"rank correlation, Laya score vs judge unrest: {rho:.2f}")
    from collections import Counter
    print("judge stages:", dict(sorted(Counter(x["stage"] for x in rows).items())),
          " laya stages:", dict(sorted(Counter(x["laya_stage"] for x in rows).items())))


if __name__ == "__main__":
    main()
