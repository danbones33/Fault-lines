"""
Fetch Cartesia's public voice catalog into data/cartesia_voices.json (ids, names, accents and taglines only).

    uv run scripts/fetch_cartesia_voices.py

Needs CARTESIA_API_KEY in .env. The app also does this by itself the first time it casts a voice and again when the
file is older than 30 days, so running it by hand is optional.
"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "backend"))
from providers import load_dotenv_file  # noqa: E402

load_dotenv_file(os.path.join(ROOT, ".env"))
if not os.environ.get("CARTESIA_API_KEY"):
    sys.exit("CARTESIA_API_KEY is not set (add it to .env)")
import casting  # noqa: E402

voices = casting._refresh(os.path.join(ROOT, "data", "cartesia_voices.json"))
print(f"{len(voices)} voices -> data/cartesia_voices.json")
