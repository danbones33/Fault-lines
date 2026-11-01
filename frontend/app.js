/* FAULT LINES map + Feed + broadcast UI. Plain JS + d3 + topojson, no build step. */
const NAME2CODE = {"Alabama":"AL","Alaska":"AK","Arizona":"AZ","Arkansas":"AR","California":"CA","Colorado":"CO","Connecticut":"CT","Delaware":"DE","Florida":"FL","Georgia":"GA","Hawaii":"HI","Idaho":"ID","Illinois":"IL","Indiana":"IN","Iowa":"IA","Kansas":"KS","Kentucky":"KY","Louisiana":"LA","Maine":"ME","Maryland":"MD","Massachusetts":"MA","Michigan":"MI","Minnesota":"MN","Mississippi":"MS","Missouri":"MO","Montana":"MT","Nebraska":"NE","Nevada":"NV","New Hampshire":"NH","New Jersey":"NJ","New Mexico":"NM","New York":"NY","North Carolina":"NC","North Dakota":"ND","Ohio":"OH","Oklahoma":"OK","Oregon":"OR","Pennsylvania":"PA","Rhode Island":"RI","South Carolina":"SC","South Dakota":"SD","Tennessee":"TN","Texas":"TX","Utah":"UT","Vermont":"VT","Virginia":"VA","Washington":"WA","West Virginia":"WV","Wisconsin":"WI","Wyoming":"WY"};
const STAGE_NAMES = ["calm","petition","protest","strike","riot"];
const STAGE_COLORS = ["#2b3a4a","#5b6f6a","#c98a3a","#d94f3d","#7a0f1a"];
const unrestColor = d3.scaleLinear().domain([0,20,40,60,80,100])
  .range(["#3a4d63","#4b6478","#8a9a74","#c98a3a","#d94f3d","#7a0f1a"]).interpolate(d3.interpolateLab);

const $ = id => document.getElementById(id);
let topo, features, path, projection, svg, root, gStates, gCracks, gLabels, zoom, featureByCode = {};
let run = null, runId = null, roundIdx = 0, playing = null, selected = null, pollTimer = null, follow = true, config = null;
let roster = {}, feedCache = null, feedRound = -1, bcNet = 0, speaking = null;

async function api(url, opts) {
  const r = await fetch(url, opts);
  if (!r.ok) throw new Error((await r.json().catch(()=>({detail:r.statusText}))).detail || r.statusText);
  return r.json();
}
function esc(s) { return String(s ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c])); }
function fmtN(n) { n = +n || 0; return n >= 1e6 ? (n/1e6).toFixed(1)+"M" : n >= 1e3 ? (n/1e3).toFixed(1)+"K" : String(n); }

// ---------- map ----------
async function initMap() {
  topo = await (await fetch("/static/vendor/us-states-10m.json")).json();
  features = topojson.feature(topo, topo.objects.states).features;
  features.forEach(f => { const c = NAME2CODE[f.properties.name]; if (c) featureByCode[c] = f; });
  svg = d3.select("#map");
  const defs = svg.append("defs");
  const f = defs.append("filter").attr("id","jag").attr("x","-10%").attr("y","-10%").attr("width","120%").attr("height","120%");
  f.append("feTurbulence").attr("type","turbulence").attr("baseFrequency","0.035").attr("numOctaves","2").attr("seed","3").attr("result","noise");
  f.append("feDisplacementMap").attr("in","SourceGraphic").attr("in2","noise").attr("scale","7").attr("xChannelSelector","R").attr("yChannelSelector","G");
  root = svg.append("g").attr("id","root");
  gStates = root.append("g").attr("id","states"); gCracks = root.append("g").attr("id","cracks"); gLabels = root.append("g").attr("id","labels");
  window.gWar = root.append("g").attr("id","war");
  defs.append("marker").attr("id", "arrow").attr("viewBox", "0 0 10 10").attr("refX", 9).attr("refY", 5).attr("markerWidth", 7).attr("markerHeight", 7).attr("orient", "auto-start-reverse")
    .append("path").attr("d", "M0,0 L10,5 L0,10 z").attr("fill", "#ff5c5c");
  window.warDefs = defs;
  zoom = d3.zoom().scaleExtent([0.8, 8]).on("zoom", e => root.attr("transform", e.transform));
  svg.call(zoom);
  window.addEventListener("resize", layout);
  layout();
}
function layout() {
  const box = $("map").getBoundingClientRect();
  projection = d3.geoAlbersUsa().fitExtent([[20,10],[box.width-20, box.height-10]], {type:"FeatureCollection", features});
  path = d3.geoPath(projection);
  const sel = gStates.selectAll("path.state").data(features, d => d.id);
  sel.enter().append("path").attr("class","state")
    .on("mousemove", (e,d) => showTip(e,d)).on("mouseleave", hideTip)
    .on("click", (e,d) => selectState(NAME2CODE[d.properties.name]))
    .merge(sel).attr("d", path);
  render();
}
function panToState(code) {
  const f = featureByCode[code]; if (!f) return;
  const [x, y] = path.centroid(f); const box = $("map").getBoundingClientRect();
  svg.transition().duration(900).call(zoom.transform, d3.zoomIdentity.translate(box.width/2, box.height/2 - 60).scale(2.2).translate(-x, -y));
  selectState(code);
}
function currentRound() { return run && run.rounds.length ? run.rounds[Math.min(roundIdx, run.rounds.length-1)] : null; }

// ---------- election view ----------
let view = "unrest", callTimer = null, calledSet = new Set(), callRound = -1;
// presidential approval per state (governing seasons): red when the state has turned on the President, green when it backs them
const approvalColor = d3.scaleLinear().domain([20, 38, 50, 62, 80]).range(["#b3261e", "#c9663a", "#6b7280", "#4f9a6a", "#2e8b57"]).clamp(true);
const LANE_COLOR = {right: "#c0392b", left: "#2f6fd6", center: "#8e5bd6", other: "#8893a2"};
const crimeColor = d3.scaleLinear().domain([10, 30, 45, 60, 80]).range(["#1f2a22", "#4a5a2e", "#b8862b", "#d9532b", "#8f0f2a"]).clamp(true);
function crimeAt(rd) { return (rd && rd.crime) || (run && run.crime) || null; }
function approvalAt(rd) {
  if (!run || run.mode !== "govern") return null;
  if (rd && rd.quarter && rd.quarter.approval) return rd.quarter.approval;
  // rounds before the first quarter (the seed) and election night show the latest known approval
  for (let i = Math.min(roundIdx, run.rounds.length - 1); i >= 0; i--) { const q = run.rounds[i].quarter; if (q && q.approval) return q.approval; }
  return run.govern && run.govern.approval ? run.govern.approval : null;
}
function electionColor(optId, margin) {
  const o = run.election.options.find(o => o.id === optId); if (!o) return "#3a4d63";
  const t = Math.max(0.25, Math.min(1, 0.25 + (margin || 0) / 45));
  return d3.interpolateLab("#4b5566", o.color)(t);
}
function stopCalls() { if (callTimer) { clearTimeout(callTimer); callTimer = null; } $("callToast").classList.add("hidden"); }
function startCalls(rd) {
  // Election Night: states get called one poll-closing group at a time; a click on the toast skips to the end
  const res = rd.election.results; stopCalls(); calledSet = new Set(); callRound = rd.round;
  const order = res.called_order.filter(c => c.vote); let i = 0;
  const names = Object.fromEntries(run.election.options.map(o => [o.id, o.name]));
  const step = () => {
    if (i >= order.length) { finishCalls(res, names); return; }
    const c = order[i++]; calledSet.add(c.code);
    const t = $("callToast"); t.classList.remove("hidden"); t.style.borderLeftColor = electionColor(c.vote, 45);
    t.textContent = `${c.slot} · FLN PROJECTS: ${(featureByCode[c.code]||{properties:{name:c.code}}).properties.name} → ${names[c.vote]}${c.too_close ? " (too close to call, leaning)" : ""}`;
    renderElectionMap(rd); renderEvBar(rd);
    callTimer = setTimeout(step, c.too_close ? 1400 : 700);
  };
  callTimer = setTimeout(step, 600);
  $("callToast").onclick = () => { order.forEach(c => calledSet.add(c.code)); finishCalls(res, names); };
}
function finishCalls(res, names) {
  stopCalls(); const t = $("callToast"); t.classList.remove("hidden");
  t.textContent = res.winner ? `FLN PROJECTS: ${names[res.winner]} WINS · ${res.ev[res.winner]} ELECTORAL VOTES` : "NO MAJORITY · CONTINGENT ELECTION";
  t.style.borderLeftColor = res.winner ? electionColor(res.winner, 45) : "#e0a63a";
  renderElectionMap(currentRound()); renderEvBar(currentRound());
}
function renderElectionMap(rd) {
  const er = rd.election || {}; const res = er.results; const polls = er.polls || {};
  gStates.selectAll("path.state").each(function(d) {
    const code = NAME2CODE[d.properties.name]; const el = d3.select(this);
    el.classed("called", false).classed("too-close", false).style("stroke", null).style("stroke-width", null).style("transform", "translate(0,0)");
    if (!code) return;
    if (res) {
      const s = res.states[code];
      if (calledSet.has(code) && s.vote) { el.style("fill", electionColor(s.vote, s.margin)).classed("called", true).classed("too-close", s.margin < 5); }
      else el.style("fill", "#2b3442");
    } else {
      const p = polls[code];
      el.style("fill", p ? electionColor(p.lean, p.margin) : "#2b3442");
    }
  });
  gCracks.selectAll("g.crackg").style("display", "none"); gLabels.selectAll("text.bloc-label").style("display", "none");
}
function renderEvBar(rd) {
  const bar = $("evBar"); if (view !== "election" || !run || !run.election) { bar.classList.add("hidden"); return; }
  bar.classList.remove("hidden");
  // red (right lane) on the left of the bar, blue on the right, the way election graphics are read
  const opts = run.election.options; const A = opts.find(o => o.lane === "right") || opts[0]; const B = opts.find(o => o !== A) || opts[1];
  const er = rd.election || {}; const res = er.results;
  $("evNameA").textContent = A.name; $("evNameB").textContent = B.name; $("evToWin").textContent = `${res ? res.to_win : 268} to win`;
  $("evFillA").style.background = A.color; $("evFillB").style.background = B.color;
  let a = 0, b = 0, status = "";
  if (res) {
    for (const c of calledSet) { const s = res.states[c]; if (s.vote === A.id) a += s.ev; else if (s.vote === B.id) b += s.ev; }
    status = calledSet.size < 50 ? `${calledSet.size}/50 called` : (res.winner ? `${(run.election.options.find(o=>o.id===res.winner)||{}).name} wins · popular ${res.popular[A.id]}% / ${res.popular[B.id]}%` : "no majority");
  } else {
    const polls = er.polls || {}; let na = 0, nb = 0;
    for (const [c, p] of Object.entries(polls)) { const ev = EV_TABLE[c] || 0; if (p.lean === A.id) { a += ev; na++; } else if (p.lean === B.id) { b += ev; nb++; } }
    status = `polling · ${na} states lean ${A.name.split(" ").pop()}, ${nb} lean ${B.name.split(" ").pop()}, ${50 - na - nb} undecided`;
  }
  const tot = res ? res.total_ev : 535;
  $("evFillA").style.width = (100 * a / tot) + "%"; $("evFillB").style.width = (100 * b / tot) + "%";
  $("evNumA").textContent = a; $("evNumB").textContent = b; $("evStatus").textContent = status;
}
const EV_TABLE = {AL:9,AK:3,AZ:11,AR:6,CA:54,CO:10,CT:7,DE:3,FL:30,GA:16,HI:4,ID:4,IL:19,IN:11,IA:6,KS:6,KY:8,LA:8,ME:4,MD:10,MA:11,MI:15,MN:10,MS:6,MO:10,MT:4,NE:5,NV:6,NH:4,NJ:14,NM:5,NY:28,NC:16,ND:3,OH:17,OK:7,OR:8,PA:19,RI:4,SC:9,SD:3,TN:11,TX:40,UT:6,VT:3,VA:13,WA:12,WV:4,WI:10,WY:3};
function setView(v) {
  view = v; document.querySelectorAll("#viewToggle button").forEach(b => b.classList.toggle("active", b.dataset.view === v));
  stopCalls(); callRound = -1; if (document.body.classList.contains("episode")) { if (window.epRedrawMap) window.epRedrawMap(); } else render();
}
document.querySelectorAll("#viewToggle button").forEach(b => b.onclick = () => setView(b.dataset.view));

