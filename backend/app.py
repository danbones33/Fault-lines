"""
FAULT LINES API + static frontend + LLM logging/spend proxy.

  GET  /                              frontend
  GET  /api/config                    caps, providers, budget
  GET  /api/states                    static state facts
  GET  /api/runs                      list runs
  POST /api/runs                      {text, rounds, agents_per_round?, demo?, title?, seed?}
  GET  /api/runs/{id}                 timeline.json (+engine progress, spend, media_pending)
  GET  /api/runs/{id}/feed?upto=N     feed items for rounds 0..N (newest first), trending + broadcasts of round N
  GET  /api/runs/{id}/feed/{n}        one round's feed file
  GET  /api/runs/{id}/roster          identities
  GET  /api/runs/{id}/spend           ledger
  GET  /api/runs/{id}/state/{XX}      persona + per-round posts + images for one state
  GET  /api/runs/{id}/media/{path}    generated assets (memes, scenes, broadcast audio/video, clips)
  GET  /api/avatars/{handle}.png      cached avatar, or an initials placeholder drawn on the fly
  POST /api/runs/{id}/stop
  GET  /api/setup                     what is ready (text model, MiroFish, voices, pictures) for the first-run screen
  GET  /api/settings                  editable settings (secrets only as set / last four characters)
  POST /api/settings                  {values, clear}: saves to .env and applies at once
  POST /api/settings/test             {name, value?}: checks a key with a free call
  POST /llm/{run_id}/v1/chat/completions   proxy for the sim agents -> OpenRouter or Ollama; logs tokens + cost
"""
import json
import os
import sys
import time

import httpx
from datetime import datetime
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
FL_ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

from providers import AvatarProvider, ImageGen, TTSRouter, TextLLM, VideoProvider, initials_avatar, load_dotenv_file, model_extras  # noqa: E402
load_dotenv_file(os.path.join(FL_ROOT, ".env"))

from engine import AVATAR_DIR, RunManager, RUNS_DIR, _read_json, mirofish_python  # noqa: E402
import settings  # noqa: E402
from llm import LLMLogger  # noqa: E402
from media import MediaQueue  # noqa: E402
from spend import Pricing, SpendGuard  # noqa: E402

with open(os.environ.get("FL_CONFIG") or os.path.join(FL_ROOT, "config.yaml"), encoding="utf-8") as f:   # FL_CONFIG: another config file (tests)
    CFG = yaml.safe_load(f)

LOGGER = LLMLogger(os.path.join(FL_ROOT, CFG["llm"]["log_file"]))
PRICING = Pricing(CFG["llm"]["openrouter_base_url"])
SPEND = SpendGuard(RUNS_DIR, CFG["spend"]["run_budget_usd"], CFG["spend"]["warn_at_fraction"], PRICING)
LLM = TextLLM(CFG["llm"], SPEND, LOGGER)
IMAGES = ImageGen(CFG["images"], SPEND, LOGGER, FL_ROOT)
TTSP = TTSRouter(CFG["broadcast"], SPEND, LOGGER)
VIDEO = VideoProvider(CFG["video"], SPEND, LOGGER)
AVATAR = AvatarProvider(CFG["broadcast"], SPEND, LOGGER)
MEDIA = MediaQueue(workers=2)
PROXY_BASE = f"http://{CFG['server']['host']}:{CFG['server']['port']}/llm/v1"
RUNS = RunManager(CFG, LLM, IMAGES, TTSP, VIDEO, AVATAR, SPEND, LOGGER, MEDIA, PROXY_BASE)

print(f"[providers] text={LLM.provider} agent_model={LLM.agent_model} images={IMAGES.provider} ({IMAGES.model if IMAGES.paid else 'local'}) "
      f"tts={TTSP.provider} video={VIDEO.provider} avatar={AVATAR.provider} budget=${SPEND.default_budget:.2f}/run")

