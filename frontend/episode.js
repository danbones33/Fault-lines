/* EPISODE MODE: plays the Director's script. Reads only the script, never results. Shares the page's
   map, feed, tape and player elements with app.js (classic-script globals). */
const EP = {ep: null, idx: -1, playing: false, token: 0, audio: null, music: null, watched: false, spoiler: true, timer: null,
            calledNight: {}, revealedRounds: new Set(), clockTimer: null};
const MUSIC_VOL = 0.18;

function epKey(k) { return `fl.ep.${runId}.${k}`; }
function epWatched() { try { return localStorage.getItem(epKey("watched")) === "1"; } catch (e) { return false; } }
function epSetWatched() { try { localStorage.setItem(epKey("watched"), "1"); } catch (e) {} EP.watched = true; renderEpControls(); }

// ---------- entry / exit ----------
async function openEpisode() {
  if (!runId) return;
  const r = await api(`/api/runs/${runId}/episode`);
  const box = $("epOverlay"); box.classList.remove("hidden"); document.body.classList.add("episode");
  if (r.status !== "ready") {
    if (r.status === "none" || r.status === "error") await api(`/api/runs/${runId}/episode`, {method: "POST"}).catch(e => { $("epStatus").textContent = "error: " + e.message; });
    $("epStatus").textContent = "building the episode: " + (r.progress.note || "queued") + (r.progress.total ? ` (${r.progress.step}/${r.progress.total})` : "");
    EP.timer = setTimeout(openEpisode, 4000); return;
  }
  $("epStatus").textContent = "";
  EP.ep = r.episode; EP.idx = -1; EP.watched = epWatched(); EP.spoiler = true; EP.calledNight = {}; EP.revealedRounds = new Set();
  stopSpeech(); stopCalls(); $("broadcast").classList.add("hidden");
  if (!marketCache || !(marketCache.history || []).length) loadMarket().catch(() => {});
  renderEpControls(); renderChapters(); showTab("feed");
  $("epTitleCard").classList.remove("hidden"); $("epCardTitle").textContent = EP.ep.title; $("epCardSub").textContent = (EP.ep.year ? `${EP.ep.year} · ${EP.ep.era} · ` : "") + "A FAULT LINES episode · press play";
  $("epEra").textContent = EP.ep.year ? `${EP.ep.year} · ${EP.ep.era}` : ""; $("epEra").classList.toggle("hidden", !EP.ep.year);
  EP.dates = {}; (run && run.rounds || []).forEach(r => { if (r.date_label) EP.dates[r.round] = r.date_label; });
}
function closeEpisode() {
  epStopAll(); document.body.classList.remove("episode"); $("epOverlay").classList.add("hidden"); $("broadcast").classList.remove("hidden");
  $("epTitleCard").classList.add("hidden"); $("epClock").classList.add("hidden"); $("epInset").classList.add("hidden");
  clearTimeout(EP.timer); EP.ep = null; if (run) { roundIdx = run.rounds.length - 1; feedRound = -1; render(); }
}
function epStopAll() {
  EP.token++; EP.playing = false;
  if (EP.audio) { EP.audio.onended = null; EP.audio.pause(); EP.audio = null; }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  clearTimeout(EP.clockTimer);
  $("epPlay").textContent = "▶";
}

