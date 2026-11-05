"""
Provider layer. Every paid call goes through SpendGuard.

  TextLLM        chat completions: OpenRouter (if OPENROUTER_API_KEY) else Ollama. JSON mode helper.
  ImageGen       text -> PNG bytes: OpenRouter Images API (Flux 3 Klein) | ComfyUI | Pillow placeholder
  TTS            text -> mp3 bytes: Cartesia (needs CARTESIA_API_KEY) else None
  VideoProvider  image + prompt -> mp4 bytes: fal (needs FAL_KEY) else None
  AvatarProvider audio + photo -> mp4 bytes: OmniHuman-style endpoint (needs OMNIHUMAN_ENDPOINT/_KEY) else None

Nothing here raises for a missing key; it reports `available` False and returns None so the
pipeline falls back (placeholder image, browser speech, no video).
"""
import base64
import io
import json
import os
import random
import time

import httpx

ENGLISH_ONLY = "Always respond in English only."


def load_dotenv_file(path):
    """Tiny KEY=VALUE loader (no dependency). Existing environment wins."""
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and k not in os.environ:
                    os.environ[k] = v
    except FileNotFoundError:
        pass


def parse_json_loose(text):
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:
        pass
    for o, c in (("{", "}"), ("[", "]")):
        i, j = text.find(o), text.rfind(c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    raise ValueError("no JSON in model output")


def model_extras(model):
    """Per-model request extras. DeepSeek V4 'thinks' by default, which makes a 150-token judge call cost 15x and take 7 s;
    with reasoning off it answers in under a second at about half of Gemini Flash's price."""
    m = (model or "").lower()
    if "deepseek" in m:
        return {"reasoning": {"enabled": False}}
    return {}


_PROBES = {}   # (local_base, model) -> (ok, checked_at)


class TextLLM:
    def __init__(self, cfg, spend, logger):
        self.cfg = cfg
        self.spend = spend
        self.logger = logger
        self.key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        prov = (os.environ.get("LLM_PROVIDER") or cfg.get("provider", "auto")).lower()
        self.local_base = cfg.get("ollama_base_url", "http://127.0.0.1:11434/v1").rstrip("/")
        self.local_model = os.environ.get("LOCAL_MODEL") or cfg.get("ollama_model", "qwen2.5:14b")
        self.local_ok = self._probe_local(fresh=True) if prov in ("auto", "hybrid", "ollama") else False
        if prov == "auto":
            prov = "hybrid" if (self.key and self.local_ok) else "openrouter" if self.key else "ollama"
        if prov == "hybrid" and not (self.key and self.local_ok):
            prov = "openrouter" if self.key else "ollama"
        self.provider = prov
        self.local_kinds = set(cfg.get("local_kinds") or ["agent", "judge", "npc", "memes_text", "news"])
        self.cloud_kinds = set(cfg.get("cloud_kinds") or [])
        if self.provider in ("openrouter", "hybrid"):
            self.base_url = cfg["openrouter_base_url"].rstrip("/")
            self.agent_model = os.environ.get("AGENT_MODEL") or cfg["agent_model"]
            self.judge_model = os.environ.get("JUDGE_MODEL") or cfg["judge_model"]
        else:
            self.base_url = self.local_base
            self.agent_model = self.judge_model = self.local_model
        self.cloud_model = os.environ.get("JUDGE_MODEL") or cfg.get("judge_model") or self.judge_model   # what a cloud-routed call uses
        if self.provider == "hybrid":
            # the sim agents and the judge run locally when their kinds are routed local
            if self.is_local("agent"):
                self.agent_model = self.local_model
            if self.is_local("judge"):
                self.judge_model = self.local_model
        self.timeout = cfg.get("model_timeout_s", 600)

    def _probe_local(self, fresh=False):
        """Is Ollama up with the model? Cached 15 s: on Windows a refused connection takes ~2 s to fail, and the page asks often."""
        key = (self.local_base, self.local_model)
        hit = _PROBES.get(key)
        if hit and not fresh and time.time() - hit[1] < 15:
            return hit[0]
        ok = self._probe_local_now()
        _PROBES[key] = (ok, time.time())
        return ok

    def _probe_local_now(self):
        try:
            r = httpx.get(f"{self.local_base}/models", timeout=4)
            ok = r.status_code == 200
            if ok:
                names = [m.get("id") for m in r.json().get("data", [])]
                if self.local_model not in names and not any(n.startswith(self.local_model.split(":")[0]) for n in names):
                    print(f"[providers] Ollama is up but {self.local_model} is not pulled (have: {names[:6]}); run: ollama pull {self.local_model}")
                    return False
            return ok
        except Exception:
            return False

    def is_local(self, kind):
        """Hybrid routing: does this call kind run on the local model?"""
        if self.provider == "ollama":
            return True
        if self.provider != "hybrid" or not self.local_ok:
            return False
        if kind in self.cloud_kinds:
            return False
        return "all" in self.local_kinds or kind in self.local_kinds

    def route(self, kind):
        """(base_url, headers, model, provider_name) for a call kind."""
        if self.is_local(kind):
            return self.local_base, {"Content-Type": "application/json"}, self.local_model, "ollama"
        return self.base_url, self.headers(), (self.cloud_model if self.provider == "hybrid" else None), self.provider if self.provider != "hybrid" else "openrouter"

    def headers(self):
        h = {"Content-Type": "application/json"}
        if self.provider in ("openrouter", "hybrid"):
            h["Authorization"] = f"Bearer {self.key}"
            h["HTTP-Referer"] = "http://localhost/faultlines"
            h["X-Title"] = "FAULT LINES"
        return h

    def available(self):
        if self.provider == "ollama":
            return self.local_ok or self._probe_local()
        # OpenRouter: a saved key is all it takes. The page asks this on every refresh, so no network call here;
        # the Settings panel's Test button checks the key itself.
        return bool(self.key)

    def chat(self, messages, *, kind, run_id=None, model=None, temperature=0.3, json_mode=True, max_tokens=1500, _retry_cloud=True):
        """Returns content string. Logs tokens + cost. Raises on transport error. Hybrid: the kind picks local or cloud;
        a failed local call falls back to the cloud model once."""
        base, headers, forced, prov = self.route(kind)
        model = forced or model or self.judge_model
        if messages and messages[0].get("role") == "system" and ENGLISH_ONLY not in messages[0]["content"]:
            messages = [{"role": "system", "content": messages[0]["content"].rstrip() + " " + ENGLISH_ONLY}] + messages[1:]
        body = {"model": model, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        if prov == "openrouter":
            body["usage"] = {"include": True}
            body.update(model_extras(model))
        t0 = time.time()
        try:
            r = httpx.post(f"{base}/chat/completions", json=body, headers=headers, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage") or {}
            cost = self.spend.chat_cost(model, usage) if prov == "openrouter" else 0.0
            self.spend.record(run_id, kind, cost, provider=prov, model=model,
                              prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"))
            self.logger.write(source="backend", purpose=kind, run_id=run_id, model=model, provider=prov,
                              prompt_tokens=usage.get("prompt_tokens"), completion_tokens=usage.get("completion_tokens"),
                              cost_usd=round(cost, 6), latency_s=round(time.time() - t0, 2), ok=True)
            return data["choices"][0]["message"]["content"]
        except Exception as e:
            self.logger.write(source="backend", purpose=kind, run_id=run_id, model=model, provider=prov,
                              latency_s=round(time.time() - t0, 2), ok=False, error=str(e)[:300])
            if prov == "ollama" and self.provider == "hybrid" and _retry_cloud:
                print(f"[llm] local {kind} call failed ({str(e)[:80]}); retrying on the cloud model")
                saved = self.local_kinds
                self.local_kinds = saved - {kind, "all"}
                try:
                    return self.chat(messages, kind=kind, run_id=run_id, model=None, temperature=temperature, json_mode=json_mode, max_tokens=max_tokens, _retry_cloud=False)
                finally:
                    self.local_kinds = saved
            raise

    def chat_json(self, system, user, **kw):
        return parse_json_loose(self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], **kw))


class ImageGen:
    def __init__(self, cfg, spend, logger, root):
        self.cfg = cfg
        self.spend = spend
        self.logger = logger
        self.root = root
        self.key = os.environ.get("OPENROUTER_API_KEY", "").strip()
        self.model = os.environ.get("IMAGE_MODEL") or cfg["openrouter_model"]
        self.quality = os.environ.get("IMAGE_QUALITY") or cfg.get("quality")
        prov = cfg.get("provider", "auto")
        if prov == "auto":
            if self.key:
                prov = "openrouter"
            else:
                prov = "comfyui" if self._comfy_up() else "placeholder"
        self.provider = prov
        self.max_calls = int(cfg.get("max_calls_per_run", 60))
        self.calls = {}  # run_id -> paid calls

    def _comfy_up(self):
        try:
            return httpx.get(self.cfg["comfyui_url"].rstrip("/") + "/system_stats", timeout=2).status_code == 200
        except Exception:
            return False

    @property
    def paid(self):
        return self.provider == "openrouter"

    def generate(self, prompt, *, kind, run_id, aspect="1:1", seed=None):
        """Returns PNG bytes, or None when no real provider is available / budget halted."""
        if self.provider == "openrouter":
            if not self.spend.allow(run_id, kind):
                self.logger.write(source="backend", purpose=kind, run_id=run_id, model=self.model, ok=False, error="budget halted")
                return None
            if kind != "avatars":  # portraits are one-time and cached forever across runs; scenes/memes count against the cap
                if self.calls.get(run_id, 0) >= self.max_calls:
                    self.logger.write(source="backend", purpose=kind, run_id=run_id, model=self.model, ok=False,
                                      error=f"max_image_calls ({self.max_calls}) reached")
                    return None
                self.calls[run_id] = self.calls.get(run_id, 0) + 1
            return self._openrouter(prompt, kind, run_id, aspect, seed)
        if self.provider == "comfyui":
            return self._comfyui(prompt, kind, run_id)
        return None

    def _openrouter(self, prompt, kind, run_id, aspect, seed):
        t0 = time.time()
        body = {"model": self.model, "prompt": prompt, "aspect_ratio": aspect}
        if self.quality and self.model.startswith("openai/"):
            body["quality"] = self.quality      # OpenAI's picture price depends mostly on this
        if self.model.startswith("black-forest-labs/"):
            body["resolution"] = "1K"
            if seed is not None:
                # provider-specific parameters go under provider.options (a top-level seed is a 400 on /images)
                body["provider"] = {"options": {"black-forest-labs": {"seed": int(seed)}}}
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json",
                   "HTTP-Referer": "http://localhost/faultlines", "X-Title": "FAULT LINES"}
        try:
            r = httpx.post("https://openrouter.ai/api/v1/images", json=body, headers=headers, timeout=180)
            if r.status_code != 200:
                raise RuntimeError(f"{r.status_code}: {r.text[:200]}")
            d = r.json()
            img = d["data"][0]
            png = base64.b64decode(img["b64_json"]) if img.get("b64_json") else httpx.get(img["url"], timeout=60).content
            cost = float((d.get("usage") or {}).get("cost") or 0)
            self.spend.record(run_id, kind, cost, provider="openrouter", model=self.model, meta={"aspect": aspect})
            self.logger.write(source="backend", purpose=kind, run_id=run_id, model=self.model, provider="openrouter",
                              cost_usd=round(cost, 6), latency_s=round(time.time() - t0, 2), ok=True)
            return png
        except Exception as e:
            self.logger.write(source="backend", purpose=kind, run_id=run_id, model=self.model, provider="openrouter",
                              latency_s=round(time.time() - t0, 2), ok=False, error=str(e)[:300])
            return None

    def _comfyui(self, prompt, kind, run_id):
        import uuid
        base = self.cfg["comfyui_url"].rstrip("/")
        try:
            with open(os.path.join(self.root, self.cfg["comfyui_workflow"]), encoding="utf-8") as f:
                wf = f.read()
            wf = wf.replace("%PROMPT%", json.dumps(prompt)[1:-1]).replace("%SEED%", str(random.randint(1, 2**31)))
            r = httpx.post(f"{base}/prompt", json={"prompt": json.loads(wf), "client_id": uuid.uuid4().hex}, timeout=30)
            r.raise_for_status()
            pid = r.json()["prompt_id"]
            for _ in range(600):
                time.sleep(1)
                h = httpx.get(f"{base}/history/{pid}", timeout=10).json()
                if pid in h:
                    for node in h[pid].get("outputs", {}).values():
                        for im in node.get("images", []):
                            v = httpx.get(f"{base}/view", params={"filename": im["filename"], "subfolder": im.get("subfolder", ""),
                                                                   "type": im.get("type", "output")}, timeout=60)
                            self.spend.record(run_id, kind, 0.0, provider="comfyui", model="local")
                            return v.content
                    return None
        except Exception as e:
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="comfyui", ok=False, error=str(e)[:200])
        return None