app = FastAPI(title="FAULT LINES", version="0.2")
_upstream = httpx.AsyncClient(base_url=LLM.base_url, timeout=httpx.Timeout(CFG["llm"]["model_timeout_s"]))
_local_upstream = httpx.AsyncClient(base_url=LLM.local_base, timeout=httpx.Timeout(CFG["llm"]["model_timeout_s"]))


def reload_providers():
    """Re-reads keys and choices from the environment into the live provider objects (the run manager holds the same
    objects, so new runs, the news desk and the media queue all pick the change up without a restart)."""
    global _upstream, _local_upstream
    LLM.__init__(CFG["llm"], SPEND, LOGGER)
    IMAGES.__init__(CFG["images"], SPEND, LOGGER, FL_ROOT)
    TTSP.__init__(CFG["broadcast"], SPEND, LOGGER)
    VIDEO.__init__(CFG["video"], SPEND, LOGGER)
    AVATAR.__init__(CFG["broadcast"], SPEND, LOGGER)
    SPEND.default_budget = float(os.environ.get("RUN_BUDGET_USD") or CFG["spend"]["run_budget_usd"])
    _upstream = httpx.AsyncClient(base_url=LLM.base_url, timeout=httpx.Timeout(CFG["llm"]["model_timeout_s"]))
    _local_upstream = httpx.AsyncClient(base_url=LLM.local_base, timeout=httpx.Timeout(CFG["llm"]["model_timeout_s"]))
    print(f"[providers] reloaded: text={LLM.provider} images={IMAGES.provider} tts={TTSP.provider} video={VIDEO.provider} budget=${SPEND.default_budget:.2f}/run")


class NewRun(BaseModel):
    text: str
    rounds: int = 6
    agents_per_round: int | None = None
    demo: bool = False
    title: str | None = None
    seed: int | None = None
    start_year: int | None = None  # calendar: the run's first day (defaults to world.start_year / start_month in config)
    start_month: int | None = None
    start_year: int | None = None  # calendar: the run's first day (defaults to world.start_year / start_month in config)
    start_month: int | None = None
    election: dict | None = None   # {question, options: [{name, party?, blurb?, color?, lane?, voice?, off_ballot?}, ...]}
    events: list | None = None     # explicit scheduled events: [{round, name, description, affected_candidates?, severity?, stock_hits?}]


@app.get("/api/config")
def get_config():
    return {"caps": CFG["caps"], "scoring": CFG["scoring"], "sim": CFG["sim"], "media": CFG["media"],
            "llm": {"provider": LLM.provider, "agent_model": LLM.agent_model, "judge_model": LLM.judge_model, "available": LLM.available(),
                    "local_model": LLM.local_model if LLM.local_ok else None, "local_kinds": sorted(LLM.local_kinds) if LLM.provider == "hybrid" else [],
                    "cloud_kinds": sorted(LLM.cloud_kinds) if LLM.provider == "hybrid" else []},
            "images": {"provider": IMAGES.provider, "model": IMAGES.model if IMAGES.paid else None},
            "tts": TTSP.provider, "video": VIDEO.provider, "avatar": AVATAR.provider,
            "budget_usd": SPEND.default_budget, "networks": CFG["broadcast"]["networks"], "world": CFG.get("world", {"start_year": 2026, "start_month": 1}), "world": CFG.get("world", {"start_year": 2026, "start_month": 1}),
            "keys": {"openrouter": bool(os.environ.get("OPENROUTER_API_KEY")), "cartesia": bool(os.environ.get("CARTESIA_API_KEY")), "fish": bool(os.environ.get("FISH_AUDIO_API_KEY")),
                     "fal": bool(os.environ.get("FAL_KEY"))},
            "fallback_voice": os.environ.get("FL_FALLBACK_VOICE") or "off",
            "label": "Simulation — LLM role-play, not a forecast."}


