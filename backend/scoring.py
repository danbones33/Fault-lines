"""
Rule-based unrest / cohesion scoring on top of agent posts, plus an LLM judge
with a fixed rubric. Every number here is inspectable: the rule half is pure
keyword + engagement arithmetic, the judge half is a JSON rubric, and the
blend/smoothing is three config numbers.

Ladder stages: 0 calm, 1 petition, 2 protest, 3 strike, 4 riot.
"""
import math
import re

STAGES = ["calm", "petition", "protest", "strike", "riot"]

LADDER_WORDS = {
    1: ["petition", "sign the", "write to", "letter to", "call your", "ballot", "referendum",
        "town hall", "lawsuit", "sue ", "legislature", "resolution"],
    2: ["protest", "march", "rally", "rallies", "demonstrat", "sit-in", "picket", "take to the streets",
        "in the streets", "capitol steps", "crowd", "gather"],
    3: ["strike", "walkout", "walk out", "walk off", "boycott", "blockade", "shut down", "shutdown",
        "work stoppage", "refuse to pay", "withhold", "general strike", "stop paying"],
    4: ["riot", "burn", "loot", "smash", "clash", "torch", "set fire", "storm the", "overturn",
        "tear gas", "molotov", "barricades", "violence", "broke out"],
}
NEG_WORDS = ["angry", "furious", "outrage", "fed up", "enough is enough", "betray", "can't afford",
             "cannot afford", "crushing", "broke", "ruin", "desperate", "fury", "rage", "insult",
             "unacceptable", "breaking point", "sick of", "won't stand", "will not stand", "robbed",
             "theft", "squeeze", "collapse", "crisis", "disaster", "panic", "fear"]
POS_WORDS = ["relief", "grateful", "fair", "manageable", "reasonable", "pleased", "optimistic",
             "welcome", "thank", "good news", "calm", "patience", "work together", "hopeful",
             "constructive", "support the plan", "sensible"]
ANTI_FED = ["washington", "the feds", "federal overreach", "secede", "secession", "on our own",
            "nullify", "ignore the mandate", "leave the union", "independence", "d.c. doesn't",
            "dc doesn't", "federal government has", "unconstitutional", "we won't comply",
            "will not comply", "our own state", "states' rights", "state's rights", "tyranny",
            "break away", "go it alone", "sovereign"]
PRO_FED = ["work with washington", "federal help", "fema", "grateful for federal", "national unity",
           "all in this together", "federal support", "congress should", "we urge congress",
           "federal relief", "united", "one nation"]
COORD_WORDS = ["join", "stand with", "together", "alongside", "with our neighbors", "coordinate",
               "same fight", "solidarity", "allies", "pact", "compact", "coalition"]
BLAME_WORDS = ["blame", "against", "unlike", "fault", "not like", "free ride", "leech", "steal our",
               "poach", "dump on"]

GRIEVANCE_WORDS = {
    "housing": ["rent", "housing", "mortgage", "landlord", "evict", "homeless", "apartment"],
    "jobs": ["job", "layoff", "laid off", "unemploy", "factory", "plant clos", "wages", "paycheck", "work"],
    "taxes": ["tax", "surcharge", "levy", "fee"],
    "federal": ["washington", "federal", "congress", "mandate", "white house", "feds", "union"],
    "services": ["hospital", "school", "medicaid", "transit", "clinic", "snap", "benefit", "services"],
    "prices": ["price", "grocer", "insurance", "electric", "bill", "cost of living", "inflation", "gas"],
}


def rule_grievance(blob):
    t = blob.lower()
    counts = {g: sum(t.count(w) for w in ws) for g, ws in GRIEVANCE_WORDS.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] > 0 else "other"


SLOGAN_RE = re.compile(r'["“]([A-Z0-9][A-Z0-9 ,!\'\-\$%\.]{5,60})["”]')


def _count(text, words):
    t = text.lower()
    return sum(t.count(w) for w in words)


HASHTAG_RE = re.compile(r"#([A-Za-z][A-Za-z0-9]{3,40})")


def sign_text(s):
    """'#RentIsTheft' -> 'RENT IS THEFT'; quoted caps pass through."""
    s = s.strip().lstrip("#").strip(" .,!\"")
    if not s:
        return ""
    if s.upper() == s and " " in s:
        return s
    words = re.sub(r"([a-z0-9])([A-Z])", lambda m: m.group(1) + " " + m.group(2), s)
    words = re.sub(r"([A-Z]+)([A-Z][a-z])", lambda m: m.group(1) + " " + m.group(2), words)
    return words.upper()[:40]


def extract_slogans(texts):
    out = []
    for t in texts:
        for m in SLOGAN_RE.findall(t):
            s = sign_text(m)
            if s and s not in out:
                out.append(s)
    for t in texts:
        for m in HASHTAG_RE.findall(t):
            s = sign_text(m)
            if s and s not in out and len(out) < 6:
                out.append(s)
    return out[:6]


