"""
Where did the money go? Reads logs/llm_calls.jsonl (every model, voice and image call the app made) and every
runs/*/spend.json (each run's ledger), groups cost and call counts by kind, provider and model, and prints short tables.

    python tools/spend_report.py            # last 14 days
    python tools/spend_report.py --days 30

The call log is the fuller source: it keeps rows for runs that were deleted later. Ledgers only exist for runs still on
disk, but they hold the exact per-run totals the spend guard enforced. Voice costs from Fish and Cartesia are estimates
(characters x rate); OpenRouter text and image costs are what OpenRouter reported.
"""
import argparse
import glob
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MEDIA = {"tts": "voices", "images": "pictures", "memes": "pictures", "avatars": "pictures", "avatar_video": "video",
         "video": "video", "scene": "pictures"}


def bucket(kind):
    """kind -> money bucket: voices, pictures, video, or text."""
    return MEDIA.get(kind, "text")


def provider_of(row):
    p = row.get("provider")
    if p:
        return p
    m = (row.get("model") or "").lower()
    if not m:
        return "unknown"
    return "ollama" if (":" in m and "/" not in m) else "openrouter"


def table(title, rows, cols):
    print(f"\n{title}")
    widths = [max(len(str(c)), *(len(str(r[i])) for r in rows)) if rows else len(c) for i, c in enumerate(cols)]
    print("  " + "  ".join(str(c).ljust(w) for c, w in zip(cols, widths)))
    for r in rows:
        print("  " + "  ".join(str(v).ljust(w) for v, w in zip(r, widths)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    args = ap.parse_args()
    since = (datetime.now() - timedelta(days=args.days)).isoformat(timespec="seconds")

    # ---- the call log ----
    by = defaultdict(lambda: [0, 0.0, 0, 0])          # (bucket, kind, provider, model) -> calls, usd, prompt, completion
    by_day = defaultdict(float)
    path = os.path.join(ROOT, "logs", "llm_calls.jsonl")
    n = 0
    with open(path, encoding="utf-8") as f:
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if (r.get("ts") or "") < since:
                continue
            n += 1
            kind = r.get("purpose") or "?"
            key = (bucket(kind), kind, provider_of(r), (r.get("model") or "-").split("/")[-1][:28])
            v = by[key]
            v[0] += 1
            v[1] += float(r.get("cost_usd") or 0)
            v[2] += int(r.get("prompt_tokens") or 0)
            v[3] += int(r.get("completion_tokens") or 0)
            by_day[(r.get("ts") or "")[:10]] += float(r.get("cost_usd") or 0)
    rows = sorted(by.items(), key=lambda kv: -kv[1][1])
    total = sum(v[1] for _, v in rows)
    buckets = defaultdict(float)
    for k, v in rows:
        buckets[k[0]] += v[1]
    table(f"Call log, last {args.days} days: {n} calls, ${total:.2f}",
          [(b, f"${u:.2f}", f"{100 * u / total:.0f}%" if total else "-") for b, u in sorted(buckets.items(), key=lambda kv: -kv[1])],
          ["money bucket", "usd", "share"])
    table("By kind, provider and model (top 20 by cost)",
          [(k[1], k[2], k[3], v[0], f"${v[1]:.2f}", f"{v[2] + v[3]:,}") for k, v in rows[:20]],
          ["kind", "provider", "model", "calls", "usd", "tokens"])
    local = [(k, v) for k, v in rows if k[2] == "ollama"]
    table("Local model calls (free)",
          [(k[1], v[0], f"{v[2] + v[3]:,}") for k, v in sorted(local, key=lambda kv: -kv[1][0])[:10]],
          ["kind", "calls", "tokens"])
    table("By day", [(d, f"${u:.2f}") for d, u in sorted(by_day.items())], ["day", "usd"])

    # ---- the run ledgers ----
    led_kind = defaultdict(float)
    led_total, runs = 0.0, 0
    for sp in glob.glob(os.path.join(ROOT, "runs", "*", "spend.json")):
        try:
            led = json.load(open(sp, encoding="utf-8"))
        except Exception:
            continue
        calls = [c for c in led.get("calls", []) if (c.get("ts") or "") >= since]
        if not calls:
            continue
        runs += 1
        for c in calls:
            led_kind[bucket(c.get("kind") or "?")] += float(c.get("usd") or 0)
            led_total += float(c.get("usd") or 0)
    table(f"Run ledgers still on disk: {runs} runs, ${led_total:.2f} (deleted runs are missing here)",
          [(b, f"${u:.2f}") for b, u in sorted(led_kind.items(), key=lambda kv: -kv[1])], ["money bucket", "usd"])


if __name__ == "__main__":
    main()