# ---------- setup and settings (the first-run screen and the Settings panel) ----------
def _local_only(request: Request):
    """Settings change keys and spending: only this computer's own browser tab may call them. Blocks other machines,
    other websites (Origin) and DNS rebinding (Host)."""
    port = CFG["server"]["port"]
    ok_hosts = {f"127.0.0.1:{port}", f"localhost:{port}", f"[::1]:{port}"}
    client = (request.client.host if request.client else "") or ""
    origin = request.headers.get("origin")
    if client not in ("127.0.0.1", "::1", "localhost") or request.headers.get("host") not in ok_hosts:
        raise HTTPException(403, "settings can only be changed from this computer")
    if origin and origin.split("://", 1)[-1] not in ok_hosts:
        raise HTTPException(403, "settings can only be changed from the app's own page")


def setup_status():
    text_ready = LLM.available()          # an OpenRouter key (or, for developers who chose it in .env, a local Ollama)
    mf = mirofish_python(RUNS.mirofish_backend)
    voices = "fish" if TTSP.fish.available else "cartesia" if TTSP.cartesia.available else "none"
    return {
        "text": {"ready": bool(text_ready), "provider": LLM.provider, "cloud": bool(LLM.key)},
        "engine": {"ready": bool(mf)},
        "pictures": "flux" if IMAGES.paid else IMAGES.provider,
        "voices": voices, "fallback_voice": os.environ.get("FL_FALLBACK_VOICE") or "off",
        "budget_usd": SPEND.default_budget,
        "can_run": bool(text_ready and mf), "can_demo": True,
    }


@app.get("/api/setup")
def get_setup():
    return setup_status()


@app.get("/api/settings")
def get_settings(request: Request):
    _local_only(request)
    return {"fields": settings.view(), "setup": setup_status()}


class SettingsBody(BaseModel):
    values: dict[str, str] = {}
    clear: list[str] = []


@app.post("/api/settings")
def post_settings(body: SettingsBody, request: Request):
    _local_only(request)
    try:
        changed = settings.apply(body.values, body.clear)
    except ValueError as e:
        raise HTTPException(400, str(e))
    reload_providers()
    return {"changed": changed, "fields": settings.view(), "setup": setup_status(), "config": get_config()}


class TestBody(BaseModel):
    name: str
    value: str | None = None


@app.post("/api/settings/test")
def test_setting(body: TestBody, request: Request):
    _local_only(request)
    return settings.test(body.name, body.value)


@app.get("/api/states")
def get_states():
    return RUNS.states


@app.get("/api/runs")
def list_runs():
    return [RUNS.summary(r) for r in RUNS.list_runs()]


@app.post("/api/runs")
def create_run(body: NewRun):
    if not body.text.strip():
        raise HTTPException(400, "scenario text is empty")
    if not body.demo and not LLM.available():
        raise HTTPException(503, "No OpenRouter key yet. Open Settings to add one, or try a demo run.")
    b = body
    jid = RUNS.start_job("run", lambda job: RUNS.create_run(b.text, b.rounds, b.agents_per_round, b.demo, b.title, b.seed, b.election, b.events,
                                                            start=(b.start_year, b.start_month) if b.start_year else None, job=job))
    return {"job_id": jid}


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = RUNS.jobs.get(job_id)
    if not j:
        raise HTTPException(404)
    return {**j, "elapsed": round(time.time() - j["started"], 1)}


@app.get("/api/runs/{run_id}")
def get_run(run_id: str):
    tl = RUNS.timeline(run_id)
    if tl is None:
        raise HTTPException(404, "no such run")
    tl["engine"] = _read_json(os.path.join(RUNS_DIR, run_id, "progress.json"), {})
    return tl