function render() {
  // while an episode plays, only the episode redraws the map (a background poll must not start the live election-night calls)
  if (document.body.classList.contains("episode") && !window.epDriven) return;
  const rd = currentRound(); const states = rd ? rd.states : {}; const blocs = rd ? rd.blocs : [];
  $("viewToggle").classList.toggle("hidden", !(run && (run.election || run.mode === "govern")));
  document.querySelector('#viewToggle [data-view="approval"]').classList.toggle("hidden", !(run && run.mode === "govern"));
  $("termTab").classList.toggle("hidden", !(run && (run.mode === "govern" || run.tech || run.crime)));
  if (run && (run.mode === "govern" || run.tech || run.crime)) renderTerm();
  const appr = view === "approval" ? approvalAt(rd) : null;
  if (run && run.election && view === "election" && rd) {
    if (rd.election && rd.election.results) { if (callRound !== rd.round) startCalls(rd); else renderElectionMap(rd); }
    else { stopCalls(); callRound = -1; renderElectionMap(rd); }
    renderEvBar(rd); renderBlocs(blocs); renderRoundLabel(); renderTicker(); renderTape(); renderMarkets(); if (selected) renderDetailScores();
    if (rd.round !== feedRound && !document.body.classList.contains("episode")) loadFeed();
    return;
  }
  stopCalls(); $("evBar").classList.add("hidden");
  const cf = conflictAt(rd);
  const cfBtn = document.querySelector('#viewToggle [data-view="conflict"]');
  if (cfBtn) cfBtn.classList.toggle("hidden", !(run && run.conflict && run.conflict.active));
  const crBtn = document.querySelector('#viewToggle [data-view="crime"]');
  if (crBtn) crBtn.classList.toggle("hidden", !(run && run.crime && run.crime.active));
  const crm = view === "crime" ? crimeAt(rd) : null;
  if (crm) {
    renderCrimeMap(rd, crm); renderBlocs(blocs); renderRoundLabel(); renderTicker(); renderTape(); renderMarkets(); if (selected) renderDetailScores();
    if (rd && rd.round !== feedRound && !document.body.classList.contains("episode")) loadFeed();
    return;
  }
  gStates.selectAll("path.state").classed("territory", false);
  if (view === "conflict" && cf) {
    renderConflictMap(rd, cf); renderBlocs(blocs); renderRoundLabel(); renderTicker(); renderTape(); renderMarkets(); if (selected) renderDetailScores();
    if (rd && rd.round !== feedRound && !document.body.classList.contains("episode")) loadFeed();
    return;
  }
  window.gWar.selectAll("*").remove(); $("warRoom").classList.add("hidden"); gStates.selectAll("path.state").classed("contested", false);
  gCracks.selectAll("g.crackg").style("display", null); gLabels.selectAll("text.bloc-label").style("display", null);
  const blocOf = {}; blocs.forEach(b => b.members.forEach(m => blocOf[m] = b));
  const usCenter = projection([-98.5, 39.8]); const offsets = {};
  blocs.forEach(b => {
    const geoms = topo.objects.states.geometries.filter(g => b.members.includes(NAME2CODE[g.properties.name]));
    const merged = topojson.merge(topo, geoms); const c = path.centroid(merged);
    let dx = c[0]-usCenter[0], dy = c[1]-usCenter[1]; const len = Math.hypot(dx,dy) || 1; const k = 16;
    offsets[b.id] = {dx: dx/len*k, dy: dy/len*k, merged, c};
  });
  gStates.selectAll("path.state")
    .attr("class", d => { const code = NAME2CODE[d.properties.name]; return "state" + (code? "" : " nodata") + (code===selected?" selected":""); })
    .style("fill", d => { const code = NAME2CODE[d.properties.name]; const s = states[code];
      if (appr) { const a = appr.states && appr.states[code]; return a == null ? "#2b3442" : approvalColor(a); }
      return s ? unrestColor(s.unrest) : null; })
    .style("stroke", d => { const b = blocOf[NAME2CODE[d.properties.name]]; return b ? b.color : null; })
    .style("stroke-width", d => blocOf[NAME2CODE[d.properties.name]] ? 1.4 : null)
    .style("transform", d => { const b = blocOf[NAME2CODE[d.properties.name]]; return b ? `translate(${offsets[b.id].dx}px,${offsets[b.id].dy}px)` : "translate(0,0)"; });
  const cr = gCracks.selectAll("g.crackg").data(blocs, b => b.id);
  const ce = cr.enter().append("g").attr("class","crackg"); ce.append("path").attr("class","crack-glow"); ce.append("path").attr("class","crack");
  const all = ce.merge(cr);
  all.select("path.crack-glow").attr("d", b => path(offsets[b.id].merged)).style("stroke", b => b.color).style("transform", b => `translate(${offsets[b.id].dx}px,${offsets[b.id].dy}px)`);
  all.select("path.crack").attr("d", b => path(offsets[b.id].merged)).style("transform", b => `translate(${offsets[b.id].dx}px,${offsets[b.id].dy}px)`);
  cr.exit().remove();
  const lb = gLabels.selectAll("text.bloc-label").data(blocs, b => b.id);
  lb.enter().append("text").attr("class","bloc-label").merge(lb).attr("x", b => offsets[b.id].c[0]).attr("y", b => offsets[b.id].c[1])
    .style("transform", b => `translate(${offsets[b.id].dx}px,${offsets[b.id].dy}px)`).text(b => b.name.toUpperCase());
  lb.exit().remove();
  renderBlocs(blocs); renderRoundLabel(); renderTicker(); renderTape(); renderMarkets(); if (selected) renderDetailScores();
  if (rd && rd.round !== feedRound && !document.body.classList.contains("episode")) loadFeed();   // episode mode: the script feeds the Feed
}
function showTip(e, d) {
  const code = NAME2CODE[d.properties.name]; const rd = currentRound(); const s = rd && rd.states[code];
  const t = $("tooltip"); t.classList.remove("hidden"); const box = $("map").getBoundingClientRect();
  t.style.left = (e.clientX - box.left + 14) + "px"; t.style.top = (e.clientY - box.top + 14) + "px";
  const crs = view === "crime" ? crimeAt(rd) : null;
  if (crs && crs.states[code]) {
    const cs = crs.states[code]; const sy = (crs.syndicates || []).filter(x => !x.gone && (x.territory[code] || 0) >= 30).map(x => `${x.name} ${Math.round(x.territory[code])}`);
    t.innerHTML = `<b>${d.properties.name}</b> · crime ${Math.round(cs.index)}<br>${Object.entries(cs.types).sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, v]) => k + " " + Math.round(v)).join(" · ")}${Object.keys(cs.era || {}).length ? "<br>era: " + Object.keys(cs.era).join(", ") : ""}${sy.length ? "<br>syndicate: " + esc(sy.join(", ")) : ""}${cs.drivers && cs.drivers.length ? "<br><i>" + esc(cs.drivers.slice(0, 2).map(x => x[0] + " " + (x[1] > 0 ? "+" : "") + x[1]).join(", ")) + "</i>" : ""}`;
    return;
  }
  const cfb = view === "conflict" ? primaryBloc(conflictAt(rd)) : null;
  if (cfb) {
    const rec = (cfb.recognition || {})[code] || "federal"; const ctl = (cfb.control || {})[code]; const g = (cfb.guard || {})[code];
    t.innerHTML = `<b>${d.properties.name}</b> · ${cfb.members.includes(code) ? "member of " + esc(cfb.name) : "recognizes: " + rec}${ctl != null ? `<br>bloc control ${Math.round(ctl)}%` : ""}${g != null ? `<br>Guard loyalty to Washington ${Math.round(g)}` : ""}${s ? `<br>unrest ${s.unrest} · cohesion ${s.federal_cohesion}` : ""}`;
    return;
  }
  if (view === "election" && run && run.election && rd && rd.election) {
    const names = Object.fromEntries(run.election.options.map(o => [o.id, o.name])); const res = rd.election.results;
    if (res && res.states[code]) { const b = res.states[code]; t.innerHTML = `<b>${d.properties.name}</b> · ${EV_TABLE[code]} EV<br>${calledSet.has(code) ? `${esc(names[b.vote]||"no vote")} by ${b.margin} · turnout ${b.turnout}%<br><i>${esc(b.reason)}</i>` : "polls not yet called"}`; }
    else { const p = (rd.election.polls||{})[code]; t.innerHTML = `<b>${d.properties.name}</b> · ${EV_TABLE[code]} EV<br>${p ? `leaning ${esc(names[p.lean]||p.lean)} (+${p.margin})` : "undecided"}`; }
    return;
  }
  t.innerHTML = s ? `<b>${d.properties.name}</b> · ${s.stage_name}<br>unrest ${s.unrest} · federal cohesion ${s.federal_cohesion}${s.bloc?`<br><i>${rd.blocs.find(b=>b.id===s.bloc)?.name}</i>`:""}` : `<b>${d.properties.name}</b>`;
}
function hideTip() { $("tooltip").classList.add("hidden"); }

// ---------- markets ----------
let marketCache = null;
function fmtPx(sym, v) { return sym === "US10Y" ? v.toFixed(2) + "%" : v >= 1000 ? Math.round(v).toLocaleString() : v.toFixed(2); }
function fmtChg(sym, p) { return sym === "US10Y" ? `${p >= 0 ? "+" : ""}${p.toFixed(0)}bp` : `${p >= 0 ? "+" : ""}${p.toFixed(1)}%`; }
function marketAt(rd) {
  if (!marketCache || !rd) return null;
  let h = null; for (const e of marketCache.history) { if (e.round <= rd.round) h = e; }
  return h;
}
function renderTape() {
  const rd = currentRound(); const h = marketAt(rd); const el = $("tape");
  if (!h) { el.innerHTML = "FLN MARKETS · no data yet"; return; }
  const all = [...marketCache.macro, ...marketCache.tickers];
  el.innerHTML = all.map(t => { const p = h.pct[t.sym] || 0; return `<span class="sym">${t.sym}</span><span class="px">${fmtPx(t.sym, h.prices[t.sym])}</span><span class="${p >= 0 ? "up" : "dn"}">${p >= 0 ? "▲" : "▼"} ${fmtChg(t.sym, p)}</span>`; }).join("")
    + `<span class="sym">·</span><span class="px">${esc((h.headlines||[]).join("  ·  "))}</span>`;
}
function spark(series, w, hgt, color) {
  if (series.length < 2) return "";
  const mn = Math.min(...series), mx = Math.max(...series), rng = (mx - mn) || 1;
  const pts = series.map((v, i) => `${(i / (series.length - 1) * w).toFixed(1)},${(hgt - 3 - (v - mn) / rng * (hgt - 6)).toFixed(1)}`).join(" ");
  return `<polyline fill="none" stroke="${color}" stroke-width="1.6" points="${pts}"/>`;
}
function renderMarkets() {
  const rd = currentRound(); const h = marketAt(rd);
  if (!h) { $("tickerList").innerHTML = '<div class="muted small">no market yet</div>'; $("marketHeadlines").innerHTML = ""; $("indexChart").innerHTML = ""; $("indexLabel").textContent = ""; return; }
  const hist = marketCache.history.filter(e => e.round <= rd.round);
  const idx = hist.map(e => e.prices.FLN500);
  const up = idx[idx.length - 1] >= idx[0];
  $("indexChart").innerHTML = spark(idx, 360, 110, up ? "#3ddc84" : "#ff5c5c") + hist.map((e, i) => `<text x="${(i / Math.max(1, hist.length - 1) * 360).toFixed(0)}" y="106" font-size="9" fill="#8b95a7" text-anchor="middle">r${e.round}</text>`).join("");
  $("indexLabel").textContent = `${fmtPx("FLN500", h.prices.FLN500)} (${fmtChg("FLN500", h.pct.FLN500 || 0)} today, ${((idx[idx.length-1]/idx[0]-1)*100).toFixed(1)}% since round 0) · 10Y ${fmtPx("US10Y", h.prices.US10Y)} · oil ${fmtPx("OIL", h.prices.OIL)} · fear ${fmtPx("FEAR", h.prices.FEAR)}`;
  $("marketHeadlines").innerHTML = (h.headlines || []).map(x => `<div>${esc(x)}</div>`).join("") + (h.rationale ? `<div class="muted">${esc(h.rationale)}</div>` : "");
  const rows = [...marketCache.tickers, ...marketCache.macro.filter(m => m.sym !== "FLN500")];
  $("tickerList").innerHTML = rows.map(t => { const p = h.pct[t.sym] || 0; const s = hist.map(e => e.prices[t.sym]);
    return `<div class="tk"><div><div class="nm">${esc(t.sym)} <span class="muted">${fmtPx(t.sym, h.prices[t.sym])}</span></div><div class="sec">${esc(t.name)}${t.sector ? " · " + esc(t.sector) : ""}</div></div>
      <svg viewBox="0 0 90 28" preserveAspectRatio="none">${spark(s, 90, 28, p >= 0 ? "#3ddc84" : "#ff5c5c")}</svg><div class="chg ${p >= 0 ? "up" : "dn"}">${fmtChg(t.sym, p)}</div>${t.note ? `<div class="note">${esc(t.note)}</div>` : ""}</div>`; }).join("");
}
async function loadMarket() { if (!runId) return; marketCache = await api(`/api/runs/${runId}/market`).catch(() => null); renderTape(); renderMarkets(); }

