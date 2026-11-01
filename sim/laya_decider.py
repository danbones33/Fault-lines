"""
Laya decider: one Laya forward pass per round decides what every active state does, so most agent turns never touch an
LLM. Laya (Apache-2.0, convaiinnovations/laya) is an encoder that answers typed questions (choice / score) about a text
state in a single pass, with no text generation.

Per round:
  1. every active state gets a short state text (who it is, how angry, the date and era, up to five posts in its feed);
  2. one Router.predict_batch answers three questions per state: action (do_nothing | like | repost | quote | post),
     target (post_1..post_N | none) and stance (5 points, toward the round's main issue);
  3. rules turn that into a plan: like/repost need a target (Laya's 'none' falls back to the likeliest post), a quote
     without a target becomes a post, quotes are capped at ~35% of the active states, neighbors' posts get a nudge;
  4. writers (post/quote): the angriest `max_llm_writers` get real text from ONE batched local LLM call
     (`write_texts`), the rest take option-bank text as today.

The engine switch lives in config.yaml `sim.decision_layer.engine` (laya | bank | llm), with env FL_DECIDER overriding
it. If Laya cannot be imported or loaded, the runner logs one warning and uses the bank. This module has no OASIS
import, so its logic is testable without the simulator (see tests/test_laya_decider.py).
"""
import json
import os
import re
import urllib.error
import urllib.request

ENGINES = ("laya", "bank", "llm")
ACTIONS = ["do_nothing", "like", "repost", "quote", "post"]
STANCE = ["strongly opposed", "opposed", "mixed", "supportive", "strongly supportive"]
MAX_POSTS = 5


def engine_from(dl, env=None):
    """Which decider runs: FL_DECIDER wins; a disabled layer is today's all-LLM path; an enabled one defaults to the bank."""
    env = os.environ if env is None else env
    e = (env.get("FL_DECIDER") or "").strip().lower()
    if e in ENGINES:
        return e
    dl = dl or {}
    if not dl.get("enabled"):
        return "llm"
    e = str(dl.get("engine") or "bank").strip().lower()
    return e if e in ENGINES else "bank"


def questions(n_posts, issue):
    """The three typed questions. Criteria are written in Fault Lines' voice: a state's public account deciding what to do."""
    tgt = {f"post_{i + 1}": f"post_{i + 1} in its feed" for i in range(n_posts)}
    tgt["none"] = "none of these posts"
    return {
        "action": {"type": "choice",
                   "instructions": "This is a US state's public social media account during a national crisis. What does it do this round?",
                   "criteria": {
                       "do_nothing": "stays quiet: nothing in its feed touches its people this round",
                       "like": "agrees with one of the posts in its feed and likes it, without saying anything",
                       "repost": "spreads a post it strongly agrees with to its own followers",
                       "quote": "answers one of the posts with its own pointed take: cheering it on, mocking it or pushing back",
                       "post": "has something of its own to say about what is happening to its people: a demand, a complaint, a warning",
                   }},
        "target": {"type": "choice", "instructions": "Which post in its feed does it react to most?", "criteria": tgt},
        "stance": {"type": "score", "instructions": f"How does this state feel about {issue}?", "criteria": list(STANCE)},
    }


def _clip(s, n):
    s = re.sub(r"\s+", " ", str(s or "")).strip()
    return s if len(s) <= n else s[: n - 1].rstrip() + "…"


def state_text(name, persona, score, date_label, era, posts):
    """The state Laya reads: who the state is, how angry, when, and up to five posts (author, likes, ~240 characters)."""
    score = score or {}
    bloc = score.get("bloc")
    lines = [
        f"State: {name}.",
        f"Persona: {_clip(persona, 600)}",
        f"Grievance: {score.get('grievance') or 'other'}. Stage: {score.get('stage_name') or 'calm'}. "
        f"{'Member of the breakaway bloc ' + str(bloc) if bloc else 'Not in any bloc'}. Lean: {score.get('lean', 'swing')}.",
        f"Unrest {float(score.get('unrest', 20)):.0f} of 100. Trust in Washington {float(score.get('federal_cohesion', 60)):.0f} of 100.",
    ]
    if date_label or era:
        lines.append(f"Date: {date_label or 'unknown'}. Era: {era or 'present day'}.")
    for i, p in enumerate(posts[:MAX_POSTS]):
        lines.append(f"post_{i + 1} (@{p.get('author') or 'unknown'}, {int(p.get('likes') or 0)} likes): {_clip(p.get('content'), 240)}")
    return "\n".join(lines)