@app.get("/api/runs/{run_id}/activity")
def get_activity(run_id: str):
    """What the run is doing right now, for the live bar: engine phase, media in flight, spend, and the recent log."""
    run_dir = os.path.join(RUNS_DIR, run_id)
    if not os.path.isdir(run_dir):
        raise HTTPException(404, "no such run")
    tl = RUNS.timelines.get(run_id) or _read_json(os.path.join(run_dir, "timeline.json"), {})
    pr = _read_json(os.path.join(run_dir, "progress.json"), {})
    act = RUNS.activity_of(run_id)
    led = SPEND.ledger(run_id)
    now = time.time()
    last = max([act.get("t") or 0] + [m["t"] for m in MEDIA.recent_done(5)])
    try:
        last = max(last, datetime.fromisoformat(pr["updated_at"]).timestamp())
    except Exception:
        pass
    for c in led.get("calls", [])[-3:]:
        try:
            last = max(last, datetime.fromisoformat(c["ts"]).timestamp())
        except Exception:
            pass
    by = led.get("by_kind", {})
    voices = by.get("tts", 0.0)
    pictures = sum(by.get(k, 0.0) for k in ("images", "memes", "avatars"))
    text = max(0.0, led.get("total_usd", 0.0) - voices - pictures)
    return {"status": tl.get("status"), "error": tl.get("error"), "mode": tl.get("mode"), "engine": pr,
            "current": act["current"], "current_t": act["t"], "log": act["log"][-60:],
            "rounds": max(0, len(tl.get("rounds", [])) - 1), "total_rounds": int((tl.get("scenario") or {}).get("rounds") or pr.get("total_rounds") or 0),
            "media": {"pending": MEDIA.pending(), "active": MEDIA.active_labels()[:6], "recent": MEDIA.recent_done(8)},
            "spend": {"total_usd": led.get("total_usd", 0.0), "budget_usd": led.get("budget_usd", 0.0), "halted": led.get("halted", False),
                      "voices": voices, "pictures": pictures, "text": text,
                      "recent": [{"ts": c.get("ts", "")[11:19], "kind": c.get("kind"), "usd": c.get("usd", 0.0), "provider": c.get("provider"), "model": (c.get("model") or "").split("/")[-1]} for c in led.get("calls", [])[-10:]]},
            "episode": _read_json(os.path.join(run_dir, "episode_progress.json"), {}),
            "warning": TTSP.warning(),
            "ago_s": int(now - last) if last else None, "server_time": now}


@app.post("/api/runs/{run_id}/revoice")
def revoice_run(run_id: str, episode: bool = True, everything: bool = False):
    """Re-voice a finished run with the current World Service and moderator voices, then rebuild its episode (queued)."""
    tl = RUNS.timeline(run_id)
    if not tl:
        raise HTTPException(404)
    if tl.get("status") not in ("done", "error"):
        raise HTTPException(409, "the run is still going")
    MEDIA.submit(lambda: RUNS.revoice(run_id, episode=episode, everything=everything), f"revoice {run_id}")
    return {"ok": True, "queued": True}


@app.get("/api/runs/{run_id}/roster")
def get_roster(run_id: str):
    r = _read_json(os.path.join(RUNS_DIR, run_id, "roster.json"))
    if r is None:
        raise HTTPException(404)
    return r


@app.get("/api/runs/{run_id}/market")
def get_market(run_id: str):
    m = _read_json(os.path.join(RUNS_DIR, run_id, "market.json"))
    if m is None:
        return {"tickers": [], "macro": [], "history": []}   # runs created before the market layer
    return m


@app.get("/api/runs/{run_id}/episode")
def get_episode(run_id: str):
    ep = _read_json(os.path.join(RUNS_DIR, run_id, "episode.json"))
    pr = _read_json(os.path.join(RUNS_DIR, run_id, "episode_progress.json"), {})
    if ep is None:
        return {"status": pr.get("status", "none"), "progress": pr, "episode": None}
    return {"status": "ready", "progress": pr, "episode": ep}


@app.post("/api/runs/{run_id}/episode")
def build_episode_endpoint(run_id: str, force: bool = False):
    tl = RUNS.timeline(run_id)
    if tl is None:
        raise HTTPException(404)
    if tl.get("status") not in ("done", "error"):
        raise HTTPException(409, "the simulation is still running; the episode is built when it finishes")
    started = RUNS.build_episode(run_id, force=force)
    return {"ok": True, "started": started}


