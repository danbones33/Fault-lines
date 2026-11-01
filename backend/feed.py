"""
The Feed: turns a round's OASIS posts into X-style items, adds NPC reactions and local-news posts
(one batched LLM call), computes trending hashtags. Memes, scenes and broadcasts are attached later
by the media queue. Each round is a file: runs/<id>/feed/round_N.json (replayable by the scrubber).
"""
import random
import re
from datetime import datetime, timedelta

HASHTAG_RE = re.compile(r"#([A-Za-z][A-Za-z0-9_]{2,40})")

NPC_SYSTEM = ("You voice a cast of fictional social-media accounts reacting to posts in a simulated US political feed. "
              "Stay strictly in each account's persona and register. Replies are short (<= 40 words), can be funny, angry, "
              "smug or bot-like as the persona demands. Local news accounts post factual-sounding local headlines. "
              "No real people's names, no slurs, no ethnic or religious targeting. Return JSON only.")


def item_id(round_num, kind, n):
    return f"r{round_num}_{kind}{n}"


def engagement(rng, base_followers, heat):
    k = max(1, base_followers // 20_000)
    likes = int(rng.expovariate(1 / (k * (1 + heat) * 3)) )
    return {"likes": likes, "reposts": int(likes * rng.uniform(0.05, 0.3)), "replies": int(likes * rng.uniform(0.02, 0.2))}


def state_items(round_data, round_scores, roster, rng, base_ts):
    """OASIS posts -> feed items. Reposts of a post are folded into that post's repost count."""
    items, by_post = [], {}
    repost_counts = {}
    for p in round_data["posts"]:
        if p["kind"] == "repost":
            repost_counts[p.get("original_post_id")] = repost_counts.get(p.get("original_post_id"), 0) + 1
    n = 0
    for p in round_data["posts"]:
        if p["kind"] == "repost":
            continue
        author = f"state:{p['state']}" if p.get("state") else "wire"
        text = p.get("quote_content") if p["kind"] == "quote" else p.get("content")
        if not (text or "").strip():
            continue
        ident = roster.get(author, roster["wire"])
        heat = (round_scores.get(p.get("state"), {}).get("unrest", 20) / 50.0) if p.get("state") else 1.0
        eng = engagement(rng, ident["followers"], heat)
        eng["likes"] += p.get("num_likes", 0) * 37
        eng["reposts"] += p.get("num_shares", 0) * 11 + repost_counts.get(p["post_id"], 0) * 23
        it = {"id": item_id(round_data["round"], "p", n), "round": round_data["round"], "author": author,
              "kind": "quote" if p["kind"] == "quote" else "post", "text": text.strip(),
              "quote_of": None, "quote_text": p.get("content") if p["kind"] == "quote" else None,
              "state": p.get("state"), "ts": (base_ts + timedelta(minutes=rng.randint(0, 50))).isoformat(timespec="minutes"),
              "sim_post_id": p["post_id"], **eng}
        by_post[p["post_id"]] = it["id"]
        items.append(it)
        n += 1
    for it in items:
        if it["kind"] == "quote":
            orig = next((p for p in round_data["posts"] if p["post_id"] == it["sim_post_id"]), None)
            if orig and orig.get("original_post_id") in by_post:
                it["quote_of"] = by_post[orig["original_post_id"]]
    return items


def npc_prompt(top_items, roster, npcs, escalated, n_react, election=None, election_round=None, events=None, round_num=None, context=None, hidden=None):
    posts = "\n".join(f"- [{it['id']}] @{roster[it['author']]['handle']}: {it['text'][:240]}" for it in top_items)
    hidden = hidden or {}
    cast = "\n".join(f"- @{x['handle']}: {x['persona']}" + (" " + hidden[x["id"]] if x.get("id") in hidden else "") for x in npcs)
    el = ""
    shock = ((election or {}).get("novelty") or {}).get("shock") or ""
    if shock:
        el += "\n\n" + shock.strip()
        if round_num == 1:
            el += " It is DAY ONE of this: every account's post this round must react to the non-human candidate first (awe, fear, jokes, fury, hope)."
    for ev in events or []:
        el += f"\n\nBREAKING NEWS THIS ROUND: {ev['name']}. {ev['description']} Every account reacts to this; local news accounts lead with it."
    if context:
        el += "\n\nTHE SITUATION (ongoing; argue about it, take sides, no violence): " + " | ".join(context)
    if election:
        names = {o["id"]: o["name"] for o in election["options"]}
        if election_round and election_round.get("results"):
            r = election_round["results"]
            el = (f"\n\nIT IS ELECTION NIGHT. {election['question']} Results: " + ", ".join(f"{names[k]} {v} electoral votes" for k, v in r["ev"].items())
                  + f". Winner: {names.get(r['winner']) or 'no majority yet'}. Every NPC reacts to the result in character; candidates post a victory or concession.")
        else:
            lead = {}
            for c, p in ((election_round or {}).get("polls") or {}).items():
                lead[p["lean"]] = lead.get(p["lean"], 0) + 1
            el = (f"\n\nCAMPAIGN CONTEXT: an election is coming. {election['question']} Candidates: "
                  + "; ".join(f"{o['id']} = {o['name']}" + (f" ({o['blurb']})" if o.get('blurb') else "") for o in election["options"])
                  + ". States leaning: " + (", ".join(f"{names.get(k, k)} {v}" for k, v in lead.items()) or "too early")
                  + ". Candidate accounts must post one campaign message each (rally, promise, attack on the other platform), aimed at the states in the posts above.")
    news = "\n".join(f"- @{roster['news:'+c]['handle']} ({roster['news:'+c]['display']}, {name}): state moved to {stage}; slogans: {', '.join(sl[:2]) or 'none'}"
                     for c, name, stage, sl in escalated) or "(none this round)"
    return (f"Top posts this round:\n{posts}{el}\n\nNPC cast:\n{cast}\n\nLocal news accounts that must post one headline each "
            f"because their state escalated:\n{news}\n\nWrite {n_react} NPC reactions (reply or quote to one of the top posts, "
            f"give its id in reply_to) spread across the cast, plus exactly one local-news post per listed news account "
            f"(reply_to null, text is a headline <= 30 words mentioning a place and a number). "
            f"Return {{\"items\": [{{\"handle\": str, \"reply_to\": str|null, \"text\": str}}]}}")


def npc_items(llm, run_id, round_num, top_items, roster, escalated, n_react, rng, base_ts, election=None, election_round=None, events=None, context=None, hidden=None):
    npcs = [v for v in roster.values() if v["kind"] not in ("state", "local_news", "wire")]
    try:
        data = llm.chat_json(NPC_SYSTEM, npc_prompt(top_items, roster, npcs, escalated, n_react, election, election_round, events, round_num, context, hidden),
                             kind="npc", run_id=run_id, temperature=0.9, max_tokens=2000)
        raw = data.get("items") or []
    except Exception as e:
        print(f"[feed] npc call failed: {e}")
        raw = []
    by_handle = {v["handle"]: v for v in roster.values()}
    valid_ids = {it["id"] for it in top_items}
    out = []
    for i, r in enumerate(raw):
        ident = by_handle.get(str(r.get("handle", "")).lstrip("@"))
        text = str(r.get("text", "")).strip()
        if not ident or not text:
            continue
        reply_to = r.get("reply_to") if r.get("reply_to") in valid_ids else None
        eng = engagement(rng, ident["followers"], 1.2)
        out.append({"id": item_id(round_num, "n", i), "round": round_num, "author": ident["id"],
                    "kind": "reply" if reply_to else "post", "text": text[:400], "reply_to": reply_to, "quote_of": None,
                    "state": ident.get("state"), "ts": (base_ts + timedelta(minutes=rng.randint(10, 59))).isoformat(timespec="minutes"), **eng})
    return out


def trending(items, prev_trending=None, k=8):
    """Rank by a score (uses + engagement, with 40% carry-over from last round); the displayed
    post count is derived from uses and engagement so different tags get different numbers."""
    import hashlib
    counts, uses, eng = {}, {}, {}
    for it in items:
        w = 1 + (it.get("reposts", 0) + it.get("likes", 0) / 10) / 50
        for tag in HASHTAG_RE.findall(it.get("text") or ""):
            key = tag.lower()
            counts[key] = counts.get(key, 0) + w
            uses[key] = uses.get(key, 0) + 1
            eng[key] = eng.get(key, 0) + it.get("likes", 0) + 3 * it.get("reposts", 0)
    prev_posts = {}
    for t in prev_trending or []:
        key = t["tag"].lower().lstrip("#")
        counts[key] = counts.get(key, 0) + t["score"] * 0.4
        prev_posts[key] = t.get("posts", 0)
    top = sorted(counts.items(), key=lambda kv: -kv[1])[:k]
    out = []
    for t, s in top:
        jitter = int(hashlib.sha1(t.encode()).hexdigest()[:4], 16) % 400
        posts = int(uses.get(t, 0) * 900 + (eng.get(t, 0) ** 0.5) * 180 + prev_posts.get(t, 0) * 0.5 + jitter)
        out.append({"tag": "#" + t, "score": round(s, 1), "posts": posts})
    return out


def build_round_feed(llm, run_id, round_data, round_scores, prev_scores, roster, cfg, prev_trending, rng_seed,
                     election=None, election_round=None, events=None, context=None, hidden=None):
    rnd = round_data["round"]
    rng = random.Random(rng_seed + rnd)
    calx = cfg.get("calendar")
    if calx:
        import calendar_sim as cal
        base_ts = datetime.fromisoformat(cal.ts(calx, rnd, 8))
    else:
        base_ts = datetime(2026, 1, 1, 8, 0) + timedelta(days=rnd)
    items = state_items(round_data, round_scores, roster, rng, base_ts)
    escalated = []
    for code, s in round_scores.items():
        prev_stage = (prev_scores or {}).get(code, {}).get("stage", 0)
        if s["stage"] > prev_stage and s["stage"] >= 1:
            escalated.append((code, roster[f"state:{code}"]["display"].replace(" Voices", ""), s["stage_name"], s.get("slogans", [])))
    escalated = sorted(escalated, key=lambda e: -round_scores[e[0]]["unrest"])[:6]
    top = sorted([i for i in items if i["author"] != "wire"], key=lambda i: -(i["likes"] + 3 * i["reposts"]))[:cfg["feed_top_posts"]]
    wire = [i for i in items if i["author"] == "wire"]
    if rnd >= 1 and (top or escalated) and cfg.get("feed", True):
        n_react = cfg["npc_reactions_per_round"] + (6 if events else 0) + (4 if (rnd == 1 and ((election or {}).get("novelty") or {}).get("score", 0) >= 7) else 0)
        items += npc_items(llm, run_id, rnd, top + wire[:1], roster, escalated, n_react, rng, base_ts, election, election_round, events, context, hidden)
    for it in items:
        it["thread"] = []
    by_id = {it["id"]: it for it in items}
    for it in items:
        if it.get("reply_to") in by_id:
            by_id[it["reply_to"]]["replies"] += 1
    if cfg.get("calendar"):   # the year check runs before trending is counted, so no tag from another year can trend
        try:
            import calendar_sim as cal, yearfix
            yearfix.fix_items(items, cal.round_date(cfg["calendar"], rnd).year, "text")
        except Exception as e:
            print(f"[yearfix] feed: {e}")
    tr = trending(items, prev_trending)
    viral = max(items, key=lambda i: i["likes"] + 3 * i["reposts"]) if items else None
    return {"round": rnd, "items": items, "trending": tr, "escalated": [e[0] for e in escalated],
            "viral": viral["id"] if viral else None, "broadcasts": [], "media_pending": 0,
            "election": election_round, "election_night": bool(round_data.get("election_night"))}