// ---------- controls ----------
function renderEpControls() {
  const ep = EP.ep; if (!ep) return;
  const cur = EP.idx >= 0 ? ep.scenes[ep.order[EP.idx]] : null;
  $("epScene").textContent = cur ? `${cur.title}` : "ready";
  $("epCount").textContent = `${Math.max(0, EP.idx + 1)} / ${ep.order.length}`;
  $("epDebug").disabled = false;
  $("epDebug").title = "stop the episode and go back to the live timeline";
  $("epSpoiler").textContent = EP.spoiler ? "spoiler-free: on" : "spoiler-free: off";
}
function renderChapters() {
  const ep = EP.ep; const cur = EP.idx >= 0 ? ep.scenes[ep.order[EP.idx]].act : null;
  const curActIdx = ep.acts.findIndex(a => a.id === cur);
  const a = ep.acts[curActIdx] || ep.acts[0];
  const box = $("epChapters"); const wasOpen = box.classList.contains("open");
  box.innerHTML = `<span class="chap-now">${esc(a.title)}${a.subtitle ? `: ${esc(a.subtitle)}` : ""} <span class="muted">· ${Math.max(1, curActIdx + 1)} of ${ep.acts.length}</span></span>
    <button class="small" id="chapToggle" title="jump to a chapter">chapters ▾</button>
    <div class="chap-list">${ep.acts.map((x, i) => { const locked = !EP.watched && i > Math.max(0, curActIdx);
      return `<button class="chap ${x.id === cur ? "active" : ""} ${locked ? "locked" : ""}" data-act="${x.id}" ${locked ? "disabled" : ""}>${locked ? "🔒 " : ""}${i + 1}. ${esc(x.title)}${x.subtitle ? ": " + esc(x.subtitle) : ""}</button>`; }).join("")}</div>`;
  box.classList.toggle("open", wasOpen);
  $("chapToggle").onclick = e => { e.stopPropagation(); box.classList.toggle("open"); };
  box.querySelectorAll("button.chap:not(.locked)").forEach(b => b.onclick = () => { box.classList.remove("open"); const x = ep.acts.find(y => y.id === b.dataset.act); const first = x.scenes[0]; EP.idx = ep.order.indexOf(first) - 1; epNext(); });
}
document.addEventListener("click", e => { const box = $("epChapters"); if (box && !box.contains(e.target)) box.classList.remove("open"); });
$("epPlay").onclick = () => { if (EP.playing) { epPause(); } else { epResume(); } };
$("epNext").onclick = () => { epNext(); };
$("epExit").onclick = closeEpisode;
$("epDebug").onclick = () => closeEpisode();
$("epSpoiler").onclick = () => { EP.spoiler = !EP.spoiler; renderEpControls(); renderChapters(); };
$("epTitleCard").onclick = () => { if (EP.idx < 0) epResume(); };
function epPause() { EP.playing = false; EP.token++; if (EP.audio) EP.audio.pause(); if (window.speechSynthesis) window.speechSynthesis.pause(); $("epPlay").textContent = "▶"; }
function epResume() {
  if (EP.idx < 0) { EP.playing = true; $("epPlay").textContent = "❚❚"; epNext(); return; }
  EP.playing = true; $("epPlay").textContent = "❚❚";
  if (EP.audio) { EP.audio.play(); EP.audio.onended = () => epAdvance(); }
  else if (window.speechSynthesis && window.speechSynthesis.paused) window.speechSynthesis.resume();
  else epNext();
}
function epAdvance() { if (EP.playing) epNext(); }
function epNext() {
  const ep = EP.ep; if (!ep) return;
  EP.token++; const token = EP.token;
  if (EP.audio) { EP.audio.onended = null; EP.audio.pause(); EP.audio = null; }
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  EP.idx++;
  if (EP.idx >= ep.order.length) { epFinish(); return; }
  EP.playing = true; $("epPlay").textContent = "❚❚"; $("epTitleCard").classList.add("hidden");
  const sc = ep.scenes[ep.order[EP.idx]];
  playScene(sc, token);
  renderEpControls(); renderChapters();
}
function epFinish() {
  EP.playing = false; $("epPlay").textContent = "▶"; epSetWatched();
  $("epTitleCard").classList.remove("hidden"); $("epCardTitle").textContent = "The End"; $("epCardSub").textContent = "You've watched the episode. Every chapter is unlocked.";
  setMusic(null);
}