STATE_NAMES_FOR_SPEECH = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California", "CO": "Colorado", "CT": "Connecticut",
    "DE": "Delaware", "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland", "MA": "Massachusetts", "MI": "Michigan",
    "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire",
    "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina", "SD": "South Dakota", "TN": "Tennessee",
    "TX": "Texas", "UT": "Utah", "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "D.C.", "US": "U.S.", "USA": "U.S.A.", "AI": "A.I.", "FLN": "F.L.N.", "CEO": "C.E.O.", "GDP": "G.D.P.",
}


def speakable(text):
    """What the TTS engine reads. Cartesia spells out ALL-CAPS words, so anything shouted on screen is
    normalized here: state codes become names, known acronyms get dots, other all-caps words go to
    normal case, hashtags lose the '#' and split CamelCase, handles lose the '@'."""
    import re
    letters = [c for c in text if c.isalpha()]
    shouting = letters and sum(c.isupper() for c in letters) / len(letters) > 0.6
    ambiguous = {"IN", "OR", "ME", "HI", "OK", "ID", "MA", "MO", "AL", "CO", "DE", "MS", "US"}

    def fix_word(m):
        w = m.group(0)
        core = w.strip(".,!?;:'\"")
        if shouting:
            # an all-caps rant: "FORCE US TO RETRAIN" means "us", not "U.S."; normalize everything to sentence case
            return w.replace(core, core.lower()) if core.isalpha() else w
        if core in STATE_NAMES_FOR_SPEECH and core not in ambiguous:
            return w.replace(core, STATE_NAMES_FOR_SPEECH[core])
        if core in ("US", "USA") and core in STATE_NAMES_FOR_SPEECH:
            return w.replace(core, STATE_NAMES_FOR_SPEECH[core])
        if len(core) >= 2 and core.isupper() and core.isalpha() and core not in ambiguous:
            return w.replace(core, core.capitalize())
        return w
    def split_camel(s):
        s = re.sub(r"([a-z0-9])([A-Z])", lambda n: n.group(1) + " " + n.group(2), s)
        return re.sub(r"([A-Z]+)([A-Z][a-z])", lambda n: n.group(1) + " " + n.group(2), s)
    t = re.sub(r"#([A-Za-z0-9_]+)", lambda m: split_camel(m.group(1)).replace("_", " "), text)
    t = re.sub(r"@([A-Za-z0-9_]+)", lambda m: m.group(1).replace("_", " "), t)
    t = re.sub(r"[A-Za-z][A-Za-z.]*[.,!?;:'\"]*", fix_word, t)
    t = re.sub(r"\s+", " ", t).strip()
    if shouting:
        # sentence case: capital after sentence breaks, and a capital I
        t = re.sub(r"(^|[.!?]\s+)([a-z])", lambda m: m.group(1) + m.group(2).upper(), t)
        t = re.sub(r"\bi\b", "I", t)
    return t[:1].upper() + t[1:] if t else t