class ContinueRun(BaseModel):
    twist: str = ""
    quarters: int = 16
    agents_per_round: int | None = None


@app.post("/api/runs/{run_id}/continue")
def continue_run(run_id: str, body: ContinueRun):
    """Season N+1: the parent's winner takes office; one round per quarter for a full term, ending in a re-election."""
    if not LLM.available():
        raise HTTPException(503, "LLM provider not reachable")
    tl = RUNS.timeline(run_id)
    if not tl or tl.get("status") not in ("done", "error"):
        raise HTTPException(409, "the parent run has not finished")
    twist, quarters, apr = body.twist.strip(), max(4, min(16, body.quarters)), body.agents_per_round
    jid = RUNS.start_job("continue", lambda job: RUNS.continue_run(run_id, twist, quarters, apr, job=job))
    return {"job_id": jid}


@app.get("/api/runs/{run_id}/govern")
def get_govern(run_id: str):
    st = _read_json(os.path.join(RUNS_DIR, run_id, "govern.json"))
    if st is None:
        raise HTTPException(404, "not a governing season")
    return st


class PhoneMsg(BaseModel):
    text: str


@app.get("/api/runs/{run_id}/phone")
def phone_summary(run_id: str):
    out = RUNS.phone_summary(run_id)
    if out is None:
        raise HTTPException(404)
    return out


@app.get("/api/runs/{run_id}/phone/log")
def phone_log(run_id: str):
    st = _read_json(os.path.join(RUNS_DIR, run_id, "phone.json")) or {}
    return {"log": st.get("log", []), "pending_events": st.get("pending_events", []), "debate_notes": st.get("debate_notes", [])}


@app.get("/api/runs/{run_id}/phone/{cid}")
def phone_thread(run_id: str, cid: str):
    out = RUNS.phone_thread(run_id, cid)
    if out is None:
        raise HTTPException(404, "no such contact")
    return out


@app.post("/api/runs/{run_id}/phone/{cid}")
def phone_send(run_id: str, cid: str, body: PhoneMsg):
    if not body.text.strip():
        raise HTTPException(400, "empty message")
    out = RUNS.phone_send(run_id, cid, body.text.strip()[:800])
    if out is None:
        raise HTTPException(404, "no such contact")
    return out


@app.post("/api/runs/{run_id}/recast")
def recast_endpoint(run_id: str):
    """Re-cast candidate voices from their bios and re-voice the debate (for runs made before bio casting existed)."""
    out = RUNS.recast_voices(run_id)
    if out is None:
        raise HTTPException(404, "no election in this run")
    return out


@app.get("/api/runs/{run_id}/spend")
def get_spend(run_id: str):
    return SPEND.ledger(run_id)


@app.get("/api/runs/{run_id}/feed/{n}")
def get_feed_round(run_id: str, n: int):
    f = RUNS.feed_round(run_id, n)
    if f is None:
        raise HTTPException(404)
    return f


@app.get("/api/runs/{run_id}/feed")
def get_feed(run_id: str, upto: int = 999, limit: int = 120):
    items, trending, broadcasts, facts, rounds_present, debate, election = [], [], [], None, [], None, None
    for n in range(0, upto + 1):
        f = RUNS.feed_round(run_id, n)
        if f is None:
            break
        rounds_present.append(n)
        items.extend(f["items"])
        trending, broadcasts, facts, election = f.get("trending", []), f.get("broadcasts", []), f.get("facts"), f.get("election")
        if f.get("debate"):
            debate = f["debate"]          # stays available on every round after debate night
    items.sort(key=lambda it: (it["round"], it.get("ts", "")), reverse=True)
    return {"items": items[:limit], "trending": trending, "broadcasts": broadcasts, "facts": facts, "rounds": rounds_present,
            "debate": debate, "election": election, "media_pending": MEDIA.pending(), "spend": SPEND.status(run_id)}


