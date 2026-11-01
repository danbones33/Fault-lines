"""
Election Night, spoiler-proof: turns the FINAL tallied results into a reveal timeline that the player
can show tick by tick, so nothing ahead of the current tick ever has to exist on the client.

For each poll-closing slot (Eastern first, then Central, Mountain, Pacific, Alaska/Hawaii) the states
in that slot start counting: 5% -> 40% -> 80% -> 100% reported. Early counts can lean the other way
(the "red mirage / blue shift" of mail ballots and big cities counting late) but always converge on the
real result, so the ending is correct by construction. A unit is CALLED when 80%+ is in and the margin is
decisive, or at 100% otherwise ("too close to call" in between). The electoral-vote bar fills as units are
called; the winner moment is the tick where someone crosses 270; if nobody does, it's a contingent election.

Output: {"ticks": [ {time, label, counts:{unit:{pct_in, shares, status, vote}}, ev:{opt:n}, new_calls:[...],
         too_close:[...], winner, revealed_text} ... ], "winner_tick": i|None, "contingent": bool}
"""
import random

from election import DISTRICTS, EV, POLL_CLOSE, TO_WIN, LANE_SIGN

# states where early returns typically overstate the right (mail/urban counted late): + means early lean right
EARLY_LEAN = {"PA": 7, "WI": 6, "MI": 6, "AZ": -4, "NV": 5, "GA": 5, "NC": 4, "CA": 6, "WA": 7, "OR": 6, "CO": 4, "MN": 4,
              "VA": 8, "OH": 3, "FL": -3, "TX": 2, "NY": 6, "NJ": 6, "IL": 7, "MD": 6}
STEPS = [5, 40, 80, 100]
TIMES_BETWEEN = ["+0:00", "+0:20", "+0:45", "+1:10"]   # label offsets within a slot


def _minutes(slot_label):
    """'7:00 PM ET' -> minutes since noon (so 12:00 AM = 720, 1:00 AM = 780): a monotonic election-night clock."""
    t, ampm, _ = slot_label.split(" ")
    h, m = [int(x) for x in t.split(":")]
    h = h % 12 + (12 if ampm == "PM" else 0)   # 0..23 with noon = 12
    return (h - 12) * 60 + m if h >= 12 else (h + 12) * 60 + m


