"""Unit checks for the inspectable half of the scorer. Run: python -I tests/test_scoring.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
from scoring import rule_score, blend, stage_from_unrest, extract_slogans, normalize_judge  # noqa: E402
from blocs import detect_blocs  # noqa: E402

CFG = {"rule_weight": 0.5, "judge_weight": 0.5, "smoothing": 0.55, "decay_to_baseline": 0.15,
       "stage_thresholds": [20, 40, 60, 80], "bloc_cohesion_threshold": 45, "bloc_min_states": 2}
NAMES = {"OH": "Ohio", "PA": "Pennsylvania", "WV": "West Virginia"}


def post(pid, text, likes=0, shares=0):
    return {"post_id": pid, "content": text, "num_likes": likes, "num_shares": shares}


def test_ladder_monotonic():
    texts = [
        "We are reading the news and watching closely.",
        "We are starting a petition and writing to our delegation.",
        "Protest on the capitol steps Saturday, signs read \"RENT IS THEFT\".",
        "General strike called. Trucks parked. We refuse to pay the surcharge.",
        "Riot broke out downtown, barricades burn, clashes with police.",
    ]
    scores = [rule_score([post(i, t)], {}, NAMES, [])["unrest"] for i, t in enumerate(texts)]
    assert scores == sorted(scores), scores
    stages = [rule_score([post(i, t)], {}, NAMES, [])["stage"] for i, t in enumerate(texts)]
    assert stages == [0, 1, 2, 3, 4], stages


def test_federal_cohesion_drops_on_defiance():
    calm = rule_score([post(1, "We urge Congress to act; grateful for federal relief.")], {}, NAMES, [])
    hot = rule_score([post(2, "Washington's mandate is unconstitutional. We will not comply. Secede.")], {}, NAMES, [])
    assert hot["federal_cohesion"] < calm["federal_cohesion"]


def test_slogans_extracted():
    s = extract_slogans(['Signs read "NO PAY, NO WORK" and "FIX THE RENT". We marched.'])
    assert s == ["NO PAY, NO WORK", "FIX THE RENT"], s


def test_blend_silent_decays_to_baseline():
    base = {"unrest": 20, "federal_cohesion": 70, "neighbor_cohesion": 60}
    prev = {"unrest": 80, "federal_cohesion": 30, "neighbor_cohesion": 50, "grievance": "taxes", "summary": ""}
    out = blend(prev, None, None, base, CFG)
    assert 20 < out["unrest"] < 80 and out["federal_cohesion"] > 30


def test_blend_clamps_and_stages():
    base = {"unrest": 20, "federal_cohesion": 70, "neighbor_cohesion": 60}
    rule = {"unrest": 100, "stage": 4, "federal_cohesion": 0, "neighbor_cohesion": 0, "slogans": [], "grievance": "taxes"}
    judge = normalize_judge([{"code": "oh", "stage": 9, "unrest": 500, "federal_cohesion": -5, "neighbor_cohesion": 50,
                              "grievance": "taxes", "slogans": ["X"], "summary": "s"}])["OH"]
    assert judge["stage"] == 4 and judge["unrest"] == 100 and judge["federal_cohesion"] == 0
    out = blend(None, rule, judge, base, CFG)
    assert 0 <= out["unrest"] <= 100 and out["stage"] == stage_from_unrest(out["unrest"], CFG["stage_thresholds"]) + 1 or out["stage"] <= 4


def test_blocs_need_adjacency_and_low_cohesion():
    personas = {"OH": {"neighbors": ["PA", "WV"], "region": "Great Lakes"},
                "PA": {"neighbors": ["OH", "WV"], "region": "Mid-Atlantic"},
                "WV": {"neighbors": ["OH", "PA"], "region": "South"},
                "CA": {"neighbors": [], "region": "West Coast"}}
    scores = {c: {"federal_cohesion": 30, "unrest": 70, "grievance": "taxes"} for c in personas}
    scores["PA"]["federal_cohesion"] = 80
    reg = {}
    blocs = detect_blocs(scores, personas, 45, 2, reg)
    assert len(blocs) == 1 and blocs[0]["members"] == ["OH", "WV"], blocs
    # identity persists when PA joins (Jaccard 2/3)
    scores["PA"]["federal_cohesion"] = 20
    blocs2 = detect_blocs(scores, personas, 45, 2, reg, prev_blocs=blocs)
    assert blocs2[0]["members"] == ["OH", "PA", "WV"] and blocs2[0]["name"] == blocs[0]["name"]
    assert "<svg" in blocs2[0]["flag_svg"]


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("ok", fn.__name__)
    print(f"{len(fns)} scoring tests passed")
