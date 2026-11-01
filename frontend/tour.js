/* TUTORIAL MODE: a self-playing, narrated walkthrough of the app.
   Open /?tour=water or /?tour=aria (or press the Tutorial button). The narrator's lines are voiced once by the
   backend (/api/tour/say, cached), then the tour drives the real UI: a visible cursor clicks, types, switches tabs
   and map views, while captions show what is being said. Esc stops it.
   Every audio element the page plays is logged with wall-clock times (TOUR.log), so a screen recording can be
   scored afterwards with the exact narration and broadcast audio (see tools/record_tour.py). */
const TOUR = {on: false, t0: 0, log: [], lines: {}, stopFlag: false, done: false, liveRunId: null, scripts: {}, cuts: [], captions: [], end: 0};
window.TOUR = TOUR;

// ---------- audio log: every <audio>/new Audio() start and stop, in page wall-clock ms ----------
(function hookMedia() {
  const P = HTMLMediaElement.prototype; const origPlay = P.play;
  let n = 0;
  P.play = function () {
    const el = this;
    if (!el.__tourHooked) {
      el.__tourHooked = true; el.__tid = "a" + (++n);
      el.addEventListener("playing", () => TOUR.log.push({ev: "start", id: el.__tid, src: el.currentSrc || el.src, at: Date.now(), pos: el.currentTime, vol: el.muted ? 0 : el.volume, loop: el.loop}));
      el.addEventListener("pause", () => TOUR.log.push({ev: "stop", id: el.__tid, at: Date.now()}));
      el.addEventListener("ended", () => TOUR.log.push({ev: "stop", id: el.__tid, at: Date.now()}));
      el.addEventListener("volumechange", () => TOUR.log.push({ev: "vol", id: el.__tid, at: Date.now(), vol: el.muted ? 0 : el.volume}));
    }
    return origPlay.apply(this, arguments);
  };
})();

