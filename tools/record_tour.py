"""
Record a FAULT LINES tutorial to MP4, with clean audio.

    <playwright venv>/python tools/record_tour.py --tour water --out videos/tutorial_water.mp4

How: headless Chrome (1920x1080) plays /?tour=<name>. The page's screencast frames are written to ffmpeg at a
constant 30 fps against the wall clock. The page logs every audio clip it plays (narration, broadcasts, the debate,
the episode) with wall-clock start/stop times, so the soundtrack is rebuilt from the original files, not captured
from a speaker. Waits the tour marked as cuts (setup, a phone reply) are removed from picture and sound alike.
Also writes <out>.srt with the narration as subtitles.
"""
import argparse
import asyncio
import base64
import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

from playwright.async_api import async_playwright

FPS = 30


def ffmpeg_bin():
    return shutil.which("ffmpeg") or "ffmpeg"


class FrameWriter:
    """Writes the latest screencast frame to ffmpeg at a steady FPS, duplicating frames when the page is still."""
    def __init__(self, path):
        self.latest = None
        self.lock = threading.Lock()
        self.stop_flag = False
        self.t0 = None
        self.frames = 0
        self.proc = subprocess.Popen([ffmpeg_bin(), "-y", "-loglevel", "error", "-f", "image2pipe", "-framerate", str(FPS), "-c:v", "mjpeg", "-i", "-",
                                      "-c:v", "libx264", "-preset", "veryfast", "-crf", "14", "-pix_fmt", "yuv420p", path],
                                     stdin=subprocess.PIPE)
        self.thread = threading.Thread(target=self._loop, daemon=True)

    def push(self, jpeg):
        with self.lock:
            self.latest = jpeg
        if self.t0 is None:
            self.t0 = time.time()
            self.thread.start()

    def _loop(self):
        while not self.stop_flag:
            due = self.t0 + self.frames / FPS
            now = time.time()
            if now < due:
                time.sleep(min(0.01, due - now))
                continue
            with self.lock:
                frame = self.latest
            try:
                self.proc.stdin.write(frame)
            except Exception:
                break
            self.frames += 1

    def close(self):
        self.stop_flag = True
        self.thread.join(timeout=5)
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        self.proc.wait()


def kept_pieces(a, b, cuts):
    """[a, b] minus the cut intervals, as a list of (start, end)."""
    pieces = [(a, b)]
    for ca, cb in cuts:
        nxt = []
        for s, e in pieces:
            if cb <= s or ca >= e:
                nxt.append((s, e))
                continue
            if s < ca:
                nxt.append((s, ca))
            if cb < e:
                nxt.append((cb, e))
        pieces = nxt
    return [(s, e) for s, e in pieces if e - s > 0.03]


def out_time(t, start, cuts):
    """Wall-clock seconds -> seconds in the edited video."""
    removed = sum(max(0.0, min(t, cb) - ca) for ca, cb in cuts if ca < t)
    return t - start - removed


def srt_time(sec):
    ms = int(round(max(0.0, sec) * 1000))
    return f"{ms // 3600000:02d}:{ms // 60000 % 60:02d}:{ms // 1000 % 60:02d},{ms % 1000:03d}"


async def record(args):
    work = os.path.abspath(args.work)
    os.makedirs(work, exist_ok=True)
    raw = os.path.join(work, "raw.mp4")
    writer = FrameWriter(raw)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True,
                                           args=["--autoplay-policy=no-user-gesture-required", "--hide-scrollbars", "--force-device-scale-factor=1"])
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page = await ctx.new_page()
        page.on("console", lambda m: m.type in ("error", "warning") and print(f"  [page {m.type}] {m.text[:200]}"))
        cdp = await ctx.new_cdp_session(page)

        async def ack(sid):
            try:
                await cdp.send("Page.screencastFrameAck", {"sessionId": sid})
            except Exception:
                pass

        def on_frame(params):
            writer.push(base64.b64decode(params["data"]))
            asyncio.ensure_future(ack(params["sessionId"]))
        cdp.on("Page.screencastFrame", on_frame)
        await page.goto(args.url.rstrip("/") + f"/?tour={args.tour}&rec=1")
        await cdp.send("Page.startScreencast", {"format": "jpeg", "quality": 90, "maxWidth": 1920, "maxHeight": 1080, "everyNthFrame": 1})
        t_start = time.time()
        await page.wait_for_function("() => window.TOUR && TOUR.t0 > 0", timeout=600000)
        miss = await page.evaluate("() => Object.entries(TOUR.lines).filter(([t, l]) => !l.url).map(([t]) => t)")
        n_lines = await page.evaluate("() => Object.keys(TOUR.lines).length")
        print(f"  narration: {n_lines - len(miss)} of {n_lines} lines voiced" + (f"; MISSING: {miss[:3]}" if miss else ""), flush=True)
        last_chapter = None
        while True:
            await asyncio.sleep(2)
            state = await page.evaluate("() => ({done: !!window.__tourDone, ch: (document.getElementById('tourChapter') || {}).textContent || '', on: window.TOUR && TOUR.on})")
            if state["ch"] != last_chapter:
                last_chapter = state["ch"]
                print(f"  {time.time() - t_start:6.0f}s  {last_chapter or '(intro/outro)'}", flush=True)
            if state["done"] or time.time() - t_start > args.timeout:
                break
        await asyncio.sleep(1.5)
        data = await page.evaluate("() => ({log: TOUR.log, cuts: TOUR.cuts, captions: TOUR.captions, t0: TOUR.t0, end: TOUR.end || Date.now(), live: TOUR.liveRunId})")
        await cdp.send("Page.stopScreencast")
        await browser.close()
    writer.close()
    print(f"  raw video: {writer.frames} frames ({writer.frames / FPS:.0f}s)")
    with open(os.path.join(work, "tour_log.json"), "w", encoding="utf-8") as f:
        json.dump({**data, "video_t0": writer.t0, "frames": writer.frames}, f, indent=1)
    return data, writer


