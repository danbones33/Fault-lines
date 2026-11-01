"""
Canned posts for demo runs (no LLM). Escalation follows each state's baseline
grievance plus the round number, so the map visibly heats up and blocs form.
Used by the smoke test and for frontend work without a GPU.
"""
import random

TEMPLATES = {
    0: ["We read the news. {pressure} is already a problem here; this won't help. Watching closely.",
        "Folks in {name} are talking about this at the diner. Not panicking yet. We urge Congress to explain the math."],
    1: ["We're starting a petition in {name}. Rent eats {rent}% of a paycheck and now this. Sign the letter to our delegation.",
        "Town hall tonight. {pressure} plus this new rule means we write to Washington first, then we'll see.",
        "Thousands have signed the petition already. We expect a fair hearing. Reasonable people can fix this."],
    2: ["Protest on the capitol steps Saturday. Signs read \"RENT IS NOT A LUXURY\" and \"FIX {pressure_caps}\". {neighbor}, stand with us.",
        "We marched in {name} today. Crowd chanting \"WE CAN'T AFFORD THIS\". {neighbor} sent a delegation. The feds aren't listening.",
        "Rally outside the statehouse. \"PAY US OR FACE US\" on every sign. Washington keeps pushing costs onto us."],
    3: ["General strike called in {name}. Trucks parked, ports idle. \"NO PAY, NO WORK\". {neighbor} joins us Monday. The feds can watch.",
        "Boycott of federal fees starts now. We refuse to pay. \"OUR MONEY, OUR STATE\". Together with {neighbor} we blockade the interstate.",
        "Walkout across every {industry} site. Washington's mandate is unconstitutional and we won't comply. \"STATES DECIDE\"."],
    4: ["Riot broke out downtown after police cleared the strike camp. Barricades burn. \"BURN THE MANDATE\". We are past petitions. Washington did this.",
        "Clashes all night in {name}. Smoke over the capitol. \"NO MORE WASHINGTON\". {neighbor}, it's time to go it alone.",
        "Crowds smashed the federal building windows. \"SECEDE\" painted on the doors. We stand with {neighbor}. The union is a dead letter here."],
}


def make_round_posts(r, total, personas, rng, pid, per_round):
    codes = list(personas.keys())
    active = rng.sample(codes, min(per_round, len(codes)))
    posts = []
    for c in active:
        pm = personas[c]
        heat = pm["baseline_grievance"] / 36.0 * 0.5 + (r / total) * 1.0 + rng.uniform(-0.3, 0.3)
        stage = max(0, min(4, int(heat * 2.6)))
        t = rng.choice(TEMPLATES[stage])
        pressure = rng.choice(pm["policy_pressures"])
        neighbor = personas[rng.choice(pm["neighbors"])]["name"] if pm["neighbors"] else "our allies"
        content = t.format(name=pm["name"], rent=pm["rent_burden_pct"], pressure=pressure,
                           pressure_caps=pressure.upper()[:24], neighbor=neighbor,
                           industry=rng.choice(pm["key_industries"]))
        posts.append({"post_id": pid, "agent_id": None, "state": c, "kind": "post", "content": content,
                      "quote_content": None, "original_post_id": None,
                      "num_likes": rng.randint(0, 5 + stage * 8), "num_shares": rng.randint(0, 2 + stage * 3)})
        pid += 1
    return posts, pid