class TTS:
    def __init__(self, cfg, spend, logger):
        self.cfg = cfg
        self.spend = spend
        self.logger = logger
        self.key = os.environ.get("CARTESIA_API_KEY", "").strip()
        self.provider = "cartesia" if (self.key and cfg.get("tts_provider", "auto") in ("cartesia", "auto", "fish")) else "none"
        self.model = os.environ.get("CARTESIA_MODEL") or cfg.get("tts_model", "sonic-3")
        # Public Cartesia library voices (see reporters.VOICES); override any role with CARTESIA_VOICE_<ROLE>
        from reporters import VOICES
        self.voices = {role: os.environ.get(f"CARTESIA_VOICE_{role.upper()}", vid) for role, vid in VOICES.items()}

    @property
    def available(self):
        return self.provider == "cartesia"

    def speak(self, text, *, role, kind, run_id, voice_id=None, emotion=None):
        """Returns mp3 bytes or None. voice_id overrides the role's voice; emotion is a sonic-3 generation_config cue. Cartesia bills per character (~$0.00003/char)."""
        if not self.available or not self.spend.allow(run_id, kind):
            return None
        t0 = time.time()
        text = speakable(text)
        body = {"model_id": self.model, "transcript": text[:4000],
                "voice": {"mode": "id", "id": voice_id or self.voices.get(role, self.voices["anchor_a"])},
                "output_format": {"container": "mp3", "bit_rate": 128000, "sample_rate": 44100}, "language": "en"}
        emo = cartesia_emotion(emotion)
        if emo and self.model.startswith("sonic-3"):
            body["generation_config"] = {"emotion": emo}
        headers = {"Authorization": f"Bearer {self.key}", "Cartesia-Version": "2025-04-16", "Content-Type": "application/json"}
        try:
            r = httpx.post("https://api.cartesia.ai/tts/bytes", json=body, headers=headers, timeout=120)
            r.raise_for_status()
            est = len(text) * float(os.environ.get("CARTESIA_USD_PER_CHAR", "0.00003"))
            self.spend.record(run_id, kind, est, provider="cartesia", model=body["model_id"], meta={"chars": len(text), "estimated": True})
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="cartesia", cost_usd=round(est, 6),
                              latency_s=round(time.time() - t0, 2), ok=True, voice=role, transcript=text[:90])
            return r.content
        except Exception as e:
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="cartesia", ok=False, error=str(e)[:300])
            return None


