# Working notes

The original, more detailed README: setup details, how a round works, the feed and broadcasts, episode mode, the
tutorial recorder, tests, and Ollama speed settings. Some of it predates the current README; where they differ, the
README and the code win.

# FAULT LINES

A visual US unrest simulator layered on [MiroFish-Offline](https://github.com/nikmcfly/MiroFish-Offline)
(OASIS multi-agent engine + Ollama, fully local).

**Simulation — LLM role-play, not a forecast.**

50 agents, one per state, each with a persona built from grievance / economic / policy data
(unemployment, rent vs income, federal dependence, live policy pressures). Feed it a seed scenario,
run N rounds, and watch unrest climb the ladder (petition → protest → strike → riot) and blocs of
low-cohesion neighbors crack away from the union.

```
faultlines/
  run.py            one command: checks Ollama + MiroFish venv, serves API + map on :8008
  config.yaml       hard caps, model names, scoring weights, bloc threshold, image backend
  data/states.json  per-state inputs (approximate; refresh with scripts/fetch_data.py)
  sim/personas.py   states.json -> OASIS twitter_profiles.csv + simulation_config.json
  sim/runner.py     runs in MiroFish's venv; subclasses MiroFish's Twitter runner, dumps rounds/round_N.json
  backend/app.py    FastAPI: runs, timeline, state detail, images, LLM logging proxy
  backend/engine.py run manager + scoring loop (reads round files, writes timeline.json)
  backend/scoring.py rule-based keyword/engagement scorer + LLM judge rubric + blend
  backend/blocs.py  adjacency union-find, auto names + SVG flags
  backend/images.py protest scenes: ComfyUI (Flux) | OpenAI-style API | SVG placeholder
  frontend/         plain JS + d3 + topojson (vendored), no build step
  tests/            test_scoring.py (unit), smoke.py (end to end; --live for the real engine)
  runs/<id>/        everything a run produced (profiles, config, sqlite db, rounds, timeline, images)
  logs/llm_calls.jsonl  every LLM call with token counts (agents via proxy, judge direct)
```

## Setup (once)

1. MiroFish-Offline cloned next to this folder as `../mirofish`, with `cd mirofish/backend && uv sync`.
2. Ollama running with `ollama pull qwen2.5:14b` (swap in `config.yaml`; 32b if it is fast enough).
3. `uv sync` in this folder.

Neo4j is only needed by MiroFish's own document → graph pipeline, not by FAULT LINES.

## Run

```
uv run run.py
```

Open http://127.0.0.1:8008. Paste a scenario, pick rounds (capped), hit **Run simulation**.
Tick **demo** to see the map without any LLM (canned escalating posts).

## How a round works

1. `sim/runner.py` picks `agents_per_round` states (weighted by current unrest), lets them act in
   OASIS (post / quote / repost / like / follow), then writes the new posts to `rounds/round_N.json`.
   States follow their land neighbors and the newswire, so geography shapes who sees what.
2. The backend scores each state that posted:
   * **rule** half: ladder keywords, affect lexicon, anti/pro-federal phrases, neighbor mentions,
     likes/shares (`scoring.py`, all visible in the UI under "how this was scored")
   * **judge** half: one qwen call per 10 states with a fixed rubric returning stage / unrest /
     cohesion / grievance / verbatim slogans
   * blend with the previous round (EMA), silent states drift back toward baseline
3. Blocs: adjacent states both under `bloc_cohesion_threshold` merge; names/flags are deterministic
   from the member set and persist while membership overlaps.
4. Scenes: first time a state reaches protest or higher at a given stage, one image is requested
   with the agent's actual slogans on the signs. Cached per (state, stage). Capped.

## Images

`images.provider: auto` uses OpenRouter's Images API (Flux 3 Klein) when `OPENROUTER_API_KEY` is set, else a local
ComfyUI at `comfyui_url` if it answers (template `images/comfy_flux_workflow.json`), else Pillow placeholders.
One scene per (state, stage) from protest upward, posted to the Feed by that state's local-news account.

## The Feed, memes, scenes and broadcasts (v0.2)

Every round also produces `feed/round_N.json`: an X-style stream with the 50 state voices, a local-news
account per state, and a fixed NPC cast (influencers, pundits, bots, a doomposter, the ratio guy, a union
organizer, a meme account). NPC reactions are one batched LLM call per round. Trending hashtags are
computed from the round's posts. Click a post's state chip to pan the map there.

| Layer | Needs | Without the key |
|---|---|---|
| Agents (OASIS) | `OPENROUTER_API_KEY` (+ `AGENT_MODEL`, default google/gemini-2.5-flash) | falls back to local Ollama (qwen2.5:14b) |
| Memes, scenes, avatars | `OPENROUTER_API_KEY` (+ `IMAGE_MODEL`, default black-forest-labs/flux-3-image-klein) | Pillow placeholders; captions are still stamped in code |
| Broadcast voices | `CARTESIA_API_KEY` | the browser reads the script aloud (SpeechSynthesis) |
| Anchor / rant video | `AVATAR_PROVIDER=omnihuman` + `OMNIHUMAN_ENDPOINT` + `OMNIHUMAN_API_KEY` | still image anchor |
| Clips on big moments | `VIDEO_PROVIDER=fal` + `FAL_KEY` | none |

Put keys in `faultlines/.env` (see `.env.example`). Toggle each layer under `media:` in `config.yaml`.

**Spend guard.** Every paid call goes through `backend/spend.py` and lands in `runs/<id>/spend.json`.
`RUN_BUDGET_USD` (default 5) halts media generation when reached; the simulation itself keeps running.
The header shows live spend per run. OpenRouter returns exact `usage.cost`, so agent and image costs are
exact; TTS/video costs are per-call estimates (`CARTESIA_USD_PER_CHAR`, `VIDEO_USD_PER_CLIP`).

**Memes.** The LLM picks one of 14 generic format descriptions (`backend/memes.py`) and writes top/bottom
captions. Flux draws only the base image with a "no text" instruction; Pillow stamps the captions in
Impact with a black stroke. `MEMES_PER_ROUND` caps it.

**Fault Lines Network.** After each round a News Desk call writes two scripts with opposite spin
(`broadcast.networks` in config): headline, lower-third chyron, 30-second script, field cut-in. The player is
docked bottom-left over the map, with a ticker of per-state unrest numbers.

**Voices and anchors.** Three networks (`broadcast.networks`): FLN Red (alarmist), FLN Blue (dismissive) and FLN World
Service (a British world-service desk explaining America). Anchor voices are American except the World Service; all ids
and accents are in `backend/reporters.py` (`VOICES`), overridable with `CARTESIA_VOICE_<ROLE>`. Each state has its own field
reporter with a state-specific portrait prompt (`BACKDROP`) and a voice; per-state voice overrides live in `STATE_VOICES`
or `CARTESIA_VOICE_STATE_<XX>`. Spoken text goes through `providers.speakable()` so all-caps words are never spelled out,
state codes become names, and hashtags read naturally; on-screen headlines stay uppercase. Anchor and reporter portraits
are 16:9 to match the player. Click any image or the broadcast frame to open it full-size; the ⤢ button widens the Feed.

**Elections and debates.** Tick *election* on the form, write the ballot question and prompt two candidates (name, party,
platform, voice). The states campaign for N rounds (the judge polls each state's lean every round, candidate accounts
post in the Feed), the candidates debate on FLN the day before the vote (`backend/debate.py`, one LLM call, voiced line
by line with an emotion cue per line, two-podium stage in the player), the wire recaps the debate the next morning, and
on Election Night every state agent is interviewed with the ballot (`election.ballot_prompt`) and returns a vote share.
`backend/election.py` tallies electoral votes (535, DC excluded, 268 to win) and a population-weighted popular vote; the
Election view colors the map by lean during the campaign and calls states one poll-closing group at a time on the night.
FLN Red openly backs the red candidate (A) and FLN Blue the blue one (B); the World Service stays neutral.

**Candidates read their bios.** At run start `backend/candidates.py` analyzes every candidate's full bio (entity type
human/AI/other, a visual description, a novelty score 0-10 and notes) and stores it on the candidate. Portraits are
generated from that description only (an AI candidate is drawn as a visibly non-human machine), cached by candidate id
plus a hash of the description, checked for near-duplicates with a perceptual hash and regenerated with a new seed if
two look alike, and used everywhere (feed avatar, debate stage, broadcast, map). Novelty >= 7 injects a SHOCK CONTEXT
block into every state persona and NPC prompt, makes the news desk lead with it in the first broadcasts and keep it
recurring, forces a debate question about it, seeds round-1 reactions in the Feed, and gives every state an AI stance
(enthusiast / curious / skeptical / hostile, from its industries and grievances) that weights its polling and ballot.

