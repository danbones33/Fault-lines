"""Laya decider checks with a fake Router: no download, no GPU, no simulator. Run: python -I tests/test_laya_decider.py"""
import os
import subprocess
from collections import Counter
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "sim"))
import laya_decider as ld  # noqa: E402


def probs(choice, keys, p=0.7):
    rest = (1 - p) / max(1, len(keys) - 1)
    return {k: (p if k == choice else rest) for k in keys}


class FakeRouter:
    """Answers from a script: {state_name: (action, target_key, stance)}."""
    def __init__(self, script):
        self.script = script
        self.calls = 0

    def predict_batch(self, requests, batch_size=None):
        self.calls += 1
        out = []
        for req in requests:
            name = req["state"].split("\n")[0].replace("State: ", "").rstrip(".")
            act, tgt, stance = self.script[name]
            tkeys = list(req["questions"]["target"]["criteria"])
            out.append({"answers": {
                "action": {"choice": act, "probabilities": probs(act, ld.ACTIONS), "answer_confidence": 0.7},
                "target": {"choice": tgt, "probabilities": probs(tgt, tkeys, 0.6)},
                "stance": {"score": stance, "probabilities": {}},
            }})
        return out


POSTS = [{"post_id": "1", "state": "CA", "author": "California", "likes": 10, "content": "California is done paying."},
         {"post_id": "2", "state": None, "author": "national_wire", "likes": 500, "content": "BREAKING: memo leaks."},
         {"post_id": "3", "state": "AZ", "author": "Arizona", "likes": 3, "content": "Arizona stands with the basin."}]


def agent(code, name, posts=POSTS, neighbors=("CA", "AZ")):
    return {"aid": code, "code": code, "neighbors": list(neighbors), "posts": list(posts),
            "text": ld.state_text(name, "x" * 900, {"unrest": 40, "stage_name": "protest"}, "July 1, 2036", "Present Day", posts)}


def test_engine_switch():
    assert ld.engine_from({"enabled": False}, env={}) == "llm"              # layer off: today's all-LLM path
    assert ld.engine_from({"enabled": True}, env={}) == "bank"              # layer on, no engine key: today's bank path
    assert ld.engine_from({"enabled": True, "engine": "laya"}, env={}) == "laya"
    assert ld.engine_from({"enabled": True, "engine": "nonsense"}, env={}) == "bank"
    assert ld.engine_from({"enabled": True, "engine": "laya"}, env={"FL_DECIDER": "llm"}) == "llm"
    assert ld.engine_from({"enabled": False}, env={"FL_DECIDER": "bank"}) == "bank"


def test_state_text_clips_and_lists_posts():
    t = ld.state_text("Nevada", "p" * 2000, {"unrest": 44, "federal_cohesion": 38, "bloc": None}, "July 1, 2036", "Present Day", POSTS * 3)
    assert "State: Nevada." in t and "post_5" in t and "post_6" not in t
    assert len(t.split("Persona: ")[1].split("\n")[0]) <= 600
    assert "Not in any bloc" in t and "Unrest 44" in t and "Trust in Washington 38" in t


def test_feed_prefers_neighbors_then_likes():
    posts = POSTS + [{"post_id": "4", "state": "NY", "likes": 900, "content": "New York"}, {"post_id": "5", "state": "NV", "likes": 1, "content": "own"}]
    feed = ld.feed_for("NV", ["CA", "AZ"], posts)
    ids = [p["post_id"] for p in feed]
    assert ids[:2] == ["3", "1"], ids          # neighbors first, newest first
    assert "5" not in ids                       # never its own post
    assert ids.index("4") < ids.index("2")      # then the most liked


def test_questions_shape():
    q = ld.questions(3, "the water seizure")
    assert list(q["action"]["criteria"]) == ld.ACTIONS
    assert list(q["target"]["criteria"]) == ["post_1", "post_2", "post_3", "none"]
    assert q["stance"]["type"] == "score" and len(q["stance"]["criteria"]) == 5


def test_decide_and_plan_rules():
    script = {"Nevada": ("like", "none", 0.5), "Utah": ("quote", "none", 1.0), "Texas": ("quote", "post_2", 0.0),
              "Ohio": ("post", "none", 3.0), "Iowa": ("do_nothing", "none", 2.0), "Idaho": ("repost", "post_3", 1.0)}
    agents = [agent("NV", "Nevada"), agent("UT", "Utah"), agent("TX", "Texas"), agent("OH", "Ohio"), agent("IA", "Iowa"), agent("ID", "Idaho")]
    dec = ld.LayaDecider(router=FakeRouter(script))
    ds = dec.decide(agents, "the water seizure")
    assert len(ds) == 6 and dec.router.calls == 1          # one batched call for everyone
    unrest = {"NV": 50, "UT": 10, "TX": 80, "OH": 60, "IA": 5, "ID": 20}
    items = {it["aid"]: it for it in ld.plan(ds, agents, unrest, max_writers=2, quote_cap=0.5)}
    assert items["NV"]["mode"] == "like" and items["NV"]["target"] is not None      # 'none' -> likeliest post
    assert items["UT"]["kind"] == "post"                                              # quote without a target -> post
    assert items["TX"]["kind"] == "quote" and items["TX"]["target"]["post_id"] == "2"
    assert items["IA"]["mode"] == "none"
    assert items["ID"]["mode"] == "repost" and items["ID"]["target"]["post_id"] == "3"
    # writers: TX, OH, UT; the two angriest (TX 80, OH 60) get LLM text, UT takes bank text
    assert items["TX"]["mode"] == "write_llm" and items["OH"]["mode"] == "write_llm" and items["UT"]["mode"] == "write_bank"