# Cartesia sonic-3 accepts only these emotion names (docs: volume-speed-emotion); free text like "dry, measured" is
# unsupported and can make a good voice sound robotic. The most reliable: neutral, calm, angry, content, sad, scared.
CARTESIA_EMOTIONS = {"neutral", "happy", "excited", "enthusiastic", "elated", "euphoric", "triumphant", "amazed", "surprised",
                     "flirtatious", "curious", "content", "peaceful", "serene", "calm", "grateful", "affectionate", "trust",
                     "sympathetic", "anticipation", "mysterious", "angry", "mad", "outraged", "frustrated", "agitated", "threatened",
                     "disgusted", "contempt", "envious", "sarcastic", "ironic", "sad", "dejected", "melancholic", "disappointed", "hurt",
                     "guilty", "bored", "tired", "rejected", "nostalgic", "wistful", "apologetic", "hesitant", "insecure", "confused",
                     "resigned", "anxious", "panicked", "alarmed", "scared", "proud", "confident", "distant", "skeptical",
                     "contemplative", "determined"}
_EMOTION_WORDS = [  # free-text cue words -> the nearest supported emotion (first match wins)
    (("furious", "shout", "rage", "angry", "yell"), "angry"), (("outrag",), "outraged"), (("exasperat", "frustrat"), "frustrated"),
    (("sarcas", "mock", "snide"), "sarcastic"), (("amused", "wry", "ironic"), "content"),
    (("urgent", "alarm", "breathless"), "alarmed"), (("panic",), "panicked"), (("shaken", "scared", "afraid", "fear"), "scared"),
    (("tense", "nervous", "anxious", "worried"), "anxious"), (("sad", "grief", "somber", "sombre", "mourn"), "sad"),
    (("excit", "energetic", "thrill", "rising", "passionate", "fired"), "excited"), (("upbeat", "enthusias", "cheer"), "enthusiastic"),
    (("proud", "triumph", "victor"), "proud"), (("determined", "resolute", "defiant"), "determined"),
    (("firm", "authorit", "confident", "assured", "commanding"), "confident"), (("warm", "kind", "gentle", "tender"), "affectionate"),
    (("cold", "clipped", "distant", "detached"), "distant"), (("skeptic", "doubt"), "skeptical"), (("curious",), "curious"),
    (("bored", "dismissive"), "calm"), (("calm", "measured", "steady", "composed", "dry", "even", "synthetic", "neutral"), "calm"),
]


