// State, fetch, and UI wiring. No geometry math here (rule 2) -- this module
// only reads the plan JSON and hands rectangles to render2d.js / render3d.js.

const state = {
  plan: null,
  currentFloor: 0,
  currentView: "2d",
};

const el = (id) => document.getElementById(id);

const PRESETS = {
  "3bhk": { plot_width_ft: 40, plot_depth_ft: 60, floors: 2, bedrooms: 3, bathrooms: 2, parking: true, balcony: true },
  "2bhk": { plot_width_ft: 30, plot_depth_ft: 45, floors: 1, bedrooms: 2, bathrooms: 1, parking: false, balcony: true },
  "tiny": { plot_width_ft: 15, plot_depth_ft: 20, floors: 1, bedrooms: 4, bathrooms: 2, parking: true, balcony: true },
};

function readSpecFromForm() {
  return {
    plot_width_ft: parseFloat(el("plot_width_ft").value),
    plot_depth_ft: parseFloat(el("plot_depth_ft").value),
    floors: parseInt(el("floors").value, 10),
    bedrooms: parseInt(el("bedrooms").value, 10),
    bathrooms: parseInt(el("bathrooms").value, 10),
    parking: el("parking").checked,
    balcony: el("balcony").checked,
    setback_front_m: parseFloat(el("setback_front_m").value),
    setback_rear_m: parseFloat(el("setback_rear_m").value),
    setback_left_m: parseFloat(el("setback_left_m").value),
    setback_right_m: parseFloat(el("setback_right_m").value),
    cost_rates: {
      structure_per_sqft: parseFloat(el("rate_structure").value),
      finishes_per_sqft: parseFloat(el("rate_finishes").value),
      electrical_per_sqft: parseFloat(el("rate_electrical").value),
      plumbing_per_bathroom: parseFloat(el("rate_plumbing").value),
      parking_per_sqft: parseFloat(el("rate_parking").value),
    },
  };
}

function writeSpecToForm(spec) {
  el("plot_width_ft").value = spec.plot_width_ft;
  el("plot_depth_ft").value = spec.plot_depth_ft;
  el("floors").value = spec.floors;
  el("bedrooms").value = spec.bedrooms;
  el("bathrooms").value = spec.bathrooms;
  el("parking").checked = !!spec.parking;
  el("balcony").checked = !!spec.balcony;
  if (spec.setback_front_m !== undefined) el("setback_front_m").value = spec.setback_front_m;
  if (spec.setback_rear_m !== undefined) el("setback_rear_m").value = spec.setback_rear_m;
  if (spec.setback_left_m !== undefined) el("setback_left_m").value = spec.setback_left_m;
  if (spec.setback_right_m !== undefined) el("setback_right_m").value = spec.setback_right_m;
}

function showError(msg) {
  const b = el("errorBanner");
  if (!msg) { b.style.display = "none"; b.textContent = ""; return; }
  b.style.display = "block";
  b.textContent = msg;
}

async function generate() {
  const spec = readSpecFromForm();
  showError(null);
  el("loadingSpinner").style.display = "flex";
  try {
    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(spec),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      throw new Error(body.detail ? JSON.stringify(body.detail) : `Server error ${res.status}`);
    }
    const plan = await res.json();
    state.plan = plan;
    // Stay on the floor the user was looking at. Resetting to 0 on every
    // regenerate bounced them to the ground floor whenever they nudged a rate.
    const maxFloor = (plan.floors && plan.floors.length) ? plan.floors.length - 1 : 0;
    state.currentFloor = Math.min(state.currentFloor, maxFloor);
    renderNotes();
    renderFloorTabs();
    renderAll();
    renderIssues();
    if (window.renderCost) window.renderCost();
    // Rule-based fixes are fast (~0.4 s) so they load automatically; the AI
    // pass costs ~7 s and a token, so it stays behind a button.
    const failing = (plan.issues || []).filter(i => i.level === "fail").length;
    if (failing > 0 || !plan.ok) fetchRecommendations(false);
    else renderRecommendations(null);
  } catch (err) {
    showError(err.message || String(err));
  } finally {
    el("loadingSpinner").style.display = "none";
  }
}

let _genTimer = null;
function debouncedGenerate() {
  if (_genTimer) clearTimeout(_genTimer);
  _genTimer = setTimeout(generate, 160);
}

function renderNotes() {
  const b = el("noteBanner");
  if (!b) return;
  const notes = (state.plan && state.plan.notes) || [];
  if (!notes.length) { b.style.display = "none"; b.textContent = ""; return; }
  b.style.display = "block";
  b.textContent = notes.join("  ");
}