def rule_score(posts, engagement, state_names, neighbors):
    """posts: list of this state's posts (dicts with content/quote_content/post_id).
    Returns dict(unrest, stage, federal_cohesion, neighbor_cohesion, evidence)."""
    if not posts:
        return None
    texts = []
    for p in posts:
        texts.append(p.get("content") or "")
        if p.get("quote_content"):
            texts.append(p["quote_content"])
    blob = " \n".join(texts)
    ladder_hits = {s: _count(blob, ws) for s, ws in LADDER_WORDS.items()}
    max_stage = max([s for s, c in ladder_hits.items() if c > 0] or [0])
    weighted_stage = sum(s * c for s, c in ladder_hits.items()) / max(1, sum(ladder_hits.values()))
    neg = _count(blob, NEG_WORDS)
    pos = _count(blob, POS_WORDS)
    likes = shares = 0
    for p in posts:
        e = engagement.get(str(p.get("post_id")), {})
        likes += e.get("likes", 0)
        shares += e.get("shares", 0)
    engage = 4 * math.log1p(likes + 2 * shares)
    unrest = 12 * max_stage + 6 * weighted_stage + 4 * neg - 4 * pos + engage
    unrest = max(0.0, min(100.0, unrest))

    anti = _count(blob, ANTI_FED)
    pro = _count(blob, PRO_FED)
    federal = max(0.0, min(100.0, 70 - 12 * anti + 8 * pos + 8 * pro - 3 * neg - 4 * max_stage))

    coord = _count(blob, COORD_WORDS)
    blame = _count(blob, BLAME_WORDS)
    mentions = sum(1 for n in neighbors if state_names.get(n, "").lower() in blob.lower())
    neighbor = max(0.0, min(100.0, 60 + 8 * coord + 6 * mentions - 10 * blame))

    return {
        "unrest": round(unrest, 1), "stage": max_stage,
        "federal_cohesion": round(federal, 1), "neighbor_cohesion": round(neighbor, 1),
        "evidence": {"ladder_hits": ladder_hits, "neg": neg, "pos": pos, "anti_fed": anti,
                     "pro_fed": pro, "coord": coord, "blame": blame, "neighbor_mentions": mentions,
                     "likes": likes, "shares": shares},
        "slogans": extract_slogans(texts),
        "grievance": rule_grievance(blob),
    }


JUDGE_SYSTEM = (
    "You are a strict, neutral analyst scoring simulated social-media posts from US state personas. "
    "Apply the rubric literally. Output JSON only. Always respond in English only."
)

JUDGE_RUBRIC = """Rubric (score each state independently, from its posts only):
- stage: 0 calm (no collective action), 1 petition (letters, petitions, lawsuits, ballot talk, "we demand"),
  2 protest (marches, rallies, demonstrations), 3 strike (strikes, boycotts, blockades, refusing to pay),
  4 riot (property destruction, violence, clashes). Pick the HIGHEST stage the posts actually describe.
- unrest: 0-100 intensity. Anchors: 10 mild worry, 25 angry petitioning, 45 street protests, 65 strikes and
  boycotts, 85 riots. Use the full range; do not default to 40.
- federal_cohesion: 0-100. Anchors: 85 cooperative with Washington; 70 ordinary criticism of a federal policy
  (this is the default for complaints); 50 calls for state-level resistance or lawsuits; 30 nullification,
  refusing federal mandates, boycotting federal funds; 10 secession or independence talk.
- neighbor_cohesion: 0-100. 60 default; higher when coordinating with or praising neighboring states, lower when blaming them.
- grievance: one of housing, jobs, taxes, federal, services, prices, other. Pick what the people are angry ABOUT
  (rent -> housing, surcharge -> taxes); use "federal" only when the complaint is about federal power itself.
- slogans: up to 3 short sign texts QUOTED VERBATIM from the posts (ALL CAPS phrases in quotes, or hashtags). Empty list if none.
- summary: <= 18 words, neutral, no names of real people.
Return: {"states": [{"code": "XX", "stage": int, "unrest": int, "federal_cohesion": int,
  "neighbor_cohesion": int, "grievance": str, "slogans": [str], "summary": str}, ...]}"""


def judge_prompt(batch, election=None):
    """batch: list of (code, name, [post texts]). With an election, the judge also reads each state's lean."""
    parts = []
    for code, name, texts in batch:
        joined = "\n".join(f"  - {t[:400]}" for t in texts[:8])
        parts.append(f"[{code}] {name}:\n{joined}")
    extra = ""
    if election:
        opts = "; ".join(f"{o['id']} = {o['name']} ({o.get('party', '')}, {o.get('lane', '')}): {(o.get('blurb') or '')[:160]}" for o in election["options"])
        extra = (f"\n\nELECTION POLLING. The question: {election['question']} Options: {opts}. For each state also return "
                 f"\"lean\" (the option letter the posts favor even slightly: read tone, grievances and whose platform the state's complaints fit; "
                 f"use \"undecided\" only when the posts give no hint at all) and \"lean_margin\" (0-100, how clearly). "
                 f"Add both keys to each state object.")
    return JUDGE_RUBRIC + extra + "\n\nPosts this round:\n\n" + "\n\n".join(parts)