def cartesia_emotion(cue):
    """A free-text acting cue ('passionate, rising') -> one emotion Cartesia supports, or None to let the voice be."""
    c = (cue or "").strip().strip("[]").lower()
    if not c:
        return None
    for word in [w.strip() for w in c.replace(";", ",").split(",")]:
        if word in CARTESIA_EMOTIONS:
            return word
    for keys, emo in _EMOTION_WORDS:
        if any(k in c for k in keys):
            return emo
    return None


class FishTTS:
    """Fish Audio S2 (s2.1-pro): inline emotion cues like '[angry]' or '[calm, dry]' at the start of the text.
    Voice ids come from reporters.FISH_VOICES (generic library voices, never clones of real people);
    override any role with FISH_VOICE_<ROLE>. Returns None on any failure (e.g. 402 no API credit) so the
    router can fall back to Cartesia."""

    def __init__(self, cfg, spend, logger):
        self.cfg = cfg
        self.spend = spend
        self.logger = logger
        self.key = os.environ.get("FISH_AUDIO_API_KEY", "").strip()
        self.provider = "fish" if self.key else "none"
        self.model = os.environ.get("FISH_MODEL") or cfg.get("fish_model", "s2.1-pro")
        from reporters import FISH_VOICES
        self.voices = {role: os.environ.get(f"FISH_VOICE_{role.upper()}", vid) for role, vid in FISH_VOICES.items()}
        self.usd_per_char = float(os.environ.get("FISH_USD_PER_CHAR", "0.00002"))

    @property
    def available(self):
        return self.provider == "fish"

    def speak(self, text, *, role, kind, run_id, voice_id=None, emotion=None):
        if not self.available or not self.spend.allow(run_id, kind):
            return None
        t0 = time.time()
        text = speakable(text)
        cue = (emotion or "").strip().strip("[]")
        if cue:
            text = f"[{cue[:60]}] {text}"
        vid = voice_id or self.voices.get(role) or self.voices.get("anchor_a")
        body = {"text": text[:4000], "format": "mp3", "mp3_bitrate": 128, "latency": "normal", "temperature": 0.7, "top_p": 0.7}
        if vid:
            body["reference_id"] = vid
        headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json", "model": self.model}
        try:
            r = httpx.post("https://api.fish.audio/v1/tts", json=body, headers=headers, timeout=120)
            if r.status_code != 200:
                self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="fish", ok=False,
                                  error=f"{r.status_code}: {r.text[:160]}")
                self.last_error = (time.time(), r.status_code, r.text[:160])
                return None
            self.last_error = None
            est = len(text) * self.usd_per_char
            self.spend.record(run_id, kind, est, provider="fish", model=self.model, meta={"chars": len(text), "estimated": True})
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="fish", model=self.model, cost_usd=round(est, 6),
                              latency_s=round(time.time() - t0, 2), ok=True, voice=vid, emotion=cue, transcript=text[:90])
            return r.content
        except Exception as e:
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="fish", ok=False, error=str(e)[:300])
            return None