def feed_for(code, neighbors, posts, wire_post=None, k=MAX_POSTS):
    """Up to k posts a state sees: neighbors first (newest first), then the wire, then the most-liked of the rest."""
    own = [p for p in posts if p.get("state") != code]
    nb = [p for p in reversed(own) if p.get("state") in neighbors]
    rest = sorted((p for p in own if p.get("state") not in neighbors), key=lambda p: -int(p.get("likes") or 0))
    out, seen = [], set()
    for p in nb[:3] + ([wire_post] if wire_post else []) + rest:
        if p and p["post_id"] not in seen:
            out.append(p)
            seen.add(p["post_id"])
        if len(out) >= k:
            break
    return out


class LayaDecider:
    def __init__(self, cfg=None, router=None, log=print):
        self.cfg = cfg or {}
        self.router = router
        self.failed = False
        self.log = log
        self.device = None

    def ready(self):
        """Load Laya once (CUDA when torch sees a GPU). Any failure logs one warning and returns False; never raises."""
        if self.router is not None:
            return True
        if self.failed:
            return False
        try:
            import torch
            from laya import Router
            self.device = "cuda" if torch.cuda.is_available() else "cpu"
            self.router = Router(device=self.device, max_loaded=1)
            self.router.preload(["english"])
            self.log(f"  laya: loaded the English checkpoint on {self.device}")
            return True
        except Exception as e:
            self.failed = True
            self.log(f"  laya: unavailable ({type(e).__name__}: {str(e)[:120]}); using the option bank instead")
            return False

    def decide(self, agents, issue, neighbor_bias=1.25):
        """agents: [{aid, code, text, posts:[{post_id, state, ...}], neighbors}] -> one decision per agent, same order."""
        reqs = [{"state": a["text"], "questions": questions(len(a["posts"]), issue), "model": "english"} for a in agents]
        res = self.router.predict_batch(reqs, batch_size=16) if reqs else []
        out = []
        for a, r in zip(agents, res):
            ans = (r or {}).get("answers") or {}
            act = ans.get("action") or {}
            tgt = ans.get("target") or {}
            st = ans.get("stance") or {}
            tprobs = dict(tgt.get("probabilities") or {})
            # neighbors' posts get a nudge, as the bank picker gives them
            for i, p in enumerate(a["posts"]):
                k = f"post_{i + 1}"
                if k in tprobs and p.get("state") in (a.get("neighbors") or []):
                    tprobs[k] *= neighbor_bias
            z = sum(tprobs.values()) or 1.0
            tprobs = {k: v / z for k, v in tprobs.items()}
            tchoice = max(tprobs, key=tprobs.get) if tprobs else "none"
            out.append({"aid": a["aid"], "code": a["code"],
                        "action": act.get("choice") or "do_nothing", "action_probs": act.get("probabilities") or {},
                        "confidence": float(act.get("answer_confidence") or 0.0),
                        "target_key": tchoice, "target_probs": tprobs,
                        "stance": float(st.get("score") if st.get("score") is not None else 2.0), "stance_probs": st.get("probabilities") or {}})
        return out


DEFAULT_MIX = {"post": 0.35, "quote": 0.25, "like": 0.20, "repost": 0.10, "do_nothing": 0.10}


def assign_mix(decisions, mix):
    """Zero-shot Laya's action probabilities are nearly flat (about 0.14-0.26 each), so the top answer turns a tiny edge
    into the same action for most states. Instead, `mix` sets how many states take each action, and each quota goes to
    the states that prefer that action most relative to the round's average. Rewrites d['action'] in place."""
    if not mix or not decisions:
        return decisions
    n = len(decisions)
    acts = [a for a in ACTIONS if mix.get(a, 0) > 0]
    mean = {a: (sum(float(d["action_probs"].get(a, 0)) for d in decisions) / n) or 1e-6 for a in acts}
    total = sum(mix[a] for a in acts)
    quota = {a: int(round(n * mix[a] / total)) for a in acts}
    while sum(quota.values()) > n:
        quota[max(quota, key=quota.get)] -= 1
    while sum(quota.values()) < n:
        quota[max(acts, key=lambda a: mix[a])] += 1
    cands = sorted(((float(d["action_probs"].get(a, 0)) / mean[a], i, a) for i, d in enumerate(decisions) for a in acts), reverse=True)
    done = set()
    for _, i, a in cands:
        if i in done or quota[a] <= 0:
            continue
        decisions[i]["action"] = a
        quota[a] -= 1
        done.add(i)
    return decisions