// ---------- the visible layer: cursor, spotlight, caption, chapter chip, title card ----------
function tourLayer() {
  let L = document.getElementById("tourLayer"); if (L) return L;
  L = document.createElement("div"); L.id = "tourLayer";
  L.innerHTML = `<div id="tourSpot" class="tour-spot hidden"><span id="tourSpotLabel"></span></div>
    <div id="tourChapter" class="tour-chapter hidden"></div>
    <div id="tourCaption" class="tour-caption hidden"></div>
    <div id="tourCard" class="tour-card hidden"><div><div id="tourCardTitle"></div><div id="tourCardSub"></div></div></div>
    <div id="tourPrep" class="tour-prep hidden"></div>
    <svg id="tourCursor" class="tour-cursor" width="30" height="30" viewBox="0 0 24 24"><path d="M3 2 L3 19 L7.5 14.8 L10.6 21.6 L13.6 20.3 L10.6 13.6 L17 13.6 Z" fill="#fff" stroke="#111" stroke-width="1.4" stroke-linejoin="round"/></svg>`;
  document.body.appendChild(L); return L;
}
const sleep = ms => new Promise(r => setTimeout(r, ms));
function el(sel) {
  if (!sel) return null;
  if (typeof sel === "function") return sel();
  if (typeof sel === "string") return document.querySelector(sel);
  return sel;
}
function stateEl(code) { let found = null; gStates.selectAll("path.state").each(function (d) { if (NAME2CODE[d.properties.name] === code) found = this; }); return found; }
let cur = {x: 960, y: 540};
async function cursorTo(target, ms = 1000) {
  const e = el(target); if (!e) return null;
  let r = e.getBoundingClientRect();
  if (r.bottom > innerHeight - 10 || r.top < 0) { e.scrollIntoView({block: "center", behavior: "smooth"}); await sleep(550); r = e.getBoundingClientRect(); }
  const x = r.left + Math.min(r.width / 2, Math.max(12, r.width * 0.45)), y = r.top + Math.min(r.height / 2, 40);
  const c = document.getElementById("tourCursor");
  c.style.transition = `transform ${ms}ms cubic-bezier(.4,.1,.2,1)`; c.style.transform = `translate(${x}px, ${y}px)`;
  cur = {x, y}; await sleep(ms + 60); return e;
}
function ripple() {
  const d = document.createElement("div"); d.className = "tour-ripple"; d.style.left = cur.x + "px"; d.style.top = cur.y + "px";
  tourLayer().appendChild(d); setTimeout(() => d.remove(), 700);
}
const T = {
  async spot(target, label, pad = 6) {
    const e = el(target); const s = document.getElementById("tourSpot");
    if (!e) { s.classList.add("hidden"); return; }
    const r = e.getBoundingClientRect();
    Object.assign(s.style, {left: (r.left - pad) + "px", top: (r.top - pad) + "px", width: (r.width + pad * 2) + "px", height: (r.height + pad * 2) + "px"});
    document.getElementById("tourSpotLabel").textContent = label || ""; document.getElementById("tourSpotLabel").classList.toggle("hidden", !label);
    s.classList.toggle("below", r.top < 120);
    s.classList.remove("hidden");
  },
  unspot() { document.getElementById("tourSpot").classList.add("hidden"); },
  async point(target, ms) { return cursorTo(target, ms); },
  async click(target, {spot = false, label = "", after = 350} = {}) {
    const e = await cursorTo(target); if (!e) return null;
    if (spot) await T.spot(e, label);
    ripple();
    if (e instanceof SVGElement) e.dispatchEvent(new MouseEvent("click", {bubbles: true}));
    else e.click();
    await sleep(after); return e;
  },
  async type(target, text, {cps = 38, burst = 240} = {}) {
    // types the first `burst` characters at a human pace, then the rest arrives fast (a paste)
    const e = await cursorTo(target); if (!e) return; ripple(); e.focus();
    e.value = ""; e.dispatchEvent(new Event("input", {bubbles: true}));
    const slow = text.slice(0, burst), fast = text.slice(burst);
    for (let i = 0; i < slow.length; i++) { if (TOUR.stopFlag) return; e.value += slow[i]; if (i % 3 === 0) { e.scrollTop = e.scrollHeight; e.dispatchEvent(new Event("input", {bubbles: true})); } await sleep(1000 / cps); }
    for (let i = 0; i < fast.length; i += 60) { if (TOUR.stopFlag) return; e.value += fast.slice(i, i + 60); e.scrollTop = e.scrollHeight; await sleep(16); }
    e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true}));
    e.scrollTop = 0; e.blur();
  },
  async fill(target, value) { const e = await cursorTo(target, 600); if (!e) return; ripple(); e.value = value; e.scrollTop = 0; e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); await sleep(350); },
  set(target, value) { const e = el(target); if (!e) return; e.value = value; e.dispatchEvent(new Event("input", {bubbles: true})); e.dispatchEvent(new Event("change", {bubbles: true})); },
  async tab(name) { return T.click(`.tabs button[data-tab="${name}"]`); },
  async view(name) { return T.click(`#viewToggle button[data-view="${name}"]`); },
  async state(code) { return T.click(() => stateEl(code)); },
  async scroll(target, dy, ms = 1600) {
    const e = el(target); if (!e) return; await cursorTo(e, 400);
    const start = e.scrollTop, t0 = performance.now();
    while (true) { const k = Math.min(1, (performance.now() - t0) / ms); e.scrollTop = start + dy * (k < .5 ? 2 * k * k : 1 - Math.pow(-2 * k + 2, 2) / 2); if (k >= 1 || TOUR.stopFlag) break; await sleep(16); }
  },
  async round(i) {
    if (run && i < run.rounds.length - 1) stopCalls();   // election-night calls only on the last round
    const s = $("scrub"); await cursorTo(s, 350); s.value = i; s.dispatchEvent(new Event("input", {bubbles: true})); await sleep(300);
  },
  async load(id) {
    stopSpeech(); if (window.EP && EP.ep && document.body.classList.contains("episode")) closeEpisode();
    $("runSelect").value = id; selected = null; follow = true; callRound = -1;
    await loadRun(id); stopCalls(); callRound = -1; await sleep(900);
  },
  async until(fn, timeout = 60000, every = 500) { const t0 = Date.now(); while (Date.now() - t0 < timeout && !TOUR.stopFlag) { try { if (await fn()) return true; } catch (e) {} await sleep(every); } return false; },
  wait: sleep,
  leftOpen(open) { document.body.classList.toggle("left-hidden", !open); setTimeout(layout, 300); return sleep(700); },
  chapter(text) { const c = document.getElementById("tourChapter"); c.textContent = text; c.classList.toggle("hidden", !text);
    const m = document.querySelector(".map-wrap"); if (m) { const r = m.getBoundingClientRect(); c.style.left = (r.left + 18) + "px"; c.style.top = (r.top + 14) + "px"; } },
  async card(title, sub, ms = 3200) {
    const c = document.getElementById("tourCard"); document.getElementById("tourCardTitle").textContent = title; document.getElementById("tourCardSub").textContent = sub || "";
    c.classList.remove("hidden", "out"); await sleep(ms); c.classList.add("out"); await sleep(600); c.classList.add("hidden");
  },
  // plays something on the page (a broadcast, the debate) and waits for its audio to finish or for maxMs
  async listen(start, maxMs = 30000, stop) {
    const before = TOUR.log.length; await start();
    await T.until(() => TOUR.log.slice(before).some(x => x.ev === "start"), 6000, 200);
    await sleep(maxMs);
    if (stop) await stop();
  },
  // waiting that the video should skip: the recorder cuts [start, end] out and shows a "later" chip
  async cut(fn, label) {
    const a = Date.now(); await fn(); const b = Date.now();
    if (b - a > 2500) TOUR.cuts.push([a + 400, b - 200]);
    await T.flash(label || "⏩ a little later");
  },
  async flash(text, ms = 1500) { const c = document.getElementById("tourChapter"); const was = c.textContent, hid = c.classList.contains("hidden");
    c.textContent = text; c.classList.remove("hidden"); await sleep(ms); c.textContent = was; c.classList.toggle("hidden", hid); },
  blank() { T.unspot(); const main = document.querySelector("main"); if (main) { main.style.transition = "none"; main.style.transform = ""; } if ($("broadcast")) $("broadcast").style.opacity = ""; clearView(); renderLive(); },
  // camera: zoom the whole workspace toward a target (the overlay layer is outside it, so captions stay put)
  async zoom(target, scale = 1.8, ms = 1000) {
    const e = el(target); const main = document.querySelector("main"); if (!e || !main) return;
    main.style.transition = "none"; main.style.transform = ""; const r = e.getBoundingClientRect(); const mr = main.getBoundingClientRect();
    const cx = r.left + r.width / 2 - mr.left, cy = r.top + r.height / 2 - mr.top;
    const tx = mr.width / 2 - cx * scale, ty = mr.height / 2 - cy * scale;
    const bc = $("broadcast"); if (bc) { bc.style.transition = "opacity .4s"; bc.style.opacity = "0"; }   // the news box would scale up with the map and sit under the caption
    main.style.transformOrigin = "0 0"; void main.offsetWidth;
    main.style.transition = `transform ${ms}ms cubic-bezier(.4,.1,.2,1)`;
    main.style.transform = `translate(${Math.min(0, Math.max(mr.width - mr.width * scale, tx))}px, ${Math.min(0, Math.max(mr.height - mr.height * scale, ty))}px) scale(${scale})`;
    await sleep(ms + 80);
  },
  async zoomOut(ms = 800) { const main = document.querySelector("main"); const bc = $("broadcast"); if (bc) bc.style.opacity = "";
    if (!main || !main.style.transform) return; main.style.transition = `transform ${ms}ms cubic-bezier(.4,.1,.2,1)`; main.style.transform = ""; await sleep(ms + 60); },
  // open one meme full size (round + index from its item id, e.g. "r3_m2")
  async meme(itemId, holdMs = 4000) {
    const [r, k] = itemId.replace("r", "").split("_m").map(Number);
    if (roundIdx !== r) await T.round(r);
    await T.tab("feed");
    await T.until(() => feedRound === r && document.querySelector(`#feed .fi .media img[src*="memes/r${r}_${k}."]`), 15000, 300);
    const img = document.querySelector(`#feed .fi .media img[src*="memes/r${r}_${k}."]`); if (!img) return false;
    img.closest(".fi").scrollIntoView({block: "center", behavior: "smooth"}); await sleep(700);
    await T.click(img, {after: 200}); await sleep(holdMs); closeLightbox(); await sleep(300); return true;
  },
  // play part of the debate: lines [from, from + count) in the broadcast player
  async debate(roundNo, from, count, maxMs = 120000) {
    if (roundIdx !== roundNo) await T.round(roundNo);
    await T.until(() => feedRound === roundNo && feedCache && feedCache.debate, 15000, 300);
    if (bcMode !== "debate") await T.click('#bcTabs button[data-i="debate"]', {after: 600});
    window.bcDebateFrom = from; window.bcDebateCount = count;
    if (TOUR.audit) { await T.spot("#broadcast", "debate"); await sleep(600); window.bcDebateFrom = 0; window.bcDebateCount = 0; return; }
    await T.click("#bcPlay", {after: 400});
    await T.until(() => !bcSegment && !bcAudio, maxMs, 300);
    if (bcSegment || bcAudio) $("bcPlay").click();
    window.bcDebateFrom = 0; window.bcDebateCount = 0;
  },
  // play a news broadcast for up to maxMs (all of it if it is shorter)
  async news(maxMs = 60000, net = 0) {
    if (bcMode !== "news" || bcNet !== net) { const b = document.querySelector(`#bcTabs button[data-i="${net}"]`); if (b) await T.click(b, {after: 500}); }
    if (TOUR.audit) { await T.spot("#broadcast", "news"); await sleep(600); return; }
    await T.click("#bcPlay", {after: 400});
    await T.until(() => !bcSegment && !bcAudio, maxMs, 300);
    if (bcSegment || bcAudio) $("bcPlay").click();
  },
  async epPlay(key, maxSeconds = 40) {
    if (!(await T.episodeScene((sc, k) => k === key))) return;
    const idx = EP.idx;
    if (TOUR.audit) { await sleep(1500); epPause(); return; }
    await sleep(800);
    await T.until(() => EP.idx !== idx || !EP.playing || (EP.audio && EP.audio.ended), maxSeconds * 1000, 200);
    epPause(); await sleep(600);
  },
  async episodeScene(pred) {
    if (!EP.ep) return false;
    const i = EP.ep.order.findIndex(k => pred(EP.ep.scenes[k], k)); if (i < 0) return false;
    EP.idx = i - 1; epNext(); return true;
  },
};
window.T = T;

