"""
SPEND GUARD: the one module every paid call goes through.

Per run it keeps runs/<id>/spend.json:
  {total_usd, budget_usd, halted, by_kind: {agents, judge, npc, memes_text, images, memes, avatars,
   video, tts, avatar_video, news}, calls: [...last 500]}

Rules:
  * record() always succeeds (we never lose a ledger entry, even over budget)
  * allow(kind) is True for sim-critical kinds (agents, judge) always, and for media kinds only
    while total < budget. The sim keeps running; media stops.
  * cost comes from the provider's usage.cost when available, else tokens x model pricing
    from OpenRouter's public catalog, else 0 (Ollama).
"""
import json
import os
import threading
import time
from datetime import datetime

import httpx

SIM_KINDS = {"agents", "judge", "wire"}
MEDIA_KINDS = {"npc", "memes_text", "images", "memes", "avatars", "video", "tts", "avatar_video", "news"}


class Pricing:
    """Model prices from OpenRouter's public /models (no key needed). Cached in memory."""

    def __init__(self, base_url):
        self.base_url = base_url.rstrip("/")
        self.table = {}
        self.loaded = False

    def load(self):
        if self.loaded:
            return
        try:
            r = httpx.get(f"{self.base_url}/models", timeout=15)
            for m in r.json().get("data", []):
                p = m.get("pricing", {})
                self.table[m["id"]] = {"prompt": float(p.get("prompt") or 0), "completion": float(p.get("completion") or 0),
                                       "image_output": float(p.get("image_output") or 0)}
            self.loaded = True
        except Exception as e:
            print(f"[spend] pricing catalog unavailable: {e}")

    def chat_cost(self, model, prompt_tokens, completion_tokens):
        self.load()
        p = self.table.get(model)
        if not p:
            return 0.0
        return (prompt_tokens or 0) * p["prompt"] + (completion_tokens or 0) * p["completion"]


class SpendGuard:
    def __init__(self, runs_dir, budget_usd, warn_at=0.8, pricing: Pricing | None = None):
        self.runs_dir = runs_dir
        self.default_budget = float(os.environ.get("RUN_BUDGET_USD") or budget_usd)
        self.warn_at = warn_at
        self.pricing = pricing
        self.ledgers = {}
        self.lock = threading.Lock()

    # ---------- ledger ----------
    def _path(self, run_id):
        return os.path.join(self.runs_dir, run_id, "spend.json")

    def ledger(self, run_id):
        with self.lock:
            if run_id not in self.ledgers:
                try:
                    with open(self._path(run_id), encoding="utf-8") as f:
                        self.ledgers[run_id] = json.load(f)
                except Exception:
                    self.ledgers[run_id] = {"total_usd": 0.0, "budget_usd": self.default_budget, "halted": False,
                                            "by_kind": {}, "calls": []}
            return self.ledgers[run_id]

    def _save(self, run_id, led):
        p = self._path(run_id)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + f".{threading.get_ident()}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(led, f, indent=1)
        for i in range(20):
            try:
                os.replace(tmp, p)
                return
            except PermissionError:
                time.sleep(0.05 * (i + 1))

    def record(self, run_id, kind, usd, *, provider, model=None, meta=None, prompt_tokens=None, completion_tokens=None):
        if not run_id:
            run_id = "_unattributed"
        usd = float(usd or 0)
        led = self.ledger(run_id)
        with self.lock:
            led["total_usd"] = round(led["total_usd"] + usd, 6)
            led["by_kind"][kind] = round(led["by_kind"].get(kind, 0.0) + usd, 6)
            led["calls"].append({"ts": datetime.now().isoformat(timespec="seconds"), "kind": kind, "usd": round(usd, 6),
                                 "provider": provider, "model": model, "prompt_tokens": prompt_tokens,
                                 "completion_tokens": completion_tokens, "meta": meta})
            led["calls"] = led["calls"][-500:]
            if led["total_usd"] >= led["budget_usd"] and not led["halted"]:
                led["halted"] = True
                print(f"[spend] run {run_id}: budget ${led['budget_usd']:.2f} reached, media generation halted")
            self._save(run_id, led)
        return led["total_usd"]

    def allow(self, run_id, kind):
        """Sim-critical kinds always run. Media kinds stop once the budget is hit."""
        if kind in SIM_KINDS:
            return True
        led = self.ledger(run_id)
        return led["total_usd"] < led["budget_usd"]

    def status(self, run_id):
        led = self.ledger(run_id)
        return {"total_usd": led["total_usd"], "budget_usd": led["budget_usd"], "halted": led["halted"],
                "by_kind": led["by_kind"], "fraction": (led["total_usd"] / led["budget_usd"]) if led["budget_usd"] else 0,
                "calls": len(led["calls"])}

    def chat_cost(self, model, usage):
        """Prefer the provider's exact cost (OpenRouter returns usage.cost when usage.include=true)."""
        if usage and usage.get("cost") is not None:
            return float(usage["cost"])
        if self.pricing and model:
            return self.pricing.chat_cost(model, (usage or {}).get("prompt_tokens"), (usage or {}).get("completion_tokens"))
        return 0.0