// ---------- ticker + blocs + round label ----------
function roundEvents(rd) {
  // the events of this round: scheduled ones that fired (with their wire text from the feed cache when it is this round) and quarter outcomes
  if (!run || !rd) return [];
  const fired = (run.events_fired || []).filter(e => e.round === rd.round);
  const sched = (run.scenario.events || []).filter(e => e.round === rd.round);
  const feedEvs = feedCache && feedCache.round === rd.round ? (feedCache.events || []) : [];
  const byName = {}; [...sched, ...feedEvs].forEach(e => { byName[e.name] = e; });
  const names = [...new Set([...fired.map(e => e.name), ...feedEvs.map(e => e.name), ...sched.map(e => e.name)])];
  return names.map(n => ({name: n, description: (byName[n] || {}).description || "", quarter: (byName[n] || {}).quarter}));
}
function renderTicker() {
  const rd = currentRound(); if (!rd) return;
  const parts = Object.entries(rd.states).sort((a,b) => b[1].unrest - a[1].unrest).map(([c,s]) => `${c} <b>${Math.round(s.unrest)}</b> ${s.stage_name.toUpperCase()}`);
  const bl = rd.blocs.map(b => `BLOC: ${b.name.toUpperCase()} (${b.members.join(",")})`);
  // breaking news rides the crawl like real TV: a red BREAKING tag, the headline, then the one-line wire text
  const brk = roundEvents(rd).map(e => `<span class="brk">BREAKING</span> ${esc(e.name.toUpperCase())}${e.description ? ` — ${esc(e.description.replace(/^BREAKING:\s*/i, "").split(/(?<=\.)\s/)[0].slice(0, 160))}` : ""}`);
  const q = rd.quarter ? [`<span class="brk q">${esc(rd.quarter.label)}</span> APPROVAL <b>${rd.quarter.approval.national}%</b>`] : [];
  const cfb = primaryBloc(conflictAt(rd)); if (cfb && cfb.stage >= 1) q.push(`<span class="brk q">${esc(cfb.name.toUpperCase())}</span> ${STAGE_NAMES_CF[cfb.stage].toUpperCase()}${cfb.power ? ` · POWER ${cfb.power.bloc} v ${cfb.power.federal}` : ""}`);
  $("ticker").innerHTML = ["FAULT LINES NETWORK" + (rd.date_label ? " · " + esc(rd.date_label.toUpperCase()) : ""), ...brk, ...q, ...bl, ...parts].join(" &nbsp;·&nbsp; ");
}
// ---------- civil conflict ----------
const STAGE_NAMES_CF = ["Grievance", "Declaration", "Provisional govt", "Bloc election", "Rival president", "Standoff", "Conflict", "Resolution"];
function conflictAt(rd) { return (rd && rd.conflict) || (run && run.conflict) || null; }
function primaryBloc(cf) { if (!cf || !cf.blocs || !cf.blocs.length) return null; return cf.blocs.filter(b => !b.gone || b.resolution).sort((a, b) => b.stage - a.stage || b.identity - a.identity)[0] || null; }
function ensureHatch(id, color) {
  if (document.getElementById("hatch-" + id)) return "url(#hatch-" + id + ")";
  const pat = window.warDefs.append("pattern").attr("id", "hatch-" + id).attr("patternUnits", "userSpaceOnUse").attr("width", 7).attr("height", 7).attr("patternTransform", "rotate(45)");
  pat.append("rect").attr("width", 7).attr("height", 7).attr("fill", "#2b3442");
  pat.append("rect").attr("width", 3).attr("height", 7).attr("fill", color).attr("opacity", .75);
  if (!document.getElementById("hatch-neutral")) {
    const pn = window.warDefs.append("pattern").attr("id", "hatch-neutral").attr("patternUnits", "userSpaceOnUse").attr("width", 6).attr("height", 6);
    pn.append("rect").attr("width", 6).attr("height", 6).attr("fill", "#2b3442"); pn.append("circle").attr("cx", 3).attr("cy", 3).attr("r", 1.3).attr("fill", "#8893a2");
  }
  return "url(#hatch-" + id + ")";
}
function renderConflictMap(rd, cf) {
  const b = primaryBloc(cf); const states = rd ? rd.states : {};
  gCracks.selectAll("g.crackg").style("display", "none"); gLabels.selectAll("text.bloc-label").style("display", "none");
  const war = window.gWar; war.selectAll("*").remove();
  if (!b) { $("warRoom").classList.add("hidden"); return; }
  const members = new Set(b.members); const rec = b.recognition || {}; const ctl = b.control || {}; const hatch = ensureHatch(b.id, b.color);
  const inConflict = b.stage >= 6 && Object.keys(ctl).length;
  const sideOf = code => inConflict ? ((ctl[code] != null ? ctl[code] : 0) >= 50 ? "bloc" : "fed") : (members.has(code) ? "bloc" : "fed");
  const front = new Set(b.front || []);
  gStates.selectAll("path.state")
    .attr("class", d => { const code = NAME2CODE[d.properties.name]; return "state" + (code ? "" : " nodata") + (code === selected ? " selected" : "") + (front.has(code) ? " contested" : ""); })
    .style("transform", "translate(0,0)").style("stroke", null).style("stroke-width", null)
    .style("fill", d => { const code = NAME2CODE[d.properties.name]; if (!code) return null;
      if (members.has(code)) { const c = inConflict ? (ctl[code] != null ? ctl[code] : 100) : 100; return d3.interpolateLab("#3a2f2f", b.color)(Math.max(.35, c / 100)); }
      const r = rec[code] || "federal";
      if (inConflict && ctl[code] != null && ctl[code] > 0) return d3.interpolateLab("#2b3442", b.color)(Math.min(.85, ctl[code] / 100));
      return r === "bloc" ? hatch : r === "neutral" ? "url(#hatch-neutral)" : "#2b3442"; });
  // the line: a checkpoint ring around the bloc in a standoff, a moving front once control is contested
  const mesh = topojson.mesh(topo, topo.objects.states, (a, c) => sideOf(NAME2CODE[a.properties.name]) !== sideOf(NAME2CODE[c.properties.name]));
  war.append("path").attr("class", inConflict ? "front" : "checkpoint").attr("d", path(mesh));
  // supply lines between members, blockades as arrows from the federal side onto a member port
  const cen = code => { const f = featureByCode[code]; return f ? path.centroid(f) : null; };
  const ms = b.members.map(cen).filter(Boolean);
  for (let i = 1; i < ms.length; i++) war.append("line").attr("class", "supply").attr("x1", ms[i-1][0]).attr("y1", ms[i-1][1]).attr("x2", ms[i][0]).attr("y2", ms[i][1]).attr("stroke", b.color);
  (b.blockades || []).forEach(bk => { const to = cen(bk.state); if (!to) return;
    const fedN = (run.rounds[0] && Object.keys(states)).find(c => !members.has(c) && (rec[c] || "federal") === "federal" && (featureByCode[c]));
    const from = [to[0] + (to[0] > 480 ? 70 : -70), to[1] - 60];
    war.append("line").attr("class", "blockade").attr("x1", from[0]).attr("y1", from[1]).attr("x2", to[0]).attr("y2", to[1]).attr("marker-end", "url(#arrow)");
    war.append("text").attr("class", "war-lbl").attr("x", from[0]).attr("y", from[1] - 4).text("BLOCKADE"); });
  if (b.president) { const c = cen(b.members[0]); if (c) war.append("text").attr("class", "war-lbl").attr("x", c[0]).attr("y", c[1] + 14).attr("text-anchor", "middle").text(b.slogan || b.name.toUpperCase()); }
  // the panel
  const pw = b.power; const cnt = b.counts || {};
  $("warRoom").classList.remove("hidden");
  $("warRoom").innerHTML = `<h3>War room · ${esc(b.name)}</h3><div class="stage" style="color:${b.color}">${STAGE_NAMES_CF[b.stage]}${b.resolution ? " · " + esc(b.resolution) : ""}</div>
    <div class="kv"><span>identity</span><b>${Math.round(b.identity)}</b></div>
    <div class="kv"><span>recognition</span><b>${cnt.bloc || 0} bloc · ${cnt.neutral || 0} neutral · ${cnt.federal || 0} federal</b></div>
    ${pw ? `<div class="kv"><span>power</span><b>bloc ${pw.bloc} · federal ${pw.federal}</b></div><div class="pw"><i style="width:${100 * pw.bloc / (pw.bloc + pw.federal)}%;background:${b.color}"></i><i style="flex:1;background:#3d6fd9"></i></div>
    <div class="kv"><span>Guard with bloc / commands with DC</span><b>${Math.round(pw.guard_bloc)} / ${Math.round(pw.military_federal)}</b></div>
    <div class="kv"><span>legitimacy fed / bloc</span><b>${pw.legitimacy.federal} / ${pw.legitimacy.bloc}</b></div>` : ""}
    ${b.stage >= 5 ? `<div class="kv"><span>trade index · bloc economy</span><b>${b.trade} · ${b.economy}</b></div><div class="kv"><span>blockades · sanctions · foreign support</span><b>${(b.blockades||[]).length} · ${(b.sanctions||[]).length} · ${Math.round(b.foreign_support)}</b></div>` : `<div class="kv"><span>foreign support</span><b>${Math.round(b.foreign_support)}</b></div>`}
    <div class="lg"><span><i class="sw" style="background:${b.color}"></i>member</span><span><i class="sw" style="background:repeating-linear-gradient(45deg,${b.color} 0 2px,#2b3442 2px 5px)"></i>recognizes bloc</span><span><i class="sw" style="background:radial-gradient(#8893a2 1px,#2b3442 1.5px)"></i>neutral</span><span><i class="sw" style="background:#2b3442"></i>federal</span>${inConflict ? '<span>⚡ contested flashes</span>' : ""}</div>`;
}
function renderCrimeMap(rd, cr) {
  gCracks.selectAll("g.crackg").style("display", "none"); gLabels.selectAll("text.bloc-label").style("display", "none");
  window.gWar.selectAll("*").remove(); $("warRoom").classList.add("hidden");
  const held = {}; (cr.syndicates || []).forEach(sy => { if (sy.gone) return; Object.entries(sy.territory || {}).forEach(([c, t]) => { if (t >= 50) held[c] = sy.name; }); });
  gStates.selectAll("path.state")
    .attr("class", d => { const code = NAME2CODE[d.properties.name]; return "state" + (code ? "" : " nodata") + (code === selected ? " selected" : "") + (held[code] ? " territory" : ""); })
    .style("transform", "translate(0,0)").style("stroke", null).style("stroke-width", null)
    .style("fill", d => { const code = NAME2CODE[d.properties.name]; const s = code && cr.states[code]; return s ? crimeColor(s.index) : "#2b3442"; });
}
function renderCrimeCard() {
  const cr = run && run.crime; if (!cr || !cr.active) return "";
  const names = c => (roster && roster["state:" + c]) ? roster["state:" + c].display : c;
  const nat = cr.national || []; const W = 360, H = 60;
  const xs = nat.length > 1 ? nat.map((_, i) => 8 + i * (W - 16) / (nat.length - 1)) : [W / 2];
  const y = v => H - 6 - Math.max(0, Math.min(100, v)) / 100 * (H - 12);
  const line = nat.map((p, i) => `${i ? "L" : "M"}${xs[i].toFixed(1)},${y(p.index).toFixed(1)}`).join(" ");
  const worst = Object.entries(cr.states).sort((a, b) => b[1].index - a[1].index).slice(0, 5);
  const hid = cr.hidden || {compromised: {}, owned: {}, dirt: {}};
  const synd = (cr.syndicates || []).filter(s => !s.gone).map(s => `<div class="synd"><b>${esc(s.name)}</b> · ${s.kind === "ai" ? "AI network" : esc(s.specialty)} · ${s.home ? "home " + esc(names(s.home)) : "no headquarters"}<br>
      <span class="muted">territory ${Object.entries(s.territory).filter(([c, t]) => t >= 40).map(([c, t]) => `${c} ${Math.round(t)}`).join(", ") || "none yet"} · shadow money ${Math.round(s.shadow_money)}${s.kind === "ai" ? ` · compute ${Math.round(s.compute)} (${s.ops} ops/round)` : ""} · sympathy ${Math.round(s.sympathy)}${(s.assets.robots || s.assets.drones) ? ` · assets: ${s.assets.robots} robots, ${s.assets.drones} drones` : ""}</span>
      <div class="small muted">leader: ${esc(s.leader)} · ${esc(s.origin || "")}</div></div>`).join("");
  const viewerOnly = [];
  Object.entries(hid.owned || {}).forEach(([p, i]) => viewerOnly.push(`${p === "president" ? "the President" : "Governor " + p.split(":")[1]} is owned by ${esc((cr.syndicates.find(s => s.id === i.syndicate) || {}).name || i.syndicate)} since ${esc(i.date || "")} (${esc(i.how)})`));
  Object.entries(hid.dirt || {}).forEach(([p, i]) => viewerOnly.push(`${p === "president" ? "the President" : "Governor " + p.split(":")[1]}: ${esc((cr.syndicates.find(s => s.id === i.syndicate) || {}).name || i.syndicate)} holds dirt (${esc(i.skeleton)}), leverage left ${i.leverage}`));
  Object.entries(hid.compromised || {}).forEach(([a, i]) => viewerOnly.push(`@${a.split(":")[1]} on ${esc((cr.syndicates.find(s => s.id === i.syndicate) || {}).name || i.syndicate)}'s payroll since ${esc(i.date || "")}`));
  const cs = Object.entries(cr.crime_states || {}).map(([b, i]) => `crime state since ${esc(i.date || "")} (front: ${esc(i.front || "?")})`).join("; ");
  const stance = Object.values(cr.stance || {}).map(s => s.stance).join(", ");
  return `<div class="card crime"><h2>Crime · national ${nat.length ? nat[nat.length - 1].index : "?"}</h2>
    <svg class="appr-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none">${line ? `<path d="${line}" fill="none" stroke="#d9532b" stroke-width="2"/>` : ""}</svg>
    <div class="small">${worst.map(([c, s]) => `<div>${esc(names(c))} <b style="color:${crimeColor(s.index)}">${Math.round(s.index)}</b> <span class="muted">${Object.entries(s.types).sort((a, b) => b[1] - a[1]).slice(0, 2).map(([k, v]) => k + " " + Math.round(v)).join(", ")}${Object.keys(s.era || {}).length ? " · era: " + Object.keys(s.era).slice(0, 2).join(", ") : ""}${s.surveillance ? " · surveillance " + Math.round(s.surveillance) : ""}</span></div>`).join("")}</div>
    ${cr.emergency ? `<div class="small" style="margin-top:4px;color:#ffb3b3">emergency crime powers in force${cr.emergency.court ? " · court: " + esc(cr.emergency.court) : ""}</div>` : ""}
    ${cs ? `<div class="small" style="margin-top:4px">${cs}${stance ? " · Washington: " + esc(stance) : ""}</div>` : ""}
    ${synd ? `<div style="margin-top:6px"><div class="small muted">syndicates</div>${synd}</div>` : '<div class="small muted" style="margin-top:6px">no syndicates yet</div>'}
    ${viewerOnly.length ? `<div class="hiddenbox"><div class="lbl">viewer only · no character knows this</div>${viewerOnly.map(x => `<div>${x}</div>`).join("")}</div>` : ""}</div>`;
}
function renderConflictCards(cf, rd) {
  if (!cf || !cf.blocs || !cf.blocs.length) return "";
  const names = c => (run && run.rounds[0] && roster && roster["state:" + c]) ? roster["state:" + c].display : c;
  return cf.blocs.slice().sort((x, y) => y.stage - x.stage || y.identity - x.identity).map(b => {
    const ladder = STAGE_NAMES_CF.map((n, i) => `<span class="${i < b.stage ? "done" : i === b.stage ? "now" : ""}">${n}</span>`).join("");
    const cnt = b.counts || {}; const pw = b.power;
    const guard = b.members.length ? Math.round(b.members.reduce((a, c) => a + ((b.guard || {})[c] != null ? b.guard[c] : 85), 0) / b.members.length) : null;
    const contested = Object.entries(b.control || {}).filter(([c, v]) => !b.members.includes(c) && v > 0).sort((x, y) => y[1] - x[1]).slice(0, 6);
    return `<div class="bloc-card"><div class="bloc">${b.flag_svg || ""}<div><div class="name" style="color:${b.color}">${esc(b.name)}</div><div class="members">${b.members.join(" · ")} · ${b.age} rounds old${b.gone ? " · dissolved" : ""}</div></div></div>
      <div class="ladder">${ladder}</div>
      <div class="small">identity ${Math.round(b.identity)}</div><div class="bar"><i style="width:${b.identity}%;background:${b.color}"></i></div>
      ${b.slogan ? `<div class="small"><b>${esc(b.slogan)}</b></div>` : ""}${b.manifesto ? `<div class="manifesto">“${esc(b.manifesto)}”</div>` : ""}
      ${(b.leaders || []).length ? `<div class="leaders"><div class="small muted">provisional government</div>${b.leaders.map(l => `<div><b>${esc(l.name)}</b> · ${esc(l.role)} (${esc(l.state)})<span class="muted"> · ${esc(l.bio || "")}</span></div>`).join("")}</div>` : ""}
      ${b.election ? `<div class="small" style="margin-top:4px">bloc election: ${b.election.candidates.map(c => `<b style="color:${c.color}">${esc(c.name)}</b> ${b.election.ev[c.id]}`).join(" vs ")} electors${b.president ? ` · <b>President ${esc(b.president.name)}</b>` : ""}</div>` : ""}
      ${b.stage >= 1 ? `<div class="small" style="margin-top:4px">recognition: ${cnt.bloc || 0} states with the bloc, ${cnt.neutral || 0} neutral, ${cnt.federal || 0} federal · foreign support ${Math.round(b.foreign_support)}</div>
      <div class="small">Guard loyalty to Washington in member states: ${guard} · commands: ${Object.entries(b.commands || {}).map(([k, v]) => `${k.replace(" Command", "")} ${Math.round(v)}`).join(", ")}</div>` : ""}
      ${pw ? `<div class="small" style="margin-top:4px">power: bloc <b>${pw.bloc}</b> vs federal <b>${pw.federal}</b> · trade ${b.trade} · bloc economy ${b.economy} · blockades ${(b.blockades || []).map(x => x.state).join(", ") || "none"} · sanctions ${(b.sanctions || []).length}</div>` : ""}
      ${contested.length ? `<div class="small muted" style="margin-top:4px">contested ground (bloc control)</div><div class="ctl">${contested.map(([c, v]) => `<span>${esc(names(c))} <b>${Math.round(v)}%</b></span>`).join("")}</div>` : ""}
      ${b.resolution ? `<div class="res"><b>${esc(b.resolution)}</b>${b.resolution_text ? ` · ${esc(b.resolution_text)}` : ""}</div>` : ""}
      <div class="small muted" style="margin-top:4px">${(b.history || []).slice(-4).map(h => `r${h.round}: ${esc(h.event)}`).join(" · ")}</div></div>`;
  }).join("") + ((cf.dissolved || []).length ? `<div class="small muted">dissolved: ${cf.dissolved.map(d => `${esc(d.name)} (round ${d.round}, ${esc(d.cause)})`).join("; ")}</div>` : "");
}
function renderBlocs(blocs) {
  const el = $("blocs");
  const cf = conflictAt(currentRound());
  if (cf && cf.blocs && cf.blocs.length) { el.innerHTML = renderConflictCards(cf, currentRound()); return; }
  if (!blocs.length) { el.innerHTML = '<span class="muted">none yet — blocs form when adjacent states both drop below the federal-cohesion threshold</span>'; return; }
  el.innerHTML = blocs.map(b => `<div class="bloc">${b.flag_svg}<div><div class="name" style="color:${b.color}">${b.name}</div><div class="members">${b.members.join(" · ")} · ${b.grievance} · cohesion ${b.avg_federal_cohesion}</div></div></div>`).join("");
}
function renderRoundLabel() {
  const rd = currentRound();
  if (!run) { $("roundLabel").textContent = "no run loaded"; return; }
  const status = run.status + (run.engine && run.engine.status ? ` / engine ${run.engine.status} r${run.engine.round}` : "") + (run.media_pending ? ` / media ${run.media_pending} pending` : "");
  const label = rd && rd.election && rd.election.election_night ? "ELECTION NIGHT" : rd ? `Round ${rd.round} of ${run.scenario.rounds}` : "";
  const when = rd && rd.date_label ? rd.date_label : "";
  $("roundLabel").textContent = rd ? `${when ? when + " · " : ""}${label} · ${rd.posts_count} posts · ${rd.blocs.length} blocs · ${status}` : `waiting for round 0 · ${status}`;
  const badge = $("dateBadge");
  if (badge) { if (when) { badge.classList.remove("hidden"); badge.innerHTML = `${esc(when)}<small>${rd.quarter ? esc(rd.quarter.label) + " · " : ""}${run.era ? esc(run.era) : (run.scenario.calendar && run.scenario.calendar.unit === "quarter" ? "the term" : "campaign")}</small>`; } else badge.classList.add("hidden"); }
  // events of the round run on the news crawl (renderTicker), not in a box over the map
  $("scrub").max = Math.max(0, run.rounds.length - 1); $("scrub").value = Math.min(roundIdx, run.rounds.length - 1);
}