def _label(minutes):
    h24 = (12 + minutes // 60) % 24
    mm = minutes % 60
    ap = "AM" if h24 < 12 else "PM"
    return f"{h24 % 12 or 12}:{mm:02d} {ap} ET"


def _offset(off):
    oh, om = [int(x) for x in off.lstrip("+").split(":")]
    return oh * 60 + om


def _shift(shares, election, points):
    sign = {o["id"]: LANE_SIGN.get(o.get("lane"), 0) for o in election["options"]}
    rights = [i for i in shares if sign.get(i, 0) > 0]
    lefts = [i for i in shares if sign.get(i, 0) < 0]
    out = dict(shares)
    if not rights or not lefts or points == 0:
        return out
    for i in rights:
        out[i] = max(0.0, out[i] + points / 2.0 / len(rights))
    for i in lefts:
        out[i] = max(0.0, out[i] - points / 2.0 / len(lefts))
    tot = sum(out.values()) or 1.0
    return {k: round(100.0 * v / tot, 1) for k, v in out.items()}


def build_night(results, election, seed=0):
    rng = random.Random(seed)
    units = results["states"]
    ids = [o["id"] for o in election["options"]]
    ev = {i: 0 for i in ids}
    counts = {}
    ticks = []
    called = set()
    winner_tick = None
    slot_units = []
    for slot, codes in POLL_CLOSE:
        us = []
        for c in codes:
            us.append(c)
            us += [d for d, (p, _, _) in DISTRICTS.items() if p == c]
        slot_units.append((slot, us))

    def snapshot(time_label, label, new_calls, too_close):
        return {"time": time_label, "label": label, "counts": {u: dict(v) for u, v in counts.items()},
                "ev": dict(ev), "new_calls": list(new_calls), "too_close": list(too_close),
                "winner": None, "called_total": len(called)}

    # one global clock: every slot's open + count steps become events, sorted by time, so slots interleave
    events = []
    for si, (slot, us) in enumerate(slot_units):
        base = _minutes(slot)
        events.append((base, si, -1))
        for step_i, _ in enumerate(STEPS):
            events.append((base + _offset(TIMES_BETWEEN[step_i]), si, step_i))
    events.sort(key=lambda e: (e[0], e[1], e[2]))

    for minute, si, step_i in events:
        slot, us = slot_units[si]
        if step_i == -1:
            # polls close: every unit starts counting with an early lean
            for u in us:
                final = units[u]["shares"]
                parent = units[u].get("parent") or u
                early = EARLY_LEAN.get(parent, rng.choice([-2, -1, 0, 1, 2, 3])) + rng.uniform(-2, 2)
                counts[u] = {"pct_in": 0, "shares": _shift(final, election, early), "status": "counting", "vote": None, "early_lean": round(early, 1)}
            ticks.append(snapshot(_label(minute), f"Polls close: {', '.join(c for c in us if '-' not in c)}", [], []))
            continue
        pct = STEPS[step_i]
        new_calls, too_close = [], []
        for u in us:
            c = counts[u]
            if c["status"] == "called":
                continue
            final = units[u]["shares"]
            w = pct / 100.0
            # converge from the early lean to the final result; a little late-count noise before 100%
            lean_now = c["early_lean"] * (1 - w) ** 1.5 + (rng.uniform(-1.5, 1.5) if pct < 100 else 0)
            c["pct_in"] = pct
            c["shares"] = _shift(final, election, lean_now)
            margin_final = units[u]["margin"]
            vote = units[u]["vote"]
            if vote is None:
                continue
            decisive = (pct >= 80 and margin_final >= 5) or (pct >= 40 and margin_final >= 20) or pct == 100
            if decisive:
                c["status"] = "called"
                c["vote"] = vote
                called.add(u)
                ev[vote] += units[u]["ev"]
                new_calls.append(u)
            elif pct >= 80:
                c["status"] = "too_close"
                too_close.append(u)
        tk = snapshot(_label(minute), f"{pct}% reporting in the {slot} states", new_calls, too_close)
        if winner_tick is None:
            for i in ids:
                if ev[i] >= TO_WIN:
                    winner_tick = len(ticks)
                    tk["winner"] = i
        ticks.append(tk)
    contingent = winner_tick is None
    # any unit never called (no winner) stays "counting" forever; flag it
    uncalled = [u for u, c in counts.items() if c["status"] != "called"]
    return {"ticks": ticks, "winner_tick": winner_tick, "contingent": contingent, "uncalled": uncalled,
            "final_ev": dict(ev), "to_win": TO_WIN, "options": results["options"]}


def revealed_summary(tick, election, unit_names):
    """Text the anchor is allowed to know at this tick: nothing beyond it."""
    names = {o["id"]: o["name"] for o in election["options"]}
    parts = [f"Time: {tick['time']}. {tick['label']}."]
    parts.append("Electoral votes so far: " + ", ".join(f"{names[i]} {n}" for i, n in tick["ev"].items()) + f" ({TO_WIN} to win).")
    if tick["new_calls"]:
        parts.append("Just called: " + "; ".join(f"{unit_names.get(u, u)} for {names[tick['counts'][u]['vote']]}" for u in tick["new_calls"]) + ".")
    if tick["too_close"]:
        parts.append("Too close to call: " + ", ".join(unit_names.get(u, u) for u in tick["too_close"]) + ".")
    counting = [(u, c) for u, c in tick["counts"].items() if c["status"] != "called" and c["pct_in"] > 0 and "-" not in u]
    if counting:
        bits = []
        for u, c in sorted(counting, key=lambda uc: -uc[1]["pct_in"])[:8]:
            lead = max(c["shares"], key=c["shares"].get)
            second = max([v for k, v in c["shares"].items() if k != lead] or [0])
            bits.append(f"{unit_names.get(u, u)} {c['pct_in']}% in, {names[lead]} ahead by {c['shares'][lead] - second:.0f}")
        parts.append("Still counting: " + "; ".join(bits) + ".")
    if tick.get("winner"):
        parts.append(f"PROJECTION: {names[tick['winner']]} has crossed {TO_WIN} electoral votes and will be the next President.")
    return " ".join(parts)
