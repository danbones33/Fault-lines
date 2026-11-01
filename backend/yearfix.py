"""
Year post-check: generated text (posts, hashtags, scripts) must live in the simulation's year.

A hashtag year (#Election2024) becomes the sim year. A bare year within a few years of the real present that is not
marked as history ("in 2024", "since 2020", "back in", "the 2022 midterms") is rewritten to the sim year. Years marked
as history, and years far from the real present (1861, 2001), are left alone.
"""
import re

HISTORY = re.compile(r"\b(in|since|back in|from|after|before|during|until|till|by|around|circa)\s+(19\d\d|20\d\d)\b", re.I)
HASHTAG_YEAR = re.compile(r"(#[A-Za-z_]*?)(19\d\d|20\d\d)\b")
BARE_YEAR = re.compile(r"(?<![#\w])(19\d\d|20\d\d)(?!\w)")
REAL_NOW = 2026


def fix_text(text, sim_year):
    if not text or not sim_year:
        return text
    sim_year = int(sim_year)
    out = HASHTAG_YEAR.sub(lambda m: m.group(1) + str(sim_year) if int(m.group(2)) != sim_year and abs(int(m.group(2)) - REAL_NOW) <= 4 else m.group(0), text)
    keep = {m.start(2) for m in HISTORY.finditer(out)}

    def bare(m):
        y = int(m.group(1))
        if y == sim_year or m.start(1) in keep:
            return m.group(0)
        if abs(y - REAL_NOW) <= 4 and y < sim_year + 1:
            return str(sim_year)
        return m.group(0)
    return BARE_YEAR.sub(bare, out)


def fix_items(items, sim_year, field="text"):
    n = 0
    for it in items or []:
        t = it.get(field)
        if isinstance(t, str):
            f = fix_text(t, sim_year)
            if f != t:
                it[field] = f
                n += 1
    return n