@app.get("/api/runs/{run_id}/state/{code}")
def get_state(run_id: str, code: str):
    code = code.upper()
    run_dir = os.path.join(RUNS_DIR, run_id)
    pm = _read_json(os.path.join(run_dir, "personas.json"))
    tl = RUNS.timeline(run_id)
    if not pm or code not in pm or tl is None:
        raise HTTPException(404, "no such run/state")
    posts = []
    rounds_dir = os.path.join(run_dir, "rounds")
    for fn in sorted(os.listdir(rounds_dir), key=lambda x: int(x.split("_")[1].split(".")[0]) if x.endswith(".json") else -1):
        if fn.endswith(".json"):
            rd = _read_json(os.path.join(rounds_dir, fn), {})
            posts += [{**p, "round": rd["round"]} for p in rd.get("posts", []) if p.get("state") == code]
    history = [{"round": r["round"], **{k: r["states"][code][k] for k in ("unrest", "stage", "stage_name", "federal_cohesion", "neighbor_cohesion", "bloc")}}
               for r in tl["rounds"] if code in r["states"]]
    images = {k: v for k, v in tl.get("images", {}).items() if k.startswith(code + "_") and v != "pending"}
    ballot = (tl.get("election") or {}).get("results", {})
    ballot = ballot["states"].get(code) if ballot else None
    return {"code": code, "persona": pm[code], "posts": posts, "history": history, "images": images, "ballot": ballot,
            "identity": RUNS.roster.get(f"state:{code}"), "latest": tl["rounds"][-1]["states"][code] if tl["rounds"] else None}


@app.post("/api/runs/{run_id}/stop")
def stop_run(run_id: str):
    RUNS.stop_run(run_id)
    return {"ok": True}


@app.get("/api/runs/{run_id}/media/{path:path}")
def get_media(run_id: str, path: str):
    base = os.path.abspath(os.path.join(RUNS_DIR, run_id, "media"))
    p = os.path.abspath(os.path.join(base, path))
    if not p.startswith(base) or not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p)


@app.get("/api/runs/{run_id}/images/{filename}")
def get_image(run_id: str, filename: str):
    for sub in ("media/scenes", "images"):
        p = os.path.join(RUNS_DIR, run_id, sub, os.path.basename(filename))
        if os.path.exists(p):
            return FileResponse(p)
    raise HTTPException(404)


@app.get("/api/avatars/{handle}.png")
def get_avatar(handle: str):
    p = os.path.join(AVATAR_DIR, os.path.basename(handle) + ".png")
    if os.path.exists(p):
        return FileResponse(p)
    ident = next((v for v in RUNS.roster.values() if v["handle"] == handle), None)
    name = ident["display"] if ident else handle
    return Response(content=initials_avatar(name, hash(handle)), media_type="image/png")


@app.get("/api/llm/totals")
def llm_totals():
    return LOGGER.totals()