**Scheduled events fire.** `backend/events.py` parses events out of the seed ("Mid-campaign event (round 4): ...") and
merges explicit ones from the POST body (`events: [...]`) and `data/events.json`. On their round the wire posts the event
as breaking news (every agent sees it), the engine applies a rule-based shock (poll hit to affected candidates by severity,
stock hits on affected tickers, an unrest bump), the news desk leads with it, the Feed bursts, the timeline logs
EVENT FIRED and the UI shows a banner. At the end of the run any unfired event is logged as a loud warning.

**Voices: Fish Audio or Cartesia.** `broadcast.tts_provider: auto` uses Fish Audio (`FISH_AUDIO_API_KEY`, model s2.1-pro)
when the key is set and falls back to Cartesia per call (for example when Fish has no API credit, which is bought
separately from platform credit at fish.audio/app/developers). Fish voices get S2 emotion cues in brackets at the start
of the text: anchors by network (`emotion` in `broadcast.networks`), field reporters by the state's ladder stage,
influencer rants `[angry, shouting]`, and debate lines from the writers' room. Voice ids live in `backend/reporters.py`
(`FISH_VOICES`, generic library voices only, no clones of real people) and can be overridden with `FISH_VOICE_<ROLE>`.

**The economy.** `backend/market.py` keeps a fictional market per run in `runs/<id>/market.json`. At run start the Market
Desk invents 6-8 fictional tickers from the scenario (sector, price, a sensitivity to unrest and to the populist side);
four macro series are fixed: FLN 500, 10-year yield, oil, a fear index. Each round one desk call reads the round's facts
(unrest, new strikes and riots, blocs formed or collapsed, polls, debate, headlines) and returns a move per ticker plus two
market headlines; that is blended 50/50 with a rule-based move (visible under `inputs`) so the tape keeps moving sensibly
without the LLM. The tape scrolls above the unrest ticker; the Markets tab has the index chart, headlines and sparklines,
and the scrubber replays prices per round. The news desk sees the market line too, so FLN Blue can talk about it.