function renderFloorTabs() {
  const wrap = el("floorTabs");
  wrap.innerHTML = "";
  if (!state.plan || !state.plan.ok) return;
  state.plan.floors.forEach(f => {
    const btn = document.createElement("button");
    btn.textContent = f.index === 0 ? "Ground Floor" : `Floor ${f.index}`;
    if (f.index === state.currentFloor) btn.classList.add("active");
    btn.addEventListener("click", () => {
      state.currentFloor = f.index;
      renderFloorTabs();
      renderAll();
    });
    wrap.appendChild(btn);
  });
}

function renderAll() {
  if (!state.plan) return;
  if (!state.plan.ok) {
    el("plan2d").innerHTML = "";
    el("score-badge").textContent = "plan infeasible";
    return;
  }
  const floor = state.plan.floors.find(f => f.index === state.currentFloor) || state.plan.floors[0];
  el("score-badge").textContent = `adjacency score ${state.plan.adjacency_score} (floor ${floor.adjacency_score})`;

  if (state.currentView === "2d") {
    renderPlan2D(el("plan2d"), state.plan, state.currentFloor, el("tooltip"));
  } else {
    renderPlan3D(el("plan3d"), state.plan);
  }
}

function renderIssues() {
  const list = el("issuesList");
  const summary = el("issuesSummary");
  list.innerHTML = "";
  if (!state.plan) return;
  const issues = state.plan.issues || [];
  const counts = { ok: 0, warn: 0, fail: 0 };
  issues.forEach(i => counts[i.level] = (counts[i.level] || 0) + 1);
  summary.textContent = `${counts.fail || 0} failed - ${counts.warn || 0} warnings - ${counts.ok || 0} passed`;
  summary.style.color = counts.fail ? "var(--fail)" : (counts.warn ? "var(--warn)" : "var(--ok)");

  const order = { fail: 0, warn: 1, ok: 2 };
  const sorted = [...issues].sort((a, b) => order[a.level] - order[b.level]);
  sorted.forEach(issue => {
    const div = document.createElement("div");
    div.className = `issue ${issue.level}`;
    let text = `<div class="msg">${issue.message}</div>`;
    if (issue.actual !== null && issue.actual !== undefined) {
      text += `<div class="nums">actual: ${issue.actual}${issue.required !== null && issue.required !== undefined ? " · required: " + issue.required : ""}</div>`;
    }
    if (issue.fix) text += `<div class="nums">fix: ${issue.fix}</div>`;
    div.innerHTML = text;
    list.appendChild(div);
  });
}

// ---- recommendations -------------------------------------------------------

function applyRecommendation(patch) {
  Object.keys(patch).forEach(key => {
    const input = el(key);
    if (!input) return;
    if (input.type === "checkbox") input.checked = !!patch[key];
    else input.value = patch[key];
  });
  generate();
}

function renderRecommendations(data, loading) {
  const panel = el("recPanel"), list = el("recList");
  const hint = el("recHint"), src = el("recSource");
  if (!panel) return;
  if (!data && !loading) { panel.style.display = "none"; return; }
  panel.style.display = "block";
  list.innerHTML = "";

  if (loading) { hint.textContent = "Checking which changes would actually fix it..."; src.textContent = ""; return; }

  const recs = data.recommendations || [];
  hint.textContent = data.fails_before
    ? `${data.fails_before} failing check${data.fails_before > 1 ? "s" : ""}. Each option below was re-run through the engine, so the numbers are measured, not guessed.`
    : "";

  if (!recs.length) {
    const d = document.createElement("div");
    d.className = "rec";
    d.innerHTML = `<div class="recWhy">${data.message || "No suggestion available."}</div>`;
    list.appendChild(d);
  }

  recs.forEach(rec => {
    const d = document.createElement("div");
    d.className = "rec" + (rec.resolves_all ? " clears" : "");
    const badge = rec.resolves_all
      ? `<span class="recBadge clears">${rec.fails_before} -> 0 · all clear</span>`
      : `<span class="recBadge">${rec.fails_before} -> ${rec.fails_after} left</span>`;
    const cost = rec.cost_after
      ? ` · Rs ${Number(rec.cost_after).toLocaleString("en-IN")}` : "";
    d.innerHTML =
      `<div class="recTitle">${rec.title}</div>` +
      `<div class="recChange">${rec.change}</div>` +
      `<div class="recWhy">${rec.why || ""}</div>` +
      `<div class="recFoot">${badge}<span></span></div>`;
    const foot = d.querySelector(".recFoot");
    const meta = foot.lastElementChild;
    meta.style.cssText = "font-size:11px;color:var(--muted);margin-left:auto;margin-right:8px";
    meta.textContent = (rec.footprint_after ? `${rec.footprint_after} m2` : "") + cost;
    const btn = document.createElement("button");
    btn.textContent = "Apply";
    btn.addEventListener("click", () => applyRecommendation(rec.patch));
    foot.appendChild(btn);
    list.appendChild(d);
  });

  src.textContent = data.source === "ai"
    ? "Proposed by AI, verified by the engine."
    : (data.error ? `Rule-based (AI unavailable: ${data.error})`
                  : "Rule-based. Press “Ask AI” for model-proposed options.");
}