class TTSRouter:
    """One speak() for the engine: Fish first (emotions), Cartesia as fallback, browser voice if neither."""

    def __init__(self, cfg, spend, logger):
        self.fish = FishTTS(cfg, spend, logger)
        self.cartesia = TTS(cfg, spend, logger)
        pref = (os.environ.get("TTS_PROVIDER") or cfg.get("tts_provider", "auto")).lower()
        if pref == "auto":
            pref = "fish" if self.fish.available else ("cartesia" if self.cartesia.available else "none")
        self.pref = pref
        self.model = self.fish.model if pref == "fish" else self.cartesia.model
        self.voices = self.fish.voices if pref == "fish" else self.cartesia.voices
        self.fish_failed = 0
        self.fish.last_error = None

    # a Fish voice that has a chosen Cartesia stand-in for when Fish is down (out of credit, outage)
    FISH_TWINS = {"80e34d5e0b2b4577a486f3a77e357261": "47c38ca4-5f35-497b-b1a3-415245fb35e1"}   # AI candidates: New History -> Daniel

    def warning(self):
        """A plain-words warning for the live bar when voices are not coming from the provider the user pays for."""
        err = getattr(self.fish, "last_error", None)
        if self.pref == "fish" and err:
            if err[1] == 402:
                return "Fish Audio is out of API credit: voices are falling back to Cartesia until you top it up"
            return f"Fish Audio is failing ({err[1]}): voices are falling back to Cartesia"
        return None

    @property
    def provider(self):
        if self.pref == "fish" and self.fish.available:
            return "fish" + ("+cartesia" if self.cartesia.available else "")
        if self.pref in ("cartesia", "fish") and self.cartesia.available:
            return "cartesia"
        return "none"

    @property
    def available(self):
        return self.provider != "none"

    def speak(self, text, *, role, kind, run_id, voice_id=None, emotion=None):
        fish_voice = voice_id if (voice_id and len(voice_id) == 32) else self.fish.voices.get(role)
        if self.fish_failed >= 3 and time.time() - getattr(self, "fish_failed_at", 0) > 600:
            self.fish_failed = 0   # try Fish again every ten minutes (credit may have been topped up)
        if self.pref == "fish" and self.fish.available and self.fish_failed < 3 and fish_voice and not (voice_id and len(voice_id) == 36):
            # a Fish voice id (32 hex) is not a Cartesia id (36 uuid): each explicit id goes to the provider it belongs to
            out = self.fish.speak(text, role=role, kind=kind, run_id=run_id, voice_id=fish_voice, emotion=emotion)
            if out:
                self.fish_failed = 0
                return out
            self.fish_failed += 1
            self.fish_failed_at = time.time()
            if self.fish_failed == 3:
                print("[tts] Fish failed 3 times in a row (no API credit?); using Cartesia for the rest of this session")
        if self.cartesia.available:
            vid = voice_id if (voice_id and len(voice_id) == 36) else self.FISH_TWINS.get(voice_id)
            return self.cartesia.speak(text, role=role, kind=kind, run_id=run_id, voice_id=vid, emotion=emotion)
        return None


