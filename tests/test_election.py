"""Election math and the spoiler-proof night. Run: .venv/Scripts/python tests/test_election.py"""
import os
import random
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
import election as e  # noqa: E402
import election_night as n  # noqa: E402


def _personas():
    import json
    base = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "states.json")
    return {s["code"]: {**s, "baseline_grievance": 20} for s in json.load(open(base, encoding="utf-8"))["states"]}


def test_total_is_538_with_dc_and_districts():
    assert e.TOTAL_EV == 538 and e.EV["DC"] == 3 and e.EV["ME"] == 2 and e.EV["NE"] == 2
    assert sum(v[1] for v in e.DISTRICTS.values()) == 5


def test_two_candidate_tally_sums_and_calls_every_unit():
    el = e.normalize_election({"question": "q", "options": [{"name": "R", "lane": "right"}, {"name": "L", "lane": "left"}]})
    res = e.tally(e.demo_ballots(_personas(), el, 1), el)
    assert sum(res["ev"].values()) == 538 and len(res["states"]) == 56 and not res["no_winner"]
    assert res["states"]["DC"]["vote"] == "B", "DC goes to the left lane"
    assert res["states"]["NE-3"]["shares"]["A"] > res["states"]["NE"]["shares"]["A"], "NE-3 leans further right than the state"
    assert res["states"]["ME-1"]["shares"]["B"] > res["states"]["ME"]["shares"]["B"]


def test_third_candidate_off_ballot_and_colors():
    el = e.normalize_election({"question": "q", "options": [{"name": "R", "lane": "right"}, {"name": "L", "lane": "left"},
                                                            {"name": "ARIA", "lane": "center", "off_ballot": ["TX", "FL"]}]})
    assert [o["color"] for o in el["options"]] == ["#d94f3d", "#3d6fd9", "#8a5bd6"]
    assert "ARIA" in e.ballot_prompt(el, "Ohio", 11, "OH") and "Not on the ballot here: ARIA" in e.ballot_prompt(el, "Texas", 14, "TX")
    b = e.parse_ballot('{"shares": {"A": 40, "B": 40, "C": 20}, "turnout": 60, "reason": "x"}', el, "TX")
    assert "C" not in b["shares"] and abs(b["shares"]["A"] - 50) < 0.01, b
    res = e.tally(e.demo_ballots(_personas(), el, 2), el)
    assert res["states"]["TX"]["shares"]["C"] == 0.0 and res["states"]["OH"]["shares"]["C"] > 0
    assert sum(res["ev"].values()) == 538


def test_night_is_monotonic_and_ends_correctly():
    el = e.normalize_election({"question": "q", "options": [{"name": "R", "lane": "right"}, {"name": "L", "lane": "left"}]})
    res = e.tally(e.demo_ballots(_personas(), el, 3), el)
    night = n.build_night(res, el, seed=3)
    times = [n._minutes(t["time"]) for t in night["ticks"]]
    assert times == sorted(times), "clock never runs backwards"
    last = night["ticks"][-1]
    assert last["ev"] == res["ev"], "the night ends on the real tally"
    for u, c in last["counts"].items():
        assert c["status"] == "called" and c["vote"] == res["states"][u]["vote"]
    if res["winner"]:
        assert night["winner_tick"] is not None and night["ticks"][night["winner_tick"]]["winner"] == res["winner"]
        before = night["ticks"][night["winner_tick"] - 1]["ev"]
        assert max(before.values()) < 270, "nobody is projected before crossing 270 on the visible map"
    # early leans exist and converge: at least one unit's first count leads differently from its final call
    first = next(t for t in night["ticks"] if t["label"].startswith("5%"))
    assert any(max(c["shares"], key=c["shares"].get) != res["states"][u]["vote"] for u, c in first["counts"].items() if c["pct_in"] == 5)
    # commentary input never mentions a unit that has not reported
    txt = n.revealed_summary(night["ticks"][2], el, {c: c for c in res["states"]})
    assert "CA" not in txt and "Electoral votes so far" in txt


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok", fn.__name__)
    print(f"{len(fns)} election tests passed")