async function fetchRecommendations(useAi) {
  const btn = el("aiRecBtn");
  renderRecommendations(null, true);
  if (useAi && btn) { btn.disabled = true; btn.textContent = "Thinking..."; }
  try {
    const res = await fetch("/api/recommend", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ spec: readSpecFromForm(), use_ai: !!useAi }),
    });
    renderRecommendations(await res.json());
  } catch (err) {
    renderRecommendations({ recommendations: [], fails_before: 0,
                            message: "Could not load suggestions: " + err.message });
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "Ask AI"; }
  }
}

function renderCost() {
  const tbody = document.querySelector("#costTable tbody");
  tbody.innerHTML = "";
  const totalEl = el("costTotal");
  if (!state.plan || !state.plan.ok || !state.plan.cost) {
    totalEl.textContent = state.plan && !state.plan.ok ? "No cost estimate -- plan is infeasible." : "";
    return;
  }
  const cost = state.plan.cost;
  const header = document.createElement("tr");
  header.innerHTML = "<th>Item</th><th>Rate</th><th>Qty</th><th>Amount</th>";
  tbody.appendChild(header);
  cost.items.forEach(item => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${item.label}</td><td>Rs ${item.rate}/${item.unit}</td><td>${item.qty}</td><td>Rs ${item.amount.toLocaleString("en-IN")}</td>`;
    tbody.appendChild(tr);
  });
  totalEl.textContent =
    `Total: Rs ${cost.total.toLocaleString("en-IN")}  (enclosed ${cost.built_up_sqft} sqft`
    + (cost.open_sqft ? ` + ${cost.open_sqft} sqft open` : "")
    + ` over ${cost.floors} floor${cost.floors > 1 ? "s" : ""})`;
}
window.renderCost = renderCost;

function applyPreset(name) {
  const p = PRESETS[name];
  if (!p) return;
  writeSpecToForm(p);
  generate();
}

function wireEvents() {
  el("generateBtn").addEventListener("click", generate);
  document.querySelectorAll(".presets button").forEach(btn => {
    btn.addEventListener("click", () => applyPreset(btn.dataset.preset));
  });

  el("tab2d").addEventListener("click", () => {
    state.currentView = "2d";
    el("tab2d").classList.add("active");
    el("tab3d").classList.remove("active");
    el("plan2d").style.display = "block";
    el("plan3d").style.display = "none";
    renderAll();
  });
  el("tab3d").addEventListener("click", () => {
    state.currentView = "3d";
    el("tab3d").classList.add("active");
    el("tab2d").classList.remove("active");
    el("plan2d").style.display = "none";
    el("plan3d").style.display = "block";
    renderAll();
  });

  ["plot_width_ft", "plot_depth_ft", "floors", "bedrooms", "bathrooms",
   "setback_front_m", "setback_rear_m", "setback_left_m", "setback_right_m",
   "rate_structure", "rate_finishes", "rate_electrical", "rate_plumbing", "rate_parking"].forEach(id => {
    el(id).addEventListener("change", debouncedGenerate);
  });
  el("parking").addEventListener("change", debouncedGenerate);
  el("balcony").addEventListener("change", debouncedGenerate);

  if (el("aiRecBtn")) {
    el("aiRecBtn").addEventListener("click", () => fetchRecommendations(true));
  }

  if (el("critiqueBtn")) {
    el("critiqueBtn").addEventListener("click", async () => {
      if (!state.plan || !state.plan.ok) return;
      el("aiCritique").textContent = "Thinking...";
      try {
        const res = await fetch("/api/critique", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ spec: readSpecFromForm() }),
        });
        const data = await res.json();
        el("aiCritique").textContent = data.critique
          || ("AI critique unavailable: " + (data.error || "unknown reason")
              + " -- the validation panel above still covers correctness.");
      } catch (err) {
        el("aiCritique").textContent = "AI critique unavailable right now -- validation panel above still covers correctness.";
      }
    });
  }

  if (el("parseBtn")) {
    el("parseBtn").addEventListener("click", async () => {
      const text = el("aiBrief").value.trim();
      if (!text) return;
      el("aiSpecEcho").textContent = "Parsing...";
      try {
        const res = await fetch("/api/parse", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ text }),
        });
        const data = await res.json();
        if (data.spec) {
          writeSpecToForm(data.spec);
          el("aiSpecEcho").textContent = "AI-extracted spec:\n" + JSON.stringify(data.spec, null, 2);
          generate();
        } else {
          el("aiSpecEcho").textContent =
            "AI parsing unavailable: " + (data.error || "unknown reason") + " -- using the form values instead.";
        }
      } catch (err) {
        el("aiSpecEcho").textContent = "AI parsing unavailable -- using slider values instead.";
      }
    });
  }
}

wireEvents();
generate();