# ---------- LLM proxy for the sim agents ----------
@app.post("/llm/v1/chat/completions")
@app.post("/llm/{run_id}/v1/chat/completions")
async def proxy_chat(request: Request, run_id: str | None = None):
    body = await request.body()
    t0 = time.time()
    purpose = request.headers.get("x-fl-purpose", "agent").strip().lower()
    purpose = purpose if purpose in ("agent", "bank", "writer", "wire") else "agent"
    try:
        payload = json.loads(body)
    except Exception:
        payload = {}
    local = LLM.is_local("agent")
    prov = "ollama" if local else ("openrouter" if LLM.provider in ("openrouter", "hybrid") else LLM.provider)
    if local:
        payload["model"] = LLM.local_model
        body = json.dumps(payload).encode()
    elif prov == "openrouter":
        payload["usage"] = {"include": True}
        payload.update(model_extras(payload.get("model")))
        body = json.dumps(payload).encode()
    headers = {"Content-Type": "application/json"} if local else LLM.headers()
    try:
        r = await (_local_upstream if local else _upstream).post("/chat/completions", content=body, headers=headers)
    except Exception as e:
        LOGGER.write(source="sim", purpose=purpose, run_id=run_id, model=payload.get("model"), provider=LLM.provider,
                     latency_s=round(time.time() - t0, 2), ok=False, error=str(e)[:200])
        raise HTTPException(502, f"upstream error: {e}")
    usage, cost = {}, 0.0
    if not payload.get("stream"):
        try:
            usage = r.json().get("usage") or {}
            cost = SPEND.chat_cost(payload.get("model"), usage) if prov == "openrouter" else 0.0
        except Exception:
            pass
    SPEND.record(run_id, "agents", cost, provider=prov, model=payload.get("model"),
                 prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
    LOGGER.write(source="sim", purpose=purpose, run_id=run_id, model=payload.get("model"), provider=prov,
                 prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"), cost_usd=round(cost, 6),
                 tools=len(payload.get("tools") or []), latency_s=round(time.time() - t0, 2), ok=r.status_code == 200, status=r.status_code)
    return Response(content=r.content, status_code=r.status_code, media_type=r.headers.get("content-type", "application/json"))


@app.get("/llm/v1/models")
@app.get("/llm/{run_id}/v1/models")
async def proxy_models(run_id: str | None = None):
    if LLM.is_local("agent"):
        r = await _local_upstream.get("/models")
    else:
        r = await _upstream.get("/models", headers=LLM.headers())
    return Response(content=r.content, status_code=r.status_code, media_type="application/json")


# ---------- frontend ----------
FRONTEND = os.path.join(FL_ROOT, "frontend")
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/")
def index():
    return FileResponse(os.path.join(FRONTEND, "index.html"))


# ---------- tutorial narration: one line voiced once by the narrator, cached on disk ----------
import hashlib as _hashlib  # noqa: E402
import re as _re  # noqa: E402
import shutil as _shutil  # noqa: E402
import subprocess as _subprocess  # noqa: E402

TOUR_DIR = os.path.join(RUNS_DIR, "_tour", "audio")
TOUR_VOICE = (CFG.get("tour") or {}).get("narrator_voice") or "4703c250-66e4-4682-a223-0a60acafcfc0"


class TourLine(BaseModel):
    text: str
    voice: str | None = None
    mood: str | None = None     # a Cartesia emotion for the narrator's delivery (excited, confident, curious, ...)


def _audio_seconds(path):
    probe = _shutil.which("ffprobe")
    if probe:
        try:
            out = _subprocess.run([probe, "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                                  capture_output=True, text=True, timeout=20).stdout.strip()
            return round(float(out), 3)
        except Exception:
            pass
    return round(os.path.getsize(path) * 8 / 128000, 3)   # 128 kbps mp3


@app.post("/api/tour/say")
def tour_say(body: TourLine):
    text = " ".join(body.text.split())
    if not text:
        raise HTTPException(400, "empty line")
    vid = body.voice or TOUR_VOICE
    mood = (body.mood or (CFG.get("tour") or {}).get("narrator_mood") or "").strip()
    key = _hashlib.sha1(f"{vid}|{mood}|{text}".encode("utf-8")).hexdigest()[:16]
    os.makedirs(TOUR_DIR, exist_ok=True)
    path = os.path.join(TOUR_DIR, key + ".mp3")
    if not os.path.exists(path):
        audio = TTSP.cartesia.speak(text, role="narrator", kind="tts", run_id="_tour", voice_id=vid, emotion=mood or None) if TTSP.cartesia.available else None
        if not audio:
            raise HTTPException(503, "the narration voice is not available")
        with open(path, "wb") as f:
            f.write(audio)
    return {"url": f"/api/tour/audio/{key}.mp3", "duration": _audio_seconds(path)}


@app.get("/api/tour/audio/{name}")
def tour_audio(name: str):
    if not _re.fullmatch(r"[0-9a-f]{16}\.mp3", name):
        raise HTTPException(404)
    path = os.path.join(TOUR_DIR, name)
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, media_type="audio/mpeg")