class VideoProvider:
    """image + prompt -> short mp4. Default 'none'. 'fal' uses fal.ai's queue API with FAL_KEY."""

    def __init__(self, cfg, spend, logger):
        self.cfg = cfg
        self.spend = spend
        self.logger = logger
        self.provider = os.environ.get("VIDEO_PROVIDER") or cfg.get("provider", "none")
        self.key = os.environ.get("FAL_KEY", "").strip()
        if self.provider == "fal" and not self.key:
            self.provider = "none"
        self.model = os.environ.get("VIDEO_MODEL") or cfg.get("fal_model")
        self.max_seconds = int(cfg.get("max_seconds", 6))

    @property
    def available(self):
        return self.provider != "none"

    def animate(self, png_bytes, prompt, *, kind, run_id):
        if not self.available or not self.spend.allow(run_id, kind):
            return None
        t0 = time.time()
        try:
            data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode()
            headers = {"Authorization": f"Key {self.key}", "Content-Type": "application/json"}
            r = httpx.post(f"https://queue.fal.run/{self.model}", json={"image_url": data_url, "prompt": prompt,
                           "num_frames": min(self.max_seconds, 8) * 16}, headers=headers, timeout=60)
            r.raise_for_status()
            status_url = r.json().get("status_url") or r.json().get("response_url")
            resp_url = r.json().get("response_url")
            for _ in range(240):
                time.sleep(3)
                s = httpx.get(status_url, headers=headers, timeout=30).json()
                if s.get("status") == "COMPLETED":
                    out = httpx.get(resp_url, headers=headers, timeout=60).json()
                    url = (out.get("video") or {}).get("url")
                    mp4 = httpx.get(url, timeout=120).content
                    est = float(os.environ.get("VIDEO_USD_PER_CLIP", "0.10"))
                    self.spend.record(run_id, kind, est, provider="fal", model=self.model, meta={"estimated": True})
                    self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="fal", cost_usd=est,
                                      latency_s=round(time.time() - t0, 2), ok=True)
                    return mp4
                if s.get("status") in ("FAILED", "ERROR"):
                    break
        except Exception as e:
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider="fal", ok=False, error=str(e)[:300])
        return None


class AvatarProvider:
    """audio + photo -> talking-head mp4. Generic OmniHuman-style HTTP endpoint behind env vars."""

    def __init__(self, cfg, spend, logger):
        self.cfg = cfg
        self.spend = spend
        self.logger = logger
        self.provider = os.environ.get("AVATAR_PROVIDER") or cfg.get("avatar_provider", "none")
        self.endpoint = os.environ.get("OMNIHUMAN_ENDPOINT", "").strip()
        self.key = os.environ.get("OMNIHUMAN_API_KEY", "").strip()
        if self.provider != "none" and not (self.endpoint and self.key):
            self.provider = "none"

    @property
    def available(self):
        return self.provider != "none"

    def animate(self, photo_png, audio_mp3, *, kind, run_id):
        if not self.available or not self.spend.allow(run_id, kind):
            return None
        t0 = time.time()
        try:
            body = {"image_base64": base64.b64encode(photo_png).decode(), "audio_base64": base64.b64encode(audio_mp3).decode()}
            r = httpx.post(self.endpoint, json=body, headers={"Authorization": f"Bearer {self.key}"}, timeout=600)
            r.raise_for_status()
            d = r.json()
            mp4 = base64.b64decode(d["video_base64"]) if d.get("video_base64") else httpx.get(d["video_url"], timeout=120).content
            est = float(os.environ.get("AVATAR_USD_PER_CLIP", "0.25"))
            self.spend.record(run_id, kind, est, provider=self.provider, meta={"estimated": True})
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider=self.provider, cost_usd=est,
                              latency_s=round(time.time() - t0, 2), ok=True)
            return mp4
        except Exception as e:
            self.logger.write(source="backend", purpose=kind, run_id=run_id, provider=self.provider, ok=False, error=str(e)[:300])
            return None


# ---------- Pillow helpers (placeholders + caption stamping) ----------
def _font(size, bold=True):
    from PIL import ImageFont
    for name in ("impact.ttf", "arialbd.ttf", "arial.ttf"):
        p = os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts", name)
        if os.path.exists(p):
            return ImageFont.truetype(p, size)
    return ImageFont.load_default()