async def audit(args):
    """Play the tour without narration or a live run and save one screenshot per step (work/audit/step_NN.jpg)."""
    out = os.path.join(os.path.abspath(args.work), "audit")
    os.makedirs(out, exist_ok=True)
    for f in os.listdir(out):
        os.remove(os.path.join(out, f))
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(channel="chrome", headless=True, args=["--autoplay-policy=no-user-gesture-required", "--hide-scrollbars"])
        ctx = await browser.new_context(viewport={"width": 1920, "height": 1080}, device_scale_factor=1)
        page = await ctx.new_page()
        page.on("console", lambda m: m.type in ("error", "warning") and "404" not in m.text and print(f"  [page {m.type}] {m.text[:200]}"))
        notes = []

        async def shot(i, chapter, say):
            await asyncio.sleep(0.25)
            path = os.path.join(out, f"step_{int(i):02d}.jpg")
            await page.screenshot(path=path, type="jpeg", quality=70)
            notes.append(f"{int(i):02d} [{chapter}] {say[:90]}")
        await page.expose_function("__auditShot", shot)
        await page.goto(args.url.rstrip("/") + f"/?tour={args.tour}&audit=1")
        await page.wait_for_function("() => window.__tourDone === true", timeout=args.timeout * 1000)
        await browser.close()
    with open(os.path.join(out, "steps.txt"), "w", encoding="utf-8") as f:
        f.write(chr(10).join(notes))
    print(f"  audit: {len(notes)} screenshots in {out}")