def test_quote_cap_and_low_confidence():
    script = {n: ("quote", "post_1", 1.0) for n in ("A", "B", "C", "D")}
    agents = [agent(c, c) for c in "ABCD"]
    ds = ld.LayaDecider(router=FakeRouter(script)).decide(agents, "x")
    items = ld.plan(ds, agents, {c: 1 for c in "ABCD"}, quote_cap=0.25)
    assert sum(1 for it in items if it["kind"] == "quote") == 1          # 25% of 4
    items = ld.plan(ds, agents, {c: 1 for c in "ABCD"}, low_conf_fallback=True, conf_min=0.9)
    assert all(it["mode"] == "llm_agent" for it in items)                 # 0.7 < 0.9: everyone back on the LLM agent


def test_action_mix_quotas_follow_relative_preference():
    # five states with nearly flat probabilities; each leans a little toward a different action
    lean = ["post", "quote", "like", "repost", "do_nothing"]
    ds = []
    for i, a in enumerate(lean * 4):
        p = {k: 0.19 for k in ld.ACTIONS}
        p[a] = 0.24
        p["like"] += 0.06                                  # like wins the raw top answer for most states
        ds.append({"action": max(p, key=p.get), "action_probs": p})
    assert sum(1 for d in ds if d["action"] == "like") >= 12
    ld.assign_mix(ds, {"post": 0.4, "quote": 0.2, "like": 0.2, "repost": 0.1, "do_nothing": 0.1})
    got = Counter(d["action"] for d in ds)
    assert got == Counter({"post": 8, "quote": 4, "like": 4, "repost": 2, "do_nothing": 2}), got
    # the quota for quote goes to states that lean toward quote
    assert all(ds[i]["action"] == "quote" for i in range(1, 20, 5))
    assert ld.assign_mix(ds[:0], ld.DEFAULT_MIX) == []


def test_neighbor_bias():
    script = {"Nevada": ("like", "post_2", 1.0)}
    a = agent("NV", "Nevada")
    d = ld.LayaDecider(router=FakeRouter(script)).decide([a], "x", neighbor_bias=1.0)[0]
    assert d["target_key"] == "post_2"
    d = ld.LayaDecider(router=FakeRouter(script)).decide([a], "x", neighbor_bias=20.0)[0]
    assert d["target_key"] in ("post_1", "post_3")                       # a strong nudge moves it to a neighbor's post


def test_writer_ids_map_back():
    """The writer call numbers its accounts 1..N; answers by number, 'id 3' or the state's name all land on the right state."""
    import io
    import json as _json
    import urllib.request as ur
    reply = {"posts": [{"id": 1, "text": "Nevada is done waiting for Washington to give our water back."},
                       {"id": "Utah", "text": "Utah will not release another acre-foot until the seizure ends."},
                       {"id": "id 3", "text": "Texas says keep your hands off our rivers, period."},
                       {"id": 4, "text": "short"}]}
    body = _json.dumps({"choices": [{"message": {"content": _json.dumps(reply)}}]}).encode()
    saved = ur.urlopen
    ur.urlopen = lambda req, timeout=None: io.BytesIO(body)
    try:
        w = [{"id": c, "state": n, "persona": "p", "stage": "protest", "stance": "opposed", "kind": "post", "target": None}
             for c, n in (("NV", "Nevada"), ("UT", "Utah"), ("TX", "Texas"), ("OH", "Ohio"))]
        out = ld.write_texts("http://x", "m", w, "ctx")
    finally:
        ur.urlopen = saved
    assert set(out) == {"NV", "UT", "TX"}, out                            # 'short' is dropped
    assert out["UT"].startswith("Utah")


def test_missing_laya_falls_back():
    logged = []
    saved = sys.modules.get("laya")
    sys.modules["laya"] = None                                           # import laya -> ImportError
    try:
        dec = ld.LayaDecider(log=logged.append)
        assert dec.ready() is False and dec.failed
        assert dec.ready() is False and len(logged) == 1                 # one warning, not one per round
    finally:
        if saved is None:
            sys.modules.pop("laya", None)
        else:
            sys.modules["laya"] = saved


def test_bank_and_llm_paths_unchanged():
    """The bank path in runner.decision_actions is byte-for-byte the baseline commit's (main)."""
    try:
        base = subprocess.run(["git", "-C", ROOT, "show", "main:sim/runner.py"], capture_output=True, text=True, check=True).stdout
    except Exception:
        print("  (git unavailable; skipped)")
        return
    cur = open(os.path.join(ROOT, "sim", "runner.py"), encoding="utf-8").read()
    start = base.index('        if not dl.get("enabled"):\n            return None\n') + len('        if not dl.get("enabled"):\n            return None\n')
    end = base.index("    def _personas(self):")
    bank_block = base[start:end]
    assert bank_block in cur, "the bank path changed"
    assert "laya_decider.engine_from(dl)" in cur and 'if engine == "llm":\n            return None' in cur


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print("ok ", fn.__name__)
    print(f"{len(fns)} passed")