def placeholder_png(text, size=(1024, 1024), seed=0, kind="scene"):
    """Flat, obviously-synthetic image so the pipeline runs without a paid provider."""
    from PIL import Image, ImageDraw
    rng = random.Random(seed)
    base = {"scene": (40, 50, 70), "meme": (240, 235, 220), "avatar": (rng.randint(40, 200), rng.randint(40, 200), rng.randint(40, 200))}.get(kind, (60, 60, 60))
    im = Image.new("RGB", size, base)
    d = ImageDraw.Draw(im)
    for _ in range(18):
        x, y = rng.randint(0, size[0]), rng.randint(0, size[1])
        r = rng.randint(20, size[0] // 4)
        c = tuple(max(0, min(255, b + rng.randint(-40, 40))) for b in base)
        d.ellipse([x - r, y - r, x + r, y + r], fill=c)
    f = _font(max(18, size[0] // 22))
    fill = (255, 255, 255) if kind != "meme" else (40, 40, 40)
    lines, line = [], ""
    for w in text.split():
        if d.textlength(line + " " + w, font=f) > size[0] * 0.85:
            lines.append(line.strip()); line = w
        else:
            line += " " + w
    lines.append(line.strip())
    y = size[1] // 2 - len(lines) * (f.size + 4) // 2
    for ln in lines[:8]:
        d.text(((size[0] - d.textlength(ln, font=f)) / 2, y), ln, font=f, fill=fill)
        y += f.size + 4
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()


def watermark_png(png_bytes, text="SIMULATED"):
    """A small label in the corner of every generated crime picture: this is a simulation."""
    import io
    from PIL import Image, ImageDraw
    try:
        im = Image.open(io.BytesIO(png_bytes)).convert("RGBA")
        w, h = im.size
        font = _font(max(14, h // 30))
        d = ImageDraw.Draw(im)
        tw = d.textlength(text, font=font)
        pad = max(6, h // 90)
        box = [w - tw - pad * 3, h - font.size - pad * 3, w - pad, h - pad]
        d.rectangle(box, fill=(0, 0, 0, 150))
        d.text((box[0] + pad, box[1] + pad // 2), text, fill=(255, 255, 255, 230), font=font)
        out = io.BytesIO()
        im.convert("RGB").save(out, format="PNG")
        return out.getvalue()
    except Exception:
        return png_bytes


def stamp_caption(png_bytes, top, bottom):
    """Classic meme typography: Impact, white fill, black stroke, top and bottom. Text never goes to the image model."""
    from PIL import Image, ImageDraw
    im = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    W, H = im.size
    d = ImageDraw.Draw(im)
    size = max(24, W // 11)
    f = _font(size)

    def wrap(t):
        words, lines, line = t.upper().split(), [], ""
        for w in words:
            if d.textlength((line + " " + w).strip(), font=f) > W * 0.92 and line:
                lines.append(line); line = w
            else:
                line = (line + " " + w).strip()
        if line:
            lines.append(line)
        return lines[:3]

    def draw_block(lines, y0, from_bottom=False):
        y = y0
        if from_bottom:
            y = H - len(lines) * (size + 6) - int(H * 0.03)
        for ln in lines:
            x = (W - d.textlength(ln, font=f)) / 2
            d.text((x, y), ln, font=f, fill=(255, 255, 255), stroke_width=max(2, size // 12), stroke_fill=(0, 0, 0))
            y += size + 6

    if top:
        draw_block(wrap(top), int(H * 0.03))
    if bottom:
        draw_block(wrap(bottom), 0, from_bottom=True)
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()


def initials_avatar(name, seed, size=256):
    from PIL import Image, ImageDraw
    rng = random.Random(seed)
    bg = (rng.randint(30, 160), rng.randint(30, 160), rng.randint(30, 160))
    im = Image.new("RGB", (size, size), bg)
    d = ImageDraw.Draw(im)
    d.ellipse([size * 0.1, size * 0.1, size * 0.9, size * 0.9], fill=tuple(min(255, c + 50) for c in bg))
    ini = "".join(w[0] for w in name.split()[:2]).upper() or "?"
    f = _font(size // 3)
    d.text(((size - d.textlength(ini, font=f)) / 2, size * 0.3), ini, font=f, fill=(255, 255, 255))
    buf = io.BytesIO(); im.save(buf, "PNG"); return buf.getvalue()
