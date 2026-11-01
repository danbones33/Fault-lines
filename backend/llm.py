"""
Ollama (OpenAI-compatible) client + the single place every LLM call gets logged.

Two paths write to the same JSONL log:
  * chat()             : backend's own calls (judge, bloc names)
  * log_proxy_call()   : the /llm/v1/chat/completions proxy the sim agents go through
"""
import json
import os
import threading
import time
from datetime import datetime

import httpx

_lock = threading.Lock()


class LLMLogger:
    def __init__(self, path):
        self.path = path
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)

    def write(self, **entry):
        entry.setdefault("ts", datetime.now().isoformat(timespec="seconds"))
        line = json.dumps(entry, ensure_ascii=False)
        with _lock:
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(line + "\n")

    def totals(self):
        prompt = completion = calls = 0
        try:
            with open(self.path, encoding="utf-8") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                    except Exception:
                        continue
                    calls += 1
                    prompt += e.get("prompt_tokens") or 0
                    completion += e.get("completion_tokens") or 0
        except FileNotFoundError:
            pass
        return {"calls": calls, "prompt_tokens": prompt, "completion_tokens": completion}


class LLM:
    def __init__(self, base_url, model, logger: LLMLogger, timeout=300):
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.logger = logger
        self.timeout = timeout

    def chat(self, messages, *, purpose, run_id=None, temperature=0.1, json_mode=True, max_tokens=1500):
        body = {"model": self.model, "messages": messages, "temperature": temperature,
                "max_tokens": max_tokens, "stream": False}
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        t0 = time.time()
        try:
            r = httpx.post(f"{self.base_url}/chat/completions", json=body, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
            usage = data.get("usage") or {}
            content = data["choices"][0]["message"]["content"]
            self.logger.write(source="backend", purpose=purpose, run_id=run_id, model=self.model,
                              prompt_tokens=usage.get("prompt_tokens"),
                              completion_tokens=usage.get("completion_tokens"),
                              latency_s=round(time.time() - t0, 2), ok=True)
            return content
        except Exception as e:
            self.logger.write(source="backend", purpose=purpose, run_id=run_id, model=self.model,
                              latency_s=round(time.time() - t0, 2), ok=False, error=str(e)[:300])
            raise

    def available(self):
        try:
            r = httpx.get(f"{self.base_url}/models", timeout=5)
            return r.status_code == 200
        except Exception:
            return False


def parse_json_loose(text):
    """Models wrap JSON in fences or prose; dig the first {...} or [...] out."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.lower().startswith("json"):
            text = text[4:]
    try:
        return json.loads(text)
    except Exception:
        pass
    for open_c, close_c in (("{", "}"), ("[", "]")):
        i, j = text.find(open_c), text.rfind(close_c)
        if i != -1 and j > i:
            try:
                return json.loads(text[i:j + 1])
            except Exception:
                continue
    raise ValueError("no JSON found in model output")
