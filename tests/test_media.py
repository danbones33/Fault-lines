"""Unit checks for the spend guard, meme stamping, identities and feed helpers. Run: .venv/Scripts/python tests/test_media.py"""
import io
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))
from spend import SpendGuard  # noqa: E402
from providers import stamp_caption, placeholder_png, initials_avatar  # noqa: E402
from identities import build_roster  # noqa: E402
from feed import trending, state_items  # noqa: E402
from memes import FORMATS, FORMAT_BY_ID, image_prompt  # noqa: E402
import random  # noqa: E402
from datetime import datetime  # noqa: E402


def test_spend_guard_halts_media_not_sim():
    with tempfile.TemporaryDirectory() as d:
        g = SpendGuard(d, budget_usd=1.0)
        os.makedirs(os.path.join(d, "r1"))
        assert g.allow("r1", "images") and g.allow("r1", "agents")
        g.record("r1", "images", 0.6, provider="test")
        g.record("r1", "memes", 0.5, provider="test")
        st = g.status("r1")
        assert st["halted"] and abs(st["total_usd"] - 1.1) < 1e-6
        assert not g.allow("r1", "images") and not g.allow("r1", "tts")
        assert g.allow("r1", "agents") and g.allow("r1", "judge"), "the sim keeps running over budget"
        assert os.path.exists(os.path.join(d, "r1", "spend.json"))


def test_stamp_caption_changes_pixels_and_keeps_size():
    from PIL import Image
    base = placeholder_png("base", (512, 512), seed=1, kind="meme")
    out = stamp_caption(base, "WHEN THE RENT", "IS DUE AGAIN")
    a, b = Image.open(io.BytesIO(base)), Image.open(io.BytesIO(out))
    assert a.size == b.size == (512, 512)
    # white text with black stroke appears near the top and bottom
    top = b.crop((0, 0, 512, 90)).convert("L").getextrema()
    assert top[0] < 30 and top[1] > 220, top
    assert len(initials_avatar("Main Street Mike", 3)) > 500


def test_roster_shapes():
    states = {"OH": {"name": "Ohio", "region": "Great Lakes", "median_income": 69000, "key_industries": ["a", "b"]},
              "TX": {"name": "Texas", "region": "Southwest", "median_income": 76000, "key_industries": ["oil"]}}
    r = build_roster(states)
    assert "state:OH" in r and "news:TX" in r and "wire" in r
    kinds = {v["kind"] for v in r.values()}
    assert {"state", "local_news", "influencer", "pundit", "bot", "doomposter", "troll", "wire"} <= kinds
    handles = [v["handle"] for v in r.values()]
    assert len(handles) == len(set(handles)), "handles are unique"


def test_feed_items_and_trending():
    roster = build_roster({"OH": {"name": "Ohio", "region": "Great Lakes", "median_income": 69000, "key_industries": ["a"]}})
    rd = {"round": 2, "posts": [
        {"post_id": 1, "agent_id": 0, "state": None, "kind": "post", "content": "BREAKING: thing", "quote_content": None, "original_post_id": None, "num_likes": 0, "num_shares": 0},
        {"post_id": 2, "agent_id": 1, "state": "OH", "kind": "quote", "content": "BREAKING: thing", "quote_content": "Ohio says no #RentStrike #OhioRising", "original_post_id": 1, "num_likes": 3, "num_shares": 1},
        {"post_id": 3, "agent_id": 1, "state": "OH", "kind": "repost", "content": "", "quote_content": None, "original_post_id": 1, "num_likes": 0, "num_shares": 0},
    ]}
    items = state_items(rd, {"OH": {"unrest": 50}}, roster, random.Random(1), datetime(2026, 1, 1))
    assert len(items) == 2 and items[1]["kind"] == "quote" and items[1]["quote_of"] == items[0]["id"]
    assert items[0]["reposts"] >= 23, "repost folded into the original's count"
    tr = trending(items)
    assert tr[0]["tag"] in ("#rentstrike", "#ohiorising")


def test_meme_formats_have_no_text_instruction():
    assert len(FORMATS) >= 10 and all(f["id"] in FORMAT_BY_ID for f in FORMATS)
    assert "no text" in image_prompt(FORMATS[0]["id"]).lower()


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn(); print("ok", fn.__name__)
    print(f"{len(fns)} media tests passed")