**Media pipeline.** `backend/media.py` is a two-worker queue; the scorer never waits on an image. Everything
is cached under `runs/<id>/media/` and the timeline scrubber replays feed, images and broadcasts per round.
Avatars are cached forever under `media/avatars/` and shared across runs.

## Episode mode (the show)

A finished run plays like a TV episode. The simulation writes its run file first (rounds, feeds, market ticks,
polls, the ballot); then `backend/director.py` reads it and builds `runs/<id>/episode.json`: ordered acts of
scenes (anchor segment, map montage, feed burst, market update, protest scene, polling update, candidate profile,
debate, election-night tick, victory or contingent, epilogue, one-year-later teaser), each with voiced narration
whose audio length sets the scene length. The player (`frontend/episode.js`) only plays the script and pins the
map, feed and ticker to the scene's round, so nothing ahead can leak. It builds automatically when a non-demo
run finishes (`episode.auto_build`) or from the 📺 Episode button; `POST /api/runs/<id>/episode?force=true` rebuilds.

Acts: Cold Open (the seed as breaking news), The Shock, The Campaign (or The Split for non-election runs), The
Debate, The October Surprise (the biggest late shock the run produced), Election Night, Epilogue.

Election night is spoiler-proof by construction (`backend/election_night.py`): the final tally is turned into
ticks on one clock, polls closing East to West, each unit counting 5% → 40% → 80% → 100% with an early lean
that converges on the real result (mail ballots and big cities counting late), calls when decisive, "too close
to call" otherwise, the bar filling toward 270, the projection on the tick that crosses it (or a contingent
election in the House). The anchor's commentary for a tick is generated from a summary that contains only what
that tick has revealed.