def build(args, data, video_t0, frames):
    work = os.path.abspath(args.work)
    raw = os.path.join(work, "raw.mp4")
    vid_end = video_t0 + frames / FPS
    start = max(video_t0, data["t0"] / 1000 - 0.4)        # begin just before the tour's first frame (skip the narration prep)
    end = min(vid_end, data["end"] / 1000 + 0.8)
    cuts = sorted((a / 1000, b / 1000) for a, b in data["cuts"])

    # audio segments: start -> next stop of the same element
    segs, open_ = [], {}
    for ev in sorted(data["log"], key=lambda x: x["at"]):
        if ev["ev"] == "start":
            if ev["id"] in open_:
                segs.append({**open_.pop(ev["id"]), "until": ev["at"]})
            open_[ev["id"]] = ev
        elif ev["ev"] == "stop" and ev["id"] in open_:
            segs.append({**open_.pop(ev["id"]), "until": ev["at"]})
    for ev in open_.values():
        segs.append({**ev, "until": data["end"]})

    cache = os.path.join(work, "audio")
    os.makedirs(cache, exist_ok=True)
    files, pieces = {}, []
    for s in segs:
        src = s["src"]
        if not src or s.get("vol", 1) <= 0:
            continue
        url = src if src.startswith("http") else args.url.rstrip("/") + src
        if url not in files:
            local = os.path.join(cache, hashlib.sha1(url.encode()).hexdigest()[:16] + os.path.splitext(url.split("?")[0])[1][:5])
            if not os.path.exists(local):
                try:
                    urllib.request.urlretrieve(url, local)
                except Exception as e:
                    print(f"  skip audio {url}: {e}")
                    continue
            files[url] = {"path": local, "loop": bool(s.get("loop"))}
        a, b = s["at"] / 1000, s["until"] / 1000
        a2, b2 = max(a, start), min(b, end)
        for ps, pe in kept_pieces(a2, b2, cuts):
            pieces.append({"url": url, "src_pos": s.get("pos", 0) + (ps - a), "dur": pe - ps, "at": out_time(ps, start, cuts), "vol": s.get("vol", 1)})
    print(f"  {len(segs)} audio clips -> {len(pieces)} pieces from {len(files)} files; {len(cuts)} cuts")

    # video: drop the cut spans and everything outside [start, end]
    keep_v = [(max(start, video_t0), end)]
    keep_v = [p for k in keep_v for p in kept_pieces(k[0], k[1], cuts)]
    sel = "+".join(f"between(t,{ps - video_t0:.3f},{pe - video_t0:.3f})" for ps, pe in keep_v)
    lines = [f"[0:v]select='{sel}',setpts=N/{FPS}/TB[v]"]
    order = list(files.keys())
    uses = {u: [p for p in pieces if p["url"] == u] for u in order}
    labels = []
    for i, u in enumerate(order):
        ps = uses[u]
        if not ps:
            continue
        idx = i + 1
        if len(ps) > 1:
            lines.append(f"[{idx}:a]asplit={len(ps)}" + "".join(f"[s{i}_{k}]" for k in range(len(ps))))
            ins = [f"[s{i}_{k}]" for k in range(len(ps))]
        else:
            ins = [f"[{idx}:a]"]
        for k, p in enumerate(ps):
            lab = f"p{i}_{k}"
            delay = int(round(p["at"] * 1000))
            lines.append(f"{ins[k]}atrim=start={p['src_pos']:.3f}:duration={p['dur']:.3f},asetpts=PTS-STARTPTS,"
                         f"aresample=48000,volume={p['vol']:.2f},adelay={delay}:all=1[{lab}]")
            labels.append(f"[{lab}]")
    if labels:
        lines.append("".join(labels) + f"amix=inputs={len(labels)}:normalize=0:dropout_transition=0,alimiter=limit=0.95[a]")
    script = os.path.join(work, "filter.txt")
    with open(script, "w", encoding="utf-8") as f:
        f.write(";\n".join(lines))
    cmd = [ffmpeg_bin(), "-y", "-loglevel", "error", "-i", raw]
    for u in order:
        if files[u]["loop"]:
            cmd += ["-stream_loop", "-1"]
        cmd += ["-i", files[u]["path"]]
    total = sum(pe - ps for ps, pe in keep_v)
    # ffmpeg 7.1+ reads the filter graph from a file with "-/filter_complex <path>"
    cmd += ["-/filter_complex", script, "-map", "[v]"] + (["-map", "[a]"] if labels else []) + \
           ["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", str(FPS),
            "-c:a", "aac", "-b:a", "192k", "-t", f"{total:.2f}", "-movflags", "+faststart", os.path.abspath(args.out)]
    print(f"  encoding {total / 60:.1f} min -> {args.out}")
    subprocess.run(cmd, check=True)

    # subtitles from the narration
    srt = os.path.splitext(os.path.abspath(args.out))[0] + ".srt"
    with open(srt, "w", encoding="utf-8") as f:
        n = 0
        for c in data["captions"]:
            a = c["at"] / 1000
            if a < start or a > end:
                continue
            n += 1
            t1 = out_time(a, start, cuts)
            f.write(f"{n}\n{srt_time(t1)} --> {srt_time(t1 + c['dur'])}\n{c['text']}\n\n")
    print(f"  subtitles: {srt}")


def main():
    try:   # chapter names carry symbols a Windows console code page cannot print
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser()
    ap.add_argument("--tour", default="water")
    ap.add_argument("--out", required=True)
    ap.add_argument("--url", default="http://127.0.0.1:8008")
    ap.add_argument("--work", default=None, help="scratch folder for the raw video, audio cache and logs")
    ap.add_argument("--timeout", type=int, default=3600)
    ap.add_argument("--rebuild", action="store_true", help="skip recording; rebuild the MP4 from the work folder")
    ap.add_argument("--audit", action="store_true", help="no video: play the tour fast and screenshot every step")
    args = ap.parse_args()
    args.work = args.work or os.path.join(os.path.dirname(os.path.abspath(args.out)), f".work_{args.tour}")
    if args.audit:
        asyncio.run(audit(args))
        return
    if args.rebuild:
        with open(os.path.join(args.work, "tour_log.json"), encoding="utf-8") as f:
            d = json.load(f)
        build(args, d, d["video_t0"], d["frames"])
        return
    data, writer = asyncio.run(record(args))
    build(args, data, writer.t0, writer.frames)
    if data.get("live"):
        print(f"  the tour started live run {data['live']} and pressed Stop on it")


if __name__ == "__main__":
    sys.exit(main())
