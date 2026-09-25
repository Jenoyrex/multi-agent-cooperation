import { CONDITIONS, esc, filterRows, fmt, mean, modelLabel, OUTCOME_LABEL, OUTCOMES, outcomeShares, pct, phase, short, signed, timeAgo, uniq } from "./lib.js";
import { dataTable, dotPlot, figure, histogram, legend, stackBars } from "./charts.js";

const main = document.getElementById("main");
const COND_COLOR = { baseline_v1: "var(--base)", structured_v1: "var(--struct)" };
const FAM_COLOR = { claude: "var(--claude)", openai: "var(--openai)" };
const OUT_COLOR = { agreed: "var(--o-agreed)", walked_away: "var(--o-walked)", timeout: "var(--o-timeout)", invalid_action: "var(--o-invalid)" };
const famColor = (f) => FAM_COLOR[f] ?? "var(--muted)";
const st = { snap: null, raw: "", filters: {}, dataset: "pilot", evaluator: false, phaseKey: null };

// ------------------------------------------------------------------ data
async function load() {
  const res = await fetch("/api/snapshot", { cache: "no-store" });
  if (!res.ok) throw new Error(`snapshot ${res.status}`);
  const raw = await res.text();
  const changed = raw !== st.raw;
  st.raw = raw;
  st.snap = JSON.parse(raw);
  return changed;
}

