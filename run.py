"""
One command to start FAULT LINES:

    uv run run.py            (the installers, start.cmd and start.sh, run this for you)

Says what is ready, then serves the app on http://127.0.0.1:8008 and opens it in your browser.
Nothing is required: without a text model or keys the app still opens, and you can watch a demo run.
Set everything else up from the app's Settings button. Add --no-browser to skip opening the browser.
"""
import os
import sys
import threading
import time
import webbrowser

import httpx
import yaml

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "backend"))

with open(os.environ.get("FL_CONFIG") or os.path.join(HERE, "config.yaml"), encoding="utf-8") as f:
    CFG = yaml.safe_load(f)


def check():
    from providers import load_dotenv_file
    from engine import find_mirofish, mirofish_python
    load_dotenv_file(os.path.join(HERE, ".env"))
    base = CFG["llm"].get("ollama_base_url", "http://127.0.0.1:11434/v1").rstrip("/")
    model = os.environ.get("LOCAL_MODEL") or CFG["llm"].get("ollama_model", "qwen2.5:14b")
    try:
        names = [m["name"] for m in httpx.get(base[:-3] + "/api/tags", timeout=3).json().get("models", [])]
        have = model in names or f"{model}:latest" in names
        print(f"[ok] Ollama is running" + ("" if have else f"; the model {model} is not downloaded yet (Settings can download it)"))
    except Exception:
        print("[--] Ollama is not running: fine if you use an OpenRouter key, or just want to watch a demo")
    print("[ok] OpenRouter key set" if os.environ.get("OPENROUTER_API_KEY") else "[--] no OpenRouter key")
    voices = [n for n, k in (("Fish Audio", "FISH_AUDIO_API_KEY"), ("Cartesia", "CARTESIA_API_KEY")) if os.environ.get(k)]
    print(f"[ok] voices: {' + '.join(voices)}" if voices else "[--] no voice key: news and debates play silently with the words on screen")
    py = mirofish_python(find_mirofish())
    print("[ok] simulation engine (MiroFish) installed" if py else "[!!] simulation engine (MiroFish) missing: run start.cmd / start.sh again; demo runs still work")


def open_browser(url):
    for _ in range(60):
        try:
            httpx.get(url + "/api/config", timeout=1)
            webbrowser.open(url)
            return
        except Exception:
            time.sleep(0.5)


if __name__ == "__main__":
    check()
    import uvicorn
    host, port = CFG["server"]["host"], CFG["server"]["port"]
    url = f"http://{host}:{port}"
    print(f"\nFAULT LINES is running at {url}   (keep this window open; close it to stop the app)")
    print("Simulation: LLM role-play, not a forecast.\n")
    if "--no-browser" not in sys.argv and not os.environ.get("FL_NO_BROWSER"):
        threading.Thread(target=open_browser, args=(url,), daemon=True).start()
    uvicorn.run("app:app", host=host, port=port, log_level="warning", app_dir=os.path.join(HERE, "backend"))
