"""
DECISION LAYER for the state agents.

Instead of every active state agent making its own long LLM call with the whole feed in the prompt, one call per
round builds an OPTION BANK: for each situation cluster present this round (ladder stage x grievance x bloc membership
x political lean) it writes several candidate posts, quotes and replies that fit the day's news, the date and the era.
Each state then picks from its cluster's options by rule (no model call): unused options first, a state-specific fill
of {state}/{neighbor}, and a quote or a like aimed at a neighbor's or the wire's post when the option calls for one.
A configurable slice of agents (the angriest states by default) still runs the full LLM agent so the feed keeps real,
surprising voices; the rest run on the bank. Round time drops from minutes to seconds on a local model.
"""
import json
import os
import random
import re
import urllib.error
import urllib.request

BANK_SYSTEM = ("You write social-media posts for the collective voices of US states in a fictional political simulation. Each state speaks as "
               "'we'. Options must fit the situation cluster they are written for, react to today's news, use the current date's year only, "
               "contain no real people, and stay in English. Return JSON only.")


def _ascii(s):
    return "".join(ch for ch in s if 32 <= ord(ch) < 127).strip()


def cluster_key(state_score, persona, hit=False):
    stage = (state_score or {}).get("stage_name") or "calm"
    griev = (state_score or {}).get("grievance") or "other"
    bloc = "bloc" if (state_score or {}).get("bloc") else "solo"
    lean = persona.get("partisan_lean", 0)
    side = "right" if lean > 4 else "left" if lean < -4 else "swing"
    return f"{stage}|{griev}|{bloc}|{side}|{'hit' if hit else 'watch'}"


def describe(key):
    parts = key.split("|")
    stage, griev, bloc, side = parts[:4]
    hit = len(parts) > 4 and parts[4] == "hit"
    return (f"stage '{stage}' on the unrest ladder (calm < petition < protest < strike < riot), main grievance '{griev}', "
            f"{'a member of a breakaway bloc' if bloc == 'bloc' else 'not in any bloc'}, political culture {side}, "
            + ("DIRECTLY HIT by the story (named in the news or next door to it): speaks as a victim and a participant"
               if hit else "NOT directly affected: watches from elsewhere, reacts as an outsider (solidarity, suspicion, 'what about us', politics, its own {issue}); never claims the crisis is happening at home"))