// ------------------------------------------------------------------ router
const routes = { "": overview, setup, negotiations, negotiation: detail, results, comparison, methodology, status };
function parse() {
  const [, name = "", ...rest] = location.hash.replace(/^#/, "").split("/");
  return { name, arg: decodeURIComponent(rest.join("/")) };
}
async function render(animate = true) {
  const { name, arg } = parse();
  const view = routes[name] ?? overview;
  const navKey = name === "negotiation" ? "negotiations" : name || "overview";
  document.querySelectorAll(".rail nav a").forEach((a) => (a.dataset.route === navKey ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  const html = await view(st.snap, arg);
  const swap = () => { main.innerHTML = html; wire(name); };
  if (animate && document.startViewTransition && !document.hidden && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const t = document.startViewTransition(swap);
    t.ready.catch(() => {}); // aborted transitions still run swap
  } else swap();
}
window.addEventListener("hashchange", async () => { await render(); window.scrollTo(0, 0); main.focus({ preventScroll: true }); });

// ------------------------------------------------------------------ shared bits
const pill = (p, extra = "") => `<span class="pill ${p.tone} ${extra}" data-phase="${p.key}"><i class="dot"></i>${esc(p.label)}</span>`;
const empty = (title, body) => `<div class="empty"><b>${esc(title)}</b><span>${body}</span></div>`;
const cond = (c) => `<span class="tag c-${esc(c)}">${esc(c)}</span>`;
const fam = (model, fallback) => { const f = String(model ?? "").split(":")[0]; return `<span class="tag m-${esc(f)}">${esc(modelLabel(model) ?? fallback)}</span>`; };
const head = (eyebrow, title, lede = "") => `<header class="page-head"><p class="eyebrow">${esc(eyebrow)}</p><h1>${esc(title)}</h1>${lede ? `<p class="lede">${lede}</p>` : ""}</header>`;
const stat = (k, v, n = "", i = 0, kExtra = "") => `<div class="panel stat" style="--i:${i}"><div class="k"><span>${esc(k)}</span>${kExtra}</div><div class="v tnum">${v}</div>${n ? `<div class="n">${n}</div>` : ""}</div>`;
const progressBar = (done, target) => `<div class="progress" role="progressbar" aria-valuemin="0" aria-valuemax="${target}" aria-valuenow="${done}"><i data-w="${Math.min(100, (100 * done) / target)}"></i></div>`;
const pilotNotice = `<p class="notice info"><span><b>Operational pilot (spec §8.4).</b> 3 instances × 8 cells. Pilot negotiations are not part of the confirmatory dataset; values here are descriptive checks of the harness, not experimental findings.</span></p>`;
const datasetSeg = (active) => `<div class="seg" role="group" aria-label="Dataset">${["pilot", "full"].map((d) => `<button type="button" data-dataset="${d}" aria-pressed="${d === active}">${d === "pilot" ? "Pilot" : "Full run"}</button>`).join("")}</div>`;

function provenanceLine(p) {
  const dirty = p.code_version.endsWith("+dirty");
  return `<dl class="kv"><dt>Code</dt><dd class="mono">${esc(short(p.code_version, 10))}${dirty ? ` <span class="pill warn">uncommitted changes</span>` : ""}</dd>
    <dt>Protocol</dt><dd class="mono">${esc(p.protocol_id)}</dd>
    <dt>Spec</dt><dd class="mono">sha256:${esc(short(p.spec_version, 12))}</dd>
    <dt>Prompts</dt><dd class="mono">${esc(short(p.prompt_hash, 12))}</dd></dl>`;
}

// ------------------------------------------------------------------ 1. overview
function overview(s) {
  const p = phase(s), { pilot, full } = s.progress, sci = s.setup.scientific;
  const last = s.runs.at(-1);
  const hasData = s.negotiations.length || s.runs.length;
  return `${head("Research dashboard", "Cooperative Capabilities in Multi-Agent AI Systems",
    "When a Claude agent and a GPT agent with private valuations split a shared resource pool, does the <code>structured_v1</code> negotiation instruction strategy change relative welfare efficiency, egalitarian welfare, or agreement rate compared with <code>baseline_v1</code>?")}
  <div class="neg-head">${pill(p)} <span class="cite">Research question: spec §8.1 · hypotheses H1–H3 are non-directional</span></div>

  <section class="block"><div class="grid g4 stagger">
    ${stat("Pilot negotiations", `${pilot.done}<small> / ${pilot.target}</small>`, progressBar(pilot.done, pilot.target), 0)}
    ${stat("Full-run negotiations", `${full.done}<small> / ${full.target}</small>`, progressBar(full.done, full.target), 1)}
    ${stat("Aborted attempts", s.progress.aborted_attempts, "Infrastructure failures, never outcomes (§3.6)", 2)}
    ${stat("Last run", last ? esc(timeAgo(last.started_at)) : "—", last ? `${esc(last.mode)} · ${esc(last.method)} · ${esc(last.status)}` : "No runs recorded", 3)}
  </div>
  ${hasData ? "" : `<div style="margin-top:14px">${empty("No experimental data collected yet", "No experiment database was found in <code>results/</code>. The pilot is started from the command line (<code>scripts/run_pilot.py</code>), never from this dashboard. Data appears here as soon as the runner stores it.")}</div>`}
  </section>

  <section class="block"><div class="block-head"><h2>Conditions</h2><span class="cite">spec §8.2 · the only manipulated factor</span></div>
    <div class="grid g2 stagger">
      <div class="panel" style="--i:0"><h3><i class="sw" style="background:var(--base)"></i>baseline_v1</h3><p class="sub">The shared system instructions, with nothing appended.</p></div>
      <div class="panel" style="--i:1"><h3><i class="sw" style="background:var(--struct)"></i>structured_v1</h3><p class="sub">The same instructions plus one fixed block with three components: preference ranking/revelation, integrative trade guidance, and disagreement-point/deadline reasoning. No fairness instruction.</p></div>
    </div></section>

  <section class="block"><div class="block-head"><h2>Models</h2><span class="cite">spec §8.15 · identical in every cell and condition</span></div>
    <div class="grid g2 stagger">
      ${[["claude", "ClaudeAgent"], ["openai", "OpenAIAgent"]].map(([f, k], i) => `<div class="panel" style="--i:${i}"><h3><span class="tag m-${f}">${f === "claude" ? "Claude" : "OpenAI"}</span></h3>
        <dl class="kv"><dt>Model</dt><dd class="mono">${esc(sci[k].model)}</dd><dt>Temperature</dt><dd>${sci[k].temperature}</dd><dt>Max output</dt><dd>${sci[k].max_output_tokens} tokens</dd>${sci[k].effort ? `<dt>Effort</dt><dd>${esc(sci[k].effort)}</dd>` : ""}</dl></div>`).join("")}
    </div></section>

  <section class="block"><div class="block-head"><h2>Version</h2><span class="cite">current working tree</span></div><div class="panel">${provenanceLine(s.provenance)}</div></section>

  <section class="block"><nav class="jump" aria-label="Sections">
    <a href="#/results"><b>Results</b><span>Preregistered metrics by condition</span></a>
    <a href="#/negotiations"><b>Negotiations</b><span>Explore every stored transcript</span></a>
    <a href="#/setup"><b>Experiments</b><span>The frozen, approved configuration</span></a>
    <a href="#/methodology"><b>Methodology</b><span>Design, metrics and analysis plan</span></a>
  </nav></section>`;
}

// ------------------------------------------------------------------ 2. setup
function setup(s) {
  const c = s.setup, sci = c.scientific, op = c.operational;
  const recordedBudgets = s.runs.filter((r) => r.mode === "pilot" || r.mode === "full").map((r) => ({ mode: r.mode, ...r.config?.experiment }));
  const caps = [...new Set(recordedBudgets.map((b) => `${b.mode}|${b.budget_max_total_tokens}|${b.budget_max_cost_usd}|${b.budget_max_input_tokens_per_call}`))];
  const row = (label, a, b) => `<tr><td>${esc(label)}</td><td>${a}</td><td>${b}</td></tr>`;
  return `${head("Experiment setup", "Approved configuration", "Read directly from the modules that enforce it. This page is read-only: every pilot and full run is checked against these values by <code>check_approved_runtime</code> before any API call, and refuses to start if one differs.")}
  <p class="notice lock"><span><b>Frozen.</b> Nothing on this page can be edited from the dashboard. Changing the configuration means changing preregistered code.</span></p>

  <section class="block"><div class="block-head"><h2>Design matrix</h2><span class="cite">spec §8.3 · seat × first mover × condition = 8 negotiations per instance</span></div>
    <div class="matrix" role="table" aria-label="Design cells">
      <div class="h" role="columnheader">Condition</div>
      ${["A", "B"].flatMap((seat) => ["A", "B"].map((fm) => `<div class="h" role="columnheader">Claude in seat ${seat}<br>Agent ${fm} moves first</div>`)).join("")}
      ${CONDITIONS.map((cd) => `<div role="rowheader">${cond(cd)}</div>${c.pilot.cells.filter((x) => x.condition === cd).map((x) => `<div class="cell" role="cell"><span>Cell ${x.index}</span><small>A: ${x.claude_seat === "A" ? "Claude" : "OpenAI"} · B: ${x.claude_seat === "B" ? "Claude" : "OpenAI"}</small></div>`).join("")}`).join("")}
    </div></section>

  <section class="block"><div class="grid g2">
    <div class="panel"><h3>Datasets &amp; seeds</h3><dl class="kv">
      <dt>Pilot</dt><dd>${c.pilot.total} negotiations · seeds ${c.pilot.seeds.join(", ")} · 8 batches of 3</dd>
      <dt>Full run</dt><dd>${c.full.total} negotiations · ${c.full.instances} instances · seeds ${c.full.seeds[0]}–${c.full.seeds[1]}</dd>
      <dt>Status</dt><dd>Pilot ${s.progress.pilot.done}/${s.progress.pilot.target} · full ${s.progress.full.done}/${s.progress.full.target}</dd>
      <dt>Source</dt><dd class="cite">${esc(c.full.source)}</dd></dl></div>
    <div class="panel"><h3>Protocol &amp; environment</h3><dl class="kv">
      <dt>Rounds</dt><dd>${c.max_rounds} turns; the final turn is response-only</dd>
      <dt>First mover</dt><dd>Fixed per batch (<code>A</code> or <code>B</code>), independent of the seed</dd>
      <dt>Categories</dt><dd>${c.environment.category_names.map(esc).join(", ")} · ${c.environment.min_qty}–${c.environment.max_qty} units each</dd>
      <dt>Valuations</dt><dd>Dirichlet(α = ${c.environment.dirichlet_alpha}) over ${c.environment.total_points} points, private</dd>
      <dt>Transport reruns</dt><dd>${c.max_transport_reruns} clean reruns from turn 1</dd></dl></div>
  </div></section>

  <section class="block"><div class="block-head"><h2>Runtime configuration</h2><span class="cite">spec §8.15 · <code>APPROVED_SCIENTIFIC</code>, <code>APPROVED_OPERATIONAL</code></span></div>
    <div class="table-wrap"><table><thead><tr><th>Setting</th><th>Claude</th><th>OpenAI</th></tr></thead><tbody>
      ${row("Model", `<code>${esc(sci.ClaudeAgent.model)}</code>`, `<code>${esc(sci.OpenAIAgent.model)}</code>`)}
      ${row("Temperature", sci.ClaudeAgent.temperature, sci.OpenAIAgent.temperature)}
      ${row("Max output tokens", sci.ClaudeAgent.max_output_tokens, sci.OpenAIAgent.max_output_tokens)}
      ${row("Thinking / reasoning", esc(c.claude_thinking), "none (non-reasoning model)")}
      ${row("Effort", esc(sci.ClaudeAgent.effort ?? "—"), esc(sci.OpenAIAgent.effort ?? "not applicable"))}
      ${row("Request timeout", `${op.timeout_s} s`, `${op.timeout_s} s`)}
      ${row("In-turn retries", `${op.max_retries} (backoff ${op.retry_backoff_s} s)`, `${op.max_retries} (backoff ${op.retry_backoff_s} s)`)}
      ${row("Price (USD / 1M in, out)", (c.prices[sci.ClaudeAgent.model] ?? []).join(", "), (c.prices[sci.OpenAIAgent.model] ?? []).join(", "))}
    </tbody></table></div></section>

  <section class="block"><div class="block-head"><h2>Budget</h2><span class="cite">hard caps supplied at launch, checked before every call</span></div>
    ${caps.length ? `<div class="table-wrap"><table><thead><tr><th>Dataset</th><th class="num">Token cap</th><th class="num">Dollar cap</th><th class="num">Input reserve / call</th></tr></thead><tbody>
      ${caps.map((k) => { const [m, t, d, r] = k.split("|"); const n = (v) => (v === "null" || v === "undefined" ? "—" : v); return `<tr><td>${esc(m)}</td><td class="num">${esc(n(t))}</td><td class="num">${n(d) === "—" ? "—" : `$${esc(d)}`}</td><td class="num">${esc(n(r))}</td></tr>`; }).join("")}
    </tbody></table></div>`
    : empty("No budget recorded yet", "Budget caps have no defaults; they are passed to <code>scripts/run_pilot.py</code> at launch and recorded with each run. The documented approved pilot budget is <code>--max-cost-usd 20 --max-input-tokens-per-call 20000</code> (no token cap).")}
  </section>

  <section class="block"><div class="block-head"><h2>Instructions</h2><span class="cite"><code>src/agents/prompting.py</code> · verbatim</span></div>
    <div class="grid g2">
      <details class="panel"><summary><b>System instructions</b> (both conditions)</summary><pre class="prompt">${esc(c.system_instructions)}</pre></details>
      <details class="panel"><summary><b>structured_v1 block</b> (appended)</summary><pre class="prompt">${esc(c.structured_block)}</pre></details>
    </div></section>`;
}

// ------------------------------------------------------------------ 3. negotiations
function negotiations(s) {
  const rows = s.negotiations;
  if (!rows.length) return `${head("Negotiations", "Negotiation explorer")}${empty("No negotiations stored yet", "Every completed negotiation from the pilot and the full run appears here, with its transcript. Nothing has been run yet.")}`;
  const f = st.filters;
  const opt = (name, label, values, labels = {}) => `<label class="field">${esc(label)}<select data-filter="${name}"><option value="all">All</option>${values.map((v) => `<option value="${esc(v)}"${f[name] === v ? " selected" : ""}>${esc(labels[v] ?? v)}</option>`).join("")}</select></label>`;
  const fams = { claude: "Claude", openai: "OpenAI" };
  return `${head("Negotiations", "Negotiation explorer", "Every completed negotiation. Aborted attempts (infrastructure failures) are listed under Experiment status.")}
  <div class="filters" role="search">
    ${opt("mode", "Dataset", uniq(rows, "mode"))}
    ${opt("condition", "Condition", CONDITIONS)}
    ${opt("modelA", "Agent A", uniq(rows, "family_A"), fams)}
    ${opt("modelB", "Agent B", uniq(rows, "family_B"), fams)}
    ${opt("firstMover", "First mover", ["A", "B"], { A: "Agent A", B: "Agent B" })}
    ${opt("outcome", "Outcome", OUTCOMES, OUTCOME_LABEL)}
    <label class="field">Seed / ID<input type="search" data-filter="seed" value="${esc(f.seed ?? "")}" placeholder="e.g. 30001"></label>
    <button class="btn-link" type="button" data-reset>Reset</button>
  </div>
  <p class="count" aria-live="polite" id="count"></p>
  <div class="table-wrap"><table><thead><tr><th>ID</th><th>Dataset</th><th>Condition</th><th>Agent A</th><th>Agent B</th><th>First</th><th class="num">Seed</th><th>Outcome</th><th class="num">Rounds</th><th class="num">u<sub>A</sub></th><th class="num">u<sub>B</sub></th><th class="num">RWE</th><th class="num">EW</th><th class="num">Equit.</th></tr></thead>
  <tbody id="rows" class="fade-list"></tbody></table></div>`;
}
function negRows() {
  const rows = filterRows(st.snap.negotiations, st.filters);
  document.getElementById("count").textContent = `${rows.length} of ${st.snap.negotiations.length} negotiations`;
  document.getElementById("rows").innerHTML = rows.length ? rows.map((r, i) => `<tr class="link" style="--i:${i}" data-href="#/negotiation/${encodeURIComponent(r.key)}">
    <td class="mono"><a href="#/negotiation/${encodeURIComponent(r.key)}">${esc(r.key)}</a></td><td>${esc(r.mode)}</td><td>${cond(r.condition)}</td>
    <td>${fam(r.model_A)}</td><td>${fam(r.model_B)}</td><td>${esc(r.first_mover)}</td><td class="num">${r.seed}</td>
    <td>${esc(OUTCOME_LABEL[r.outcome] ?? r.outcome)}${r.invalid_reason ? ` <span class="cite">${esc(r.invalid_reason)}</span>` : ""}</td>
    <td class="num">${r.rounds}</td><td class="num">${fmt(r.utility_A, 1)}</td><td class="num">${fmt(r.utility_B, 1)}</td>
    <td class="num">${fmt(r.rwe)}</td><td class="num">${fmt(r.ew)}</td><td class="num">${fmt(r.equitability)}</td></tr>`).join("")
    : `<tr><td colspan="14">${empty("No negotiations match these filters", "Adjust or reset the filters above.")}</td></tr>`;
}

// ------------------------------------------------------------------ 4. detail
async function detail(s, key) {
  const res = await fetch(`/api/negotiation?key=${encodeURIComponent(key)}`, { cache: "no-store" });
  if (!res.ok) return `${head("Negotiation", "Not found")}${empty("This negotiation is not in any database", `No stored negotiation has the ID <code>${esc(key)}</code>. <a href="#/negotiations">Back to the explorer</a>.`)}`;
  const n = await res.json();
  const fA = n.family_A, fB = n.family_B;
  const seat = (r) => `<div class="panel seat" style="--c:${famColor(r === "A" ? fA : fB)}"><div class="who"><b>Agent ${r}</b>${r === n.first_mover ? `<span class="pill">moves first</span>` : ""}</div>
    <div class="mono">${esc(r === "A" ? n.model_A : n.model_B)}</div>
    <div class="evalv" data-eval ${st.evaluator ? "" : "hidden"}>Private per-unit values: ${Object.entries(r === "A" ? n.valuation_A : n.valuation_B).map(([c, v]) => `${esc(c)} ${fmt(v, 2)}`).join(" · ")}</div></div>`;
  const agreed = n.outcome === "agreed";
  const events = n.events;
  return `<header class="page-head"><p class="eyebrow"><a href="#/negotiations">Negotiations</a> / <span class="mono">${esc(n.key)}</span></p>
    <h1>Seed ${n.seed} · ${esc(OUTCOME_LABEL[n.outcome])}</h1>
    <div class="neg-head">${cond(n.condition)} <span class="pill">${esc(n.mode)}</span> <span class="pill">${n.rounds} / ${n.max_rounds} rounds</span>
      <span class="cite mono">run ${esc(short(n.run_id, 10))} · #${n.index}</span></div></header>
  <div class="grid g4 stagger">
    ${stat("RWE", fmt(n.rwe), "unconditional · 0 if not agreed", 0)}
    ${stat("Egalitarian welfare", fmt(n.ew), "unconditional · 0 if not agreed", 1)}
    ${stat("Equitability", fmt(n.equitability), agreed ? "agreement-only" : "undefined (not agreed)", 2)}
    ${stat("Claude − GPT", signed(n.imbalance), agreed ? "model imbalance, agreement-only" : "undefined (not agreed)", 3)}
  </div>
  <div class="seats">${seat("A")}${seat("B")}</div>
  <div class="block-head"><h2>Transcript</h2>
    <label class="eval-toggle"><input type="checkbox" id="evaltoggle" ${st.evaluator ? "checked" : ""}> Evaluator view <span class="cite">(hidden valuations and offer values; agents never saw these)</span></label></div>
  <p class="sub">Pool: ${Object.entries(n.resource_pool).map(([c, q]) => `${esc(c)} ${q}`).join(" · ")} · each agent's maximum possible utility is 100.</p>
  <div class="timeline">
    ${events.map((e, i) => turnCard(e, n, i)).join("")}
    ${finalCard(n, events.length)}
  </div>
  <div class="grid g4" style="margin-top:28px">
    ${stat("API calls", n.usage.api_calls, `${n.usage.api_attempts} attempts`)}
    ${stat("Tokens in / out", `${n.usage.input_tokens ?? "—"}<small> / ${n.usage.output_tokens ?? "—"}</small>`, "as reported by providers")}
    ${stat("Latency", n.usage.latency_s === null ? "—" : `${fmt(n.usage.latency_s, 1)}<small> s</small>`)}
    ${stat("Cost", n.usage.cost_usd === null ? "—" : `$${fmt(n.usage.cost_usd, 4)}`, "from recorded usage, not billing")}
  </div>`;
}
function turnCard(e, n, i) {
  const f = e.actor === "A" ? n.family_A : n.family_B;
  const alloc = e.allocation && e.allocation.A && e.allocation.B ? e.allocation : null;
  const bars = alloc ? `<div class="split" style="--ca:${famColor(n.family_A)};--cb:${famColor(n.family_B)}">${Object.keys(n.resource_pool).map((c) => {
    const q = n.resource_pool[c], a = alloc.A[c] ?? 0, b = alloc.B[c] ?? 0;
    return `<div class="row"><span>${esc(c)}</span><div class="bar" role="img" aria-label="${esc(c)}: A ${a}, B ${b} of ${q}"><i class="a${a ? "" : " zero"}" style="flex:${a};--i:${i}">${a ? `A ${a}` : ""}</i><i class="b${b ? "" : " zero"}" style="flex:${b};--i:${i}">${b ? `B ${b}` : ""}</i></div></div>`;
  }).join("")}</div>` : "";
  const label = { OFFER: "OFFER", ACCEPT: "ACCEPT", WALK_AWAY: "WALK AWAY", INVALID: "INVALID ACTION" }[e.action] ?? e.action;
  let state = "";
  if (e.action === "OFFER") state = e.standing ? `<span class="on">● Standing offer</span>` : `<span>○ ${esc(e.note ?? "Not standing")}</span>`;
  if (e.action === "ACCEPT") state = e.accepted ? `<span class="on">✓ Accepts Agent ${esc(e.accepted.actor)}'s offer from turn ${e.accepted.turn}</span>` : "";
  if (e.action === "WALK_AWAY") state = `<span>Ends the negotiation with no agreement</span>`;
  if (e.action === "INVALID") state = `<span>${esc(e.reason)}${e.detail ? ` — ${esc(e.detail)}` : ""}</span>`;
  const call = e.call;
  return `<div class="turn ${e.actor}" data-turn="${e.turn}" style="--i:${i};--c:${famColor(f)}"><div class="card">
    <div class="top"><span class="tag m-${esc(f)}">Agent ${e.actor} · ${esc(modelLabel(e.actor === "A" ? n.model_A : n.model_B))}</span><span class="act ${e.action}">${label}</span></div>
    ${e.message ? `<p class="msg">${esc(e.message)}</p>` : e.action === "INVALID" ? "" : `<p class="msg none">No message</p>`}
    ${bars}
    <div class="state">${state}</div>
    ${e.value_A !== null && e.value_A !== undefined ? `<div class="evalv" data-eval ${st.evaluator ? "" : "hidden"}>Offer worth: A ${fmt(e.value_A, 1)} · B ${fmt(e.value_B, 1)} (of 100 each)</div>` : ""}
    ${call ? `<details><summary>Call diagnostics</summary><dl class="kv" style="margin-top:8px"><dt>Model</dt><dd class="mono">${esc(call.response_model ?? call.model ?? "—")}</dd><dt>Tokens</dt><dd>${call.input_tokens ?? "—"} in · ${call.output_tokens ?? "—"} out</dd><dt>Latency</dt><dd>${fmt(call.latency_s, 2)} s · ${call.attempts} attempt(s)</dd><dt>Stop</dt><dd>${esc(call.stop_reason ?? "—")}</dd>${call.error ? `<dt>Error</dt><dd>${esc(call.error)}</dd>` : ""}</dl>${call.raw_output ? `<pre>${esc(call.raw_output)}</pre>` : ""}</details>` : ""}
  </div></div>`;
}
function finalCard(n, i) {
  const alloc = n.final_allocation;
  return `<div class="final" style="--i:${i}"><div class="card"><p class="eyebrow">Outcome</p><div class="big">${esc(OUTCOME_LABEL[n.outcome])}</div>
    <p class="sub" style="margin:6px 0 0">${n.outcome === "agreed" ? `Final allocation — A: ${Object.entries(alloc.A).map(([c, q]) => `${esc(c)} ${q}`).join(", ")} · B: ${Object.entries(alloc.B).map(([c, q]) => `${esc(c)} ${q}`).join(", ")}<br>u<sub>A</sub> = ${fmt(n.utility_A, 1)} · u<sub>B</sub> = ${fmt(n.utility_B, 1)} · W* = ${fmt(n.optimal_welfare, 1)}`
      : n.outcome === "invalid_action" ? `Protocol/output fault (${esc(n.invalid_reason)}). No allocation; both utilities are 0.` : "No allocation; both agents receive the disagreement value 0."}</p></div></div>`;
}

// ------------------------------------------------------------------ 5. results
function datasetState(s, ds) {
  const v = s.results[ds];
  if (v.locked) return `<p class="notice lock"><span><b>Locked until the full run is complete (${v.n} / ${v.target}).</b> Spec §8.9: outcomes are not compared by condition before all ${v.target} full-run negotiations are stored, and the sample size does not depend on any observed result.</span></p>${progressBar(v.n, v.target)}`;
  if (!v.n) return empty(ds === "pilot" ? "No pilot data yet" : "No full-run data yet", "Results will appear here after the experiment completes. Nothing is estimated or simulated in the meantime.");
  return null;
}
function results(s) {
  const ds = st.dataset, v = s.results[ds], blocked = datasetState(s, ds);
  const top = `${head("Results", "Preregistered metrics", "Confirmatory metrics are unconditional (failures score 0). Secondary and diagnostic metrics are agreement-only and always show the number of agreed negotiations they rest on.")}
    <div class="block-head"><div>${datasetSeg(ds)}</div><span class="cite">spec §8.6–§8.7</span></div>`;
  if (blocked) return top + blocked;
  const rows = s.negotiations.filter((r) => r.mode === ds);
  const by = v.by_condition, g = (c) => rows.filter((r) => r.condition === c);
  const dots = (metric, agreedOnly = false) => CONDITIONS.map((c) => {
    const pts = g(c).filter((r) => r[metric] !== null && (!agreedOnly || r.outcome === "agreed"));
    return { label: c, color: COND_COLOR[c], mean: mean(pts.map((r) => r[metric])), points: pts.map((r) => ({ v: r[metric], tip: `${r.key} · seed ${r.seed} · ${OUTCOME_LABEL[r.outcome]} · ${metric} ${fmt(r[metric])}` })) };
  });
  const condLegend = legend(CONDITIONS.map((c) => [c, COND_COLOR[c]]));
  const outLegend = legend(OUTCOMES.map((o) => [OUTCOME_LABEL[o], OUT_COLOR[o]]));
  const metricTile = (key, label, i, isRate = false) => `<div class="panel stat" style="--i:${i}"><div class="k"><span>${label}</span><span>confirmatory</span></div>
    ${CONDITIONS.map((c) => `<div style="display:flex;justify-content:space-between;align-items:baseline;margin-top:10px"><span class="tag c-${c}">${c}</span><span class="v tnum" style="font-size:26px;margin:0">${by[c] ? (isRate ? pct(by[c][key]) : fmt(by[c][key])) : "—"}</span></div><div class="n">n = ${by[c]?.n ?? 0}${isRate && by[c] ? ` · ${by[c].outcomes.agreed ?? 0} agreed` : ""}</div>`).join("")}</div>`;
  const tableFor = (metric) => dataTable(["Negotiation", "Condition", "Seed", metric], rows.filter((r) => r[metric] !== null).map((r) => [r.key, r.condition, r.seed, fmt(r[metric])]));
  return `${top}${ds === "pilot" ? pilotNotice : ""}
  <section class="block"><div class="block-head"><h2>Confirmatory</h2><span class="cite">H1–H3 · unconditional over all n</span></div>
    <div class="grid g3 stagger">${metricTile("rwe", "Relative welfare efficiency", 0)}${metricTile("ew", "Egalitarian welfare", 1)}${metricTile("agreement_rate", "Agreement rate", 2, true)}</div>
    <div class="grid g2" style="margin-top:14px">
      <div class="panel">${figure("RWE per negotiation", "0 = no agreement · tick = mean", dotPlot(dots("rwe")), condLegend, tableFor("rwe"))}</div>
      <div class="panel">${figure("Egalitarian welfare per negotiation", "min(uA, uB) / 100 · tick = mean", dotPlot(dots("ew")), condLegend, tableFor("ew"))}</div>
    </div></section>

  <section class="block"><div class="block-head"><h2>Outcomes</h2><span class="cite">rates over all negotiations</span></div>
    <div class="grid g2">
      <div class="panel">${figure("Outcome distribution by condition", "share of all negotiations", stackBars(CONDITIONS.map((c) => ({ label: c, n: by[c]?.n ?? 0, parts: outcomeShares(by[c]?.outcomes, by[c]?.n).map((p) => ({ ...p, key: p.outcome, color: OUT_COLOR[p.outcome] })) }))), outLegend,
        dataTable(["Condition", ...OUTCOMES.map((o) => OUTCOME_LABEL[o]), "n"], CONDITIONS.map((c) => [c, ...OUTCOMES.map((o) => by[c]?.outcomes[o] ?? 0), by[c]?.n ?? 0])))}</div>
      <div class="panel">${figure("Outcome by first mover", "A-first vs B-first cells, all conditions", stackBars(["A", "B"].map((fm) => { const o = v.pooled.outcomes_by_first_mover[fm], n = Object.values(o).reduce((a, b) => a + b, 0); return { label: `Agent ${fm} first`, n, parts: outcomeShares(o, n).map((p) => ({ ...p, key: p.outcome, color: OUT_COLOR[p.outcome] })) }; })), outLegend)}</div>
    </div>
    <div class="grid g2" style="margin-top:14px">${CONDITIONS.map((c) => `<div class="panel">${figure(`Rounds used · ${c}`, "all outcomes", histogram(by[c]?.rounds ?? {}, Array.from({ length: s.setup.max_rounds }, (_, i) => i + 1), COND_COLOR[c], c))}
      <p class="sub" style="margin:8px 0 0">Mean rounds to agreement: ${fmt(by[c]?.mean_rounds_to_agreement, 1)} · invalid-action rate ${pct(by[c]?.invalid_action_rate)}${Object.keys(by[c]?.invalid_reasons ?? {}).length ? ` (${Object.entries(by[c].invalid_reasons).map(([k, n]) => `${esc(k)} ${n}`).join(", ")})` : ""}</p></div>`).join("")}</div>
  </section>

  <section class="block"><div class="block-head"><h2>Secondary · descriptive</h2><span class="cite">agreement-only · not tested · subject to post-treatment selection (§8.12)</span></div>
    <div class="grid g2">
      <div class="panel">${figure("Equitability", "1 − |uA − uB| / 100 · agreed only", dotPlot(dots("equitability", true)), condLegend, tableFor("equitability"))}</div>
      <div class="panel">${figure("Conditional social welfare efficiency", "SW / W* · agreed only", dotPlot(dots("swe", true)), condLegend, tableFor("swe"))}</div>
    </div></section>

  <section class="block"><div class="block-head"><h2>Diagnostics</h2><span class="cite">agreement-only · not tested</span></div>
    <div class="grid g2">
      <div class="panel">${figure("Model imbalance", "u_Claude/100 − u_GPT/100 · dashed = 0", dotPlot(dots("imbalance", true), { domain: [-1, 1], zero: true, bin: 0.04, ticks: 4 }), condLegend, tableFor("imbalance"))}</div>
      <div class="panel">${figure("First-mover gap", "u_first/100 − u_second/100 · dashed = 0", dotPlot(dots("first_mover_gap", true), { domain: [-1, 1], zero: true, bin: 0.04, ticks: 4 }), condLegend, tableFor("first_mover_gap"))}</div>
    </div>
    <div class="table-wrap" style="margin-top:14px"><table><thead><tr><th>Condition</th><th class="num">Agreed n</th><th class="num">Envy-free</th><th class="num">A envious</th><th class="num">B envious</th><th class="num">Mean imbalance</th><th class="num">Mean first-mover gap</th></tr></thead><tbody>
      ${CONDITIONS.map((c) => { const b = by[c]; return `<tr><td>${cond(c)}</td><td class="num">${b?.n_agreed ?? 0}</td><td class="num">${pct(b?.envy_free_rate)}</td><td class="num">${pct(b?.envious_A_rate)}</td><td class="num">${pct(b?.envious_B_rate)}</td><td class="num">${signed(b?.imbalance)}</td><td class="num">${signed(b?.first_mover_gap)}</td></tr>`; }).join("")}
    </tbody></table></div></section>`;
}

// ------------------------------------------------------------------ 6. comparison
function comparison(s) {
  const ds = st.dataset, v = s.results[ds], blocked = datasetState(s, ds);
  const top = `${head("Comparison", "baseline_v1 vs structured_v1", "Paired, per-instance differences d<sub>i</sub> = m<sub>i,structured</sub> − m<sub>i,baseline</sub>, where m is the mean over the instance's negotiations in that condition (spec §8.9). Presented descriptively; no condition is ranked.")}
    <div class="block-head"><div>${datasetSeg(ds)}</div><span class="cite">unit of analysis: the instance</span></div>`;
  if (blocked) return top + blocked;
  const rows = s.negotiations.filter((r) => r.mode === ds), by = v.by_condition;
  const paired = v.paired;
  const METRICS = [["rwe", "Δ RWE"], ["ew", "Δ Egalitarian welfare"], ["agreement_rate", "Δ Agreement rate"]];
  const diffs = figure("Per-instance paired differences", `one dot per instance · tick = mean d · n = ${paired.length} instances`,
    dotPlot(METRICS.map(([m, label]) => ({ label, color: "var(--ink-2)", mean: mean(paired.map((p) => p[m])), points: paired.map((p) => ({ v: p[m], tip: `${label} · seed ${p.seed}: d = ${signed(p[m])} (n ${p.n_structured} structured vs ${p.n_baseline} baseline)` })) })),
      { domain: [-1, 1], zero: true, bin: 0.04, ticks: 4, width: 760, fmtTick: (x) => signed(x, 1) }), "",
    dataTable(["Instance seed", ...METRICS.map(([, l]) => l), "n structured", "n baseline"], paired.map((p) => [p.seed, ...METRICS.map(([m]) => signed(p[m])), p.n_structured, p.n_baseline])));
  const rate = (xs) => (xs.length ? pct(xs.filter((r) => r.outcome === "agreed").length / xs.length) : "—");
  const seatRow = (c) => { const g = rows.filter((r) => r.condition === c); return `<tr><td>${cond(c)}</td>
    <td class="num">${rate(g.filter((r) => r.claude_seat === "A"))} <span class="cite">n=${g.filter((r) => r.claude_seat === "A").length}</span></td>
    <td class="num">${rate(g.filter((r) => r.claude_seat === "B"))} <span class="cite">n=${g.filter((r) => r.claude_seat === "B").length}</span></td>
    <td class="num">${rate(g.filter((r) => r.first_mover === "A"))} <span class="cite">n=${g.filter((r) => r.first_mover === "A").length}</span></td>
    <td class="num">${rate(g.filter((r) => r.first_mover === "B"))} <span class="cite">n=${g.filter((r) => r.first_mover === "B").length}</span></td>
    <td class="num">${signed(by[c]?.imbalance)} <span class="cite">n=${by[c]?.n_imbalance ?? 0}</span></td></tr>`; };
  return `${top}${ds === "pilot" ? pilotNotice : ""}
  <p class="notice"><span><b>No inferential statistics are shown.</b> The preregistered sign-flip permutation test, 95% bootstrap CI, Wilcoxon sensitivity check and Holm correction (§8.9) have not been implemented or run; no analysis output exists, so no intervals or p-values are displayed.</span></p>
  ${paired.length ? `<section class="block"><div class="block-head"><h2>Paired differences</h2><span class="cite">confirmatory metrics · dashed line = no difference</span></div>
    <div class="panel">${diffs}<p class="sub" style="margin:10px 0 0">Mean d: ${METRICS.map(([m, l]) => `${l} ${signed(mean(paired.map((p) => p[m])))}`).join(" · ")}. Right of the dashed line: higher under structured_v1; left: higher under baseline_v1.</p></div></section>` : `<section class="block">${empty("No paired instances yet", "An instance appears once it has negotiations stored under both conditions.")}</section>`}
  <section class="block"><div class="block-head"><h2>Side by side</h2><span class="cite">condition-level summaries</span></div>
    <div class="table-wrap"><table><thead><tr><th>Metric</th>${CONDITIONS.map((c) => `<th class="num">${cond(c)}</th>`).join("")}<th>Basis</th></tr></thead><tbody>
      ${[["Relative welfare efficiency", "rwe", fmt, "all n"], ["Egalitarian welfare", "ew", fmt, "all n"], ["Agreement rate", "agreement_rate", pct, "all n"], ["Equitability", "equitability", fmt, "agreed only"], ["Conditional SWE", "swe", fmt, "agreed only"], ["Envy-free rate", "envy_free_rate", pct, "agreed only"], ["Invalid-action rate", "invalid_action_rate", pct, "all n"]]
        .map(([l, k, f, basis]) => `<tr><td>${l}</td>${CONDITIONS.map((c) => `<td class="num">${by[c] ? f(by[c][k]) : "—"} <span class="cite">n=${basis === "all n" ? by[c]?.n ?? 0 : by[c]?.n_agreed ?? 0}</span></td>`).join("")}<td class="cite">${basis}</td></tr>`).join("")}
    </tbody></table></div></section>
  <section class="block"><div class="block-head"><h2>Model &amp; seat diagnostics</h2><span class="cite">agreement rate by cell · imbalance agreement-only</span></div>
    <div class="table-wrap"><table><thead><tr><th>Condition</th><th class="num">Claude in A</th><th class="num">Claude in B</th><th class="num">A first</th><th class="num">B first</th><th class="num">Claude − GPT</th></tr></thead><tbody>${CONDITIONS.map(seatRow).join("")}</tbody></table></div></section>`;
}

// ------------------------------------------------------------------ 7. methodology
function methodology(s) {
  const c = s.setup, env = c.environment;
  const secs = [
    ["question", "Research question", `<p>${esc("When two LLM agents (one Claude model, one GPT model) with private linear valuations negotiate a split of a shared resource pool under the frozen alternating-offers protocol, does the structured_v1 negotiation instruction strategy change relative welfare efficiency, egalitarian welfare, or agreement rate compared with the baseline_v1 instructions?")}</p><p class="sub">Hypotheses H1–H3 are non-directional: each metric <em>differs</em> between conditions; the null is no difference. §8.1, §8.8</p>`],
    ["setup", "Negotiation setup", `<ul><li><b>Alternating offers</b>, one agent's turn per round, ${c.max_rounds} rounds. Actions: <code>OFFER</code>, <code>ACCEPT</code>, <code>WALK_AWAY</code>.</li><li><code>ACCEPT</code> needs a valid standing offer from the <b>opponent</b>.</li><li>The <b>final turn is the deadline</b>: a valid offer there is never standing and ends in <code>timeout</code>.</li><li>Disagreement point: both agents receive 0.</li></ul><p class="sub">§3</p>`],
    ["pool", "Shared resource pool", `<p>${env.num_categories} categories (${env.category_names.map(esc).join(", ")}), each with an integer quantity drawn from [${env.min_qty}, ${env.max_qty}] by a seeded RNG. Units are indivisible but fungible; every allocation must assign all units.</p><div class="formula">a_A[c] + a_B[c] == q_c   for every category c</div><p class="sub">§1</p>`],
    ["valuations", "Private valuations", `<p>Each agent gets ${env.total_points} importance points spread over the categories by a Dirichlet(α = ${env.dirichlet_alpha}) draw, seeded from (instance seed, role) with SHA-256. Per-unit value = points / quantity, so every agent's maximum utility is exactly 100.</p><p>Agents <b>never</b> see the opponent's valuation: it is absent from the object the agent code receives, not merely unprinted.</p><p class="sub">§2, §3.4</p>`],
    ["conditions", "Baseline and structured conditions", `<ul><li><b>baseline_v1</b>: the shared system instructions, nothing appended.</li><li><b>structured_v1</b>: the same instructions plus one fixed block: (1) preference ranking/revelation, (2) integrative trade guidance, (3) disagreement-point/deadline reasoning. No fairness instruction.</li></ul><p>Same engine, turn structure, action schema, information and deadline in both. structured_v1 is an instruction strategy, not a new protocol.</p><p class="sub">§8.2</p>`],
    ["design", "2×2 design, crossed with condition", `<p>Every instance is played under every combination of <b>model seat</b> (Claude as A / GPT as A) × <b>first mover</b> (A first / B first) × <b>condition</b>: 8 negotiations per instance.</p><div class="formula">Full run:  ${c.full.instances} instances (seeds ${c.full.seeds[0]}–${c.full.seeds[1]}) × 8 = ${c.full.total}
Pilot:     3 instances (seeds ${c.pilot.seeds.join(", ")}) × 8 = ${c.pilot.total}   — operational, not confirmatory</div><p class="sub">§8.3, §8.4</p>`],
    ["metrics", "Metrics", `<div class="formula">RWE = SW / W*            if agreed, else 0        (confirmatory)
EW  = min(u_A, u_B)/100  if agreed, else 0        (confirmatory)
agreement_rate = agreed / n                       (confirmatory)
SWE = SW / W*            agreed only              (secondary)
equitability = 1 − |u_A − u_B| / 100  agreed only (secondary)
imbalance = u_Claude/100 − u_GPT/100  agreed only (diagnostic)
first_mover_gap = u_first/100 − u_second/100      (diagnostic)
W* = Σ_c q_c · max(v_A[c], v_B[c])</div><p class="sub">§8.6, §8.7 · computed by the evaluator from hidden valuations, never self-reported</p>`],
    ["failures", "Failure semantics", `<ul><li><b>Strategic outcomes</b>: agreed, walked_away, timeout, invalid_action (<code>malformed_output</code>, <code>invalid_allocation</code>, <code>illegal_accept</code>). Never retried; allocations never repaired.</li><li><b>Infrastructure failures</b> (transport errors, budget stops) are <em>not</em> outcomes. They are stored separately and the negotiation is re-run cleanly from turn 1 (up to ${c.max_transport_reruns} times).</li><li>Sensitivity analysis: H1–H3 recomputed with invalid_action negotiations removed.</li></ul><p class="sub">§3.3, §3.6, §8.5, §8.11</p>`],
    ["analysis", "Statistical analysis plan", `<ul><li>Unit of analysis: the instance (n = ${c.full.instances}). Per-instance mean over the 4 negotiations of each condition; paired difference d<sub>i</sub>.</li><li>Primary: two-sided sign-flip permutation test on mean(d<sub>i</sub>), 10,000 permutations.</li><li>95% percentile bootstrap CI, 10,000 resamples of instances.</li><li>Sensitivity: Wilcoxon signed-rank. Holm correction across H1–H3 at α = 0.05.</li><li>No interim stopping: outcomes are not compared by condition before all ${c.full.total} full-run negotiations exist. This dashboard enforces that lock.</li></ul><p class="sub">§8.9</p>`],
    ["limits", "Limitations", `<ul><li><b>Bundled intervention</b>: effects belong to the three-part package, not any one component.</li><li><b>Output-schema asymmetry</b>: Claude uses a forced tool call, GPT strict JSON schema.</li><li><b>One model pairing</b>; <b>LLM stochasticity</b>; <b>model/version drift</b>.</li><li><b>Linear utilities</b> make efficiency and fairness diverge by construction.</li><li><b>Post-treatment selection</b> in agreement-only metrics; <b>correlated</b> confirmatory outcomes.</li></ul><p class="sub">§8.12</p>`],
  ];
  return `${head("Methodology", "How the experiment works", "A summary of the preregistered design. <code>docs/spec.md</code> is the source of truth; section numbers are cited throughout.")}
  <div class="method"><nav class="toc" aria-label="Methodology sections">${secs.map(([id, t]) => `<a href="#/methodology" data-jump="${id}">${t}</a>`).join("")}</nav>
  <div>${secs.map(([id, t, body]) => `<section id="m-${id}"><h2>${t}</h2>${body}</section>`).join("")}</div></div>`;
}

// ------------------------------------------------------------------ 8. status
function status(s) {
  const p = phase(s), { pilot, full } = s.progress;
  const spent = (mode) => { const rs = s.runs.filter((r) => r.mode === mode && r.totals); if (!rs.length) return null; const c = rs.map((r) => r.totals.cost_usd); return c.some((x) => x === null) ? null : c.reduce((a, b) => a + b, 0); };
  const cap = (mode) => s.runs.find((r) => r.mode === mode)?.config?.experiment?.budget_max_cost_usd ?? null;
  const tone = { completed: "good", running: "good", incomplete: "warn", budget_exhausted: "warn", failed: "crit" };
  const cur = s.provenance;
  return `${head("Experiment status", "Runs and progress", "Everything here is read from the run records the runner writes. The dashboard never starts, stops or reruns anything.")}
  <div class="neg-head">${pill(p)} <span class="cite">refreshes every 10 s</span></div>
  <section class="block"><div class="grid g2 stagger">
    ${["pilot", "full"].map((m, i) => { const pr = s.progress[m], sp = spent(m), cp = cap(m); return `<div class="panel stat" style="--i:${i}"><div class="k"><span>${m === "pilot" ? "Pilot" : "Full run"}</span><span>${pr.done >= pr.target ? "complete" : pr.done ? "in progress" : "not started"}</span></div>
      <div class="v tnum">${pr.done}<small> / ${pr.target} negotiations</small></div>${progressBar(pr.done, pr.target)}
      <div class="n" style="margin-top:10px">Recorded cost: ${sp === null ? "—" : `$${fmt(sp, 4)}`}${cp ? ` of $${cp} cap` : ""} · aborted attempts: ${s.aborted.filter((a) => a.mode === m).length}</div></div>`; }).join("")}
  </div>${!s.runs.length ? `<div style="margin-top:14px">${empty("No runs recorded", "No pilot or full run has been started. The pilot database (<code>results/pilot.db</code>) is created by the runner on its first run.")}</div>` : ""}</section>

  ${s.runs.length ? `<section class="block"><div class="block-head"><h2>Runs</h2><span class="cite">one row per batch · table <code>runs</code></span></div>
    <div class="table-wrap"><table><thead><tr><th>Run</th><th>Status</th><th>Dataset</th><th>Condition</th><th>Started</th><th class="num">Done</th><th class="num">Aborted</th><th class="num">Tokens in/out</th><th class="num">Cost</th><th>Code</th></tr></thead><tbody>
    ${s.runs.map((r) => { const t = r.totals ?? {}; const stale = r.code_version !== cur.code_version.replace("+dirty", "") && r.code_version !== cur.code_version;
      return `<tr><td class="mono">${esc(short(r.run_id, 10))}</td><td><span class="pill ${tone[r.status] ?? "muted"}"><i class="dot"></i>${esc(r.status)}</span></td><td>${esc(r.mode)}</td><td>${cond(r.method)}</td>
      <td title="${esc(new Date(r.started_at * 1000).toISOString())}">${esc(timeAgo(r.started_at))}</td><td class="num">${t.completed_negotiations ?? s.negotiations.filter((n) => n.run_id === r.run_id).length} / ${r.num_negotiations}</td>
      <td class="num">${t.aborted_attempts ?? s.aborted.filter((a) => a.run_id === r.run_id).length}</td><td class="num">${t.input_tokens ?? "—"} / ${t.output_tokens ?? "—"}</td><td class="num">${t.cost_usd === undefined || t.cost_usd === null ? "—" : `$${fmt(t.cost_usd, 4)}`}</td>
      <td class="mono">${esc(short(r.code_version, 8))}${stale ? ` <span class="pill warn" title="Recorded with a different commit than the current working tree">differs</span>` : ""}</td></tr>`; }).join("")}
    </tbody></table></div></section>` : ""}

  <section class="block"><div class="block-head"><h2>Failures &amp; aborts</h2><span class="cite">table <code>aborted_negotiations</code> · infrastructure only</span></div>
    ${s.aborted.length ? `<div class="table-wrap"><table><thead><tr><th>Run</th><th>#</th><th>Attempt</th><th>Reason</th><th>Actor / round</th><th>Rerun</th><th>Detail</th></tr></thead><tbody>
      ${s.aborted.map((a) => `<tr><td class="mono">${esc(short(a.run_id, 10))}</td><td>${a.negotiation_index}</td><td>${a.attempt_number}</td><td><span class="pill ${a.reason === "budget_exhausted" ? "warn" : "crit"}"><i class="dot"></i>${esc(a.reason)}</span></td><td>${esc(a.actor)} · ${a.round_number}</td><td>${a.will_rerun ? "yes" : "no"}</td><td style="white-space:normal;min-width:260px">${esc(a.detail)}</td></tr>`).join("")}
    </tbody></table></div>` : empty("No aborted attempts", "Transport failures and budget stops are listed here when they happen. None are recorded.")}
    ${s.negotiations.some((n) => n.outcome === "invalid_action") ? `<p class="sub" style="margin-top:12px">Invalid actions are strategic outcomes, not failures of the harness: filter by outcome in <a href="#/negotiations">Negotiations</a>.</p>` : ""}
  </section>

  <section class="block"><div class="block-head"><h2>Databases</h2><span class="cite">opened read-only · <code>results/*.db</code></span></div>
    ${s.databases.length ? `<div class="table-wrap"><table><thead><tr><th>Database</th><th>Schema</th><th>Path</th></tr></thead><tbody>${s.databases.map((d) => `<tr><td class="mono">${esc(d.name)}</td><td>${d.ok ? `<span class="pill good"><i class="dot"></i>v2 ok</span>` : `<span class="pill crit"><i class="dot"></i>${esc(d.error)}</span>`}</td><td class="mono" style="white-space:normal">${esc(d.path)}</td></tr>`).join("")}</tbody></table></div>`
    : empty("No database found", "No <code>.db</code> file exists in <code>results/</code> yet.")}
  </section>
  <section class="block"><div class="block-head"><h2>Current version</h2></div><div class="panel">${provenanceLine(cur)}</div></section>`;
}

// ------------------------------------------------------------------ wiring
function wire(name) {
  // Commit scaleX(0) with a forced reflow, then set the target so the bar animates (no rAF: it stalls in hidden tabs).
  main.querySelectorAll(".progress i").forEach((el) => { el.getBoundingClientRect(); el.style.transform = `scaleX(${el.dataset.w / 100})`; });
  main.querySelectorAll("[data-dataset]").forEach((b) => b.addEventListener("click", () => { st.dataset = b.dataset.dataset; render(); }));
  if (name === "negotiations" && st.snap.negotiations.length) {
    negRows();
    main.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("input", () => { st.filters[el.dataset.filter] = el.value; negRows(); }));
    main.querySelector("[data-reset]").addEventListener("click", () => { st.filters = {}; render(false); });
    main.querySelector("#rows").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-href]"); if (tr && !e.target.closest("a")) location.hash = tr.dataset.href; });
  }
  const t = main.querySelector("#evaltoggle");
  if (t) t.addEventListener("change", () => { st.evaluator = t.checked; main.querySelectorAll("[data-eval]").forEach((el) => (el.hidden = !t.checked)); });
  main.querySelectorAll("[data-jump]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); document.getElementById(`m-${a.dataset.jump}`)?.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" }); }));
  const p = main.querySelector("[data-phase]");
  if (p) { if (st.phaseKey && st.phaseKey !== p.dataset.phase) p.classList.add("changed"); st.phaseKey = p.dataset.phase; }
}

