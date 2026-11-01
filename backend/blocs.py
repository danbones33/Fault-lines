"""
Bloc detection + silly-but-deterministic names and flags.

Rule: two ADJACENT states whose federal cohesion are both below
`bloc_cohesion_threshold` are joined. Connected components with at least
`bloc_min_states` members are blocs. A bloc's identity (name, colors) is keyed
on its member set so it stays stable across rounds and re-forms the same way.
"""
import hashlib

REGION_ADJ = {
    "Northeast": "Northern", "Mid-Atlantic": "Atlantic", "South": "Southern", "Deep South": "Delta",
    "Gulf": "Gulf", "Midwest": "Heartland", "Great Lakes": "Lakes", "Plains": "Prairie",
    "Mountain West": "Highland", "Southwest": "Desert", "Pacific Northwest": "Cascadian",
    "West Coast": "Pacific", "Pacific": "Island",
}
GRIEVANCE_NOUN = {
    "housing": "Rent", "jobs": "Labor", "taxes": "Tax", "federal": "Sovereignty",
    "services": "Commons", "prices": "Bread", "other": "Grievance",
}
SUFFIXES = ["Compact", "Accord", "League", "Pact", "Union", "Assembly", "Concord", "Front", "Bloc", "Covenant"]
PALETTE = ["#d94f3d", "#e08a2e", "#c9a227", "#5b9c4a", "#2f8f9d", "#3d6fd9", "#7b4fc9", "#c2437c",
           "#8a6d3b", "#3c8f72"]
EMBLEM = {"housing": "⌂", "jobs": "⚒", "taxes": "$", "federal": "⚖", "services": "✚", "prices": "¤", "other": "✶"}


def _h(key):
    return int(hashlib.sha1(key.encode()).hexdigest(), 16)


class DSU:
    def __init__(self, items):
        self.p = {i: i for i in items}

    def find(self, x):
        while self.p[x] != x:
            self.p[x] = self.p[self.p[x]]
            x = self.p[x]
        return x

    def union(self, a, b):
        ra, rb = self.find(a), self.find(b)
        if ra != rb:
            self.p[rb] = ra


MAX_STATES = 10   # a breakaway bloc is a region, not the country: bigger components split by region


def _split_big(members, personas, min_states, max_states):
    """A component bigger than max_states becomes one bloc per region (regions with enough states), the rest dropped."""
    if len(members) <= max_states:
        return [members]
    by_region = {}
    for m in members:
        by_region.setdefault(personas[m].get("region", "?"), []).append(m)
    groups = [sorted(g) for g in by_region.values() if len(g) >= min_states]
    # a small regional remnant joins an adjacent group when it touches one
    small = [m for g in by_region.values() if len(g) < min_states for m in g]
    for m in small:
        for g in groups:
            if any(n in g for n in personas[m].get("neighbors", [])) and len(g) < max_states:
                g.append(m); g.sort(); break
    return [g[:max_states] for g in groups]


def detect_blocs(states_scores, personas, threshold, min_states, registry, prev_blocs=None, keep=None, max_states=MAX_STATES):
    """states_scores: {code: {federal_cohesion, grievance, ...}}; personas: {code: {neighbors, region}}.
    registry: dict persisted across rounds, {member_key: {name, color, flag, ...}}.
    Returns list of bloc dicts for this round."""
    low = {c for c, s in states_scores.items() if s["federal_cohesion"] < threshold} | {c for c in (keep or ()) if c in states_scores}
    dsu = DSU(low)
    for c in low:
        for n in personas[c]["neighbors"]:
            if n in low:
                dsu.union(c, n)
    groups = {}
    for c in low:
        groups.setdefault(dsu.find(c), []).append(c)
    blocs = []
    comps = []
    for members in groups.values():
        if len(members) < min_states:
            continue
        comps += _split_big(sorted(members), personas, min_states, max_states)
    for members in comps:
        if len(members) < min_states:
            continue
        members = sorted(members)
        key = "+".join(members)
        if key not in registry:
            # inherit identity from the previous round's most-overlapping bloc (Jaccard >= 0.4)
            best, best_j = None, 0.0
            for pb in prev_blocs or []:
                inter = len(set(pb["members"]) & set(members))
                j = inter / len(set(pb["members"]) | set(members))
                if j > best_j:
                    best, best_j = pb, j
            if best is not None and best_j >= 0.4:
                registry[key] = {k: best[k] for k in ("id", "name", "color", "color2", "grievance", "region", "flag_svg")}
            else:
                registry[key] = make_identity(members, states_scores, personas)
        ident = registry[key]
        avg_f = sum(states_scores[m]["federal_cohesion"] for m in members) / len(members)
        avg_u = sum(states_scores[m]["unrest"] for m in members) / len(members)
        blocs.append({**ident, "members": members, "avg_federal_cohesion": round(avg_f, 1),
                      "avg_unrest": round(avg_u, 1)})
    blocs.sort(key=lambda b: -len(b["members"]))
    return blocs


def make_identity(members, states_scores, personas):
    h = _h("+".join(members))
    regions = {}
    griev = {}
    for m in members:
        regions[personas[m]["region"]] = regions.get(personas[m]["region"], 0) + 1
        g = states_scores[m].get("grievance", "other")
        griev[g] = griev.get(g, 0) + 1
    region = max(regions, key=regions.get)
    grievance = max(griev, key=griev.get)
    adj = REGION_ADJ.get(region, "Free")
    noun = GRIEVANCE_NOUN.get(grievance, "Grievance")
    suffix = SUFFIXES[h % len(SUFFIXES)]
    name = f"The {adj} {noun} {suffix}"
    c1 = PALETTE[h % len(PALETTE)]
    c2 = PALETTE[(h // 7) % len(PALETTE)]
    if c2 == c1:
        c2 = PALETTE[(h // 7 + 3) % len(PALETTE)]
    return {"id": "bloc_" + hashlib.sha1("+".join(members).encode()).hexdigest()[:8],
            "name": name, "color": c1, "color2": c2, "grievance": grievance, "region": region,
            "flag_svg": flag_svg(c1, c2, len(members), EMBLEM.get(grievance, "✶"), h)}


def flag_svg(c1, c2, n_stars, emblem, h):
    layout = h % 3
    w, hgt = 120, 80
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {hgt}" width="{w}" height="{hgt}">']
    if layout == 0:      # horizontal bands
        parts.append(f'<rect width="{w}" height="{hgt}" fill="{c1}"/><rect y="{hgt//3}" width="{w}" height="{hgt//3}" fill="{c2}"/>')
    elif layout == 1:    # diagonal
        parts.append(f'<rect width="{w}" height="{hgt}" fill="{c2}"/><polygon points="0,0 {w},0 0,{hgt}" fill="{c1}"/>')
    else:                # canton
        parts.append(f'<rect width="{w}" height="{hgt}" fill="{c1}"/><rect width="{w//2}" height="{hgt//2}" fill="{c2}"/>')
    # stars = member count (max 12 drawn)
    for i in range(min(n_stars, 12)):
        cx = 10 + (i % 6) * 10
        cy = hgt - 14 + (i // 6) * 9
        parts.append(f'<circle cx="{cx}" cy="{cy}" r="2.6" fill="#fff" stroke="#000" stroke-opacity=".25"/>')
    parts.append(f'<text x="{w-28}" y="{hgt//2+9}" font-size="26" text-anchor="middle" fill="#fff" '
                 f'stroke="#000" stroke-opacity=".35" font-family="serif">{emblem}</text>')
    parts.append("</svg>")
    return "".join(parts)