// ---------- music + stingers ----------
let actx = null;
function ctx() { if (!actx) actx = new (window.AudioContext || window.webkitAudioContext)(); return actx; }
function stinger(kind) {
  try {
    const c = ctx(); const t = c.currentTime; const g = c.createGain(); g.gain.value = 0.0001; g.connect(c.destination);
    const notes = kind === "breaking" ? [[880, 0], [1174, 0.12], [1568, 0.24]] : kind === "call" ? [[660, 0], [990, 0.1]] : [[440, 0]];
    notes.forEach(([f, dt]) => { const o = c.createOscillator(); o.type = kind === "breaking" ? "square" : "sine"; o.frequency.value = f; o.connect(g); o.start(t + dt); o.stop(t + dt + 0.22); });
    g.gain.exponentialRampToValueAtTime(0.12, t + 0.02); g.gain.exponentialRampToValueAtTime(0.0001, t + 0.6);
    // swappable file beats the synth if present
    const a = new Audio(`/static/audio/stingers/${kind}.mp3`); a.volume = 0.5; a.play().catch(() => {});
  } catch (e) {}
}
function setMusic(act) {
  const want = act ? `/static/audio/music/${act}.mp3` : null;
  if (EP.music && EP.music.dataset.src === want) return;
  if (EP.music) { const m = EP.music; EP.music = null; let v = m.volume; const fade = setInterval(() => { v -= 0.03; if (v <= 0) { clearInterval(fade); m.pause(); } else m.volume = v; }, 100); }
  if (!want) return;
  const m = new Audio(want); m.dataset.src = want; m.loop = true; m.volume = (config && config.music_volume) || MUSIC_VOL;
  m.play().catch(() => {}); EP.music = m;   // 404s are silently ignored: drop files in frontend/audio/music/<act>.mp3
}

// ---------- scene playback ----------
function sceneRound(sc) { return sc.round; }
function playScene(sc, token) {
  const ep = EP.ep;
  const act = ep.acts.find(a => a.id === sc.act);
  setMusic(act ? act.music : null);
  if (sc.stinger) stinger(sc.stinger);
  // the data the page shows is pinned to this scene's round: never ahead
  EP.revealedRounds.add(sc.round);
  roundIdx = Math.max(0, run.rounds.findIndex(r => r.round === sc.round));
  if (sc.type === "title") {
    $("epTitleCard").classList.remove("hidden"); $("epCardTitle").textContent = sc.title; $("epCardSub").textContent = sc.subtitle || "";
    $("epInset").classList.add("hidden"); $("epClock").classList.add("hidden");
    EP.clockTimer = setTimeout(() => { if (token === EP.token && EP.playing) epNext(); }, (sc.duration || 4) * 1000);
    return;
  }
  $("epTitleCard").classList.add("hidden");
  // map
  const ri = run.rounds.findIndex(r => r.round === sc.round); if (ri >= 0) roundIdx = ri;   // the right side (markets, state, term) follows the scene's round
  EP.scene = sc;
  if (sc.map) {
    if (sc.map.mode === "night") { view = "election"; renderNightMap(sc.night || null, sc.map.tick); }
    else { view = sc.map.mode === "election" ? "election" : sc.map.mode === "approval" ? "approval" : sc.map.mode === "conflict" ? "conflict" : "unrest"; stopCalls(); callRound = -1; renderPinned(sc.round); }
    document.querySelectorAll("#viewToggle button").forEach(b => b.classList.toggle("active", b.dataset.view === view));
    if (sc.map.focus) panToState(sc.map.focus); else if (sc.type !== "night") { try { svg.transition().duration(600).call(zoom.transform, d3.zoomIdentity); } catch (e) {} }
  }
  // feed
  if (sc.feed && sc.feed.length) renderEpFeed(sc.feed, sc.round); else if (sc.type === "night") renderEpFeed([], sc.round);
  // tape + markets pinned to the scene's round (the full market history is loaded when the episode opens)
  if (marketCache && marketCache.history && marketCache.history.length) { renderTape(); renderMarkets(); }
  else if (sc.market && sc.market.entry) renderEpTape(sc.market);
  if (run.mode === "govern" && typeof renderTerm === "function") renderTerm();
  // player screen
  const dl = EP.dates && EP.dates[sc.round];
  $("epClock").classList.toggle("hidden", !(sc.clock || dl)); if (sc.clock) $("epClock").textContent = sc.clock; else if (dl) $("epClock").textContent = dl;
  if (dl) { $("epEra").textContent = `${dl.replace(/^(\w+) \d+, /, "$1 ")}${EP.ep.era ? " · " + EP.ep.era : ""}`; $("epEra").classList.remove("hidden"); }
  renderEpScreen(sc);
  // narration: audio length drives the scene
  const n = sc.narration;
  if (n && n.audio) {
    const a = new Audio(n.audio); EP.audio = a; a.onended = () => { if (token === EP.token) epAdvance(); }; a.onerror = () => { if (token === EP.token) epAdvance(); };
    a.play().catch(() => { if (token === EP.token) setTimeout(() => epAdvance(), (sc.duration || 6) * 1000); });
  } else if (n && n.text && browserVoiceOn()) {
    const u = new SpeechSynthesisUtterance(n.text); u.voice = pickVoice(n.voice === "anchor_world" ? /GB|UK|British|Hazel|George/i : n.voice === "anchor_a" ? /female|zira|aria|jenny/i : /male|david|guy|mark/i);
    u.onend = () => { if (token === EP.token) epAdvance(); }; window.speechSynthesis.speak(u);
  } else {
    EP.clockTimer = setTimeout(() => { if (token === EP.token) epAdvance(); }, (sc.duration || 6) * 1000);
  }
  if (sc.type === "debate") playDebateScene(sc, token);
  if (sc.type === "night") animateNight(sc, token);
  if (sc.type === "bloc_night") animateBlocNight(sc, token);
}

