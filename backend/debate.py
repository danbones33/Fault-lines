"""
Debate night: the candidates (2-4, built from the user's prompts) debate on FLN the day before the
vote. One LLM call writes a moderated transcript grounded in the race so far, with an emotion cue and
an instant-poll shift per line, a burst of Feed reactions keyed to lines, and a verdict (who won, poll
bump). Each line is voiced with its own voice; the player shows a split-screen stage with a live meter.
"""
SYSTEM = ("You write a televised presidential debate for a FICTIONAL simulation. Fictional candidates, one fictional "
          "moderator. Sharp, specific, quotable; each candidate stays rigorously in character with their platform; they "
          "attack each other's policies, never persons. Reference the real events of the campaign given below (states, "
          "unrest, slogans, polls, markets). No real people. Return JSON only.")

# Cartesia voices for the stage; override with CARTESIA_VOICE_CANDIDATE_<ID> / _MODERATOR, or the form's voice choice
STAGE_VOICES = {
    "A_f": "d7bf7d75-64b7-4c1e-86c0-79d647366587", "A_m": "5ee9feff-1265-424a-9d7f-8e4d431a12c7",
    "B_f": "9626c31c-bec5-4cca-baa8-f8ba9e84c8bc", "B_m": "a5136bf9-224c-4d76-b823-52bd5efcffcc",
    "C_f": "f786b574-daa5-4673-aa0c-cbe3e8534c02", "C_m": "1fcd23d0-bf12-4896-8f60-4f21ef5c9b98",
    "D_f": "db6b0ed5-d5d3-463d-ae85-518a07d3c2b4", "D_m": "630ed21c-2c5c-41cf-9d82-10a7fd668370",
    "moderator": "25d7abcb-4d6d-4aca-adce-8a1c85620c8b",
}


# Fictional moderators. The name decides the voice: the writer is told who is moderating, never invents one.
MODERATORS = [("Diane Okafor", "f"), ("Dana Whitfield", "f"), ("Renee Castillo", "f"), ("Claire Donovan", "f"), ("Marisol Vega", "f"),
              ("Gordon Pryce", "m"), ("Raymond Cole", "m"), ("Elliot Marsh", "m"), ("Victor Hollis", "m")]
MODERATOR_VOICES = {"f": "c2ac25f9-ecc4-4f56-9095-651354df60c0",   # Renee - Commander: firm, for broadcasts (general American)
                    "m": "74f42072-6245-4fe2-b5dc-3dc9b56fdbd0"}   # Regis - News Anchor: authoritative (general American)


# AI candidates (ARIA and the like) always speak with the flat synthetic voice the user picked on 2026-10-22: Fish "New History"
# with the [calm, synthetic, evenly paced] cue. If Fish is down the router swaps in a Cartesia digital-assistant voice.
AI_VOICE_FISH = "80e34d5e0b2b4577a486f3a77e357261"
AI_VOICE_CARTESIA = "47c38ca4-5f35-497b-b1a3-415245fb35e1"   # Daniel - Modern Assistant
AI_CUE = "calm, synthetic, evenly paced"


def is_ai(option):
    return ((option or {}).get("analysis") or {}).get("entity_type") == "AI"


def pick_moderator(run_id):
    import hashlib
    i = int(hashlib.sha1(str(run_id).encode()).hexdigest(), 16) % len(MODERATORS)
    return MODERATORS[i]


def moderator_voice(deb):
    """The moderator's Cartesia voice: by the debate's recorded gender; older debates default by name in the roster."""
    import os
    g = (deb or {}).get("moderator_gender") or dict(MODERATORS).get((deb or {}).get("moderator")) or "m"
    return os.environ.get(f"CARTESIA_VOICE_MODERATOR_{g.upper()}") or MODERATOR_VOICES[g]


def voice_for(option, gender, provider="cartesia"):
    """Voice id for a candidate on the chosen TTS provider (Fish ids are 32 hex, Cartesia ids are uuids)."""
    import os
    g = (gender or option.get("voice") or "m").lower()[:1]
    if is_ai(option):
        return AI_VOICE_FISH
    if option.get("voice_id"):
        # cast from the bio by casting.cast (a Cartesia uuid: the router sends uuids to Cartesia whatever the default provider)
        return option["voice_id"]
    if provider.startswith("fish"):
        env = os.environ.get(f"FISH_VOICE_CANDIDATE_{option['id']}")
        if env:
            return env
        from reporters import FISH_VOICES
        return FISH_VOICES.get(f"{option['id']}_{g}") or FISH_VOICES.get(f"A_{g}") or FISH_VOICES["A_m"]
    env = os.environ.get(f"CARTESIA_VOICE_CANDIDATE_{option['id']}")
    if env:
        return env
    return STAGE_VOICES.get(f"{option['id']}_{g}") or STAGE_VOICES["A_m"]