// ---------- narration ----------
async function prepareLines(script) {
  const texts = [...new Set(script.filter(s => s.say).map(s => s.say))];
  const moods = {}; script.forEach(s => { if (s.say && s.mood) moods[s.say] = s.mood; });
  if (TOUR.audit) { texts.forEach(t => TOUR.lines[t] = {url: null, duration: 0.3}); return; }
  const prep = document.getElementById("tourPrep"); prep.classList.remove("hidden");
  let done = 0;
  const work = texts.map(t => async () => {
    try { TOUR.lines[t] = await api("/api/tour/say", {method: "POST", body: JSON.stringify({text: t, mood: moods[t] || null}), headers: {"Content-Type": "application/json"}}); }
    catch (e) { TOUR.lines[t] = {url: null, duration: Math.max(2.5, t.split(/\s+/).length / 2.6)}; }
    done++; prep.textContent = `Preparing the narration… ${done} of ${texts.length}`;
  });
  const pool = []; for (let k = 0; k < 3; k++) pool.push((async () => { while (work.length) await work.shift()(); })());
  await Promise.all(pool);
  // warm the cache so the first word of each line is not cut
  await Promise.all(texts.filter(t => TOUR.lines[t].url).map(t => fetch(TOUR.lines[t].url).catch(() => null)));
  prep.classList.add("hidden");
}
function placeCaption(cap) {
  const map = document.querySelector(".map-wrap"); const tape = document.querySelector(".tape");
  if (!map) return;
  const m = map.getBoundingClientRect();
  let left = m.left + 24, right = m.right - 24;
  const bc = $("broadcast"); const lb = $("lightbox");
  if (lb && !lb.classList.contains("hidden")) { left = 120; right = innerWidth - 120; }
  else if (bc && !bc.classList.contains("hidden") && bc.style.opacity !== "0" && !document.body.classList.contains("episode")) { const r = bc.getBoundingClientRect(); if (r.width && r.right < m.right - 400) left = Math.max(left, r.right + 24); }
  const bottomEdge = tape ? tape.getBoundingClientRect().top : innerHeight - 90;
  const ep = $("epOverlay"); const epTop = ep && !ep.classList.contains("hidden") ? ep.getBoundingClientRect().top : Infinity;
  const floor = Math.min(bottomEdge, epTop);
  Object.assign(cap.style, {left: left + "px", width: Math.max(320, right - left) + "px", transform: "none", maxWidth: "none", bottom: (innerHeight - floor + 14) + "px"});
}
function sayLine(text) {
  const line = TOUR.lines[text] || {url: null, duration: 3};
  const cap = document.getElementById("tourCaption"); cap.innerHTML = `<span>${esc(text)}</span>`; placeCaption(cap); cap.classList.remove("hidden");
  TOUR.captions.push({at: Date.now(), dur: line.duration, text});
  return new Promise(resolve => {
    if (!line.url) { setTimeout(resolve, line.duration * 1000); return; }
    const a = new Audio(line.url); a.volume = 1; TOUR.narrator = a;
    let doneOnce = false; const fin = () => { if (!doneOnce) { doneOnce = true; resolve(); } };
    a.onended = fin; a.onerror = fin; a.play().catch(fin);
    setTimeout(fin, (line.duration + 1.5) * 1000);   // never hang on a stalled clip
  });
}