function renderPinned(rnum) {
  // use app.js renderers but with the round pinned (and no auto-calls on election night rounds)
  const rd = run.rounds.find(r => r.round === rnum); if (!rd) return;
  if (view === "election" && run.election) { renderElectionMap({...rd, election: {...(rd.election || {}), results: null}}); renderEvBarPolls(rd); }
  else { window.epDriven = true; try { render(); } finally { window.epDriven = false; } }
}
// the viewer flipped the Unrest / Election / Approval toggle during an episode: redraw the current scene's map in that view
window.epRedrawMap = () => {
  const sc = EP.scene; if (!sc || !sc.map) return;
  if (sc.map.mode === "night") { view = "election"; document.querySelectorAll("#viewToggle button").forEach(b => b.classList.toggle("active", b.dataset.view === view)); return; }
  renderPinned(sc.round);
};
function renderEvBarPolls(rd) { calledSet = new Set(); renderEvBar({...rd, election: {...(rd.election || {}), results: null}}); }

// ---------- feed / tape / screen ----------
function renderEpFeed(items, rnum) {
  // the viewer keeps whatever tab they opened (markets, a state, blocs, the term); the Feed updates underneath
  $("trending").innerHTML = `<div class="muted small">day ${rnum}</div>`;
  $("feed").innerHTML = items.map(it => {
    const vb = it.verified && it.verified !== "none" ? `<span class="vb ${it.verified}"></span>` : "";
    let media = "";
    if (it.media && it.media.status !== "pending") media = it.media.type === "video" ? `<div class="media"><video src="${it.media.url}" muted autoplay loop playsinline></video></div>`
      : it.media.type === "audio" ? "" : `<div class="media"><img src="${it.media.url}" alt=""></div>`;
    const txt = esc(it.text).replace(/#([A-Za-z0-9_]+)/g, '<span class="tag">#$1</span>');
    return `<div class="fi ${it.kind} ep-in"><img class="av" src="${it.avatar ? esc(it.avatar) : "/api/avatars/" + esc(it.handle) + ".png"}" alt=""><div><div class="hd"><span class="name">${esc(it.display)}</span>${vb}<span class="handle">@${esc(it.handle)}</span></div><div class="txt">${txt}</div>${it.quote_text ? `<div class="quote">${esc(it.quote_text.slice(0, 200))}</div>` : ""}${media}<div class="ft"><span>💬 ${fmtN(it.replies)}</span><span>🔁 ${fmtN(it.reposts)}</span><span>♥ ${fmtN(it.likes)}</span>${it.state ? `<span class="flag" data-code="${it.state}">${it.state}</span>` : ""}</div></div></div>`;
  }).join("") || '<div class="muted small">quiet</div>';
  // stagger them in
  [...$("feed").querySelectorAll(".fi")].forEach((el, i) => { el.style.animationDelay = (i * 0.35) + "s"; });
}
function renderEpTape(m) {
  const h = m.entry; const all = [...(m.macro || []), ...(m.tickers || [])];
  $("tape").innerHTML = all.map(t => { const p = h.pct[t.sym] || 0; return `<span class="sym">${t.sym}</span><span class="px">${fmtPx(t.sym, h.prices[t.sym])}</span><span class="${p >= 0 ? "up" : "dn"}">${p >= 0 ? "▲" : "▼"} ${fmtChg(t.sym, p)}</span>`; }).join("") + `<span class="sym">·</span><span class="px">${esc((h.headlines || []).join("  ·  "))}</span>`;
  if (!marketCache || !(marketCache.history || []).length) marketCache = {history: [], tickers: m.tickers, macro: m.macro};
}
function renderEpScreen(sc) {
  const inset = $("epInset"); inset.classList.remove("hidden");
  $("epStage").classList.add("hidden");
  const img = $("epImg");
  if (sc.type === "protest") { img.src = sc.media.image; $("epReporter").src = sc.media.reporter; $("epReporter").classList.remove("hidden"); }
  else if (sc.type === "candidate" || sc.type === "victory") { img.src = sc.media.image; $("epReporter").classList.add("hidden"); }
  else if (sc.type === "warroom" || sc.type === "bloc_night") { inset.classList.add("hidden"); }
  else if (sc.type === "debate") { inset.classList.add("hidden"); $("epStage").classList.remove("hidden"); }
  else if (sc.anchor) { img.src = sc.anchor.image; $("epReporter").classList.add("hidden"); }
  else inset.classList.add("hidden");
  const ch = sc.chyron || {}; $("epHead").textContent = ch.head || sc.title || ""; $("epSub").textContent = ch.sub || "";
  $("epNet").textContent = sc.anchor ? sc.anchor.network_name : sc.type === "debate" ? "FLN DEBATE" : "FLN";
  $("epCaption").textContent = sc.narration ? sc.narration.text : "";
}

// ---------- debate ----------
function playDebateScene(sc, token) {
  const d = sc.debate; const names = d.candidates || {}; const ids = Object.keys(names);
  const colors = {}; (EP.ep.options || []).forEach(o => { colors[o.id] = o.color; });
  buildPods($("epPods"), names, d.stage_image);
  const share = {}; ids.forEach(id => { share[id] = 100 / Math.max(1, ids.length); });   // instant poll, all candidates
  const drawMeter = () => {
    $("epMeter").innerHTML = ids.map(id => `<i style="width:${share[id]}%;background:${colors[id] || "#8893a2"}"><b>${esc((names[id] || {}).name || id)} ${Math.round(share[id])}</b></i>`).join("");
    $("epMeterLbl").textContent = "instant poll";
  };
  drawMeter();
  const feed = []; renderEpFeed([], sc.round);
  let i = 0;
  const step = () => {
    if (token !== EP.token) return;
    if (i >= d.lines.length) { if (EP.playing) epNext(); return; }
    const l = d.lines[i];
    lightPod($("epPods"), l.speaker);
    const who = l.speaker === "mod" ? d.moderator : (names[l.speaker] || {}).name;
    $("epHead").textContent = (l.speaker === "mod" ? "MODERATOR · " : "") + (who || "").toUpperCase(); $("epSub").textContent = l.text.slice(0, 95); $("epCaption").textContent = l.text;
    if (l.poll_shift_for && l.poll_shift && share[l.poll_shift_for] != null && ids.length > 1) {
      const gain = Math.min(l.poll_shift * 1.6, 90 - share[l.poll_shift_for]); const others = ids.filter(x => x !== l.poll_shift_for);
      others.forEach(x => { share[x] = Math.max(5, share[x] - gain / others.length); });
      share[l.poll_shift_for] = 100 - others.reduce((a, x) => a + share[x], 0);
      drawMeter();
    }
    (d.reactions || []).filter(r => r.after_line === i).forEach(r => { feed.unshift({handle: r.handle, display: r.display, verified: r.verified, text: r.text, kind: "reply", likes: 200 + Math.floor(Math.random() * 3000), reposts: 40 + Math.floor(Math.random() * 900), replies: 10 + Math.floor(Math.random() * 300), ts: ""}); });
    renderEpFeed(feed.slice(0, 8), sc.round);
    const done = () => { i++; step(); };
    if (l.audio) { const a = new Audio(l.audio); EP.audio = a; a.onended = done; a.onerror = done; a.play().catch(done); }
    else if (browserVoiceOn()) { const u = new SpeechSynthesisUtterance(l.text); u.voice = pickVoice(ids.indexOf(l.speaker) % 2 === 0 ? /female|zira|aria/i : /male|david|mark/i); u.onend = done; window.speechSynthesis.speak(u); }
    else setTimeout(done, (l.seconds || readSecs(l.text)) * 1000);
  };
  step();
}

// ---------- a bloc's own election night: members light up as they are called, spoiler-safe ----------
function animateBlocNight(sc, token) {
  const bn = sc.bloc_night; if (!bn) return;
  const colors = {}; bn.candidates.forEach(c => { colors[c.id] = c.color; });
  const ticks = bn.ticks || []; const total = Math.max(4, sc.duration || 12); const per = (total * 1000) / Math.max(1, ticks.length);
  const paint = called => gStates.selectAll("path.state").style("fill", d => { const code = NAME2CODE[d.properties.name];
    if (!bn.members.includes(code)) return "#2b3442";
    const u = bn.units[code]; return called.includes(code) && u ? colors[u.vote] : "#5a4a3a"; });
  paint([]);
  ticks.forEach((t, i) => setTimeout(() => { if (token !== EP.token) return; paint(t.called); $("epSub").textContent = t.label; }, i * per));
}

// ---------- election night map ----------
function nightColor(opt, shares) {
  const o = (EP.ep.options || []).find(x => x.id === opt); if (!o) return "#3a4d63";
  const lead = Math.max(...Object.values(shares)); const second = Math.max(...Object.values(shares).filter(v => v !== lead), 0);
  const t = Math.max(0.3, Math.min(1, 0.3 + (lead - second) / 30));
  return d3.interpolateLab("#4b5566", o.color)(t);
}
function renderNightMap(tick, tickIdx) {
  const opts = EP.ep.options || [];
  const cnt = tick ? tick.counts : {};
  gStates.selectAll("path.state").each(function (d) {
    const code = NAME2CODE[d.properties.name]; const el = d3.select(this);
    el.classed("called", false).classed("too-close", false).classed("counting", false).style("stroke", null).style("stroke-width", null).style("transform", "translate(0,0)");
    if (!code) return;
    const c = cnt[code];
    if (!c || c.pct_in === 0) { el.style("fill", "#2b3442"); return; }
    const lead = Object.keys(c.shares).reduce((a, b) => c.shares[a] >= c.shares[b] ? a : b);
    if (c.status === "called") el.style("fill", nightColor(c.vote, c.shares)).classed("called", true);
    else { el.style("fill", d3.interpolateLab("#2b3442", nightColor(lead, c.shares))(0.45)).classed("counting", true).classed("too-close", c.status === "too_close"); }
  });
  gCracks.selectAll("g.crackg").style("display", "none"); gLabels.selectAll("text.bloc-label").style("display", "none");
  // EV bar from the tick only
  const bar = $("evBar"); bar.classList.remove("hidden");
  const A = opts.find(o => o.lane === "right") || opts[0]; const B = opts.find(o => o !== A) || opts[1];
  $("evNameA").textContent = A.name; $("evNameB").textContent = B.name; $("evToWin").textContent = "270 to win";
  $("evFillA").style.background = A.color; $("evFillB").style.background = B.color;
  const ev = tick ? tick.ev : {}; const a = ev[A.id] || 0, b = ev[B.id] || 0; const others = opts.filter(o => o !== A && o !== B).map(o => `${o.name.split(" ").pop()} ${ev[o.id] || 0}`).join(" · ");
  $("evFillA").style.width = (100 * a / 538) + "%"; $("evFillB").style.width = (100 * b / 538) + "%";
  $("evNumA").textContent = a; $("evNumB").textContent = b;
  const tcc = tick ? tick.too_close.length : 0; const called = tick ? tick.called_total : 0;
  $("evStatus").textContent = tick ? `${tick.time} · ${called}/56 called${tcc ? ` · ${tcc} too close` : ""}${others ? " · " + others : ""}` : "polls closing soon";
  const toast = $("callToast");
  if (tick && (tick.new_calls.length || tick.winner)) {
    const names = Object.fromEntries(opts.map(o => [o.id, o.name]));
    toast.classList.remove("hidden");
    if (tick.winner) { toast.textContent = `FLN PROJECTS: ${names[tick.winner]} ELECTED PRESIDENT`; toast.style.borderLeftColor = (opts.find(o => o.id === tick.winner) || {}).color; }
    else { const u = tick.new_calls[0]; toast.textContent = `${tick.time} · FLN PROJECTS: ${(featureByCode[u] || {properties: {name: u}}).properties.name} → ${names[tick.counts[u].vote]}` + (tick.new_calls.length > 1 ? ` (+${tick.new_calls.length - 1} more)` : ""); toast.style.borderLeftColor = (opts.find(o => o.id === tick.counts[u].vote) || {}).color; }
  } else toast.classList.add("hidden");
}
function animateNight(sc, token) {
  // the count creeps within the scene: interpolate pct_in display from the previous tick's values
  const tick = sc.night; if (!tick) return;
  const prevSc = EP.ep.scenes[EP.ep.order[EP.idx - 1]]; const prev = prevSc && prevSc.night ? prevSc.night.counts : {};
  const dur = Math.max(1.5, (sc.duration || 3) - 0.5) * 1000; const t0 = performance.now();
  const frame = () => {
    if (token !== EP.token) return;
    const t = Math.min(1, (performance.now() - t0) / dur);
    const counts = {};
    for (const [u, c] of Object.entries(tick.counts)) { const p = prev[u]; counts[u] = {...c, pct_in: p ? Math.round(p.pct_in + (c.pct_in - p.pct_in) * t) : c.pct_in}; }
    renderNightMap({...tick, counts}, sc.map.tick);
    renderNightList({...tick, counts});
    if (t < 1) requestAnimationFrame(frame);
  };
  requestAnimationFrame(frame);
}
function renderNightList(tick) {
  const names = Object.fromEntries((EP.ep.options || []).map(o => [o.id, o]));
  const rows = Object.entries(tick.counts).filter(([u, c]) => c.pct_in > 0).sort((a, b) => (b[1].status === "called") - (a[1].status === "called") || b[1].pct_in - a[1].pct_in).slice(0, 14);
  $("trending").innerHTML = `<div class="small muted">${tick.time} · ${tick.label}</div>` + rows.map(([u, c]) => {
    const lead = Object.keys(c.shares).reduce((a, b) => c.shares[a] >= c.shares[b] ? a : b);
    const col = (names[c.vote || lead] || {}).color || "#888";
    return `<div class="nrow"><b style="color:${col}">${u}</b><span class="muted">${c.pct_in}% in</span><span>${Object.entries(c.shares).map(([k, v]) => `<i style="color:${(names[k] || {}).color}">${v.toFixed(0)}</i>`).join(" · ")}</span><span class="st ${c.status}">${c.status === "called" ? "✔ " + (names[c.vote] || {}).name.split(" ").pop() : c.status === "too_close" ? "too close" : "counting"}</span></div>`;
  }).join("");
}

// ---------- button in the main UI ----------
$("epBtn").onclick = openEpisode;
