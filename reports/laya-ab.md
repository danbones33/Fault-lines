# Laya A/B: who decides what the states do

Same scenario (Colorado River seizure), same seed, 6 rounds, 25 active states per round, local qwen2.5:14b, media off. Run on 2026-10-30. Dollar columns are what the tokens would have cost on OpenRouter deepseek/deepseek-v4.1-flash (backend Pricing class); the runs themselves cost $0.

| engine | run | LLM calls per round, by purpose | agent-side tokens (agent+bank+writer) | all tokens (incl. judge) | sec / round | agent-side $ equiv | all $ equiv | Laya pass |
|---|---|---|---|---|---|---|---|---|
| llm | 20261030-094017-76c8 | agent 25.0, judge 3.0, market 1.2, npc 1.0, wire 0.8, crime 0.2 | 401,385 | 450,501 | 56 (rounds 2+: 59) | $0.0251 | $0.0353 | - |
| bank | 20261030-094853-037c | agent 9.0, judge 3.0, bank 1.7, market 1.2, npc 1.0, wire 0.8, crime 0.2 | 169,860 | 217,763 | 55 (rounds 2+: 59) | $0.0139 | $0.0246 | - |
| laya (zero-shot top answer) | 20261030-095557-619a | judge 1.3, market 1.2, writer 1.0, npc 1.0, wire 0.8, bank 0.7, crime 0.2 | 10,651 | 37,557 | 15 (rounds 2+: 13) | $0.0019 | $0.0074 | 1.46s |
| laya + action mix | 20261030-100145-94b5 | judge 2.0, market 1.2, writer 1.0, bank 1.0, npc 1.0, wire 0.8, crime 0.2 | 19,798 | 56,347 | 28 (rounds 2+: 30) | $0.0039 | $0.0118 | 1.50s |

## Quality sniff

| engine | turns | post | quote | like | repost | nothing | state posts | duplicate-text rate |
|---|---|---|---|---|---|---|---|---|
| llm | 150 | 61% | 37% | 1% | 7% | 10% | 147 | 1% |
| bank | 150 | 67% | 36% | 21% | 17% | 3% | 155 | 5% |
| laya (zero-shot top answer) | 150 | 31% | 0% | 69% | 0% | 1% | 46 | 0% |
| laya + action mix | 150 | 40% | 24% | 20% | 8% | 8% | 96 | 17% |

Percentages are actions per active-state turn (a bank turn can post and like in the same turn, so rows can pass 100%). Duplicate-text rate: share of state posts at least 75% similar to an earlier post in the same run.

### Sample posts: llm

- **MT**: North Dakota's support means a lot, but we're not alone. #MontanaAndWyoming together demand that our farmers and ranchers not be the collateral damage in federal politics. #WaterFirst #FarmersFirst #MontanaSolidarity
- **DE**: The collapse of the Colorado River Compact means disaster for our farmers and industries. Delaware and our neighbors must protect our own water resources and reject federal overreach and corporate greed. #DelawareForWater #StateRights #NoWaterPrivatization
- **NJ**: With property taxes in New Jersey at an all-time high, we can't afford to sit quietly. It's time for a coordinated push to the state legislature. Let's turn up the heat on Trenton. #PropertyTaxRelief #NJActionNow
- **SC**: This could devastate SC ag as well. Georgia farmers are our neighbors and trading partners. Will SC take a stand on this? #SCStandsWithGA #WaterRights
- **MD**: The water crisis intensifies, threatening Pennsylvania and the Mid-Atlantic region. We must demand a fair federal response and protect our state water rights. #MarylandWaterRights #WaterForAll #ClimateJustice. Quoted from a sign: 'WATER IS NOT A COMMODITY'

### Sample posts: bank

- **NJ**: CO's federal dilemma shows the importance of state control. In New Jersey, we're advocating for local governance over electric bill spike.
- **ME**: @New Hampshire We see the cuts you're facing. Hope New Hampshire gets some relief soon.
- **DE**: Another day, another reminder that Delaware's economy depends on stable water resources and federal support. We must keep up pressure for policies that benefit us, not just the West #DelawareEconomy #ClimateConcerns
- **WI**: The water crisis affects us all, but how Iowa responds will show the way forward.
- **FL**: Water resources are dwindling and the federal response is causing alarm across the South and West, but what does this mean for Florida's agriculture? Rhetoric aside, we need real solutions from our neighbors and Washington, not more control. #FloridasWaterAgriculture

### Sample posts: laya (zero-shot top answer)

- **MD**: The drought's impact hits West Virginia hard, reminding us to conserve water here in Maryland. Let's not make the same mistakes.
- **WY**: It's unsettling seeing Idaho face tough water cuts. Are we next with our coal revenue decline?
- **SC**: Balancing growth and preservation ain't easy. We're torn between supporting businesses and protecting our natural beauty. What say y'all? 🤔 #SCFuture
- **ME**: Maine's beautiful, but living here is tough. Rent at $1,150 eats up a huge chunk of income. We need economic reforms now. #MeWorkMeLive
- **LA**: Hurricanes plus high rents? It’s too much. Louisiana demands affordable housing NOW. Join us or watch our community crumble. #LouisianaNeedsChange

### Sample posts: laya + action mix

- **CO**: The Colorado River water cuts are a painful necessity, but we need federal support for sustainable solutions and equitable water distribution. #WaterCrisis
- **WV**: Proud to support policies helping the Colorado River. West Virginia's rivers need the same care. #RiverCare
- **CO**: Colorado River cuts are a stark reminder of the challenges ahead. We must act now to prevent a crisis in our agricultural sector. #WaterCrisis
- **MD**: We stand with Colorado but need more on federal workforce layoffs in West Virginia.
- **RI**: Water crisis shows systemic failures. Urgent petition demands responsible governance on hospital system finances.

## Laya as the judge (not wired in; agreement only)

`tools/laya_judge_agreement.py` asked Laya one score question per state-round (calm, petition, protest, strike, riot)
from that state's own posts, on 826 state-rounds the LLM judge had scored in three finished runs (the ARIA race, the
Colorado River election and its Season 2):

| measure | result |
|---|---|
| exact stage agreement with the LLM judge | 48% (always guessing the judge's most common stage, calm, scores 49%) |
| within one step | 84% |
| rank correlation, Laya score vs judge unrest | 0.26 |

Zero-shot Laya does not track the judge well enough to replace it, so `judge_engine: laya` is not offered. The judge
also runs in the web server's environment, which has no torch; moving it would mean installing torch there too.