// ---------- the player ----------
async function runTour(name) {
  const script = (TOUR.scripts[name] || (() => []))();
  if (!script.length) { alert("No tutorial named " + name); return; }
  tourLayer(); document.body.classList.add("touring");
  let saved = null; try { saved = localStorage.getItem("fl.form"); } catch (e) {}
  TOUR.on = true; TOUR.stopFlag = false; TOUR.done = false; TOUR.audit = new URLSearchParams(location.search).has("audit");
  await prepareLines(script);
  await sleep(600);
  TOUR.t0 = Date.now(); window.__tourT0 = TOUR.t0;
  for (const step of script) {
    if (TOUR.stopFlag) break;
    if (step.chapter !== undefined) T.chapter(step.chapter);
    const jobs = [];
    if (step.say) jobs.push(sayLine(step.say));
    if (step.do) jobs.push((async () => { try { await step.do(T); } catch (e) { console.warn("tour step failed", e); } })());
    await Promise.all(jobs);
    if (TOUR.audit && window.__auditShot) { try { await window.__auditShot(script.indexOf(step), step.chapter || "", step.say || "(action)"); } catch (e) {} }
    document.getElementById("tourCaption").classList.add("hidden");
    await sleep(step.gap != null ? step.gap : 700);
  }
  T.unspot(); T.chapter(""); await T.zoomOut(10);
  try { if (saved !== null) localStorage.setItem("fl.form", saved); } catch (e) {}
  TOUR.on = false; TOUR.done = true; TOUR.end = Date.now(); window.__tourDone = true; document.body.classList.remove("touring");
}
document.addEventListener("keydown", e => { if (e.key === "Escape" && TOUR.on) { TOUR.stopFlag = true; if (TOUR.narrator) TOUR.narrator.pause(); } });

// a Tutorial button in the left column, and ?tour=<name> to start one on load
(async function mountTourButton() {
  const box = document.getElementById("runBtn"); if (!box) return;
  // the tutorial replays recorded runs; on a fresh install they don't exist, so the button only shows where they do
  try { const runs = await (await fetch("/api/runs")).json(); if (!runs.some(r => r.run_id === TOUR_RUNS.water)) return; } catch (e) { return; }
  const b = document.createElement("button"); b.id = "tourBtn"; b.className = "tour-btn"; b.textContent = "▶ Watch the tutorial";
  b.title = "a narrated walkthrough that drives the app for you (Esc stops it)";
  b.onclick = () => runTour("water");
  box.parentNode.insertBefore(b, box.nextSibling.nextSibling);
})();
window.addEventListener("load", () => {
  const name = new URLSearchParams(location.search).get("tour");
  if (name) T.until(() => typeof booted !== "undefined" && booted, 30000, 200).then(() => sleep(1200)).then(() => runTour(name));
});