// tooltip layer
const tip = document.getElementById("tip");
document.addEventListener("pointerover", (e) => { const el = e.target.closest("[data-tip]"); if (!el) return; tip.textContent = el.dataset.tip; tip.hidden = false; });
document.addEventListener("pointermove", (e) => { if (tip.hidden) return; const x = Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8); tip.style.left = `${x}px`; tip.style.top = `${e.clientY + 14}px`; });
document.addEventListener("pointerout", (e) => { if (e.target.closest("[data-tip]")) tip.hidden = true; });

// theme
document.getElementById("theme").addEventListener("click", () => {
  const next = document.documentElement.dataset.theme === "light" ? "dark" : "light";
  document.documentElement.dataset.theme = next;
  try { localStorage.setItem("theme", next); } catch (e) { /* storage unavailable */ }
});

// boot + live refresh (re-render only when the stored data changed)
const live = document.getElementById("live");
async function boot() {
  try { await load(); await render(false); }
  catch (e) { main.innerHTML = empty("Cannot reach the dashboard server", `Start it with <code>PYTHONPATH=. python -m dashboard.server</code>. (${esc(e.message)})`); live.classList.add("stale"); return; }
  setInterval(async () => {
    try {
      const changed = await load();
      live.classList.remove("stale"); live.classList.remove("tick"); void live.offsetWidth; live.classList.add("tick");
      const { name } = parse();
      if (changed && name !== "negotiation") { const y = scrollY; await render(false); scrollTo(0, y); }
    } catch { live.classList.add("stale"); }
  }, 10000);
}
boot();
