/* SETTINGS + FIRST RUN: the OpenRouter key, voices and the spending cap from the app instead of the .env file.
   Every piece is optional; the panel says in plain words what works now and what each key adds. */
const ST = {fields: [], setup: null, clear: new Set(), welcome: false};

const GROUPS = [
  {id: "text", title: "The AI behind the 50 states",
   intro: "One OpenRouter key runs your own scenarios: the states, the judge, the news and the debates. It also draws the pictures (about half a cent each). Without a key you can still watch a demo run."},
  {id: "voices", title: "Voices",
   intro: "With a Fish Audio or Cartesia key, the anchors, reporters and candidates speak. Without one, everything still plays; you read the words instead."},
  {id: "spend", title: "Spending", intro: "Only paid services count toward the cap."},
];

function stEsc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, c => ({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"}[c])); }
async function stApi(path, opts) {
  const r = await fetch(path, opts);
  const j = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(j.detail || `HTTP ${r.status}`);
  return j;
}
const stPost = (path, body) => stApi(path, {method: "POST", headers: {"content-type": "application/json"}, body: JSON.stringify(body || {})});

function statusLines(s) {
  const t = s.text;
  const text = t.ready ? `<b class="ok">Ready</b> · ${t.cloud ? "OpenRouter" : stEsc(t.provider)}`
    : `<b class="no">Not set up</b> · demo runs only, until you add an OpenRouter key below`;
  const eng = s.engine.ready ? `<b class="ok">Installed</b>` : `<b class="no">Missing</b> · run the installer again (start.cmd on Windows, start.sh on Mac and Linux)`;
  const pics = s.pictures === "flux" ? `<b class="ok">AI pictures</b>` : `<b class="meh">Placeholders</b> (free)`;
  const voices = s.voices !== "none" ? `<b class="ok">${s.voices === "fish" ? "Fish Audio" : "Cartesia"}</b>` : s.fallback_voice === "browser" ? `<b class="meh">Browser voice</b>` : `<b class="meh">Silent</b> · words on screen`;
  return `<div class="st-status">
    <div><span>AI</span>${text}</div><div><span>Simulation engine</span>${eng}</div>
    <div><span>Pictures</span>${pics}</div><div><span>Voices</span>${voices}</div>
    <div><span>Spending cap</span><b>$${(+s.budget_usd).toFixed(2)} a run</b></div></div>`;
}

function fieldHtml(f) {
  const help = f.help ? `<div class="hint">${stEsc(f.help)}</div>` : "";
  if (f.kind === "secret") {
    const cleared = ST.clear.has(f.key);
    const ph = cleared ? "will be removed when you save" : f.set ? `saved (${f.hint}); paste a new one to replace it` : "paste your key here";
    return `<div class="st-field" data-key="${f.key}"><label>${stEsc(f.label)}</label>
      <div class="st-row"><input type="password" autocomplete="off" spellcheck="false" data-secret="${f.key}" placeholder="${stEsc(ph)}">
        ${f.test ? `<button type="button" data-test="${f.test}" data-for="${f.key}">Test</button>` : ""}
        ${f.set && !cleared ? `<button type="button" class="st-remove" data-clear="${f.key}" title="forget this key">Remove</button>` : ""}</div>
      <div class="st-result" id="res_${f.key}"></div>${help}</div>`;
  }
  if (f.kind === "choice") {
    const cur = f.value || f.choices[0][0];
    return `<div class="st-field"><label>${stEsc(f.label)}</label><select data-plain="${f.key}">${f.choices.map(([v, l]) => `<option value="${v}" ${v === cur ? "selected" : ""}>${stEsc(l)}</option>`).join("")}</select>${help}</div>`;
  }
  const ph = f.key === "RUN_BUDGET_USD" ? (ST.setup ? String(ST.setup.budget_usd) : "5") : "";
  return `<div class="st-field"><label>${stEsc(f.label)}</label><input type="text" data-plain="${f.key}" value="${stEsc(f.value)}" placeholder="${stEsc(ph)}">${help}</div>`;
}

function renderSettings() {
  const s = ST.setup; const box = $("settingsBox");
  const by = Object.fromEntries(GROUPS.map(g => [g.id, ST.fields.filter(f => f.group === g.id)]));
  const welcome = ST.welcome ? `<div class="st-welcome"><h3>Welcome to Fault Lines</h3>
      <p>Nothing here is required. You can watch a demo run right away. To run your own scenarios, paste an OpenRouter key below (get one at <a href="https://openrouter.ai/keys" target="_blank" rel="noopener">openrouter.ai/keys</a>); a run usually costs under a dollar, and the spending cap stops paid pictures and voices at $5. Voice keys are optional.</p>
      <div class="st-actions"><button type="button" class="primary-sm" id="stDemo">Try a demo run now</button><button type="button" id="stLater">Set up first</button></div></div>` : "";
  box.innerHTML = `<div class="st-head"><h2>Settings</h2><button type="button" id="stClose" title="close">✕</button></div>
    ${welcome}${statusLines(s)}
    ${GROUPS.map(g => `<section class="st-group"><h3>${g.title}</h3><div class="hint">${g.intro}</div>${by[g.id].map(fieldHtml).join("")}</section>`).join("")}
    <div class="st-foot"><button type="button" class="primary-sm" id="stSave">Save</button><span id="stMsg" class="muted small"></span>
      <div class="hint">Keys are saved in the <code>.env</code> file in the app's folder, on this computer only. They are never shown again in full.</div></div>`;
  wireSettings();
}

function wireSettings() {
  $("stClose").onclick = closeSettings;
  if ($("stLater")) $("stLater").onclick = () => { ST.welcome = false; renderSettings(); };
  if ($("stDemo")) $("stDemo").onclick = () => { closeSettings(); if ($("demoBox")) $("demoBox").checked = true; $("runBtn").click(); };
  $("settingsBox").querySelectorAll("[data-test]").forEach(b => b.onclick = async () => {
    const key = b.dataset.for; const res = $("res_" + key); const v = $("settingsBox").querySelector(`[data-secret="${key}"]`).value.trim();
    res.className = "st-result"; res.textContent = "checking… (a free check, nothing is charged)";
    try { const r = await stPost("/api/settings/test", {name: b.dataset.test, value: v || null}); res.className = "st-result " + (r.ok ? "ok" : "no"); res.textContent = (r.ok ? "✓ " : "✗ ") + r.message; }
    catch (e) { res.className = "st-result no"; res.textContent = "✗ " + e.message; }
  });
  $("settingsBox").querySelectorAll("[data-clear]").forEach(b => b.onclick = () => { ST.clear.add(b.dataset.clear); renderSettings(); });
  $("stSave").onclick = () => saveSettings();
}

async function saveSettings() {
  const values = {};
  $("settingsBox").querySelectorAll("[data-secret]").forEach(i => { if (i.value.trim()) values[i.dataset.secret] = i.value.trim(); });
  $("settingsBox").querySelectorAll("[data-plain]").forEach(i => { values[i.dataset.plain] = i.value.trim(); });
  $("stMsg").textContent = "saving…";
  try {
    const r = await stPost("/api/settings", {values, clear: [...ST.clear]});
    ST.fields = r.fields; ST.setup = r.setup; ST.clear.clear();
    config = r.config; if (typeof setStatus === "function") setStatus();
    renderSettings(); applySetupToForm();
    $("stMsg").textContent = r.changed.length ? `saved: ${r.changed.length} change${r.changed.length > 1 ? "s" : ""}, already in effect` : "nothing changed";
  } catch (e) { $("stMsg").textContent = "not saved: " + e.message; }
}

async function openSettings(welcome) {
  ST.welcome = !!welcome; ST.clear.clear();
  $("settingsModal").classList.remove("hidden");
  $("settingsBox").innerHTML = `<div class="muted">loading…</div>`;
  try { const r = await stApi("/api/settings"); ST.fields = r.fields; ST.setup = r.setup; renderSettings(); }
  catch (e) { $("settingsBox").innerHTML = `<div class="st-head"><h2>Settings</h2><button type="button" id="stClose">✕</button></div><p class="no">${stEsc(e.message)}</p>`; $("stClose").onclick = closeSettings; }
}
function closeSettings() {
  $("settingsModal").classList.add("hidden");
  try { localStorage.setItem("fl.welcomed", "1"); } catch (e) {}
}

// the run form: with no OpenRouter key, runs are demo runs (and the box says so)
function applySetupToForm() {
  const s = ST.setup; if (!s || !$("demoBox")) return;
  const forced = !s.can_run;
  $("demoBox").disabled = forced; if (forced) $("demoBox").checked = true;
  $("demoHint").textContent = forced ? (s.text.ready ? "The simulation engine is missing, so runs are demos for now." : "No OpenRouter key yet, so runs are demos. Open Settings to add one.")
    : "A demo uses canned text instead of the AI: quick, free, good for a look around.";
}

$("settingsBtn").onclick = () => openSettings(false);
$("settingsModal").addEventListener("click", e => { if (e.target.id === "settingsModal") closeSettings(); });
document.addEventListener("keydown", e => { if (e.key === "Escape" && !$("settingsModal").classList.contains("hidden")) closeSettings(); });
(async () => {
  try { ST.setup = await stApi("/api/setup"); } catch (e) { return; }
  applySetupToForm();
  let seen = false; try { seen = localStorage.getItem("fl.welcomed") === "1"; } catch (e) {}
  const tour = new URLSearchParams(location.search).has("tour");
  if (!seen && !tour && !ST.setup.can_run) openSettings(true);
})();
