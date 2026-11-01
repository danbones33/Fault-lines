"""
Memes. The LLM picks a format from a list of GENERIC descriptions (no copyrighted templates) and
writes top/bottom captions tied to the round's events. The image model only ever draws the base
scene; captions are stamped in code (Pillow, Impact-style, white with black stroke).
"""
import random

FORMATS = [
    {"id": "prez_cartoon", "name": "political cartoon",
     "image": "a satirical newspaper political cartoon of the President (described below), exaggerated features, ink lines and watercolor wash, editorial-page style, the President in a tiny situation that mocks the week's news"},
    {"id": "two_panel_reaction", "name": "two-panel reaction",
     "image": "two-panel comic: left panel a calm cartoon person reading a phone, right panel the same person screaming with hair on fire, bold flat colors"},
    {"id": "room_on_fire", "name": "everything is fine",
     "image": "cartoon dog in a hat sitting at a kitchen table sipping coffee while the whole room burns around it, calm smile, flat colors"},
    {"id": "tempting_choice", "name": "distracted choice",
     "image": "a cartoon person walking with a partner, head turned to stare at a passing stranger holding a giant glowing dollar sign, partner furious, flat illustration"},
    {"id": "expanding_brain", "name": "expanding brain",
     "image": "four stacked panels of a human head, the brain glowing brighter and larger in each panel until it radiates cosmic light, blue tones"},
    {"id": "sad_guy", "name": "sad guy in hoodie",
     "image": "minimal line drawing of a sad pale man in a gray hoodie staring at an eviction notice, muted colors, hand-drawn style"},
    {"id": "two_buttons", "name": "sweating over two buttons",
     "image": "cartoon of a sweating man in a suit hovering a finger between two big red buttons on a console, anxious, flat colors"},
    {"id": "wall_chart_guy", "name": "conspiracy wall",
     "image": "a wild-eyed man in front of a corkboard covered in photos and red string, pointing, dramatic lighting, illustration"},
    {"id": "tiny_violin", "name": "world's smallest violin",
     "image": "close-up of two fingers playing a comically tiny violin, dramatic spotlight, photorealistic"},
    {"id": "bus_two_sides", "name": "two sides of the bus",
     "image": "cartoon bus interior: left window shows a sunny meadow and a smiling passenger, right window shows a gray cliff and a crying passenger, flat colors"},
    {"id": "galaxy_brain_cat", "name": "cat at dinner table",
     "image": "a confused white cat sitting at a dinner table in front of a salad, a woman pointing and yelling from the left, meme photo style"},
    {"id": "change_my_mind", "name": "table sign",
     "image": "a man sitting behind a folding table on a campus lawn with a big blank white banner on the front of the table, sunny day, photo style"},
    {"id": "guy_pointing_butterfly", "name": "is this a...",
     "image": "anime-style man in glasses pointing at a butterfly in a garden, bright colors"},
    {"id": "drowning_hand", "name": "drowning handshake",
     "image": "a hand reaching out of stormy water, another hand on a dock reaching down but holding a tiny coin instead of helping, dramatic"},
    {"id": "trojan_horse", "name": "trojan horse",
     "image": "a giant wooden horse rolled up to a city gate at night, soldiers peeking out, torches, painterly"},
]
FORMAT_BY_ID = {f["id"]: f for f in FORMATS}

SYSTEM = ("You write internet memes for a simulated social feed about US state politics and the economy. "
          "Captions are short, punchy, in character for the posting account, and tied to the specific events given. "
          "No real people, no slurs, no ethnic or religious targeting; grievances are about money, policy and power. "
          "Return JSON only.")


def prompt(round_ctx, authors, n):
    fmts = "\n".join(f"- {f['id']}: {f['name']} ({f['image']})" for f in FORMATS)
    auths = "\n".join(f"- {a['id']}: @{a['handle']} ({a['kind']}) {a.get('persona') or a.get('bio','')}" for a in authors)
    return (f"Round events:\n{round_ctx}\n\nAvailable meme formats:\n{fmts}\n\nAccounts that could post a meme:\n{auths}\n\n"
            f"Write {n} memes. Each: pick a format_id, an author id, a 'top' caption (<= 8 words) and a 'bottom' caption "
            f"(<= 10 words), and a one-line 'post_text' the account writes above the image (<= 20 words, may include one hashtag). "
            f"Vary formats and authors. Return {{\"memes\": [{{\"format_id\": str, \"author\": str, \"top\": str, \"bottom\": str, \"post_text\": str}}]}}")


def plan_memes(llm, round_ctx, authors, n, run_id):
    try:
        data = llm.chat_json(SYSTEM, prompt(round_ctx, authors, n), kind="memes_text", run_id=run_id, temperature=0.9, max_tokens=900)
        out = []
        for m in (data.get("memes") or [])[:n]:
            fid = m.get("format_id") if m.get("format_id") in FORMAT_BY_ID else random.choice(FORMATS)["id"]
            aid = m.get("author") if any(a["id"] == m.get("author") for a in authors) else random.choice(authors)["id"]
            out.append({"format_id": fid, "author": aid, "top": str(m.get("top", ""))[:60], "bottom": str(m.get("bottom", ""))[:70],
                        "post_text": str(m.get("post_text", ""))[:200]})
        return out
    except Exception as e:
        print(f"[memes] plan failed: {e}")
        return []


def image_prompt(format_id, subject=None):
    f = FORMAT_BY_ID[format_id]
    return ((f"The President looks like this: {subject}. " if subject else "") + f"{f['image']}. Clean composition with empty space at the top and bottom for captions. "
            f"Absolutely no text, letters, words, numbers, logos or watermarks in the image. Fictional characters only.")