def build_bank(base, model, clusters, context, headline, top_posts, options_per_cluster=6, timeout=300, avoid=None, retry=False):
    """One LLM call: {cluster_key: [{kind, text}, ...]}. Returns {} on failure (callers fall back to the full LLM agents)."""
    cl = "\n".join(f"- key \"{k}\": {describe(k)}" for k in clusters)
    posts = "\n".join(f"- {p}" for p in top_posts[:6]) or "- (quiet)"
    avoid_txt = ("\n\nDo not reuse or lightly rephrase any of these lines from the last round:\n" + "\n".join(f"- {a[:90]}" for a in (avoid or [])[:14])) if avoid else ""
    user = (f"{context}\n\nTODAY'S WIRE: {headline or '(nothing new)'}\n\nWhat states posted most recently:\n{posts}{avoid_txt}\n\n"
            f"Situation clusters present today:\n{cl}\n\n"
            f"For EACH cluster key write {options_per_cluster} options. Each option: 'kind' = post | quote | reply (quote = quoting a neighbor's "
            f"or the wire's post, reply = answering a neighbor; at most a third of the options), 'text' = 12-45 words in the first person plural. "
            f"Placeholders the picker fills per state: {{state}} = the state's own name, {{neighbor}} = a neighboring state, {{issue}} = one of the "
            f"state's real local issues. EVERY option must contain the literal token {{issue}} at least once (the picker replaces it with the state's own issue), and at least a third must contain {{neighbor}}. Specific beats "
            f"generic: name a number, a demand, a deadline, a place, or today's wire. Vary the voice across the options (furious, wry, "
            f"organizing, weary, defiant, hopeful, sarcastic). No platitudes like 'stand together' or 'make our voices heard'; never open with 'We demand', 'We petition', 'Echoing' or 'As {{state}}'; each option in a cluster must open with a different word. Sound like real people online: short sentences, specifics, a little humor or anger, sometimes a question. At most one hashtag. "
            f"Return {{\"banks\": {{\"<key>\": [{{\"kind\": str, \"text\": str}}, ...], ...}}}}")
    def call(json_mode, max_tokens):
        payload = {"model": model, "temperature": 0.9, "max_tokens": max_tokens, "stream": False,
                   "messages": [{"role": "system", "content": BANK_SYSTEM}, {"role": "user", "content": user}]}
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(f"{base}/chat/completions", data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "Authorization": "Bearer proxy", "X-FL-Purpose": "bank"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.load(resp)["choices"][0]["message"]["content"]
    try:
        txt = call(True, 3500)
    except urllib.error.HTTPError as e:
        if e.code < 500:
            raise
        txt = call(False, 2500)   # a local model occasionally fails under the JSON grammar; plain text parses loosely below
    d = None
    try:
        d = json.loads(txt)
    except Exception:
        try:
            m = re.search(r"\{.*\}", txt, re.S)
            d = json.loads(m.group(0)) if m else None
        except Exception:
            d = None
    banks = d.get("banks") if isinstance(d, dict) else None
    if isinstance(banks, dict) and not retry:
        # the local model sometimes answers for the first cluster only: ask again for the ones it skipped
        missing = [k for k in clusters if k not in banks or not banks.get(k)]
        if missing and len(missing) < len(clusters):
            try:
                more = build_bank(base, model, missing, context, headline, top_posts, options_per_cluster, timeout, avoid, retry=True)
                for k, v in more.items():
                    banks[k] = [{"kind": o["kind"], "text": o["text"]} for o in v]
            except Exception as e:
                print(f"  decision layer: retry for {len(missing)} clusters failed ({str(e)[:60]})")
    if not banks:
        # truncated or malformed output: salvage every complete option and serve them from one shared bank
        found = re.findall(r'"kind"\s*:\s*"(\w+)"\s*,\s*"text"\s*:\s*"((?:[^"\\]|\\.)*)"', txt)
        if found:
            banks = {"*": [{"kind": k, "text": t.replace('\\"', '"')} for k, t in found]}
    out = {}
    for k, opts in (banks or {}).items():
        clean = []
        for o in opts or []:
            if isinstance(o, dict) and o.get("text"):
                t = _ascii(str(o["text"]))[:400]
                if len(t) > 15:
                    clean.append({"kind": str(o.get("kind", "post")).lower()[:6], "text": t})
        if clean:
            out[str(k)] = clean
    return out


def nearest(bank, key):
    if key in bank:
        return bank[key]
    parts = key.split("|")
    stage, griev, hit = parts[0], parts[1] if len(parts) > 1 else "", parts[4] if len(parts) > 4 else "watch"
    keyed = [k for k in bank if k.count("|") >= 3]
    same_side = [k for k in keyed if (k.split("|")[4] if k.count("|") >= 4 else "watch") == hit]
    for pool in (same_side, keyed):
        for k in pool:
            kp = k.split("|")
            if kp[0] == stage and kp[1] == griev:
                return bank[k]
        for k in pool:
            if k.split("|")[0] == stage:
                return bank[k]
        if pool:
            return bank[pool[0]]
    return bank.get("*") or next(iter(bank.values()), [])


class Picker:
    """Per-state choice from the bank, by rule: unused options first, placeholders filled, quotes aimed at a real post."""

    def __init__(self, rng, n_total=25, max_quote_share=0.35):
        self.rng = rng
        self.used = set()
        self.quotes = 0
        self.quote_budget = max(1, int(n_total * max_quote_share))

    def pick(self, bank, key, state_name, neighbors, targets, issues=None):
        opts = nearest(bank, key)
        if not opts:
            return None
        fresh = [o for o in opts if o["text"] not in self.used] or opts
        o = self.rng.choice(fresh)
        self.used.add(o["text"])
        issue = self.rng.choice(issues) if issues else "the budget"
        nb = self.rng.choice(neighbors) if neighbors else "the mainland"
        text = (o["text"].replace("{state}", state_name).replace("{State}", state_name).replace("{STATE}", state_name)
                .replace("{neighbor}", nb).replace("{Neighbor}", nb).replace("{issue}", issue).replace("{Issue}", issue))
        text = re.sub(r"\{[a-z_]+\}", "", text).strip()
        kind = o["kind"] if o["kind"] in ("post", "quote", "reply") else "post"
        if kind in ("quote", "reply"):
            if self.quotes >= self.quote_budget:
                kind = "post"
            else:
                self.quotes += 1
        target = None
        if kind in ("quote", "reply") and targets:
            # a neighbor's post if there is one, else the wire
            nb = [t for t in targets if t.get("state") in neighbors]
            target = self.rng.choice(nb) if nb else self.rng.choice(targets)
        if kind in ("quote", "reply") and not target:
            kind = "post"
        return {"kind": kind, "text": text, "target": target}
