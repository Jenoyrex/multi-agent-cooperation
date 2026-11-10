// Sandbox pages: model connections, the Run-it-yourself page, the consent panel and the behaviour analysis.
// Every number comes from the server (sandbox/analysis.py). A key goes to the server once and is never shown again.
import { esc, fmt, fmtCI, simBody } from "./lib.js";

const INSTALL = `python -m venv .venv
# Windows:        .venv\\Scripts\\activate
# macOS / Linux:  source .venv/bin/activate
pip install git+https://github.com/Jenoyrex/multi-agent-cooperation
cooperation-lab`;

export function createSandboxUI({ main, h, render, shared }) {
  const { icon, notice } = h;
  const { getJSON, post, badge, top, backLink, section, catalog } = shared;
  const state = { connections: null, models: {}, results: {}, model: {} };
  const localOnly = () => !state.catalog?.local_features;
  const lockNote = (what) => notice("info", "lock", `<b>${esc(what)} works on your own computer.</b> ${state.catalog?.demo ? "This public demo runs rule-based agents only, with no keys, no model calls and no code execution." : "This page answers only on the computer running the app."} <a href="#/run-locally">Download and run it yourself</a>; it's free and doesn't need a key.`);

  // ------------------------------------------------------------ run it yourself
  async function runLocally() {
    state.catalog = await catalog();
    return `${top("Run it on your computer", "Install the full app and bring your own agents: free local models, your own API keys, HTTP services or Python files. It needs Python 3.10 or newer, and no key.")}
      <section class="block" style="margin-top:0"><div class="block-head"><h2>Install and start</h2><span class="note">The same on Windows, macOS and Linux. The app opens in your browser at 127.0.0.1.</span></div>
        <pre class="prompt codeblock">${esc(INSTALL)}</pre></section>
      <section class="block"><div class="block-head"><h2>Add a free local model (optional)</h2><span class="note">Runs on your computer, and nothing leaves it.</span></div>
        <ul class="plainlist"><li><b><a href="https://ollama.com" rel="noopener" target="_blank">Ollama</a></b>: after installing, run <code>ollama pull llama3.2</code>. The app looks at <code>http://localhost:11434/v1</code>.</li>
          <li><b><a href="https://lmstudio.ai" rel="noopener" target="_blank">LM Studio</a></b>: load a model and start its local server. The app looks at <code>http://localhost:1234/v1</code>.</li></ul></section>
      <section class="block"><div class="block-head"><h2>What you can do there</h2></div><ul class="plainlist">
        <li><b>Write a prompt agent</b>: choose a model and write its instructions, with no coding.</li>
        <li><b>Use your own API key</b> for OpenAI, Anthropic, Google Gemini, or any OpenAI-compatible provider. You pay your provider directly. You see an estimate and set a hard cap before each run.</li>
        <li><b>Connect your own agent</b> over HTTP, or run a Python file after you confirm.</li>
        <li><b>Compare resource sets</b> and add a new resource in the middle of a negotiation.</li>
        <li><b>Read a behaviour profile</b> of each agent, with sample sizes and 95% intervals.</li></ul></section>
      <section class="block"><div class="block-head"><h2>Privacy and cost</h2></div><ul class="plainlist">
        <li>Your experiments stay in a folder in your home directory.</li>
        <li>Keys stay in memory or your own <code>.env</code> file. They are never saved to the database, exports, reports or logs.</li>
        <li>With an API key, your agent's turns go to that provider, under its terms.</li></ul></section>`;
  }

  // ------------------------------------------------------------ model connections
  const statusLine = (r) => !r ? "" : r.ok
    ? `<span class="ok-line">${icon("check")} ${esc(r.title ?? `${r.models?.length ?? 0} models found`)}${r.latency_s != null ? ` in ${r.latency_s} s` : ""}${r.input_tokens != null ? `, ${r.input_tokens + r.output_tokens} tokens` : ""}</span>`
    : `<span class="bad-line">${icon("x")} ${esc(r.title.replace(/\.$/, ""))}. ${esc(r.hint ?? "")}</span>${r.detail ? `<details class="more"><summary>Details</summary><p class="note">${esc(r.detail)}</p></details>` : ""}`;

  const connCard = (c) => {
    const models = state.models[c.id] ?? [];
    const keyRow = !c.needs_key ? "" : c.key
      ? `<div class="keyrow"><span>${icon("lock")} Key <code>${esc(c.key)}</code> <span class="sub">${c.key_source === "environment" ? "from your environment or .env" : "in memory until you close the app"}</span></span><button type="button" class="btn" data-key-del="${c.id}">Remove</button></div>`
      : `<form class="keyrow" data-key-form="${c.id}"><label class="field grow"><span>API key</span><input type="password" autocomplete="off" spellcheck="false" data-key-input placeholder="Paste your key" data-field="key"></label><button type="submit" class="btn">Add key</button></form>`;
    return `<li class="sheet conn" data-conn="${c.id}"><div class="rc-head"><div><h3>${esc(c.name)}</h3><p class="sub">${c.base_url ? `<code>${esc(c.base_url)}</code>` : "Anthropic API"}</p></div>
        ${c.tier === "local" ? badge("good", "ready", "Free, on this computer") : badge("warn", "cloud", "You pay your provider")}</div>
      <p class="note">${esc(c.help)}</p>${keyRow}
      <div class="testrow"><button type="button" class="btn" data-list="${c.id}">${icon("list")}Find models</button>
        <label class="field grow"><span>Model</span><input type="text" list="m-${c.id}" spellcheck="false" value="${esc(state.model[c.id] ?? "")}" data-model-input placeholder="Model name"><datalist id="m-${c.id}">${models.map((m) => `<option value="${esc(m)}">`).join("")}</datalist></label>
        <button type="button" class="btn" data-test="${c.id}">${icon("bolt")}Test connection</button></div>
      <small class="note">${c.tier === "api" ? "Test sends one tiny request to your provider, which may cost a tiny amount. " : ""}Nothing is sent until you click.</small>
      <div role="status" class="conn-out">${statusLine(state.results[c.id])}</div>
      ${c.builtin ? "" : `<div class="actions"><button type="button" class="btn" data-conn-del="${c.id}">${icon("trash")}Delete connection</button></div>`}</li>`;
  };

  async function models() {
    state.catalog = await catalog();
    if (localOnly()) return `${top("Model connections", "Connect free local models or your own API keys.")}${lockNote("Connecting models")}`;
    const data = await getJSON("/api/models/connections");
    state.connections = data.connections;
    const local = data.connections.filter((c) => c.tier === "local"), api = data.connections.filter((c) => c.tier === "api");
    return `${top("Model connections", "Where prompt agents get their answers: a free model on this computer, or a provider with your own key.", `<a class="cta" href="#/agents/new/prompt">${icon("plus")}Write a prompt agent</a>`)}
      ${section("On this computer", "Free. Nothing leaves your computer.", `<ul class="conn-list">${local.map(connCard).join("")}</ul>`)}
      ${section("With your own API key", "You pay your provider directly. Keys stay in memory or your .env, and are never saved or shown again.", `<ul class="conn-list">${api.map(connCard).join("")}</ul>`)}
      <section class="block"><div class="block-head"><h2>Add a connection</h2><span class="note">Any OpenAI-compatible server.</span></div>
        <form class="sheet" id="conn-form" novalidate><div class="wb-grid2"><label class="field"><span>Name</span><input type="text" maxlength="40" data-c="name" data-field="name" placeholder="e.g. Groq"></label>
          <label class="field"><span>Where it runs</span><select data-c="tier" data-field="tier"><option value="api">A provider, with a key</option><option value="local">This computer, free</option></select></label></div>
          <label class="field"><span>Base URL</span><input type="url" spellcheck="false" data-c="base_url" data-field="base_url" placeholder="https://api.groq.com/openai/v1"><small class="note">${esc(data.custom_help.api)}</small></label>
          <div class="wb-grid2"><label class="field"><span>Reply format</span><select data-c="structured" data-field="structured"><option value="json_object">JSON mode</option><option value="json_schema">JSON schema</option><option value="none">Ask for JSON in the prompt</option></select><small class="note">Support varies by model. A reply that isn't valid is recorded as an invalid action, never repaired.</small></label>
            <label class="field"><span>Requests per minute</span><input type="number" min="0" max="6000" step="1" value="0" data-c="rpm" data-field="rpm"><small class="note">0 means no limit.</small></label></div>
          <p class="step-error" id="wb-error" role="alert" hidden></p>
          <div class="actions"><button type="submit" class="btn">${icon("plus")}Add connection</button></div></form></section>`;
  }

  function wireModels() {
    const rerender = () => render(false);
    const errorOut = (res) => { const el = main.querySelector("#wb-error"); if (el) { el.textContent = res.data?.error ?? "That did not work."; el.hidden = false; } };
    main.querySelectorAll("[data-model-input]").forEach((el) => el.addEventListener("input", () => { state.model[el.closest("[data-conn]").dataset.conn] = el.value; }));
    main.querySelectorAll("[data-key-form]").forEach((f) => f.addEventListener("submit", async (e) => {
      e.preventDefault();
      const input = f.querySelector("[data-key-input]"), res = await post("/api/models/keys", { connection: f.dataset.keyForm, key: input.value });
      input.value = ""; // the page keeps no copy
      if (res.ok) rerender(); else { state.results[f.dataset.keyForm] = { ok: false, title: res.data?.error ?? "The key was not accepted", hint: "" }; rerender(); }
    }));
    main.querySelectorAll("[data-key-del]").forEach((b) => b.addEventListener("click", async () => { await post("/api/models/keys/delete", { connection: b.dataset.keyDel }); rerender(); }));
    main.querySelectorAll("[data-conn-del]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "Confirm delete"; b.classList.add("danger"); return; }
      await post(`/api/models/connections/${b.dataset.connDel}/delete`, {}); rerender();
    }));
    main.querySelectorAll("[data-list]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.list; b.disabled = true;
      const res = await post("/api/models/list", { connection: id });
      state.models[id] = res.data.models ?? [];
      state.results[id] = res.ok ? res.data : { ok: false, title: res.data?.error ?? "Could not list models", hint: "" };
      if (res.ok && res.data.ok && !state.model[id] && state.models[id].length) state.model[id] = state.models[id][0];
      rerender();
    }));
    main.querySelectorAll("[data-test]").forEach((b) => b.addEventListener("click", async () => {
      const id = b.dataset.test;
      if (!state.model[id]?.trim()) { state.results[id] = { ok: false, title: "Choose a model first", hint: "Use Find models, or type its name." }; return rerender(); }
      b.disabled = true;
      const res = await post("/api/models/test", { connection: id, model: state.model[id] ?? "" });
      state.results[id] = res.ok ? res.data : { ok: false, title: res.data?.error ?? "The test could not run", hint: "" };
      rerender();
    }));
    const form = main.querySelector("#conn-form");
    form?.addEventListener("submit", async (e) => {
      e.preventDefault();
      const v = (k) => form.querySelector(`[data-c="${k}"]`).value;
      const res = await post("/api/models/connections", { name: v("name"), tier: v("tier"), base_url: v("base_url"), structured: v("structured"), rpm: Number(v("rpm")) || 0 });
      if (res.ok) rerender(); else errorOut(res);
    });
  }

  // ------------------------------------------------------------ consent, on the wizard's Review step
  async function estimateFor(d) {
    let res = await post("/api/workbench/simulations/estimate", simBody(d));
    if (!res.ok) return res;
    if (res.data.required.some((c) => c.id === "api_cost") && d.caps.max_tokens == null) { // start from the suggestion; the user can lower it
      d.caps.max_tokens = res.data.suggested_token_cap;
      res = await post("/api/workbench/simulations/estimate", simBody(d));
    }
    return res;
  }

  function consentPanel(d, est) {
    const money = (v) => (v == null ? "unknown without prices" : v < 0.01 ? `under 0.01` : fmt(v, 2));
    const rows = est.agents.map((r) => `<tr><td><b>${esc(r.name)}</b><span class="sub">${esc(r.source)}</span></td><td class="num">${r.calls_max}</td><td class="num">${r.tokens_estimate.toLocaleString()}</td>
      <td>${r.tier === "api" ? `<div class="pricepair"><input type="number" min="0" step="any" aria-label="Price per million input tokens for ${esc(r.model)}" placeholder="in" value="${d.prices[r.model]?.[0] ?? ""}" data-price="${esc(r.model)}" data-i="0"><input type="number" min="0" step="any" aria-label="Price per million output tokens for ${esc(r.model)}" placeholder="out" value="${d.prices[r.model]?.[1] ?? ""}" data-price="${esc(r.model)}" data-i="1"></div>` : '<span class="sub">free, local</span>'}</td></tr>`).join("");
    const usesKey = est.required.some((c) => c.id === "api_cost");
    const table = usesKey ? `<div class="table-wrap"><table><thead><tr><th>Model agent</th><th class="num">Most calls</th><th class="num">Tokens (estimate)</th><th>Your price per 1M tokens (in, out)</th></tr></thead><tbody>${rows}</tbody></table></div>
        <p class="note">${esc(est.note)}${usesKey ? " Enter prices from your provider's price page, in the currency you pay in; this app never guesses them." : ""}</p>` : "";
    const caps = usesKey ? `<dl class="review"><div><dt>Estimated tokens</dt><dd>${est.api_tokens_estimate.toLocaleString()}</dd></div><div><dt>Estimated cost</dt><dd>${money(est.cost_estimate)}</dd></div></dl>
        <div class="wb-grid2"><label class="field"><span>Stop at this many tokens</span><input type="number" min="1" step="1" value="${d.caps.max_tokens ?? ""}" data-cap="max_tokens" data-field="caps.max_tokens"><small class="note">Required. Checked before every call, keeping room for that call's worst case, so a run stops before the cap rather than after it.</small></label>
          <label class="field"><span>Stop at this spend <span class="sub">(optional)</span></span><input type="number" min="0" step="any" value="${d.caps.max_cost ?? ""}" data-cap="max_cost" data-field="caps.max_cost"><small class="note">Uses your prices above.</small></label></div>` : "";
    const problem = est.caps_problem ? `<p class="step-error" role="alert">${esc(est.caps_problem.error)}</p>` : "";
    const checks = est.required.length ? `<fieldset class="choice"><legend>Please confirm</legend>${est.required.map((c) => `<label class="toggle"><input type="checkbox" data-consent="${c.id}"${d.consented.includes(c.id) ? " checked" : ""}> ${esc(c.text)}</label>`).join("")}
        ${est.required.some((c) => c.id === "python_file") ? '<small class="note">It runs in its own process with no API keys and a time limit per turn, but with your permissions: it could read your files. Only run files you trust.</small>' : ""}</fieldset>` : "";
    return table || checks ? `<section class="consent" id="consent">${est.required.length ? "<h3>Before this runs</h3>" : ""}${table}${caps}${problem}${checks}</section>` : "";
  }

  const consentReady = (d, est) => !est.caps_problem && est.required.every((c) => d.consented.includes(c.id));
  const runLabel = (est, n) => (est.required.some((c) => c.id === "api_cost") ? "Run (uses your API key, you pay)" : est.required.some((c) => c.id === "python_file") ? "Run this file on my computer" : `Run ${n} negotiation${n === 1 ? "" : "s"}`);

  function wireConsent(form, d) {
    const rerender = () => render(false);
    form.querySelectorAll("[data-price]").forEach((el) => el.addEventListener("change", () => {
      const m = el.dataset.price, pair = d.prices[m] ?? [null, null];
      pair[+el.dataset.i] = el.value === "" ? null : Number(el.value);
      if (pair.every((v) => v != null)) d.prices[m] = pair; else if (pair.every((v) => v == null)) delete d.prices[m]; else { d.prices[m] = pair; return; } // one half typed: wait for the other
      d.consented = []; rerender();
    }));
    form.querySelectorAll("[data-cap]").forEach((el) => el.addEventListener("change", () => {
      d.caps[el.dataset.cap] = el.value === "" ? null : Number(el.value); d.consented = []; rerender();
    }));
    form.querySelectorAll("[data-consent]").forEach((el) => el.addEventListener("change", () => {
      d.consented = el.checked ? [...new Set([...d.consented, el.dataset.consent])] : d.consented.filter((c) => c !== el.dataset.consent);
      rerender();
    }));
  }

  // ------------------------------------------------------------ the wizard's sweep, event and persona editors
  function sweepEditor(d, envList) {
    return `<details class="more"${d.extra_envs.length ? " open" : ""}><summary>Compare on more resource sets${d.extra_envs.length ? ` (${d.extra_envs.length})` : ""}</summary><p class="note">The same agents and seeds on each set you tick, so the comparison is paired.</p>
      <div class="pickgrid wide">${envList.filter((e) => e.id !== d.environment).map((e) => `<label class="opt"><input type="checkbox" data-sweep="${e.id}"${d.extra_envs.includes(e.id) ? " checked" : ""}><span><b>${esc(e.name)}</b></span></label>`).join("")}</div></details>`;
  }

  function eventEditor(d) {
    const row = (e, i) => `<div class="evrow" data-ev="${i}"><label class="field"><span>At the start of turn</span><input type="number" min="2" step="1" value="${e.turn}" data-ev-k="turn" data-field="events.${i}.turn"></label>
      <label class="field"><span>New resource</span><input type="text" maxlength="20" spellcheck="false" value="${esc(e.category)}" data-ev-k="category" data-field="events.${i}.category"></label>
      <label class="field"><span>Units</span><input type="number" min="1" step="1" value="${e.units}" data-ev-k="units" data-field="events.${i}.units"></label>
      <label class="field"><span>Move deadline back by</span><input type="number" min="0" max="10" step="1" value="${e.extend_rounds}" data-ev-k="extend_rounds" data-field="events.${i}.extend_rounds"></label>
      <button type="button" class="btn" data-ev-del="${i}" aria-label="Remove this event">${icon("x")}</button></div>`;
    return `<details class="more"${d.events.length ? " open" : ""}><summary>Add a resource during the negotiation${d.events.length ? ` (${d.events.length})` : ""}</summary>
      <p class="note">At the chosen turn the pool grows and each agent gets its own private value for the new resource. Any standing offer stops counting. The analysis then shows how each agent reacted. <a href="#/help">Rules</a></p>
      ${d.events.map(row).join("")}${d.events.length < 3 ? `<button type="button" class="btn" data-ev-add>${icon("plus")}Add an event</button>` : ""}</details>`;
  }

  function personaEditor(d) {
    const row = (p, i) => `<div class="evrow persona" data-pe="${i}"><label class="field"><span>Name</span><input type="text" maxlength="40" value="${esc(p.name)}" data-pe-k="name" data-field="personas.${i}"></label>
      <label class="field grow"><span>Instructions</span><textarea rows="2" maxlength="4000" data-pe-k="text">${esc(p.text)}</textarea></label><button type="button" class="btn" data-pe-del="${i}" aria-label="Remove this persona">${icon("x")}</button></div>`;
    return `<details class="more"${d.personas.length ? " open" : ""}><summary>Compare personas${d.personas.length ? ` (${d.personas.length})` : ""}</summary><p class="note">Run each design with each set of instructions, replacing the prompt agents' own.</p>
      ${d.personas.map(row).join("")}${d.personas.length < 6 ? `<button type="button" class="btn" data-pe-add>${icon("plus")}Add a persona</button>` : ""}</details>`;
  }

  function wireDesign(form, d) {
    const rerender = () => render(false);
    const on = (sel, ev, fn) => form.querySelectorAll(sel).forEach((el) => el.addEventListener(ev, () => fn(el)));
    on("[data-sweep]", "change", (el) => { d.extra_envs = el.checked ? [...d.extra_envs, el.dataset.sweep] : d.extra_envs.filter((x) => x !== el.dataset.sweep); rerender(); });
    on("[data-ev-add]", "click", () => { d.events.push({ turn: 3, category: "bonus", units: 6, extend_rounds: 0 }); rerender(); });
    on("[data-ev-del]", "click", (el) => { d.events.splice(+el.dataset.evDel, 1); rerender(); });
    on("[data-ev-k]", "change", (el) => { const k = el.dataset.evK; d.events[+el.closest("[data-ev]").dataset.ev][k] = el.type === "number" ? Number(el.value) : el.value; });
    on("[data-pe-add]", "click", () => { d.personas.push({ name: `Persona ${d.personas.length + 1}`, text: "" }); rerender(); });
    on("[data-pe-del]", "click", (el) => { d.personas.splice(+el.dataset.peDel, 1); rerender(); });
    on("[data-pe-k]", "change", (el) => { d.personas[+el.closest("[data-pe]").dataset.pe][el.dataset.peK] = el.value; });
  }

  // ------------------------------------------------------------ behaviour analysis
  const SIGNED = new Set(["concession_speed", "demand_shift", "shift"]);
  /** A mean and its interval on a fixed scale. The numbers beside it say the same thing, for anyone who can't see it. */
  function ciBar(s, metric, unit, color) {
    if (s.mean == null) return "";
    const [lo, hi] = unit === "turns" ? [0, 20] : SIGNED.has(metric) ? [-1, 1] : [0, 1];
    const x = (v) => Math.max(0, Math.min(100, ((v - lo) / (hi - lo)) * 100));
    return `<span class="cibar" style="--c:${color}" aria-hidden="true">${SIGNED.has(metric) ? '<i class="zero"></i>' : ""}${s.ci ? `<i class="ci" style="left:${x(s.ci[0])}%;width:${Math.max(0.5, x(s.ci[1]) - x(s.ci[0]))}%"></i>` : ""}<i class="pt" style="left:${x(s.mean)}%"></i></span>`;
  }
  const cell = (s, metric, unit, color) => (s.mean == null ? '<td class="metric none"><span class="sub">not observed</span></td>'
    : `<td class="metric"><b>${esc(fmtCI(s, unit).value)}</b> ${s.small_n ? `<span class="smalln" title="Fewer than 10 repetitions">small n</span>` : ""}<span class="sub">${esc(fmtCI(s, unit).detail)}</span>${ciBar(s, metric, unit, color)}</td>`);

  const metricName = (m, def) => `${esc(def.label)}<button type="button" class="hint" data-tip="${esc(def.definition)}" aria-label="${esc(`${def.label}. ${def.definition}`)}">${icon("info")}</button>`;
  const COLORS = { X: "var(--base)", Y: "var(--struct)" };

  function profileTable(sb, names) {
    const labels = Object.keys(sb.profiles), order = Object.keys(sb.profiles[labels[0]] ?? {}).filter((m) => m !== "demand_shift" || sb.reactions);
    return `<div class="table-wrap"><table class="metrics"><thead><tr><th>Measure</th>${labels.map((l) => `<th><i class="wb-dot" style="--c:${COLORS[l] ?? "var(--scale)"}"></i> ${esc(names[l])}</th>`).join("")}</tr></thead><tbody>
      ${order.map((m) => `<tr><th scope="row">${metricName(m, sb.metrics[m])}</th>${labels.map((l) => cell(sb.profiles[l][m], m, sb.metrics[m].unit, COLORS[l] ?? "var(--scale)")).join("")}</tr>`).join("")}</tbody></table></div>`;
  }

  function usageBlock(sb) {
    const u = sb.usage;
    const models = Object.values(sb.source_labels ?? {}).some((l) => /^(Local|API) model/.test(l)); // HTTP and Python agents have turns, not tokens
    if (!u.calls || !models) return "";
    const known = (v) => (v == null ? "not reported" : v.toLocaleString());
    return `<section class="block"><div class="block-head"><h2>Calls, tokens and cost</h2><span class="note">As your model server reported. Tokens from discarded retries can't be seen, so your provider's bill is the real figure.</span></div>
      <dl class="factline"><div><dt>Calls</dt><dd>${u.calls}</dd></div><div><dt>Attempts</dt><dd>${u.attempts}</dd></div><div><dt>Input tokens</dt><dd>${known(u.input_tokens)}</dd></div><div><dt>Output tokens</dt><dd>${known(u.output_tokens)}</dd></div>
      <div><dt>Cost</dt><dd>${u.priced ? (u.cost == null ? "unknown" : fmt(u.cost, 4)) : "no prices entered"}</dd></div></dl></section>`;
  }

  function findingsBlock(sb) {
    return sb.findings.length ? `<section class="block"><div class="block-head"><h2>In plain words</h2><span class="note">Generated from the numbers below. Nothing here says why an agent acted.</span></div><ul class="plainlist findings">${sb.findings.map((f) => `<li>${esc(f.text)}</li>`).join("")}</ul></section>` : "";
  }

  const warningsBlock = (sb) => `${notice("info", "info", `<b>Exploratory.</b> ${esc(sb.exploratory.replace(/^Exploratory sandbox analysis: it describes/, "This describes"))}`)}${sb.warnings.map((w) => notice("warn", "alert", esc(w))).join("")}${sb.intervals_ready ? "" : notice("info", "await", "Intervals appear when the experiment has finished.")}`;

  function profileView(doc, sb, names) {
    return `${warningsBlock(sb)}${findingsBlock(sb)}
      <section class="block"><div class="block-head"><h2>Behaviour profile</h2><span class="note">The dot is the mean, the band its 95% interval, and n counts repetitions (${sb.n_repetitions}; ${sb.n_completed} negotiations). Hover a measure's ⓘ for its definition.</span></div>${profileTable(sb, names)}</section>
      ${usageBlock(sb)}`;
  }

  function compareView(sb, names) {
    const sw = sb.sweep, ms = ["agreement", "rwe", "ew"];
    const row = (g) => `<tr><th scope="row">${esc(g.name)}<span class="sub">${g.n_negotiations} negotiations</span></th>${ms.map((m) => cell(g.outcomes[m], m, sb.metrics[m].unit, "var(--accent)")).join("")}</tr>`;
    const diff = (g) => `<tr><th scope="row">${esc(g.name)}</th>${ms.map((m) => { const d = g.differences[m]; return d?.mean == null ? '<td class="metric none"><span class="sub">no pairs</span></td>' : `<td class="metric"><b>${signedPct(d.mean, sb.metrics[m].unit)}</b> ${d.small_n ? '<span class="smalln">small n</span>' : ""}<span class="sub">${esc(diffDetail(d))}</span>${ciBar(d, "shift", sb.metrics[m].unit, "var(--accent)")}</td>`; }).join("")}</tr>`;
    return `${warningsBlock(sb)}${findingsBlock(sb)}
      <section class="block"><div class="block-head"><h2>Each group</h2><span class="note">The dot is the mean, the band its 95% interval, and n counts repetitions.</span></div>
        <div class="table-wrap"><table class="metrics"><thead><tr><th>Group</th>${ms.map((m) => `<th>${metricName(m, sb.metrics[m])}</th>`).join("")}</tr></thead><tbody>${sw.groups.map(row).join("")}</tbody></table></div></section>
      ${sw.groups.length > 1 ? `<section class="block"><div class="block-head"><h2>Difference from ${esc(sw.baseline)}</h2><span class="note">${esc(sw.note)} An interval that crosses zero means these runs can't tell the two apart.</span></div>
        <div class="table-wrap"><table class="metrics"><thead><tr><th>Group</th>${ms.map((m) => `<th>${esc(sb.metrics[m].label)}</th>`).join("")}</tr></thead><tbody>${sw.groups.slice(1).map(diff).join("")}</tbody></table></div></section>` : ""}
      ${sw.groups.map((g) => (g.agents && Object.keys(g.agents).length ? `<details class="more"><summary>Behaviour profile on ${esc(g.name)}</summary>${profileTable({ ...sb, profiles: g.agents }, names)}</details>` : "")).join("")}`;
  }
  const signedPct = (v, unit) => (unit === "turns" ? `${v > 0 ? "+" : ""}${fmt(v, 1)}` : `${v > 0 ? "+" : ""}${(v * 100).toFixed(0)}%`);
  const diffDetail = (d) => (d.ci ? `95% CI ${(d.ci[0] * 100).toFixed(0)}% to ${(d.ci[1] * 100).toFixed(0)}%, n = ${d.n}` : `n = ${d.n}, too few for an interval`);

  function reactionView(sb, names) {
    const r = sb.reactions;
    const dumbbell = (a) => (a.before.mean == null || a.after.mean == null ? "" : `<span class="cibar dumb" aria-hidden="true"><i class="ci" style="left:${Math.min(a.before.mean, a.after.mean) * 100}%;width:${Math.abs(a.after.mean - a.before.mean) * 100}%"></i><i class="pt before" style="left:${a.before.mean * 100}%"></i><i class="pt" style="left:${a.after.mean * 100}%"></i></span>`);
    const rows = Object.entries(r.agents).map(([l, a]) => `<tr><th scope="row">${esc(names[l])}</th>${cell(a.before, "before", "share", COLORS[l])}${cell(a.after, "after", "share", COLORS[l])}
      ${a.shift.mean == null ? '<td class="metric none"><span class="sub">not observed</span></td>' : `<td class="metric"><b>${signedPct(a.shift.mean, "share")}</b> ${a.shift.small_n ? '<span class="smalln">small n</span>' : ""}<span class="sub">${esc(diffDetail(a.shift))}</span>${dumbbell(a)}</td>`}
      ${cell(a.new_resource_claim, "new_resource_claim", "share", COLORS[l])}<td class="metric"><span class="sub">${Object.entries(a.responses).map(([k, v]) => `${esc(k.toLowerCase().replace("_", " "))}: ${v}`).join(" · ") || "none"}</span></td></tr>`).join("");
    const ev = r.events.map((e) => `turn ${e.turn}: ${e.units} units of ${esc(e.category)}${e.extend_rounds ? `, deadline moved back ${e.extend_rounds}` : ""}`).join("; ");
    return `${warningsBlock(sb)}${findingsBlock(sb)}
      <section class="block"><div class="block-head"><h2>How each agent reacted</h2><span class="note">Demand is an offer's value to the agent as a share of its maximum on the pool at that moment. Events: ${ev}. ${r.n_reached} of ${r.n_negotiations} negotiations reached one.</span></div>
        <div class="table-wrap"><table class="metrics"><thead><tr><th>Agent</th><th>Last offer before</th><th>First offer after</th><th>Change</th><th>Share of new resource claimed</th><th>What it did first</th></tr></thead><tbody>${rows}</tbody></table></div>
        <dl class="factline" style="margin-top:14px"><div><dt>Best possible total before (W*)</dt><dd>${fmt(r.pre_event_welfare.mean, 1)}</dd></div><div><dt>After</dt><dd>${fmt(r.final_welfare.mean, 1)}</dd></div></dl>
        <p class="note">Value created is scored against the larger total after the event. ${r.n_ended_before ? `${r.n_ended_before} negotiation(s) ended before the event and are left out here.` : ""}</p></section>`;
  }

  /** Views this experiment has, before the older analytics views. */
  const analysisViews = (sb) => [["profile", "Behaviour profile"], ...(sb?.sweep ? [["compare", "Compare groups"]] : []), ...(sb?.reactions ? [["reaction", "Resource events"]] : [])];
  function analysisBody(view, doc, names) {
    const sb = doc.analytics.sandbox;
    return view === "compare" ? compareView(sb, names) : view === "reaction" ? reactionView(sb, names) : profileView(doc, sb, names);
  }

  // ------------------------------------------------------------ shared
  function wire(name) {
    if (name === "models" && main.querySelector(".conn-list")) wireModels();
  }

  return { routes: { models, "run-locally": runLocally }, names: ["models", "run-locally"], wire, estimateFor, consentPanel, consentReady, runLabel, wireConsent, sweepEditor, eventEditor, personaEditor, wireDesign, analysisViews, analysisBody };
}
