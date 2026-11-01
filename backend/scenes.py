"""Protest scene prompts: crowd matches the ladder stage, signs carry the agent's real slogans."""
STAGE_SCENE = {
    1: "a small daytime gathering around a folding petition table outside a county building, a few dozen people signing papers, "
       "clipboards, a hand-lettered banner, overcast light",
    2: "a peaceful daytime protest crowd of a few hundred people in front of a state capitol building, handmade cardboard signs, "
       "calm mood, overcast light",
    3: "a large strike and boycott rally filling a city street, picket lines, closed storefronts, thousands of people, "
       "union-style banners, tense but orderly, late afternoon",
    4: "a chaotic night scene of civil unrest on a city street, dense crowd, smoke in the distance, overturned barricades, "
       "flashing emergency lights, handmade signs still raised",
}


def build_prompt(state_name, stage, slogans, grievance):
    scene = STAGE_SCENE.get(stage, STAGE_SCENE[2])
    signs = ", ".join(f'"{s}"' for s in slogans[:3]) or '"ENOUGH"'
    return (f"{scene}, set in {state_name}, USA. The signs read exactly: {signs}. The crowd is about {grievance}. "
            f"Photojournalism style, 35mm, wide shot, realistic. Fictional generic people, no recognizable faces, "
            f"no real politicians, no logos, no flags of real parties.")


def headline(state_name, stage_name, slogans):
    lead = slogans[0] if slogans else "ENOUGH"
    return {1: f"Petition drive underway across {state_name}: organizers report thousands of signatures",
            2: f"Crowds gather at the {state_name} capitol; signs read \"{lead}\"",
            3: f"Strike and boycott actions spread across {state_name}; picket lines report \"{lead}\"",
            4: f"Unrest overnight in {state_name}: barricades, smoke, and signs reading \"{lead}\""}.get(
        {"petition": 1, "protest": 2, "strike": 3, "riot": 4}.get(stage_name, 2), f"{state_name}: {stage_name}")
