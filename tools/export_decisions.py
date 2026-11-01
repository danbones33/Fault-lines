"""
Turn past runs' real LLM agent turns into Laya fine-tune pairs: (state text) -> (action, target).

    python tools/export_decisions.py                  # every run under runs/, writes runs/_laya_train/pairs.jsonl
    python tools/export_decisions.py --runs 2026102*  # a glob of run ids

Only turns an LLM actually decided are exported: in all-LLM runs every active state's turn; in option-bank runs the
active states not listed in log/bank_r<N>.json; in Laya runs only the states whose decision log says llm_agent.
The state text is built exactly as the Laya decider builds it (sim/laya_decider.state_text), with the feed taken from
the agent's own 'refresh' trace row (the posts it really saw) when there is one, else rebuilt from the previous round.
Labels: action in do_nothing | like | repost | quote | post (a turn with several actions keeps the strongest:
quote > post > repost > like), target post_1..post_5 | none.
"""
import argparse
import fnmatch
import glob
import json
import os
import sqlite3
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sim"))
import laya_decider as ld  # noqa: E402

PRIORITY = ["quote", "post", "repost", "like"]
KEEP = {"create_post": "post", "quote_post": "quote", "like_post": "like", "repost": "repost"}


def jload(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def export_run(run_dir):
    db = os.path.join(run_dir, "twitter_simulation.db")
    tl = jload(os.path.join(run_dir, "timeline.json"), {})
    pmap = jload(os.path.join(run_dir, "personas.json"), {})
    if not (os.path.exists(db) and tl.get("rounds") and pmap):
        return []
    era = ((jload(os.path.join(run_dir, "scenario.json"), {}) or {}).get("tech") or {}).get("era") or ""
    con = sqlite3.connect(db)
    users = {uid: name for uid, name in con.execute("select user_id, name from user")}
    code_of = {uid: (name or "").split("_")[0].upper() for uid, name in users.items() if (name or "").endswith("_voice")}
    uid_of = {c: u for u, c in code_of.items()}
    scores = {r["round"]: r["states"] for r in tl["rounds"]}
    dates = {r["round"]: r.get("date_label") for r in tl["rounds"]}
    out = []
    rnd = 1
    while True:
        rd = jload(os.path.join(run_dir, "rounds", f"round_{rnd}.json"))
        if not rd:
            break
        prev = jload(os.path.join(run_dir, "rounds", f"round_{rnd - 1}.json"), {}) or {}
        # the trace timestep of this round, from its own posts (fallback: round + 1)
        ts = rnd + 1
        if rd.get("posts"):
            row = con.execute("select created_at from post where post_id=?", (int(rd["posts"][0]["post_id"]),)).fetchone()
            ts = row[0] if row else ts
        bank = jload(os.path.join(run_dir, "log", f"bank_r{rnd}.json"), {}) or {}
        bank_ids = {int(a) for a in (bank.get("clusters") or {})}
        laya = jload(os.path.join(run_dir, "log", f"decisions_r{rnd}.json"), {}) or {}
        laya_llm = {d["state"] for d in laya.get("decisions", []) if d.get("executed") == "llm_agent"}
        prev_posts = []
        for p in prev.get("posts", []):
            txt = p.get("quote_content") or p.get("content") or ""
            if txt.strip():
                st = p.get("state")
                prev_posts.append({"post_id": str(p["post_id"]), "state": st, "author": (pmap.get(st, {}).get("name") or st) if st else "national_wire",
                                   "likes": int(p.get("num_likes") or 0), "content": txt})
        for code in rd.get("active_states") or []:
            uid = uid_of.get(code)
            if uid is None or (laya and code not in laya_llm) or (not laya and uid in bank_ids):
                continue
            rows = con.execute("select action, info from trace where user_id=? and created_at=?", (uid, ts)).fetchall()
            if any(a == "interview" for a, _ in rows):
                continue
            # the feed: what the agent's own refresh returned, else rebuilt the way the decider would
            feed = None
            for a, info in rows:
                if a == "refresh" and info:
                    try:
                        seen = json.loads(info).get("posts") or []
                        feed = [{"post_id": str(p["post_id"]), "state": code_of.get(p.get("user_id")),
                                 "author": pmap.get(code_of.get(p.get("user_id")), {}).get("name") or users.get(p.get("user_id")) or "account",
                                 "likes": int(p.get("num_likes") or 0), "content": p.get("content") or ""}
                                for p in seen if p.get("user_id") != uid][: ld.MAX_POSTS]
                    except Exception:
                        feed = None
            if feed is None:
                feed = ld.feed_for(code, pmap.get(code, {}).get("neighbors") or [], prev_posts)
            acts, target_id = [], None
            for a, info in rows:
                lab = KEEP.get(a)
                if not lab:
                    continue
                acts.append(lab)
                try:
                    j = json.loads(info or "{}")
                except Exception:
                    j = {}
                tid = j.get("post_id") if lab == "like" else j.get("quoted_id") if lab == "quote" else j.get("reposted_id") if lab == "repost" else None
                if tid is not None and (target_id is None or lab in ("quote", "repost")):
                    target_id = str(tid)
            action = next((k for k in PRIORITY if k in acts), "do_nothing")
            ids = [p["post_id"] for p in feed]
            target = f"post_{ids.index(target_id) + 1}" if target_id in ids else "none"
            sc = dict((scores.get(rnd - 1) or {}).get(code) or {})
            lean = float(pmap.get(code, {}).get("partisan_lean") or 0)
            sc["lean"] = "right" if lean > 4 else "left" if lean < -4 else "swing"
            text = ld.state_text(pmap.get(code, {}).get("name", code), pmap.get(code, {}).get("persona", ""), sc,
                                 dates.get(rnd - 1), era, feed)
            out.append({"run_id": os.path.basename(run_dir), "round": rnd, "state": code, "text": text,
                        "action": action, "target": target, "n_posts": len(feed)})
        rnd += 1
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="*")
    ap.add_argument("--out", default=os.path.join(ROOT, "runs", "_laya_train", "pairs.jsonl"))
    args = ap.parse_args()
    dirs = [d for d in sorted(glob.glob(os.path.join(ROOT, "runs", "*")))
            if os.path.isdir(d) and not os.path.basename(d).startswith("_") and fnmatch.fnmatch(os.path.basename(d), args.runs)]
    pairs, per_run = [], Counter()
    for d in dirs:
        try:
            got = export_run(d)
        except Exception as e:
            print(f"  skip {os.path.basename(d)}: {type(e).__name__}: {e}")
            continue
        pairs += got
        per_run[os.path.basename(d)] = len(got)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        for p in pairs:
            f.write(json.dumps(p) + "\n")
    print(f"{len(pairs)} samples from {sum(1 for v in per_run.values() if v)} runs -> {args.out}")
    print("actions:", dict(Counter(p["action"] for p in pairs).most_common()))
    print("targets:", dict(Counter("none" if p["target"] == "none" else "a post" for p in pairs)))


if __name__ == "__main__":
    main()