def normalize_judge(items, election=None):
    out = {}
    names = {}
    for o in (election or {}).get("options", []):
        names[o["id"].upper()] = o["id"]
        for tok in re.findall(r"[A-Za-z]{3,}", o.get("name", "")):
            names[tok.upper()] = o["id"]
        if o.get("party"):
            names[str(o["party"]).upper()[:24]] = o["id"]

    def lean_of(v):
        if not v:
            return "undecided"
        t = str(v).strip().upper()
        if t[:1] in "ABCD" and (len(t) == 1 or not t[1:2].isalpha()):
            return t[:1]
        for tok in re.findall(r"[A-Z]{3,}", t):
            if tok in names:
                return names[tok]
        return "undecided"
    for it in items or []:
        code = str(it.get("code", "")).upper()[:2]
        if not code:
            continue
        def clamp(v, lo=0, hi=100):
            try:
                return max(lo, min(hi, float(v)))
            except Exception:
                return None
        out[code] = {
            "stage": int(clamp(it.get("stage", 0), 0, 4) or 0),
            "unrest": clamp(it.get("unrest", 0)),
            "federal_cohesion": clamp(it.get("federal_cohesion", 70)),
            "neighbor_cohesion": clamp(it.get("neighbor_cohesion", 60)),
            "grievance": str(it.get("grievance", "other"))[:20].lower(),
            "slogans": [x for x in (sign_text(str(s)) for s in (it.get("slogans") or [])) if x][:3],
            "summary": str(it.get("summary", ""))[:160],
            "lean": lean_of(it.get("lean")),
            "lean_margin": int(clamp(it.get("lean_margin", 0)) or 0),
        }
    return out


def stage_from_unrest(unrest, thresholds):
    s = 0
    for t in thresholds:
        if unrest >= t:
            s += 1
    return s


def blend(prev, rule, judge, baseline, cfg):
    """Combine previous round state, rule score, judge score into this round's state."""
    rw, jw = cfg["rule_weight"], cfg["judge_weight"]
    alpha = cfg["smoothing"]
    decay = cfg["decay_to_baseline"]
    prev_u = prev["unrest"] if prev else baseline["unrest"]
    prev_f = prev["federal_cohesion"] if prev else baseline["federal_cohesion"]
    prev_n = prev["neighbor_cohesion"] if prev else baseline["neighbor_cohesion"]

    if rule is None and judge is None:
        # silent this round: drift toward baseline
        u = prev_u + (baseline["unrest"] - prev_u) * decay
        f = prev_f + (baseline["federal_cohesion"] - prev_f) * decay
        n = prev_n + (baseline["neighbor_cohesion"] - prev_n) * decay
        obs = None
    else:
        def mix(key):
            vals, ws = [], []
            if rule is not None:
                vals.append(rule[key]); ws.append(rw)
            if judge is not None and judge.get(key) is not None:
                vals.append(judge[key]); ws.append(jw)
            return sum(v * w for v, w in zip(vals, ws)) / sum(ws)
        obs = {"unrest": mix("unrest"), "federal_cohesion": mix("federal_cohesion"),
               "neighbor_cohesion": mix("neighbor_cohesion")}
        u = alpha * prev_u + (1 - alpha) * obs["unrest"]
        f = alpha * prev_f + (1 - alpha) * obs["federal_cohesion"]
        n = alpha * prev_n + (1 - alpha) * obs["neighbor_cohesion"]

    u, f, n = (max(0, min(100, x)) for x in (u, f, n))
    stage = stage_from_unrest(u, cfg["stage_thresholds"])
    # evidence can pull the stage up one notch early (a riot post at unrest 55 reads as strike)
    evidence_stage = max(rule["stage"] if rule else 0, judge["stage"] if judge else 0)
    if evidence_stage > stage:
        stage = min(evidence_stage, stage + 1)
    return {
        "unrest": round(u, 1), "stage": stage, "stage_name": STAGES[stage],
        "federal_cohesion": round(f, 1), "neighbor_cohesion": round(n, 1),
        "observed": {k: round(v, 1) for k, v in obs.items()} if obs else None,
        "rule": rule, "judge": judge,
        "grievance": (judge or {}).get("grievance") or (rule or {}).get("grievance") or (prev or {}).get("grievance") or "other",
        "slogans": ((judge or {}).get("slogans") or []) + [s for s in ((rule or {}).get("slogans") or [])
                                                             if s not in ((judge or {}).get("slogans") or [])],
        "summary": (judge or {}).get("summary") or (prev or {}).get("summary") or "",
    }