// ---------- feed ----------
async function loadFeed() {
  const rd = currentRound(); if (!rd || !runId) return;
  feedRound = rd.round;
  const f = await api(`/api/runs/${runId}/feed?upto=${rd.round}`).catch(() => null);
  if (!f || feedRound !== rd.round) return;
  feedCache = f; renderFeed(f); renderBroadcast(f); renderSpend(f.spend); loadMarket(); renderTicker();
}
function payrollBadge(author) {
  const hid = run && run.crime && run.crime.hidden; if (!hid || !hid.compromised || !hid.compromised[author]) return "";
  const i = hid.compromised[author]; const sy = (run.crime.syndicates || []).find(x => x.id === i.syndicate) || {name: i.syndicate};
  return `<span class="payroll" title="viewer only: on ${sy.name}'s payroll since ${i.date || ""}">paid</span>`;
}
function renderFeed(f) {
  $("trending").innerHTML = f.trending.length ? f.trending.map((t,i) => `<div class="tag"><span>${i+1} · <b>${esc(t.tag)}</b></span><span class="muted">${fmtN(t.posts)} posts</span></div>`).join("") : '<span class="muted">nothing yet</span>';
  const byId = {}; f.items.forEach(it => byId[it.id] = it);
  $("feed").innerHTML = f.items.map(it => {
    const a = roster[it.author] || {handle: it.author, display: it.author, verified: "none"};
    const vb = a.verified && a.verified !== "none" ? `<span class="vb ${a.verified}"></span>` : "";
    const ctx = it.reply_to && byId[it.reply_to] ? `<div class="ctx">replying to @${esc((roster[byId[it.reply_to].author]||{}).handle||"")}</div>` : "";
    const quote = it.quote_text ? `<div class="quote">${esc(it.quote_text.slice(0, 280))}</div>` : "";
    let media = "";
    if (it.media) {
      if (it.media.status === "pending") media = `<div class="media"><div class="pending">generating ${esc(it.media.format||"image")}…<br><span class="small">${esc(it.media.alt||"")}</span></div></div>`;
      else if (it.media.type === "video") media = `<div class="media"><video src="${it.media.url}" controls muted playsinline></video></div>`;
      else if (it.media.type === "audio") media = `<div class="media audio"><img src="${it.media.poster||""}" alt=""><audio src="${it.media.url}" controls preload="none"></audio></div>`;
      else media = `<div class="media"><img src="${it.media.url}" alt="${esc(it.media.alt||"")}" loading="lazy"></div>`;
    }
    const txt = esc(it.text).replace(/#([A-Za-z0-9_]+)/g, '<span class="tag">#$1</span>');
    const flag = it.state ? `<span class="flag" data-code="${it.state}" title="pan to ${it.state}">${it.state}</span>` : "";
    return `<div class="fi ${it.kind}"><img class="av" src="${a.avatar ? esc(a.avatar) : "/api/avatars/" + esc(a.handle) + ".png"}" loading="lazy" alt="">
      <div><div class="hd"><span class="name">${esc(a.display)}</span>${vb}${payrollBadge(it.author)}<span class="handle">@${esc(a.handle)}</span><span class="ts">· r${it.round}</span></div>
      ${ctx}<div class="txt">${txt}</div>${quote}${media}
      <div class="ft"><span>💬 ${fmtN(it.replies)}</span><span>🔁 ${fmtN(it.reposts)}</span><span>♥ ${fmtN(it.likes)}</span>${flag}</div></div></div>`;
  }).join("") || '<div class="muted small">no posts yet</div>';
  $("feed").querySelectorAll(".flag").forEach(el => el.onclick = () => panToState(el.dataset.code));
}
function renderSpend(sp) {
  if (!sp) return; const el = $("spend"); const frac = Math.min(1, sp.fraction || 0);
  el.querySelector("i").style.width = (frac*100) + "%"; el.querySelector("span").textContent = `$${(sp.total_usd||0).toFixed(3)} / $${(sp.budget_usd||0).toFixed(2)}`;
  el.className = "spend" + (sp.halted ? " halt" : frac > 0.8 ? " warn" : "");
}

// ---------- live bar: what the sim is doing right now (polled apart from the heavy run poll) ----------
let act = null, actTimer = null, actFetched = 0, booted = false;
function money(v) { return "$" + (v || 0).toFixed(2); }
async function pollActivity() {
  clearTimeout(actTimer);
  if (!runId) { act = null; renderLive(); return; }
  const id = runId;
  try { const a = await api(`/api/runs/${id}/activity`); if (runId !== id) return; act = a; actFetched = Date.now(); }
  catch (e) { if (act) act._lost = true; }
  renderLive(); renderActivityLog();
  const busy = act && (["starting", "running", "scoring"].includes(act.status) || (act.media && act.media.pending) || (act.episode && act.episode.status && !["done", "ready", "error"].includes(act.episode.status)));
  actTimer = setTimeout(pollActivity, busy ? 2000 : 15000);
}
function agoText(s) { return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.floor(s / 60)} min ${s % 60} s ago` : `${Math.floor(s / 3600)} h ago`; }
function renderLive() {
  const bar = $("liveBar"); let cls = "idle", state = "NO RUN", round = "", phase = "Write a scenario on the left and press Run simulation.", media = "", ago = "", spend = "", fill = 0;
  if (setupJob) {
    cls = "setup"; state = "SETTING UP"; phase = $("setupNote").textContent || "starting"; ago = $("setupElapsed").textContent;
  } else if (!runId && !booted) {
    cls = "setup"; state = "LOADING"; phase = "reading your runs";
  } else if (runId && !act) {
    cls = "setup"; state = "LOADING"; phase = "reading the run";
  } else if (act) {
    const eng = act.engine || {}; const total = act.total_rounds || eng.total_rounds || 0; const done = act.rounds || 0;
    fill = total ? Math.min(1, done / total) : 0;
    const agoS = act.ago_s == null ? null : act.ago_s + Math.round((Date.now() - actFetched) / 1000);
    const sp = act.spend || {};
    spend = `<b>${money(sp.total_usd)}</b> this run · voices ${money(sp.voices)} · pictures ${money(sp.pictures)} · text ${money(sp.text)}`;
    const m = act.media || {};
    if (m.pending) media = `${m.pending} ${m.pending === 1 ? "picture/voice" : "pictures/voices"} in the works${m.active && m.active.length ? ": " + m.active.slice(0, 2).join(", ") : ""}`;
    // the newest word wins: the agent process (progress note) or the backend (activity)
    let engT = 0; try { engT = eng.updated_at ? new Date(eng.updated_at).getTime() / 1000 : 0; } catch (e) {}
    const newest = (act.current_t || 0) >= engT ? act.current : (eng.note || act.current);
    if (act.status === "error") { cls = "error"; state = "FAILED"; phase = act.error || newest || "the run failed; see runner.log"; round = total ? `Round ${done} of ${total}` : ""; }
    else if (act.status === "done") {
      cls = "done"; state = "FINISHED"; round = `${Math.min(done, total || done)} rounds${done > total && total ? " + election day" : ""}`; fill = 1;
      const ep = act.episode || {};
      const epBuilding = (m.active || []).some(l => /^episode/.test(l));
      phase = epBuilding ? "finished; writing the episode now (the Episode button lights up when it is ready)" : m.pending ? `finished; the last ${m.pending} pictures and voices are still rendering` : ep.status && !["done", "ready"].includes(ep.status) ? `finished; writing the episode (${ep.note || ep.status})` : "finished. Press Episode to watch it as TV, or Continue for the next season.";
      if (act._lost) { phase = "lost contact with the server"; cls = "error"; state = "SERVER?"; }
    } else {
      // the agents may already be writing the round after the one being scored: show the furthest one along
      const writing = Math.min(total || 99, (eng.round || 0) + (/writing|writes|option bank/i.test(eng.note || "") ? 1 : 0));
      const cur = Math.max(done + 1, writing);
      cls = "running"; state = "RUNNING"; round = total ? (cur > total || (done >= total && /election|voting/i.test(eng.note || "")) ? "Election day" : `Round ${Math.min(cur, total)} of ${total}`) : "";
      phase = newest || "working";
      if (agoS != null) ago = `last activity ${agoText(agoS)}`;
      if (agoS != null && agoS > 240) { cls = "stalled"; state = "STALLED?"; phase = `nothing has happened for ${Math.floor(agoS / 60)} min (was: ${newest || "?"}). The engine may be stuck: check the server window or press Stop current.`; }
      if (act._lost) { cls = "error"; state = "SERVER?"; phase = "lost contact with the server; is it still running?"; }
    }
  }
  bar.className = "live-bar " + cls;
  $("lbState").textContent = state; $("lbRound").textContent = round; $("lbPhase").textContent = phase; $("lbPhase").className = "lb-phase";
  const warn = act && act.warning; $("lbWarn").textContent = warn ? "⚠ " + warn : ""; $("lbWarn").title = warn || ""; $("lbWarn").classList.toggle("hidden", !warn);
  $("lbMedia").textContent = media; $("lbAgo").textContent = ago; $("lbSpend").innerHTML = spend; $("lbFill").style.width = (fill * 100) + "%";
  const tab = $("activityTab"); if (tab) tab.classList.toggle("live", cls === "running" || cls === "setup");
}
function renderActivityLog() {
  if (!act) { $("activityNow").textContent = "no run loaded"; $("activityLog").textContent = "nothing yet"; $("activitySpend").textContent = "—"; return; }
  const eng = act.engine || {}; const sp = act.spend || {};
  $("activityNow").innerHTML = `${esc($("lbState").textContent)} · ${esc($("lbRound").textContent)} ${esc($("lbPhase").textContent)}<div class="small">agent process: ${esc(eng.note || eng.status || "—")} · backend: ${esc(act.current || "—")}${act.media && act.media.pending ? ` · media queue: ${act.media.pending} (${esc((act.media.active || []).join(", "))})` : ""}</div>`;
  $("activitySpend").innerHTML = `<b>${money(sp.total_usd)}</b> of a ${money(sp.budget_usd)} budget${sp.halted ? " · HALTED: budget reached" : ""}<br>voices ${money(sp.voices)} · pictures ${money(sp.pictures)} · text ${money(sp.text)} (text is local and free when the agents run on Ollama)`;
  const rows = [];
  for (const l of act.log || []) rows.push({t: l.t, ts: l.ts, cls: "", text: l.text});
  for (const m of (act.media && act.media.recent) || []) rows.push({t: m.t, ts: new Date(m.t * 1000).toTimeString().slice(0, 8), cls: m.ok ? "media" : "fail", text: `${m.ok ? "ready" : "FAILED"}: ${m.label} (${m.secs}s)`});
  for (const c of sp.recent || []) if (c.usd) rows.push({t: 0, ts: c.ts, cls: "spend", text: `${c.kind} ${money(c.usd).replace("$0.00", "<$0.01")} · ${c.provider} ${c.model}`});
  rows.sort((a, b) => (b.ts || "").localeCompare(a.ts || ""));
  $("activityLog").innerHTML = rows.slice(0, 80).map(r => `<div class="${r.cls}"><span class="t">${esc(r.ts || "")}</span><span class="x">${esc(r.text)}</span></div>`).join("") || "nothing yet";
}
setInterval(() => { if (!document.body.classList.contains("episode")) renderLive(); }, 1000);
$("lbLog").onclick = () => showTab("activity");

// ---------- broadcast player ----------
// ---------- broadcast player: anchor -> field reporter, or the debate stage (a generic playlist) ----------
let bcSegment = null, bcAudio = null, playlistToken = 0, bcMode = "news";   // bcMode: "news" | "debate"
const bcSeen = new Set();
function bcReady(b) { return !!(b.video || b.audio || (config && config.tts === "none")); }
function debateReady(d) { return !!d && (d.status === "voiced" || (config && config.tts === "none")); }
function renderBroadcast(f) {
  const bcs = f.broadcasts || []; const deb = f.debate || null;
  const rd = currentRound(); bcs.forEach(b => b._round = rd ? rd.round : -1);
  if (bcMode === "debate" && !deb) bcMode = "news";
  const tabs = $("bcTabs");
  tabs.innerHTML = bcs.map((b, i) => `<button class="${bcMode === "news" && i === bcNet ? "active" : ""}${bcReady(b) && !bcSeen.has(`${b._round}:${b.network}`) ? " new" : ""}" data-i="${i}">${esc(b.network_name)}</button>`).join("")
    + (deb ? `<button class="${bcMode === "debate" ? "active" : ""}${debateReady(deb) && !bcSeen.has(`${deb.round}:debate`) ? " new" : ""}" data-i="debate">Debate</button>` : "");
  tabs.querySelectorAll("button").forEach(b => b.onclick = () => { stopSpeech(); if (b.dataset.i === "debate") bcMode = "debate"; else { bcMode = "news"; bcNet = +b.dataset.i; } renderBroadcast(feedCache); });
  const player = $("broadcast");
  if (bcMode === "debate" && deb) { renderDebate(deb); return; }
  $("bcStage").classList.add("hidden"); $("bcAnchor").classList.remove("hidden");
  const b = bcs[bcNet] || bcs[0];
  if (!b) { $("chyronHead").textContent = "FAULT LINES NETWORK"; $("chyronSub").textContent = run && run.status === "running" ? "news desk writing…" : "standing by"; $("bcScript").textContent = ""; $("bcNetName").textContent = "FLN"; $("bcMode").textContent = ""; $("bcPlay").disabled = true; player.classList.remove("new"); return; }
  $("bcNetName").textContent = b.network_name;
  const ready = bcReady(b);
  if (!ready) {
    $("chyronHead").textContent = `PRODUCING ROUND ${b._round}…`; $("chyronSub").textContent = "news desk done, voices rendering";
    $("bcScript").textContent = ""; $("bcMode").textContent = "producing…"; $("bcPlay").disabled = true;
    $("bcAnchor").src = b.anchor_image || `/api/avatars/anchor_${b.network}.png`; player.classList.remove("new"); return;
  }
  $("bcPlay").disabled = false;
  player.classList.toggle("new", !bcSeen.has(`${b._round}:${b.network}`));
  $("bcScript").textContent = b.script + (b.field_cutin ? `\n\n[${b.field_reporter || "FIELD"}, ${b.field_state_name || ""}] ` + b.field_cutin : "");
  const onAir = bcSegment && bcSegment.network === b.network && bcSegment.round === b._round;
  if (!onAir) showSegment(b, "anchor");
  const v = $("bcVideo"); if (b.video && !onAir) { v.src = b.video; v.classList.remove("hidden"); } else if (!b.video) { v.classList.add("hidden"); v.removeAttribute("src"); }
  $("bcMode").textContent = b.video ? "anchor video" : b.audio ? `voiced · ${(config && config.tts) || "tts"}` : "browser voice";
}
// one podium per candidate (two, three or four), tagged with the candidate id so a line can light its speaker
function buildPods(container, candidates, stageImage) {
  const ids = Object.keys(candidates || {});
  if (stageImage) {
    // one wide shot of the stage; the speaker's third (or quarter) is lit, the others dimmed
    const key = "shot:" + stageImage + ":" + ids.join(",");
    if (container.dataset.ids !== key || !container.querySelector(".stage-shot")) {
      container.innerHTML = `<div class="stage-shot"><img src="${stageImage}" alt=""><div class="spots">${ids.map(id => `<div class="spot" data-id="${id}"><span></span></div>`).join("")}</div></div>`;
      container.dataset.ids = key;
    }
    ids.forEach(id => { const c = candidates[id] || {}; container.querySelector(`.spot[data-id="${id}"] span`).textContent = c.name || id; });
    return;
  }
  if (container.dataset.ids !== ids.join(",") || !container.querySelector(".pod")) {
    container.innerHTML = ids.map(id => `<div class="pod" data-id="${id}"><img alt=""><span></span></div>`).join("");
    container.dataset.ids = ids.join(",");
  }
  ids.forEach(id => { const c = candidates[id] || {}; const pod = container.querySelector(`.pod[data-id="${id}"]`);
    pod.querySelector("img").src = c.stage_image || c.image || ""; pod.querySelector("span").textContent = c.name || id; });
}
function lightPod(container, speaker) {
  container.querySelectorAll(".pod, .spot").forEach(p => p.classList.toggle("active", p.dataset.id === speaker));
  const shot = container.querySelector(".stage-shot"); if (shot) shot.classList.toggle("mod", speaker === "mod");
}
function renderDebate(d) {
  const player = $("broadcast"); $("bcStage").classList.remove("hidden"); $("bcAnchor").classList.add("hidden"); $("bcVideo").classList.add("hidden");
  $("bcNetName").textContent = "FLN DEBATE";
  buildPods($("bcStage"), d.candidates, d.stage_image);
  $("bcScript").textContent = d.lines.map(l => `${l.speaker === "mod" ? d.moderator : (d.candidates[l.speaker] || {}).name}: ${l.text}`).join("\n\n");
  const ready = debateReady(d); $("bcPlay").disabled = !ready;
  if (!ready) { $("chyronHead").textContent = "PRODUCING THE DEBATE…"; $("chyronSub").textContent = "voices rendering"; $("bcMode").textContent = "producing…"; player.classList.remove("new"); return; }
  player.classList.toggle("new", !bcSeen.has(`${d.round}:debate`));
  const onAir = bcSegment && bcSegment.network === "debate" && bcSegment.round === d.round;
  if (!onAir) { $("chyronHead").textContent = ("DEBATE NIGHT · " + (d.topics || []).join(" · ")).slice(0, 72); $("chyronSub").textContent = `Moderated by ${d.moderator}`; document.querySelectorAll(".bc-stage .pod").forEach(p => p.classList.remove("active")); }
  $("bcMode").textContent = d.audio && d.audio.some(Boolean) ? `voiced · ${(config && config.tts) || "tts"}` : "browser voice";
}
function showSegment(b, which) {
  if (which === "field") {
    $("bcAnchor").src = b.field_image || $("bcAnchor").src;
    $("chyronHead").textContent = `LIVE · ${b.field_state_name || b.field_state || ""}`.toUpperCase();
    $("chyronSub").textContent = `${b.field_reporter || "Field reporter"} reporting · ${b.network_name}`;
  } else {
    $("bcAnchor").src = b.anchor_image || `/api/avatars/anchor_${b.network}.png`;
    $("chyronHead").textContent = b.headline || "BREAKING"; $("chyronSub").textContent = b.chyron || "";
  }
}
function stopSpeech() {
  playlistToken++;
  if (window.speechSynthesis) window.speechSynthesis.cancel();
  if (bcAudio) { bcAudio.onended = null; bcAudio.pause(); bcAudio = null; }
  const v = $("bcVideo"); v.pause(); $("bcPlay").textContent = "▶ play";
  bcSegment = null;
  if (feedCache) renderBroadcast(feedCache);
}
if ($("bcToggle")) $("bcToggle").onclick = () => { const b = $("broadcast"); b.classList.toggle("collapsed"); $("bcToggle").textContent = b.classList.contains("collapsed") ? "▴" : "▾"; if (b.classList.contains("collapsed")) stopSpeech(); };
// with no voice key the app is silent by default (words on screen); Settings can switch on the browser's own voice
function browserVoiceOn() { return !!(window.speechSynthesis && config && config.fallback_voice === "browser"); }
function readSecs(text) { return Math.max(2.5, String(text || "").split(/\s+/).filter(Boolean).length / 2.6); }
function pickVoice(re) { const vs = window.speechSynthesis ? window.speechSynthesis.getVoices() : []; return vs.find(v => /en/i.test(v.lang) && re.test(v.name)) || vs.find(v => /en/i.test(v.lang)) || null; }
function playOne(item, token) {
  // one segment: show it, then play its audio (Cartesia mp3) or read it with the browser voice
  return new Promise(resolve => {
    if (token !== playlistToken) return resolve();
    item.show();
    if (item.audio) { bcAudio = new Audio(item.audio); bcAudio.onended = () => resolve(); bcAudio.onerror = () => resolve(); bcAudio.play().catch(() => resolve()); return; }
    if (!item.text) return resolve();
    if (!browserVoiceOn()) { setTimeout(resolve, readSecs(item.text) * 1000); return; }   // silent: hold the words on screen for a reading pace
    const u = new SpeechSynthesisUtterance(item.text); u.voice = pickVoice(item.voice || /./); u.rate = item.rate || 1; u.pitch = item.pitch || 1;
    u.onend = () => resolve(); u.onerror = () => resolve(); window.speechSynthesis.speak(u);
  });
}
async function playPlaylist(items, key) {
  const token = ++playlistToken; bcSegment = {network: key.network, round: key.round};
  $("bcPlay").textContent = "■ stop";
  for (const it of items) { if (token !== playlistToken) return; await playOne(it, token); }
  if (token === playlistToken) stopSpeech();
}
$("bcPlay").onclick = () => {
  if (!feedCache) return;
  if (bcSegment || bcAudio || (window.speechSynthesis && window.speechSynthesis.speaking)) { stopSpeech(); return; }
  if (bcMode === "debate" && feedCache.debate) {
    const d = feedCache.debate; if (!debateReady(d)) return;
    bcSeen.add(`${d.round}:debate`);
    const items = d.lines.map((l, i) => ({
      audio: (d.audio || [])[i] || null, text: l.text,
      voice: l.speaker === "mod" ? /./ : Object.keys(d.candidates).indexOf(l.speaker) % 2 === 0 ? /female|zira|aria|jenny/i : /male|david|guy|mark/i, rate: 1.02,
      show: () => { lightPod($("bcStage"), l.speaker);
        const who = l.speaker === "mod" ? d.moderator : (d.candidates[l.speaker] || {}).name;
        $("chyronHead").textContent = (l.speaker === "mod" ? "MODERATOR · " : "") + (who || "").toUpperCase(); $("chyronSub").textContent = l.text.slice(0, 90); }
    }));
    const from = window.bcDebateFrom || 0, count = window.bcDebateCount || items.length;   // the tutorial plays the sharpest exchange
    playPlaylist(items.slice(from, from + count), {network: "debate", round: d.round}); return;
  }
  const b = (feedCache.broadcasts || [])[bcNet] || (feedCache.broadcasts || [])[0]; if (!b || !bcReady(b)) return;
  bcSeen.add(`${b._round}:${b.network}`);
  if (b.video) { bcSegment = {network: b.network, round: b._round}; const v = $("bcVideo"); v.muted = false; v.play(); v.onended = stopSpeech; $("bcPlay").textContent = "■ stop"; return; }
  const items = [{audio: b.audio, text: b.script, voice: b.network === "fln_world" ? /GB|UK|British|Hazel|George|Susan/i : bcNet === 0 ? /female|zira|aria|jenny/i : /male|david|guy|mark/i,
                  rate: bcNet === 0 ? 1.12 : 0.95, pitch: bcNet === 0 ? 1.1 : 0.9, show: () => showSegment(b, "anchor")}];
  if (b.field_cutin) items.push({audio: b.field_audio, text: b.field_cutin, voice: /male|david|guy|mark/i, rate: 1.05, show: () => showSegment(b, "field")});
  playPlaylist(items, {network: b.network, round: b._round});
};

// ---------- state detail ----------
async function selectState(code) {
  if (!code || !runId) return;
  selected = code; render(); showTab("state");
  const d = await api(`/api/runs/${runId}/state/${code}`); const p = d.persona; const el = $("detail");
  const names = run && run.election ? Object.fromEntries(run.election.options.map(o => [o.id, o])) : {};
  const ballot = d.ballot && d.ballot.vote ? `<div class="ballot"><div class="muted">BALLOT · ${EV_TABLE[code]} electoral votes</div><div class="v" style="color:${names[d.ballot.vote]?.color||'#fff'}">${esc(names[d.ballot.vote]?.name||d.ballot.vote)}</div>margin ${d.ballot.margin} · turnout ${d.ballot.turnout}%<br><i>${esc(d.ballot.reason)}</i></div>` : "";
  el.innerHTML = `<h2>${p.name} <span class="muted">${p.region}</span></h2>${ballot}<div id="scores"></div>
    <details><summary>Persona (what the agent was told)</summary><div class="facts">${esc(p.persona)}</div></details>
    <div class="facts">unemployment ${p.unemployment_pct}% · rent $${p.median_rent}/mo (${p.rent_burden_pct}% of income) · income $${p.median_income.toLocaleString()} · federal share ${p.federal_funds_share_pct}% · baseline grievance ${p.baseline_grievance}</div>
    <div id="scene" class="scene"></div><h2 style="margin-top:10px">Posts</h2><div id="posts"></div>`;
  el._data = d; renderDetailScores();
}
function renderDetailScores() {
  const el = $("detail"); const d = el._data; if (!d || d.code !== selected) return;
  const rd = currentRound(); const s = rd && rd.states[selected]; if (!s) return;
  const b = rd.blocs.find(b => b.id === s.bloc);
  $("scores").innerHTML = `<span class="badge" style="background:${STAGE_COLORS[s.stage]}">${s.stage_name.toUpperCase()}</span>${b ? `<span class="badge" style="background:${b.color}">${esc(b.name)}</span>` : ""}
    <div class="small" style="margin-top:6px">unrest ${s.unrest}</div><div class="bar"><i style="width:${s.unrest}%;background:${unrestColor(s.unrest)}"></i></div>
    <div class="small">federal cohesion ${s.federal_cohesion}</div><div class="bar"><i style="width:${s.federal_cohesion}%;background:#3d6fd9"></i></div>
    <div class="small">neighbor cohesion ${s.neighbor_cohesion}</div><div class="bar"><i style="width:${s.neighbor_cohesion}%;background:#3c8f72"></i></div>
    <div class="small muted">grievance: ${esc(s.grievance)}${s.summary ? " · " + esc(s.summary) : ""}</div>
    <div>${(s.slogans||[]).map(x => `<span class="slogan">${esc(x)}</span>`).join("")}</div>
    <details><summary>how this was scored</summary><pre class="small muted" style="white-space:pre-wrap">${esc(JSON.stringify({observed:s.observed, rule:s.rule && s.rule.evidence, judge:s.judge && {stage:s.judge.stage, unrest:s.judge.unrest, federal:s.judge.federal_cohesion}}, null, 1))}</pre></details>`;
  let img = null;
  for (let st = s.stage; st >= 1; st--) { const k = `${selected}_${st}`; if (run.images[k] && run.images[k] !== "pending") { img = {k, f: run.images[k], st}; break; } }
  $("scene").innerHTML = img ? `<div class="small muted">protest scene · ${STAGE_NAMES[img.st]} · ${esc(run.image_notes[img.k]||"")}</div><img src="/api/runs/${runId}/media/${img.f}">` : (s.stage >= 2 ? '<div class="small muted">scene generating…</div>' : "");
  const posts = d.posts.filter(p => p.round <= rd.round && ((p.content||"").trim() || (p.quote_content||"").trim())).sort((a,b) => (b.num_likes+2*b.num_shares)-(a.num_likes+2*a.num_shares) || b.round-a.round).slice(0, 12);
  $("posts").innerHTML = posts.map(p => `<div class="post"><div class="meta">round ${p.round} · ${p.kind} · ♥${p.num_likes} ↻${p.num_shares}</div>${esc(p.kind==="quote" ? p.quote_content : p.content)}${p.kind==="quote"?`<div class="muted">↳ ${esc(p.content)}</div>`:""}</div>`).join("") || '<span class="muted">no posts yet</span>';
}

// ---------- lightbox + wide mode ----------
function openLightbox(src, caption, isVideo) {
  const lb = $("lightbox"), img = $("lbImg"), v = $("lbVideo");
  if (isVideo) { img.classList.add("hidden"); v.classList.remove("hidden"); v.src = src; v.play(); }
  else { v.classList.add("hidden"); v.pause(); v.removeAttribute("src"); img.classList.remove("hidden"); img.src = src; }
  $("lbCap").textContent = caption || ""; lb.classList.remove("hidden");
}
function closeLightbox() { const lb = $("lightbox"); lb.classList.add("hidden"); const v = $("lbVideo"); v.pause(); v.removeAttribute("src"); }
$("lightbox").onclick = e => { if (e.target.id === "lightbox" || e.target.id === "lbClose") closeLightbox(); };
document.addEventListener("keydown", e => { if (e.key === "Escape") closeLightbox(); });
document.addEventListener("click", e => {
  const t = e.target;
  if (t.closest && t.closest("#lightbox")) return;
  if (t.matches(".fi .media img, .fi .media video")) {
    const fi = t.closest(".fi"); const name = fi.querySelector(".name")?.textContent || ""; const txt = fi.querySelector(".txt")?.textContent || "";
    if (t.tagName === "VIDEO") { e.preventDefault(); openLightbox(t.currentSrc || t.src, `${name}: ${txt}`, true); }
    else openLightbox(t.src, `${name}: ${txt}` + (t.alt ? ` · ${t.alt}` : ""), false);
  } else if (t.matches(".scene img")) openLightbox(t.src, t.previousElementSibling?.textContent || "", false);
  else if (t.matches(".bc-anchor")) openLightbox(t.src, $("chyronHead").textContent + " · " + $("chyronSub").textContent, false);
});
$("wideBtn").onclick = () => { document.body.classList.toggle("wide"); setTimeout(layout, 50); };
// left column is a drawer: hidden by default, remembered per browser
try { if (localStorage.getItem("fl.left") !== "open") document.body.classList.add("left-hidden"); } catch (e) { document.body.classList.add("left-hidden"); }
$("leftToggle").onclick = () => {
  document.body.classList.toggle("left-hidden");
  try { localStorage.setItem("fl.left", document.body.classList.contains("left-hidden") ? "closed" : "open"); } catch (e) {}
  setTimeout(layout, 300);
};

// ---------- tabs ----------
function showTab(name) {
  document.querySelectorAll(".tabs button").forEach(b => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab").forEach(t => t.classList.toggle("hidden", t.id !== "tab-" + name));
}
document.querySelectorAll(".tabs button").forEach(b => b.onclick = () => showTab(b.dataset.tab));

// ---------- runs ----------
async function loadRun(id, {keepIndex=false} = {}) {
  const r = await api(`/api/runs/${id}`);
  const wasLast = !run || roundIdx >= run.rounds.length - 1;
  if (runId !== id) { phoneState = {contact: null, contacts: [], busy: false}; roster = await api(`/api/runs/${id}/roster`).catch(() => ({})); feedRound = -1; feedCache = null; marketCache = null; view = r.mode === "govern" ? "approval" : r.election ? "election" : "unrest"; document.querySelectorAll("#viewToggle button").forEach(b => b.classList.toggle("active", b.dataset.view === view)); }
  run = r; if (runId !== id) { runId = id; act = null; pollActivity(); }
  const wid = r.status === "done" && r.election && r.election.results && (r.election.results.winner || r.election.results.house_winner);
  $("continueBox").classList.toggle("hidden", !wid);
  if (wid) { const w = r.election.options.find(o => o.id === wid); $("continueMsg").textContent = `inaugurate ${w ? w.name : "the winner"}${r.election.results.winner ? "" : " (chosen by the House)"} · season ${(r.season || 1) + 1}`; }
  if (!keepIndex || (follow && wasLast)) roundIdx = Math.max(0, run.rounds.length - 1);
  render(); renderSpend(run.spend);
  if (run.rounds.length) phoneRefresh();
  if (feedRound === (currentRound()||{}).round && (run.status !== "done" || run.media_pending) && !document.body.classList.contains("episode")) loadFeed();
  if (selected) { const d = await api(`/api/runs/${runId}/state/${selected}`).catch(()=>null); if (d) { $("detail")._data = d; renderDetailScores(); } }
  clearTimeout(pollTimer);
  if (["starting","running","scoring"].includes(run.status) || run.media_pending) pollTimer = setTimeout(() => loadRun(id, {keepIndex:true}), 4000);
  setStatus();
}
async function refreshRuns() {
  const runs = await api("/api/runs"); const sel = $("runSelect"); const cur = sel.value;
  const st = r => r.status === "running" && r.engine && r.engine.status === "running" ? `running r${r.engine.round}/${r.engine.total_rounds}` : r.status === "starting" ? "starting…" : r.status;
  sel.innerHTML = runs.map(r => `<option value="${r.run_id}">${r.demo?"[demo] ":""}${r.parent_run_id ? "↳ " : ""}${(r.season||1) > 1 ? "" : (r.winner ? "S1 " : "")}${esc(r.title)} · ${st(r)} ${r.scored_rounds}/${r.rounds} · $${(r.spend||0).toFixed(2)}</option>`).join("");
  if (cur) sel.value = cur; else if (runs.length) { sel.value = runs[0].run_id; loadRun(runs[0].run_id); }
}
function setStatus() {
  if (!config) return;
  const t = run && run.llm_totals ? ` · ${run.llm_totals.calls} LLM calls` : "";
  $("status").textContent = `agents: ${config.llm.provider}/${config.llm.agent_model}${config.llm.available?"":" (offline)"} · images: ${config.images.provider}${config.images.model?" "+config.images.model.split("/").pop():""} · tts: ${config.tts} · video: ${config.video}${t}`;
}
// the form remembers what you typed (per browser); a refresh never puts the example back over your work
const FORM_FIELDS = ["scenario", "rounds", "apr", "startYear", "startMonth", "elQuestion", "candAName", "candAParty", "candALane", "candAVoice", "candABlurb", "candBName", "candBParty", "candBLane", "candBVoice", "candBBlurb", "twist"];
function saveForm() {
  try { const d = {}; FORM_FIELDS.forEach(id => { const el = $(id); if (el) d[id] = el.value; }); d.mode = $("modeSeg").dataset.mode; localStorage.setItem("fl.form", JSON.stringify(d)); } catch (e) {}
}
function restoreForm() {
  try {
    const d = JSON.parse(localStorage.getItem("fl.form") || "null"); if (!d) return false;
    FORM_FIELDS.forEach(id => { const el = $(id); if (el && d[id] != null) el.value = d[id]; });
    if (d.mode) { $("modeSeg").dataset.mode = d.mode; $("modeSeg").querySelectorAll("button").forEach(x => x.classList.toggle("active", x.dataset.mode === d.mode)); $("electionForm").classList.toggle("hidden", d.mode !== "election"); }
    [["candALane", "candABox"], ["candBLane", "candBBox"]].forEach(([sel, box]) => { $(box).className = "cand " + ({right: "red", left: "blue", center: "purple"}[$(sel).value] || ""); });
    return true;
  } catch (e) { return false; }
}
FORM_FIELDS.forEach(id => { const el = $(id); if (el) el.addEventListener("input", saveForm); if (el) el.addEventListener("change", saveForm); });
const formRestored = restoreForm();
function applyWorldDefaults() {
  if (formRestored) return;
  const w = config && config.world; if (!w) return;
  if ($("startYear") && !$("startYear").dataset.touched) $("startYear").value = w.start_year || 2026;
  if ($("startMonth") && !$("startMonth").dataset.touched) $("startMonth").value = String(w.start_month || 1);
  ["startYear", "startMonth"].forEach(id => $(id).addEventListener("change", () => { $(id).dataset.touched = "1"; }));
}
function renderLegend() {
  applyWorldDefaults();
  $("legend").innerHTML = STAGE_NAMES.map((n,i) => `<div class="item"><span class="sw" style="background:${STAGE_COLORS[i]}"></span>${i} ${n} <span class="muted">${["0-20","20-40","40-60","60-80","80-100"][i]}</span></div>`).join("") + `<div class="item"><span class="sw" style="background:#000;border:1px solid #888"></span>crack = bloc pulling away</div>`;
  $("caps").textContent = `caps: ${config.caps.max_rounds} rounds · ${config.caps.max_agents} agents · ${config.caps.max_image_calls} images · budget $${config.budget_usd}/run · bloc threshold ${config.scoring.bloc_cohesion_threshold}`;
}

// ---------- wiring ----------
let setupJob = null, setupTimer = null;
function clearView() {
  stopSpeech(); stopCalls(); clearTimeout(pollTimer); clearTimeout(actTimer); run = null; runId = null; act = null; selected = null; feedCache = null; feedRound = -1; marketCache = null; roundIdx = 0;
  gStates.selectAll("path.state").style("fill", "#1a2130").classed("contested", false).classed("territory", false);
  gCracks.selectAll("*").remove(); gLabels.selectAll("*").remove(); if (window.gWar) window.gWar.selectAll("*").remove();
  $("feed").innerHTML = ""; $("trending").textContent = ""; $("blocs").innerHTML = ""; $("term").innerHTML = ""; $("tickerList").innerHTML = ""; $("marketHeadlines").innerHTML = ""; $("indexChart").innerHTML = "";
  $("warRoom").classList.add("hidden"); $("dateBadge").classList.add("hidden"); $("evBar").classList.add("hidden"); $("viewToggle").classList.add("hidden");
  $("roundLabel").textContent = "starting…"; $("ticker").textContent = "FAULT LINES NETWORK · starting a new run"; $("tape").textContent = "FLN MARKETS · starting";
  // the broadcast card and the state card still show the old run otherwise
  $("bcAnchor").removeAttribute("src"); $("bcAnchor").classList.add("hidden"); $("bcVideo").classList.add("hidden"); $("bcStage").classList.add("hidden"); $("bcStage").innerHTML = ""; delete $("bcStage").dataset.ids; $("bcTabs").innerHTML = "";
  $("chyronHead").textContent = "FAULT LINES NETWORK"; $("chyronSub").textContent = "standing by"; $("bcScript").textContent = ""; $("bcMode").textContent = ""; $("bcNetName").textContent = "FLN";
  $("detail").innerHTML = "<h2>Click a state</h2><p class=\"muted\">Persona, ladder stage, top posts, and the protest scene.</p>";
  $("callToast").classList.add("hidden"); $("phoneContacts").innerHTML = ""; $("phoneBadge").classList.add("hidden");
}
function showSetup(title, note) { $("setupOverlay").classList.remove("hidden"); $("setupTitle").textContent = title; $("setupNote").textContent = note || ""; $("setupElapsed").textContent = ""; setupJob = setupJob || "pending"; renderLive(); }
function hideSetup() { $("setupOverlay").classList.add("hidden"); clearTimeout(setupTimer); setupJob = null; renderLive(); pollActivity(); }
async function watchJob(jobId, title, onDone) {
  setupJob = jobId; const t0 = Date.now();
  const tick = async () => {
    if (setupJob !== jobId) return;
    let j = null; try { j = await api(`/api/jobs/${jobId}`); } catch (e) { $("setupNote").textContent = "lost contact with the server… retrying"; setupTimer = setTimeout(tick, 2000); return; }
    $("setupTitle").textContent = title; $("setupNote").textContent = j.note || ""; $("setupElapsed").textContent = `${Math.round((Date.now() - t0) / 1000)} s`;
    if (j.status === "error") { $("setupTitle").textContent = "That did not start"; $("setupNote").textContent = j.error || "unknown error"; setTimeout(hideSetup, 6000); $("runBtn").disabled = false; $("continueBtn").disabled = false; return; }
    if (j.status === "done") { $("runBtn").disabled = false; $("continueBtn").disabled = false; onDone(j.run_id); return; }
    setupTimer = setTimeout(tick, 1200);
  };
  tick();
}
function watchFirstRound(id) {
  // the run exists: keep the panel up until the first scored round arrives, showing the engine's own progress
  const t0 = Date.now() - 1000 * (parseInt($("setupElapsed").textContent) || 0);
  const tick = async () => {
    $("setupElapsed").textContent = `${Math.round((Date.now() - t0) / 1000)} s`;
    if (!run || runId !== id) { hideSetup(); return; }
    if (run.rounds && run.rounds.some(r => (r.round || 0) >= 1)) { hideSetup(); return; }   // round 0 is only the seed
    const eng = run.engine || {}; const n = eng.round != null && eng.total_rounds ? `round ${Math.max(1, eng.round)} of ${eng.total_rounds}` : "the first round";
    $("setupTitle").textContent = "The agents are posting"; $("setupNote").textContent = eng.status === "running" ? `writing ${n}; the first scored round appears in about a minute` : (eng.note || "warming up");
    setupTimer = setTimeout(tick, 2000);
  };
  tick();
}
$("runBtn").onclick = async () => {
  $("runMsg").textContent = "starting…"; saveForm();
  try {
    const body = {text: $("scenario").value, rounds: +$("rounds").value, agents_per_round: +$("apr").value, demo: ($("demoBox") && $("demoBox").checked) || new URLSearchParams(location.search).has("demo"),
                  start_year: +$("startYear").value || null, start_month: +$("startMonth").value || 1};
    if ($("modeSeg").dataset.mode === "election") body.election = {question: $("elQuestion").value, options: [
      {name: $("candAName").value, party: $("candAParty").value, blurb: $("candABlurb").value, voice: $("candAVoice").value, lane: $("candALane").value},
      {name: $("candBName").value, party: $("candBParty").value, blurb: $("candBBlurb").value, voice: $("candBVoice").value, lane: $("candBLane").value}]};
    $("runBtn").disabled = true; clearView(); showSetup("Starting a run", "sending the scenario to the server");
    const r = await api("/api/runs", {method:"POST", headers:{"content-type":"application/json"}, body: JSON.stringify(body)});
    $("runMsg").textContent = "setting up…";
    watchJob(r.job_id, "Setting up the run", async id => {
      $("runMsg").textContent = `run ${id} started`; follow = true; roundIdx = 0;
      await refreshRuns(); $("runSelect").value = id; await loadRun(id); showTab("feed"); watchFirstRound(id);
    });
  } catch (e) { $("runMsg").textContent = "error: " + e.message; hideSetup(); $("runBtn").disabled = false; }
};
$("runSelect").onchange = e => { selected = null; follow = true; stopSpeech(); stopCalls(); callRound = -1; loadRun(e.target.value); };
const MODE_HINT = {unrest: "States react to the scenario round by round; blocs form and crack.",
                   election: "Two candidates campaign, debate, and the states vote after the last round. Then you can continue into their term."};
$("modeSeg").querySelectorAll("button").forEach(b => b.onclick = () => {
  const m = b.dataset.mode; $("modeSeg").dataset.mode = m;
  $("modeSeg").querySelectorAll("button").forEach(x => x.classList.toggle("active", x === b));
  $("electionForm").classList.toggle("hidden", m !== "election"); $("modeHint").textContent = MODE_HINT[m];
});
// the box color follows the lane: right = red, left = blue, center = purple
const laneClass = {right: "red", left: "blue", center: "purple"};
[["candALane", "candABox"], ["candBLane", "candBBox"]].forEach(([sel, box]) => { const f = () => { $(box).className = "cand " + laneClass[$(sel).value]; }; $(sel).onchange = f; f(); });
$("refreshRuns").onclick = refreshRuns;
$("stopBtn").onclick = async () => { if (!runId) return; $("lbPhase").textContent = "stop requested; the current round finishes first"; await api(`/api/runs/${runId}/stop`, {method:"POST"}); setTimeout(pollActivity, 1500); };
$("continueBtn").onclick = async () => {
  if (!runId) return;
  $("continueBtn").disabled = true; $("continueMsg").textContent = "setting up the next season…"; saveForm();
  try {
    const parent = runId;
    const r = await api(`/api/runs/${parent}/continue`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({twist: $("twist").value})});
    clearView(); showSetup("Setting up the next season", "writing the history book");
    watchJob(r.job_id, "Setting up the next season", async id => {
      $("continueMsg").textContent = `season started: ${id}`; follow = true; roundIdx = 0;
      await refreshRuns(); $("runSelect").value = id; await loadRun(id); showTab("term"); watchFirstRound(id);
    });
  } catch (e) { $("continueMsg").textContent = "error: " + e.message; hideSetup(); $("continueBtn").disabled = false; }
};

// ---------- the term (governing seasons) ----------
function seatsBar(ch, total) {
  const lanes = Object.keys(ch || {});
  return `<div class="seats">${lanes.map(l => `<i style="width:${100 * ch[l] / total}%;background:${LANE_COLOR[l] || LANE_COLOR.other}" title="${l} ${ch[l]}"></i>`).join("")}</div>
          <div class="small muted">${lanes.map(l => `${l} ${ch[l]}`).join(" · ")}</div>`;
}
function renderTechCard() {
  const t = run && run.tech; if (!t) return "";
  return `<div class="card"><h2>Technology · ${t.year} · ${esc(t.era)}</h2>${t.tracks.map(tr => `<div class="techline"><span class="tk">${esc(tr.track)}</span>${tr.tiers.map(x => `<span class="tier ${x.unlocked ? "on" : ""} ${x.gated ? "gated" : ""}" data-name="${esc(x.name)}${x.year ? " (" + x.year + ")" : ""}"></span>`).join("")}</div>`).join("")}
    ${(t.headlines || []).length ? `<div class="small" style="margin-top:6px">${t.headlines.map(h => `<div>${esc(h)}</div>`).join("")}</div>` : ""}<div class="small muted">pace: ${esc(t.pace)} · hover a tier for its name</div></div>`;
}
function renderTerm() {
  const g = run && run.govern; const el = $("term");
  if (!g) { el.innerHTML = (run && run.tech ? renderTechCard() : "") + renderCrimeCard() || "not a governing season"; return; }
  const rd = currentRound(); const appr = approvalAt(rd) || g.approval; const pres = g.president || {};
  const series = g.approval_series || [];
  const W = 360, H = 70, xs = series.length > 1 ? series.map((_, i) => 8 + i * (W - 16) / (series.length - 1)) : [W / 2];
  const y = v => H - 6 - (Math.max(10, Math.min(90, v)) - 10) / 80 * (H - 12);
  const line = series.map((p, i) => `${i ? "L" : "M"}${xs[i].toFixed(1)},${y(p.national).toFixed(1)}`).join(" ");
  const chart = `<svg class="appr-chart" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none"><line x1="0" x2="${W}" y1="${y(50)}" y2="${y(50)}" stroke="#2b3442" stroke-dasharray="3 3"/>
      ${line ? `<path d="${line}" fill="none" stroke="#4f9a6a" stroke-width="2"/>` : ""}${series.map((p, i) => `<circle cx="${xs[i]}" cy="${y(p.national)}" r="2.5" fill="${approvalColor(p.national)}"><title>${p.label}: ${p.national}%</title></circle>`).join("")}</svg>`;
  const pol = (g.policies || []).map(p => `<tr><td><b>${esc(p.name)}</b><div class="muted">${esc(p.summary || "")} · $${Math.round(p.cost_bn)}bn/yr · lag ${p.lag_quarters}q</div>${p.unintended ? `<div class="muted">risk: ${esc(p.unintended)}</div>` : ""}</td>
      <td class="st-${p.status}">${p.status.replace("_", " ")}<div class="muted small">${p.status === "pending" ? "vote Q" + p.vote_quarter : (p.history || []).slice(-1).map(h => esc(h.event)).join("")}</div></td></tr>`).join("");
  const qs = (g.quarters || []).slice().reverse().map(q => `<div class="q"><b>${q.label}</b> · approval ${q.approval}%${q.vote ? ` · <span class="st-${q.vote.result}">${esc(q.vote.name)}: ${q.vote.result.replace("_", " ")}</span>` : ""}
      ${(q.events || []).map(e => `<div class="ev">⚡ ${esc(e.name)}${e.response ? `<div class="resp">“${esc(e.response)}”</div>` : ""}</div>`).join("")}
      ${(q.effects || []).filter(e => /lagged|unintended|struck/.test(e.tag)).map(e => `<div class="small muted">↳ ${esc(e.tag)}</div>`).join("")}
      ${q.struck_down && q.struck_down.length ? `<div class="small st-struck_down">courts struck down: ${esc(q.struck_down.join(", "))}</div>` : ""}</div>`).join("");
  const recap = g.recap ? `<div class="card"><h2>Season recap</h2><div class="small">Approval ${g.recap.approval_start}% → ${g.recap.approval_end}%${g.recap.reelected == null ? "" : g.recap.reelected ? " · RE-ELECTED" : " · DEFEATED"}</div>
      <div class="small">Worked: ${esc((g.recap.policies_worked || []).join(", ") || "nothing clearly")}</div><div class="small">Backfired: ${esc((g.recap.policies_backfired || []).join("; ") || "nothing")}</div>
      <div class="small">Biggest moments: ${esc((g.recap.biggest_moments || []).join(", "))}</div><pre class="small muted" style="white-space:pre-wrap">${esc(g.recap.record || "")}</pre></div>` : "";
  el.className = "term"; el.innerHTML = `
    <div class="card"><div class="pres">${pres.portrait ? `<img src="${pres.portrait}" alt="">` : ""}<div><div class="small muted">SEASON ${g.season} · PRESIDENT</div><div><b>${esc(pres.name || "")}</b> <span class="muted small">${esc(pres.party || "")}</span></div>
      <div class="appr" style="color:${approvalColor(appr.national)}">${appr.national}%</div><div class="small muted">national approval · ${rd && rd.quarter ? rd.quarter.label : "inauguration"}</div></div></div>${chart}</div>
    <div class="card"><h2>Congress</h2><div class="small">House (435)</div>${seatsBar(g.congress.house, 435)}<div class="small" style="margin-top:6px">Senate (100)</div>${seatsBar(g.congress.senate, 100)}${g.midterm ? `<div class="small muted">midterms after Q8: swing ${g.midterm.swing > 0 ? "+" : ""}${g.midterm.swing} for the President's side</div>` : ""}</div>
    <div class="card"><h2>Agenda</h2><table>${pol}</table>${g.executive_orders && g.executive_orders.length ? `<div class="small muted">executive orders in force: ${g.executive_orders.length}</div>` : ""}</div>
    ${recap}
    ${renderTechCard()}
    ${renderCrimeCard()}
    <div class="card"><h2>Quarter by quarter</h2>${qs || '<div class="muted small">the first quarter is being simulated…</div>'}</div>`;
}
$("scrub").oninput = e => { roundIdx = +e.target.value; follow = roundIdx >= run.rounds.length - 1; stopSpeech(); render(); };
$("playBtn").onclick = () => {
  if (playing) { clearInterval(playing); playing = null; $("playBtn").textContent = "▶"; return; }
  if (run && roundIdx >= run.rounds.length - 1) roundIdx = 0;
  $("playBtn").textContent = "❚❚";
  playing = setInterval(() => { if (!run) return; if (roundIdx < run.rounds.length - 1) { roundIdx++; render(); } else { clearInterval(playing); playing = null; $("playBtn").textContent = "▶"; } }, 2500);
};

(async () => { renderLive(); config = await api("/api/config"); renderLegend(); await initMap(); await refreshRuns(); setStatus(); booted = true; renderLive(); })();


// ---------- THE PHONE: text any character as an insider ----------
let phoneState = {contact: null, contacts: [], busy: false};
async function phoneRefresh() {
  if (!runId) return;
  const d = await api(`/api/runs/${runId}/phone`).catch(() => null); if (!d) return;
  phoneState.contacts = d.contacts;
  const badge = $("phoneBadge"); badge.textContent = d.unread; badge.classList.toggle("hidden", !d.unread);
  if (!phoneState.contact) renderContacts();
}
function renderContacts() {
  const el = $("phoneContacts"); el.classList.remove("hidden"); $("phoneThread").classList.add("hidden"); $("phoneCompose").classList.add("hidden"); $("phoneBack").classList.add("hidden");
  $("phoneTitle").innerHTML = `<b>Phone</b> <span class="muted small">you are an insider; influence is earned</span>`;
  el.innerHTML = phoneState.contacts.map(c => `<div class="ct" data-id="${c.id}"><img src="${c.avatar}" alt="" onerror="this.src='/api/avatars/${encodeURIComponent(c.name)}.png'">
      <div><div class="nm">${esc(c.name)} ${c.unread ? `<span class="ub">${c.unread}</span>` : ""}</div><div class="kd">${esc(c.kind)}${c.tag ? " · " + esc(c.tag) : ""}</div>${c.last ? `<div class="lastmsg">${c.last.from === "player" ? "You: " : ""}${esc(c.last.text || "(left on read)")}</div>` : ""}</div>
      <div class="tr">trust ${c.trust}<i><b style="width:${c.trust}%"></b></i></div></div>`).join("") || '<div class="muted small">no contacts yet (a run needs a scored round)</div>';
  el.querySelectorAll(".ct").forEach(x => x.onclick = () => openThread(x.dataset.id));
}
async function openThread(cid) {
  const t = await api(`/api/runs/${runId}/phone/${encodeURIComponent(cid)}`).catch(() => null); if (!t) return;
  phoneState.contact = cid; phoneState.thread = t;
  $("phoneContacts").classList.add("hidden"); $("phoneThread").classList.remove("hidden"); $("phoneCompose").classList.remove("hidden"); $("phoneBack").classList.remove("hidden");
  $("phoneTitle").innerHTML = `<div class="thread-head"><img src="${t.avatar}" alt="" onerror="this.src='/api/avatars/${encodeURIComponent(t.name)}.png'"><div><b>${esc(t.name)}</b><div class="small muted">${esc(t.kind)}${t.tag ? " · " + esc(t.tag) : ""} · trust ${t.trust}</div></div></div>`;
  renderThread(t.thread); phoneRefresh();
}
function renderThread(thread, typing) {
  const el = $("phoneThread"); let last = null; const parts = [];
  (thread || []).forEach(m => { if (m.date !== last) { parts.push(`<div class="stamp">${esc(m.date || "")}</div>`); last = m.date; }
    if (m.from === "player") parts.push(`<div class="bub me">${esc(m.text)}</div>`);
    else if (m.text) parts.push(`<div class="bub them">${esc(m.text)}</div>`);
    else parts.push(`<div class="bub silent">${esc(m.mood || "no reply")}</div>`); });
  if (typing) parts.push(`<div class="typing">typing <i>·</i><i>·</i><i>·</i></div>`);
  el.innerHTML = parts.join("") || '<div class="muted small">Say something. They may not answer.</div>'; el.scrollTop = el.scrollHeight;
}
$("phoneBack").onclick = () => { phoneState.contact = null; renderContacts(); };
$("phoneSend").onclick = async () => {
  const text = $("phoneText").value.trim(); if (!text || !phoneState.contact || phoneState.busy) return;
  phoneState.busy = true; $("phoneText").value = ""; const cid = phoneState.contact;
  const cur = (phoneState.thread && phoneState.thread.thread) || []; renderThread([...cur, {from: "player", text, date: cur.length ? cur[cur.length - 1].date : ""}], true);
  try { const r = await api(`/api/runs/${runId}/phone/${encodeURIComponent(cid)}`, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify({text})});
    phoneState.thread.thread = r.thread; renderThread(r.thread); $("phoneTitle").querySelector(".small").textContent = `${phoneState.thread.kind} · trust ${r.trust}`; }
  catch (e) { renderThread([...cur, {from: "player", text, date: ""}, {from: "char", text: "", mood: "error: " + e.message}]); }
  phoneState.busy = false;
};
$("phoneText").addEventListener("keydown", e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("phoneSend").click(); } });
document.querySelector('.tabs button[data-tab="phone"]').addEventListener("click", () => { phoneState.contact = null; phoneRefresh(); });