def plan(decisions, agents, unrest, max_writers=6, quote_cap=0.35, low_conf_fallback=False, conf_min=0.55):
    """Rules on top of Laya's answers. Returns [{aid, mode, kind, target, decision}] where mode is one of
    none | like | repost | llm_agent | write_llm | write_bank."""
    by_aid = {a["aid"]: a for a in agents}
    items = []
    for d in decisions:
        a = by_aid[d["aid"]]
        posts = a["posts"]
        act = d["action"]
        if low_conf_fallback and d["confidence"] < conf_min:
            items.append({"aid": d["aid"], "mode": "llm_agent", "kind": None, "target": None, "decision": d})
            continue
        target = None
        if d["target_key"].startswith("post_"):
            i = int(d["target_key"].split("_")[1]) - 1
            target = posts[i] if 0 <= i < len(posts) else None
        if act in ("like", "repost"):
            if target is None and posts:   # Laya said 'none': take the likeliest post
                ranked = sorted((k for k in d["target_probs"] if k.startswith("post_")), key=lambda k: -d["target_probs"][k])
                target = posts[int(ranked[0].split("_")[1]) - 1] if ranked else posts[0]
            mode = act if target else "none"
            items.append({"aid": d["aid"], "mode": mode, "kind": None, "target": target, "decision": d})
        elif act in ("post", "quote"):
            kind = "quote" if (act == "quote" and target is not None) else "post"   # a quote without a target becomes a post
            items.append({"aid": d["aid"], "mode": "write", "kind": kind, "target": target if kind == "quote" else None, "decision": d})
        else:
            items.append({"aid": d["aid"], "mode": "none", "kind": None, "target": None, "decision": d})
    # quote cap: the least confident quotes beyond the cap become posts
    cap = int(round(quote_cap * max(1, len(decisions))))
    quotes = sorted((it for it in items if it["kind"] == "quote"), key=lambda it: -it["decision"]["confidence"])
    for it in quotes[cap:]:
        it["kind"], it["target"] = "post", None
    # the angriest writers get real LLM text, the rest take bank text
    writers = sorted((it for it in items if it["mode"] == "write"), key=lambda it: -float(unrest.get(it["decision"]["code"], 0)))
    for i, it in enumerate(writers):
        it["mode"] = "write_llm" if i < max_writers else "write_bank"
    return items


WRITER_SYSTEM = ("You write social media posts for the public accounts of US states in a FICTIONAL simulation. Each account speaks "
                 "for its state's people in the first person plural. Short sentences, specifics (a number, a place, a demand, a "
                 "deadline), real anger or humor where it fits, at most one hashtag. No real people. Return JSON only.")


def write_texts(base, model, writers, context, timeout=180, purpose="writer", story=""):
    """ONE local LLM call writes a post for each writer. writers: [{id, state, persona, stage, stance, kind, target}].
    Returns {id: text}; {} on failure (callers use bank text instead)."""
    if not writers:
        return {}
    items = []
    for n, w in enumerate(writers, 1):
        line = (f"- id {n}: {w['state']} (stage: {w['stage']}, feels {w['stance']} about the main issue). "
                f"Who they are: {_clip(w['persona'], 220)}")
        if w["kind"] == "quote" and w.get("target"):
            line += f" They are QUOTING this post and answering it: \"{_clip(w['target'], 200)}\""
        else:
            line += " They write their own new post."
        items.append(line)
    user = (f"{context}\n\nTODAY'S STORY: {_clip(story, 600) or '(no new story)'}\n\n"
            f"Write one post for each account below, 15-45 words, in that state's own voice, reacting to today's story as it "
            f"lands on that state (its money, jobs, water, neighbors). Each must sound "
            f"different: vary openings, tone and specifics.\n" + "\n".join(items) +
            "\n\nReturn {\"posts\": [{\"id\": <the number>, \"text\": str}, ...]} with one entry per id.")
    payload = {"model": model, "temperature": 0.9, "max_tokens": 200 * len(writers) + 200, "stream": False,
               "response_format": {"type": "json_object"},
               "messages": [{"role": "system", "content": WRITER_SYSTEM}, {"role": "user", "content": user}]}
    req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": "Bearer proxy", "X-FL-Purpose": purpose})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            txt = json.load(resp)["choices"][0]["message"]["content"]
        d = json.loads(re.search(r"\{.*\}", txt, re.S).group(0))
    except Exception as e:
        print(f"  laya: writer call failed ({str(e)[:80]}); bank text for everyone")
        return {}
    # ids are 1..N in the prompt; map them back (a model that answers with the state's name is matched too)
    by_num = {str(n): w["id"] for n, w in enumerate(writers, 1)}
    by_name = {str(w["state"]).lower(): w["id"] for w in writers}
    out = {}
    for p in d.get("posts") or []:
        if not (isinstance(p, dict) and p.get("text") and p.get("id") is not None):
            continue
        key = re.sub(r"^id\s*[:#]?\s*", "", str(p["id"]).strip().lower())
        wid = by_num.get(key) or by_name.get(key) or by_name.get(str(p.get("state", "")).lower())
        t = re.sub(r"\s+", " ", str(p["text"])).strip()
        if wid and len(t) > 15:
            out[wid] = t[:600]
    return out