Electoral math: 538 (DC included, Maine and Nebraska split by district), with an assert; 2 to 4 candidates with
lane colors (right red, left blue, center purple, extras green/amber); per-candidate ballot access (`off_ballot`)
zeroes that candidate in those states. Controls: play, pause, next scene; chapters ahead of the current act stay
locked and the debug timeline stays disabled until the episode has been watched once (remembered per browser).
Music beds are optional files at `frontend/audio/music/<act>.mp3` (cold, shock, campaign, debate, night,
epilogue); stingers are synthesized, or `frontend/audio/stingers/{breaking,call}.mp3` if present.

## Tutorial mode and recording a video

The app can give its own guided tour. Press **Watch the tutorial** under Run simulation, or open
`http://127.0.0.1:8008/?tour=water` (the Colorado River election plus Season 2) or `?tour=aria` (ARIA vs Pastor
Ruth Calloway). A narrator voice (Cartesia, set in `config.yaml` under `tour.narrator_voice`) reads each line, a
cursor drives the real UI, and captions show what is being said. Esc stops it. The tour types a scenario, presses
Run, shows the first round arriving, presses Stop, then walks through a finished run of the same kind.
The scripts live in `frontend/tour_scripts.js`; the run ids they open are at the top of that file.
Narration lines are voiced once and cached under `runs/_tour/audio/`.

To record a tutorial to MP4 with clean audio (no microphone, no OBS):

```
uv venv tools/.rec && uv pip install --python tools/.rec/Scripts/python.exe playwright
tools/.rec/Scripts/python.exe -m playwright install ffmpeg
tools/.rec/Scripts/python.exe tools/record_tour.py --tour water --out videos/tutorial_water.mp4
```

It plays the tour in headless Chrome at 1920x1080, streams the frames into ffmpeg, and rebuilds the soundtrack
from the original narration and broadcast files using the times the page logged. The waits for setup and for a
phone reply are cut out. A matching `.srt` subtitle file is written next to the video. `--rebuild` re-encodes
from the saved work folder without recording again.

## Tests

```
python -I tests/test_scoring.py          # scorer + bloc unit checks
.venv/Scripts/python tests/test_media.py   # spend guard, meme stamping, roster, feed
python -I tests/smoke.py                 # demo end to end (server must be running)
python -I tests/smoke.py --live --rounds 2 --agents 8   # real OASIS + Ollama
```

## Local model speed (Ollama on one GPU)

Set these as **user environment variables** on Windows and restart the Ollama app (they are read at startup):

| variable | value | why |
| --- | --- | --- |
| `OLLAMA_NUM_PARALLEL` | `4` | four agent calls served at once (matches `sim.semaphore` in config.yaml) |
| `OLLAMA_MAX_LOADED_MODELS` | `1` | one model in VRAM, no thrashing |
| `OLLAMA_KEEP_ALIVE` | `-1` | keep the model loaded between rounds |
| `OLLAMA_FLASH_ATTENTION` | `1` | faster prompt processing |
| `OLLAMA_KV_CACHE_TYPE` | `q8_0` | halves the KV-cache VRAM so four 16k slots fit on a 24 GB card |
| `OLLAMA_CONTEXT_LENGTH` | `16384` | the agents' prompts are 6-9k tokens; each parallel slot reserves this much |

Check them in `%LOCALAPPDATA%\Ollama\server.log` ("server config" line). The sim's own knobs: `sim.semaphore` (concurrent
agent calls), `sim.judge_concurrency` (judge batches scored in parallel) and `sim.decision_layer` (one option-bank call a
round for most states). Per-call latency and tokens are in `logs/llm_calls.jsonl`; per-round timing is in each run's
`runner.log`.