def prompt(election, race_context, n_questions=5, npc_handles=None, moderator=None):
    cands = "\n".join(f"Candidate {o['id']}: {o['name']} ({o.get('party','')}, {o.get('lane','')} lane). Platform: {o.get('blurb','')}"
                      for o in election["options"])
    ids = "|".join(f'"{o["id"]}"' for o in election["options"])
    handles = ", ".join(f"@{h}" for h in (npc_handles or [])[:10])
    n_questions = max(4, min(6, int(n_questions)))
    return (f"The election: {election['question']}\n{cands}\n\nThe campaign so far:\n{race_context}\n\n"
            + (f"The moderator is {moderator[0]} ({'she' if moderator[1] == 'f' else 'he'}); use exactly that name.\n" if moderator else "")
            + (f"NOVELTY: one of the candidates is not a human being ({', '.join(o['name'] for o in election['options'] if (o.get('analysis') or {}).get('entity_type') == 'AI')}). "
               f"One question MUST be about that directly ('Can a machine be president? Who is accountable?') and the human candidates must press it.\n"
               if any((o.get('analysis') or {}).get('novelty_score', 0) >= 7 for o in election['options']) else "")
            + f"Write the debate as a list of lines. Structure: moderator opens (1 line), then {n_questions} questions drawn from the "
            f"campaign's biggest issues; for each question the moderator asks (1 line), each candidate answers (1 line each, 40-65 words), "
            f"then one rebuttal from the two who clashed hardest (25-40 words each); finally closing statements from each candidate "
            f"(35-55 words) and a moderator sign-off. Questions must name states, numbers and slogans from the campaign.\n"
            f"Each line carries: 'emotion' (2-4 words for the voice actor: 'calm', 'angry', 'sarcastic', 'passionate, rising', "
            f"'cold, clipped', 'warm', 'exasperated', 'firm') and 'poll_shift' (-3 to 3: how an instant poll of viewers moves after the "
            f"line, positive toward candidate A... use 'poll_shift_for': the candidate id who gains, and 'poll_shift': 0-3 magnitude).\n"
            f"Also write 8-12 'reactions': posts from fictional social accounts ({handles or 'influencers, pundits, bots, state voices'}) "
            f"reacting to specific lines, each with 'after_line' (0-based index of the line it reacts to), 'handle' and 'text' (<= 35 words).\n"
            f"Finally a 'verdict': {{\"winner\": {ids}, \"bump\": <0-6 points of national swing toward the winner>, "
            f"\"reason\": \"<= 30 words\"}} and 'sharpest_exchange' (<= 45 words recap for the wire).\n"
            f"Return {{\"moderator\": \"<fictional name>\", \"topic_lines\": [\"short label per question\"], "
            f"\"lines\": [{{\"speaker\": \"mod\"|{ids}, \"emotion\": str, \"poll_shift_for\": str|null, \"poll_shift\": int, \"text\": str}}], "
            f"\"reactions\": [{{\"after_line\": int, \"handle\": str, \"text\": str}}], \"verdict\": {{...}}, \"sharpest_exchange\": str}}")


def write_debate(llm, run_id, election, race_context, n_questions=5, npc_handles=None):
    ids = [o["id"] for o in election["options"]]
    mod = pick_moderator(run_id)
    try:
        d = llm.chat_json(SYSTEM, prompt(election, race_context, n_questions, npc_handles, mod), kind="debate", run_id=run_id,
                          temperature=0.8, max_tokens=6000)
        lines = []
        for l in d.get("lines") or []:
            sp = str(l.get("speaker", "mod")).strip()[:3]
            if sp not in ids and sp != "mod":
                continue
            txt = str(l.get("text", "")).strip()[:900]
            if not txt:
                continue
            pf = str(l.get("poll_shift_for") or "").strip()[:1]
            try:
                mag = max(0, min(3, int(l.get("poll_shift", 0))))
            except Exception:
                mag = 0
            lines.append({"speaker": sp, "text": txt, "emotion": str(l.get("emotion", "")).strip()[:40],
                          "poll_shift_for": pf if pf in ids else None, "poll_shift": mag})
        if len(lines) < 4:
            return None
        reactions = []
        for r in d.get("reactions") or []:
            try:
                idx = max(0, min(len(lines) - 1, int(r.get("after_line", 0))))
            except Exception:
                idx = 0
            txt = str(r.get("text", "")).strip()[:300]
            if txt:
                reactions.append({"after_line": idx, "handle": str(r.get("handle", "")).lstrip("@")[:40], "text": txt})
        v = d.get("verdict") or {}
        try:
            bump = max(0.0, min(6.0, float(v.get("bump", 0))))
        except Exception:
            bump = 0.0
        verdict = {"winner": v.get("winner") if v.get("winner") in ids else None, "bump": bump, "reason": str(v.get("reason", ""))[:200]}
        return {"moderator": mod[0], "moderator_gender": mod[1], "topics": [str(t)[:60] for t in (d.get("topic_lines") or [])][:6],
                "lines": lines, "reactions": reactions, "verdict": verdict,
                "recap": str(d.get("sharpest_exchange", ""))[:400], "audio": [], "status": "script"}
    except Exception as e:
        print(f"[debate] failed: {e}")
        return None


def stage_prompt(option, gender):
    who = "a woman" if (gender or option.get("voice") or "m").lower().startswith("f") else "a man"
    accent = {"right": "deep red", "left": "navy blue", "center": "purple"}.get(option.get("lane"), "amber")
    return (f"photorealistic 16:9 television frame of a fictional presidential candidate, {who} in their 50s, standing at a debate "
            f"podium under studio lights, {accent} accents on the podium and backdrop, mid-sentence, confident, chest-up with clear "
            f"headroom, centered. No text, no logos, no real people.")
