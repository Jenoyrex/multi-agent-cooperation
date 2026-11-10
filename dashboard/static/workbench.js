// Workbench: agents, resources, the experiment wizard, the live workspace and per-experiment analytics.
// Every value shown comes from /api/workbench/*; the browser only re-describes stored negotiations
// (lib.js helpers, unit-tested) and computes the size of a design before it starts.
import { actionCounts, boxStats, completedTrials, cumulativeOutcomes, designSize, esc, flows, fmt, normalize100, OUTCOME_LABEL, OUTCOMES, pct, simBody, sourceSummary, summarize, timeAgo } from "./lib.js";
import { createSandboxUI } from "./sandboxui.js";
import { dataTable, histogram, stackBars } from "./charts.js";
import { boxPlot, donut, heatmap, lineChart, network, radar, sankey, scatter, stackedArea, timeline, toggleLegend } from "./viz.js";

export const OUT_COLOR = { agreed: "var(--o-agreed)", walked_away: "var(--o-walked)", timeout: "var(--o-timeout)", invalid_action: "var(--o-invalid)" };
const OUT_INK = { agreed: "var(--panel)", walked_away: "var(--ink)", timeout: "var(--ink)", invalid_action: "var(--panel)" };
export const AGENT_COLOR = { X: "var(--base)", Y: "var(--struct)" };
const COND_COLOR = { baseline_v1: "var(--base)", structured_v1: "var(--struct)" };
export const COND_NAME = { baseline_v1: "Baseline", structured_v1: "Structured" };
const STATUS = { running: ["accent", "await", "Running"], completed: ["good", "check", "Completed"], cancelled: ["warn", "alert", "Cancelled"],
  failed: ["crit", "x", "Failed"], interrupted: ["warn", "alert", "Interrupted"] };
const CAT = {
  mock: { label: "Rule-based", runs: "Simulated · runs free on the lab's server" },
  python: { label: "Built-in Python", runs: "Simulated · runs free on the lab's server" },
  http: { label: "HTTP agent", runs: "Your own program, over HTTP" },
  model: { label: "Prompt agent", runs: "Free with a local model; with an API key, you pay your provider" },
  llm: { label: "Local LLM", runs: "A model on your own computer · free" },
  upload: { label: "Uploaded Python", runs: "Inspected only · never executed here" },
  provider: { label: "External AI provider", runs: "Locked · paid API, never called" },
};
const ACT = { OFFER: "Offer", ACCEPT: "Accept", WALK_AWAY: "Walk away", INVALID: "Invalid" };
const PROVIDERS = { claude: "Anthropic", openai: "OpenAI", gemini: "Google" };
// Where a kind runs, for kinds whose category alone doesn't say.
const RUNS = { python_file: "Runs on your computer, after you confirm", http_remote: "On your own server, after you confirm", http_local: "Your program on this computer · free", prompt: CAT.model.runs };
const runsOf = (kindId, category) => RUNS[kindId] ?? CAT[category].runs;
const LOCAL_KINDS = ["prompt", "http_local", "http_remote", "python_file"];

export function createWorkbench({ main, h, render, onCleanup, motion }) {
  const { icon, notice } = h;
  const state = { catalog: null, agentDraft: null, upload: null, envDraft: null, envTab: "visual", importFmt: "text", importText: "",
    simDraft: null, simTab: "live", openTrial: null, af: { condition: "all", outcome: "all", view: "profile" }, test: null };

  // ------------------------------------------------------------ data
  const getJSON = async (url) => { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw Object.assign(new Error(`${url} ${r.status}`), { status: r.status }); return r.json(); };
  async function post(url, body) {
    const r = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body ?? {}) });
    let data = null;
    try { data = await r.json(); } catch { /* empty body */ }
    return { ok: r.ok, status: r.status, data: data ?? {} };
  }
  const catalog = async () => (state.catalog ??= await getJSON("/api/workbench/catalog"));
  const kindOf = (id) => state.catalog.kinds.find((k) => k.id === id);

  // ------------------------------------------------------------ shared bits
  const badge = (tone, ic, text) => `<span class="badge ${tone}">${icon(ic)}${esc(text)}</span>`;
  const statusBadge = (s) => { const [tone, ic, text] = STATUS[s] ?? ["", "info", s]; return badge(tone, ic, text); };
  const runBadge = (a) => (a.executable ? (a.kind === "prompt" ? badge("good", "ready", "Model") : ["python_file", "http_remote"].includes(a.kind) ? badge("warn", "alert", "Asks before it runs") : badge("good", "ready", "Runs free"))
    : LOCAL_KINDS.includes(a.kind) ? badge("", "lock", "Runs on your own computer") : a.category === "upload" ? badge("", "lock", "Inspected, not executed") : a.kind === "gemini" ? badge("", "x", "No adapter") : badge("warn", "lock", "Locked"));
  const top = (title, sub = "", actions = "", back = "") => `${back}<header class="page-head split"><div><h1>${esc(title)}</h1>${sub ? `<p class="standfirst">${sub}</p>` : ""}</div>${actions ? `<div class="head-actions">${actions}</div>` : ""}</header>`;
  const backLink = (href, label) => `<a class="back" href="${href}">${icon("chev-l")}${esc(label)}</a>`;
  const section = (title, note = "", body = "", id = "") => `<section class="block"${id ? ` id="${id}"` : ""}><div class="block-head"><h2>${esc(title)}</h2>${note ? `<span class="note">${note}</span>` : ""}</div>${body}</section>`;
  const when = (t) => (t ? timeAgo(t) : "—");
  const errorLine = (msg) => `<p class="step-error" id="wb-error" role="alert"${msg ? "" : " hidden"}>${msg ? esc(msg) : ""}</p>`;
  function showError(res, fallback, focus = true) {
    const el = main.querySelector("#wb-error");
    const msg = res.data?.error ?? fallback;
    if (el) { el.innerHTML = `${esc(msg)}${res.data?.field ? ` <span class="sub">(${esc(res.data.field)})</span>` : ""}`; el.hidden = false; }
    main.querySelectorAll("[aria-invalid]").forEach((x) => x.removeAttribute("aria-invalid"));
    const f = res.data?.field && main.querySelector(`[data-field="${CSS.escape(res.data.field)}"]`);
    if (f) { f.setAttribute("aria-invalid", "true"); if (focus) f.focus(); }
  }
  const fade = (els) => { if (motion.on() && els?.length) motion.M.animate(els, { opacity: [0, 1], transform: ["translateY(6px)", "none"] }, { duration: 0.3, ease: motion.EASE, delay: motion.M.stagger(0.025) }); };
  function download(file) {
    const url = URL.createObjectURL(new Blob([file.content], { type: file.content_type }));
    const a = Object.assign(document.createElement("a"), { href: url, download: file.filename });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const unavailable = (msg) => `${top("Workbench unavailable", esc(msg))}<a class="cta" href="#/">Start${icon("chev-r")}</a>`;
  const empty = (title, text, cta = "") => `<div class="empty">${icon("info")}<div><h3>${esc(title)}</h3><p>${text}</p>${cta}</div></div>`;
  const avatar = (name, color = "var(--scale)") => `<span class="avatar" style="--c:${color}" aria-hidden="true">${esc(String(name).trim().charAt(0).toUpperCase() || "?")}</span>`;
  const chartCard = (title, what, body, { lg = "", table = "", wide = false } = {}) =>
    `<figure class="ccard${wide ? " wide" : ""}"><figcaption class="ch"><h3>${esc(title)}</h3><p>${what}</p></figcaption>${lg}<div class="cbody">${body}</div>${table}</figure>`;

  const sbx = createSandboxUI({ main, h, render, shared: { getJSON, post, badge, top, backLink, section, catalog } });

  // ------------------------------------------------------------ agents
  async function agents(_s, arg) {
    try { await catalog(); } catch (e) { return unavailable(e.message); }
    state.conns = arg && state.catalog.local_features ? (await getJSON("/api/models/connections")).connections : [];
    if (arg === "new") return agentChooser();
    if (arg === "new/llm") return llmPage();
    if (arg?.startsWith("new/")) return agentNew(arg.slice(4));
    if (arg) return agentDetail(arg);
    const list = await getJSON("/api/workbench/agents");
    const group = (title, note, cats) => {
      const rows = list.filter((a) => cats.includes(a.category));
      if (!rows.length) return "";
      return section(title, note, `<ul class="ilist">${rows.map((a) => `<li><a class="irow" href="#/agents/${a.id}">${avatar(a.name, a.executable ? "var(--accent)" : "var(--scale)")}
        <span class="imain"><b>${esc(a.name)}</b><span class="sub">${esc(a.kind_label)}${a.builtin ? " · built-in" : ""}${a.history.length ? ` · used in ${a.history.length} experiment${a.history.length === 1 ? "" : "s"}` : ""}</span></span>
        ${runBadge(a)}${icon("chev-r")}</a></li>`).join("")}</ul>`);
    };
    return `${top("Agents", "The participants in your experiments: local LLMs, models with your instructions, rule-based strategies and your own programs.", `<a class="cta" href="#/agents/new">${icon("plus")}Add agent</a>`)}
      ${group("Local LLMs", "Open models running on your own computer.", ["llm"])}
      ${group("Prompt agents", "Models with your instructions. Free with a local model; with an API key, you pay your provider.", ["model"])}
      ${group("Strategies and programs", "Rule-based strategies, and your own programs over HTTP. Results are labelled by what ran.", ["mock", "python", "http"])}
      ${group("Your Python files", state.catalog.local_features ? "Files you added. A file set to run asks you to confirm first, and runs in its own process. An inspected file never runs." : "Python files visitors added. Each was inspected for the agent interface; none is ever executed by this server.", ["upload"])}
      ${group("Research study configurations", "The study's Claude, GPT and Gemini setups. They stay locked here, so the study's zero-cost rule holds. To use these models yourself, write a prompt agent.", ["provider"])}
      ${errorLine()}`;
  }

  function agentChooser() {
    const local = state.catalog.local_features;
    const opt = (href, ic, title, text, tag) => `<a class="choice-card" href="${href}"><span class="cc-icon">${icon(ic)}</span><span class="cc-body"><b>${title}</b><span>${text}</span><span class="cc-tag">${tag}</span></span>${icon("chev-r")}</a>`;
    return `${top("Add an agent", "Choose how your agent is made. You can change its settings later.", "", backLink("#/agents", "Agents"))}
      <div class="choices">
        ${opt("#/agents/new/llm", "bolt", "Connect a local LLM", "Test an open model such as Llama, Qwen or Mistral, running free on your own computer with Ollama.", state.catalog.demo ? badge("", "lock", "Needs your computer") : badge("good", "ready", "Runs free"))}
        ${local ? opt("#/agents/new/prompt", "doc", "Write a prompt agent", "Choose a model and write its instructions. No coding. Use a free local model or your own API key.", badge("good", "ready", "No coding")) : ""}
        ${opt("#/agents/new/rule", "sliders", "Configure a built-in strategy", "Pick a ready-made negotiating strategy and tune how hard it bargains. Runs instantly.", badge("good", "ready", "Runs free"))}
        ${local ? opt("#/agents/new/http", "cloud", "Connect your HTTP agent", "Your own service at a URL, on this computer or elsewhere. A URL elsewhere asks you to confirm before it runs.", badge("", "info", "Your program")) : ""}
        ${opt("#/agents/new/upload", "upload", local ? "Add a Python file" : "Inspect a Python agent", local ? "Bring a .py file with your agent class. It runs in its own process, only after you confirm." : "Bring a .py file with your agent class. The lab checks it against the agent interface. It never runs here.", local ? badge("warn", "alert", "Asks before it runs") : badge("", "lock", "Inspected, not executed"))}
      </div>
      ${local ? "" : `<div style="margin-top:18px">${notice("info", "lock", `<b>Prompt agents, your API keys, HTTP agents and running Python files work on your own computer.</b> This public demo has none of them. <a href="#/run-locally">Download and run it yourself</a>; it's free.`)}</div>`}`;
  }

  // Sliders for 0–1 shares; numbers for everything else.
  function paramField(p, value, ro) {
    const v = value ?? p.default, dis = ro ? " disabled" : "";
    if (p.name === "connection") return `<label class="field"><span>${esc(p.label)}</span><select data-param="connection" data-field="params.connection"${dis}>${(state.conns ?? []).map((c) => `<option value="${esc(c.id)}"${c.id === v ? " selected" : ""}>${esc(c.name)}${c.needs_key ? (c.key ? " (key added)" : " (needs a key)") : " (free, local)"}</option>`).join("")}</select><small class="note">${esc(p.help)} <a href="#/models">Manage connections</a></small></label>`;
    if (p.name === "model" && state.agentDraft?.kind === "prompt") return `<label class="field"><span>${esc(p.label)}</span><input type="text" list="model-options" spellcheck="false" value="${esc(v)}" data-param="model" data-field="params.model"${dis}><datalist id="model-options">${(state.modelList ?? []).map((m) => `<option value="${esc(m)}">`).join("")}</datalist><small class="note">${esc(p.help)}</small></label>`;
    if (p.type === "text" && p.max_len > 200) return `<label class="field wide"><span>${esc(p.label)}</span><textarea rows="5" maxlength="${p.max_len}" data-param="${p.name}" data-field="params.${p.name}"${dis} placeholder="e.g. You are patient and want a deal both sides can live with.">${esc(v)}</textarea><small class="note">${esc(p.help)}</small></label>`;
    if (p.type === "choice") return `<label class="field"><span>${esc(p.label)}</span><select data-param="${p.name}" data-field="params.${p.name}"${dis}>${p.choices.map((c) => `<option${c === v ? " selected" : ""}>${esc(c)}</option>`).join("")}</select><small class="note">${esc(p.help)}</small></label>`;
    if (p.type === "number" && p.max <= 1) return `<label class="field slider"><span>${esc(p.label)} <output data-out="${p.name}">${Math.round(v * 100)}%</output></span>
      <input type="range" min="${p.min}" max="${p.max}" step="0.01" value="${v}" data-param="${p.name}" data-field="params.${p.name}"${dis}><small class="note">${esc(p.help)}</small></label>`;
    return `<label class="field"><span>${esc(p.label)}</span><input type="${p.type === "number" ? "number" : "text"}" ${p.type === "number" ? `step="${p.optional ? "any" : "1"}" min="${p.min}" max="${p.max}"` : 'spellcheck="false"'} value="${esc(v ?? "")}" data-param="${p.name}" data-field="params.${p.name}"${dis}><small class="note">${esc(p.help)}</small></label>`;
  }

  function agentNew(path) {
    const [mode, query = ""] = path.split("?");
    if (mode === "upload") return uploadPage();
    state.modelList = mode === "prompt" ? state.modelList : [];
    const provider = mode === "provider", prompt = mode === "prompt", httpMode = mode === "http";
    const kinds = state.catalog.kinds.filter((k) => (provider ? k.category === "provider" : prompt ? k.id === "prompt" && k.executable
      : httpMode ? k.category === "http" && k.executable : k.executable && ["mock", "python", "http"].includes(k.category)));
    if (!kinds.length) return `${top("Add an agent", "", "", backLink("#/agents/new", "Add an agent"))}${notice("info", "lock", `<b>This works on your own computer.</b> This public demo runs rule-based agents only. <a href="#/run-locally">Download and run it yourself</a>.`)}`;
    const want = new URLSearchParams(query).get("kind");
    if (!state.agentDraft || state.agentDraft.for !== mode) state.agentDraft = { for: mode, name: "", notes: "", params: {}, kind: kinds.find((k) => k.id === want)?.id ?? kinds[0].id };
    const d = state.agentDraft, kind = kindOf(d.kind);
    const kindPick = prompt ? "" : `<fieldset class="choice cards3"><legend>${provider ? "Provider" : httpMode ? "Where it runs" : "Strategy"}</legend>${kinds.map((k) => `<label class="opt"><input type="radio" name="kind" value="${k.id}"${k.id === d.kind ? " checked" : ""}>
      <span><b>${esc(provider ? `${PROVIDERS[k.id]} · ${k.label.split(" (")[0]}` : k.label)}</b><small>${esc(runsOf(k.id, k.category))}</small></span></label>`).join("")}</fieldset>`;
    const honesty = provider ? `${notice("warn", "lock", `<b>This agent will be saved but cannot run.</b> ${esc(kind.reason)}`)}
      <details class="more"><summary>Why can't I sign in with my ChatGPT, Claude or Gemini account?</summary><ul class="plainlist">
        <li>A consumer subscription (ChatGPT Plus, Claude Pro, Gemini Advanced) does not include API access. Each provider bills API use separately, per request.</li>
        <li>API keys are never typed into this page. The server reads them from its own <code>.env</code> file, and they stay unused while zero-cost mode is on.</li>
        <li>${kind.id === "gemini" ? "There is no Gemini adapter in this project yet; saving records a configuration only." : "The adapter exists (it is how the research study is configured), but the server refuses every paid call before anything is sent."}</li></ul></details>` : "";
    const test = provider ? `<div class="testline"><button type="button" class="btn" data-test>${icon("bolt")}Test connection</button><span id="test-out" role="status">${state.test ? esc(state.test) : ""}</span></div>` : "";
    const promptTest = prompt ? `<div class="testline"><button type="button" class="btn" data-find>${icon("list")}Find models</button><button type="button" class="btn" data-ptest>${icon("bolt")}Test connection</button><span id="test-out" role="status">${esc(state.test ?? "")}</span></div><small class="note">Nothing is sent until you click. With an API key, a test is one tiny request to your provider.</small>` : "";
    const [title, sub] = provider ? ["Research study configuration", "The study's provider setups. Nothing is sent to any provider. To use a model yourself, write a prompt agent instead."]
      : prompt ? ["Write a prompt agent", "Choose a model and write its instructions. It gets the rules, its own values and the transcript, and never sees the other agent's values."]
      : httpMode ? ["Connect your HTTP agent", "Your own service, answering each turn over HTTP (see <code>lab/example_http_agent.py</code>)."]
      : ["Configure a built-in strategy", "Choose a strategy, name it, and set how it bargains."];
    return `${top(title, sub, "", backLink("#/agents/new", "Add an agent"))}
      <form class="sheet" id="agent-form" novalidate>
        ${kindPick}
        <p class="note">${esc(kind.summary)}</p>
        ${honesty}
        <label class="field"><span>Name</span><input type="text" maxlength="60" value="${esc(d.name)}" data-k="name" data-field="name" placeholder="${esc(provider ? `${kind.label.split(" (")[0]} negotiator` : "e.g. Patient trader")}" required></label>
        ${kind.id === "http_remote" ? notice("warn", "alert", "Each turn sends this agent's view (the pool, its own values, the transcript) to that server. You'll be asked to confirm before an experiment runs.") : ""}
        <div class="wb-params">${kind.params.map((p) => paramField(p, d.params[p.name], false)).join("")}</div>
        <label class="field"><span>Role and notes <span class="sub">(optional)</span></span><textarea rows="3" maxlength="500" data-k="notes" data-field="notes" placeholder="What this agent stands for in your experiment">${esc(d.notes)}</textarea>
          <small class="note">${prompt ? "Notes are for you; they are never sent to the model." : kind.reads_instructions ? "In an experiment this agent receives the frozen instructions of the chosen condition. Notes are for you." : "This agent does not read instructions; notes are for you."}</small></label>
        ${test}${promptTest}${errorLine()}
        <div class="actions"><button type="submit" class="cta">${icon("check")}Save agent</button><a class="btn" href="#/agents">Cancel</a></div>
      </form>`;
  }

  /** Local LLM setup: says plainly what is missing, then offers only the models actually installed. */
  async function llmPage() {
    const demo = state.catalog.demo, kind = kindOf("local_llm");
    const found = await getJSON("/api/workbench/local-llm/models").catch(() => ({ running: false, models: [] }));
    const head = top("Connect a local LLM", "Run an open model on your own computer, then test how it negotiates. Free, and nothing leaves your machine.", "", backLink("#/agents/new", "Add an agent"));
    const step = (title, body) => `<li><b>${title}</b>${body}</li>`, cmd = (c) => `<code class="cmd">${esc(c)}</code>`;
    const install = step("Install Ollama", '<span class="sub">Free, from <a href="https://ollama.com/download" target="_blank" rel="noopener">ollama.com/download</a>. Then open it.</span>');
    const pull = step("Download a model", `<span class="sub">About 2 GB. This one suits most laptops.</span>${cmd("ollama pull llama3.2")}`);
    if (demo) return `${head}${notice("info", "info", "<b>Local LLMs need the lab running on your computer.</b> This public demo runs on a small free server that cannot run a model, so it uses rule-based agents.")}
      <ol class="setup">${step("Get the lab", `${cmd("git clone https://github.com/Jenoyrex/multi-agent-cooperation")}${cmd("cd multi-agent-cooperation")}${cmd('pip install "pydantic>=2.6" "python-dotenv>=1.0"')}`)}${install}${pull}${step("Start the lab", `<span class="sub">Then open <code>127.0.0.1:8765</code> and come back to this page.</span>${cmd("python -m dashboard.server")}`)}</ol>`;
    if (!found.models.length) return `${head}${notice("warn", "alert", found.running ? "<b>Ollama is running, but no models are installed yet.</b>" : "<b>Ollama isn't running on this computer.</b>")}
      <ol class="setup">${found.running ? "" : install}${pull}</ol><button type="button" class="cta" id="llm-recheck">Check again</button>`;
    if (state.agentDraft?.for !== "llm") state.agentDraft = { for: "llm", name: found.models[0], notes: "", params: { model: found.models[0] }, kind: kind.id };
    const d = state.agentDraft, [model, ...advanced] = kind.params;
    return `${head}<form class="sheet" id="agent-form" novalidate>
        ${paramField({ ...model, type: "choice", choices: found.models }, d.params.model, false)}
        <label class="field"><span>Name</span><input type="text" maxlength="60" value="${esc(d.name)}" data-k="name" data-field="name" required></label>
        <details class="advanced"><summary>Advanced settings</summary>${advanced.map((p) => paramField(p, d.params[p.name], false)).join("")}</details>
        ${errorLine()}
        <div class="actions"><button type="submit" class="cta">${icon("check")}Save agent</button><a class="btn" href="#/agents">Cancel</a></div>
      </form>`;
  }

  function uploadPage() {
    const u = state.upload, r = u?.report, lim = state.catalog.upload?.max_bytes ?? 48000;
    const verdict = !r ? "" : r.status === "compatible" ? badge("good", "check", "Compatible") : r.status === "needs-changes" ? badge("warn", "alert", "Needs changes") : badge("crit", "x", "Not compatible");
    const issues = r?.issues.length ? `<ul class="issues">${r.issues.map((i) => `<li class="${i.level}">${icon(i.level === "error" ? "x" : i.level === "warning" ? "alert" : "info")}<span>${esc(i.text)}${i.line ? ` <span class="sub">line ${i.line}</span>` : ""}</span></li>`).join("")}</ul>` : "";
    const report = !r ? "" : `<section class="sheet report-card" aria-live="polite">
        <div class="rc-head"><div><h2>${esc(r.filename)}</h2><p class="sub">${r.lines} lines · ${(r.bytes / 1000).toFixed(1)} KB · SHA-256 <code>${esc(r.sha256.slice(0, 12))}</code> · parsed, not executed</p></div>${verdict}</div>
        ${r.classes.length ? `<div class="table-wrap"><table><thead><tr><th>Class</th><th>Interface</th><th>Actions it mentions</th><th>Description</th></tr></thead><tbody>
          ${r.classes.map((c) => `<tr><td><code>${esc(c.name)}</code> <span class="sub">line ${c.line}</span></td><td>${c.compatible ? badge("good", "check", "Matches") : `<span class="sub">${esc(c.problems.join("; "))}</span>`}</td>
            <td>${c.actions.length ? c.actions.map((a) => esc(ACT[a])).join(", ") : '<span class="sub">none</span>'}</td><td class="wb-clip">${esc(c.doc || "—")}</td></tr>`).join("")}</tbody></table></div>` : ""}
        ${issues}
        ${r.compatible_classes.length ? `<form id="upload-form" class="upload-save" novalidate>
          <div class="wb-grid2"><label class="field"><span>Agent name</span><input type="text" maxlength="60" data-u="name" data-field="name" value="${esc(u.name)}" required></label>
          <label class="field"><span>Agent class</span><select data-u="class_name" data-field="params.class_name">${r.compatible_classes.map((c) => `<option${c === u.class_name ? " selected" : ""}>${esc(c)}</option>`).join("")}</select></label></div>
          <label class="field"><span>Role and notes <span class="sub">(optional)</span></span><textarea rows="2" maxlength="500" data-u="notes" data-field="notes">${esc(u.notes)}</textarea></label>
          ${state.catalog.local_features ? notice("warn", "alert", "<b>This file runs on your computer, only after you confirm.</b> It runs in its own process with no API keys and a time limit per turn, but with your permissions: it could read your files. Only use files you trust.")
            : notice("info", "lock", `<b>Inspected here, never executed.</b> This public demo has no way to run uploaded code safely. <a href="#/run-locally">Run the app on your computer</a> to run your own file.`)}
          ${errorLine()}<div class="actions"><button type="submit" class="cta">${icon("check")}Save agent</button><button type="button" class="btn" data-clear>Choose another file</button></div></form>`
          : `${errorLine()}<div class="actions"><button type="button" class="btn" data-clear>Choose another file</button></div>`}
      </section>`;
    return `${top(state.catalog.local_features ? "Add a Python file" : "Inspect a Python agent", state.catalog.local_features ? "Bring a .py file containing your agent class. The lab first reads the file to check it against the agent interface, without running it." : "Bring a .py file containing your agent class. The lab reads the file to check it against the agent interface. It never runs it.", "", backLink("#/agents/new", "Add an agent"))}
      ${state.catalog.demo ? notice("warn", "alert", "<b>Public demo.</b> A saved agent, including its source code, is visible to every visitor and erased when the server restarts. Checking a file without saving stores nothing.") : ""}
      ${r ? "" : `<label class="dropzone" id="dropzone"><input type="file" accept=".py,text/x-python" data-file class="vh">${icon("upload")}<b>Choose a Python file</b><span>or drop it here · up to ${Math.round(lim / 1000)} KB</span></label>${errorLine()}`}
      ${report}
      <section class="block"><div class="block-head"><h2>What the file needs</h2><a class="btn" href="templates/my_custom_agent.py" download>${icon("download")}Download a starter template</a></div>
        <div class="explain">
          <p>One class with an <code>async def generate_response(self, view)</code> method. Each turn it reads <code>view</code> and returns a <code>NegotiationAction</code>:</p>
          <dl class="kvs"><dt>OFFER</dt><dd>propose how every unit of every resource is split between the two seats</dd><dt>ACCEPT</dt><dd>take the other agent's standing offer</dd><dt>WALK_AWAY</dt><dd>end the negotiation with no agreement</dd></dl>
          <p class="note"><code>view</code> holds only what your agent may know: the resource pool, its own private values per unit, the public transcript, the turn number and the deadline. The other agent's values stay hidden. Imports that reach the network, files, processes or AI providers are flagged.</p>
        </div></section>`;
  }

  async function agentDetail(id) {
    let a;
    try { a = await getJSON(`/api/workbench/agents/${encodeURIComponent(id)}`); } catch { return `${top("Agent not found", "No agent has this id.", "", backLink("#/agents", "Agents"))}`; }
    const kind = kindOf(a.kind), editable = !a.builtin && kind.executable && !state.catalog.demo && a.kind !== "python_file"; // the public demo never edits saved entries; a file is replaced, not edited
    if (editable && (!state.agentDraft || state.agentDraft.for !== id)) state.agentDraft = { for: id, name: a.name, kind: a.kind, notes: a.notes, params: { ...a.params } };
    const d = editable ? state.agentDraft : a;
    const actions = `<button type="button" class="btn" data-dup="${a.id}">${icon("copy")}Duplicate</button>${a.builtin || state.catalog.demo ? "" : `<button type="button" class="btn" data-del="${a.id}">${icon("trash")}Delete</button>`}`;
    const up = a.upload;
    const body = editable ? `<form class="sheet" id="agent-form" novalidate>
        <label class="field"><span>Name</span><input type="text" maxlength="60" value="${esc(d.name)}" data-k="name" data-field="name" required></label>
        <div class="wb-params">${kind.params.map((p) => paramField(p, d.params[p.name], false)).join("")}</div>
        <label class="field"><span>Role and notes</span><textarea rows="3" maxlength="500" data-k="notes" data-field="notes">${esc(d.notes)}</textarea></label>
        ${errorLine()}<div class="actions"><button type="submit" class="cta">${icon("check")}Save changes</button></div></form>`
      : `<div class="sheet">${a.builtin && kind.executable ? notice("info", "info", "A built-in agent. Duplicate it to change its settings.") : !a.builtin && kind.executable && state.catalog.demo ? notice("info", "info", "In the public demo, saved agents are shared and cannot be changed. Duplicate it to make your own version.") : ""}${kind.executable ? "" : notice(kind.category === "upload" ? "info" : "warn", "lock", kind.category === "upload" ? esc(kind.reason) : `<b>Cannot run here.</b> ${esc(kind.reason)}`)}
        <dl class="kvs">${kind.params.map((p) => `<dt>${esc(p.label)}</dt><dd>${p.type === "number" && p.max <= 1 ? pct(a.params[p.name]) : esc(a.params[p.name])}</dd>`).join("")}${a.notes ? `<dt>Role and notes</dt><dd>${esc(a.notes)}</dd>` : ""}</dl>
        ${up ? `<h3 class="wb-h3" style="margin-top:18px">Uploaded file</h3><dl class="kvs"><dt>File</dt><dd><code>${esc(up.filename)}</code>, ${up.lines} lines</dd><dt>SHA-256</dt><dd><code>${esc(up.sha256)}</code></dd>
          <dt>Inspection</dt><dd>${up.status === "compatible" ? badge("good", "check", "Compatible") : badge("warn", "alert", "Needs changes")} ${up.issues.length ? `<span class="sub">${up.issues.length} note${up.issues.length === 1 ? "" : "s"}</span>` : ""}</dd></dl>
          ${up.issues.length ? `<ul class="issues">${up.issues.map((i) => `<li class="${i.level}">${icon(i.level === "error" ? "x" : i.level === "warning" ? "alert" : "info")}<span>${esc(i.text)}${i.line ? ` <span class="sub">line ${i.line}</span>` : ""}</span></li>`).join("")}</ul>` : ""}
          <details class="more"><summary>View source</summary><pre class="prompt">${esc(up.source)}</pre></details>` : ""}${errorLine()}</div>`;
    return `${top(a.name, `${esc(a.kind_label)} · ${esc(runsOf(a.kind, a.category))}`, actions, backLink("#/agents", "Agents"))}
      <div class="wb-split">${body}
        <aside class="sheet wb-aside"><h2 class="wb-h3">Capabilities</h2><dl class="kvs">
          <dt>Kind</dt><dd>${esc(CAT[kind.category].label)}</dd><dt>Status</dt><dd>${runBadge({ ...a, category: kind.category })}</dd><dt>Moves</dt><dd>Offer · Accept · Walk away</dd>
          <dt>Repeatable</dt><dd>${kind.deterministic ? "Yes: same situation, same move" : "Not guaranteed"}</dd><dt>Reads instructions</dt><dd>${a.kind === "prompt" ? "Yes: the condition's and yours" : kind.reads_instructions ? "Yes, the condition's frozen ones" : "No"}</dd>
          <dt>Cost</dt><dd>${kind.cost === "free" ? "Free" : kind.cost === "paid" ? "Paid provider, never called" : esc(kind.cost)}</dd></dl>
          <h2 class="wb-h3">Experiments</h2>${a.history.length ? `<ul class="plainlist">${a.history.map((s) => `<li><a href="#/simulation/${s.id}">${esc(s.name)}</a> <span class="sub">${when(s.created)}</span></li>`).join("")}</ul>` : '<p class="note">Not used yet.</p>'}
        </aside></div>`;
  }

  function wireAgents(arg) {
    main.querySelectorAll("[data-dup]").forEach((b) => b.addEventListener("click", async () => {
      const a = await getJSON(`/api/workbench/agents/${b.dataset.dup}`);
      const res = await post("/api/workbench/agents", { name: `${a.name} (copy)`.slice(0, 60), kind: a.kind, params: a.params, notes: a.notes, ...(a.upload ? { upload: { filename: a.upload.filename, source: a.upload.source } } : {}) });
      if (res.ok) { state.agentDraft = null; location.hash = `#/agents/${res.data.id}`; } else showError(res, "Could not duplicate the agent.");
    }));
    main.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.innerHTML = `${icon("trash")}Confirm delete`; b.classList.add("danger"); return; } // two-step, no modal
      const res = await post(`/api/workbench/agents/${b.dataset.del}/delete`, {});
      if (res.ok) { state.agentDraft = null; location.hash = "#/agents"; } else showError(res, "Could not delete the agent.");
    }));
    if (arg === "new/upload") return wireUpload();
    main.querySelector("#llm-recheck")?.addEventListener("click", () => render(false));
    const form = main.querySelector("#agent-form");
    if (!form) return;
    const d = state.agentDraft;
    form.querySelectorAll('input[name="kind"]').forEach((el) => el.addEventListener("change", () => { d.kind = el.value; d.params = {}; state.test = null; render(false); }));
    form.querySelectorAll("[data-k]").forEach((el) => el.addEventListener("input", () => { d[el.dataset.k] = el.value; }));
    form.querySelectorAll("[data-param]").forEach((el) => el.addEventListener("input", () => {
      d.params[el.dataset.param] = el.type === "range" || el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value;
      const label = form.querySelector(`[data-out="${el.dataset.param}"]`);
      if (label) label.textContent = `${Math.round(Number(el.value) * 100)}%`;
    }));
    if (arg === "new/llm") { // the name follows the model until the user types their own
      const model = form.querySelector('[data-param="model"]'), name = form.querySelector('[data-k="name"]');
      let was = model.value;
      model.addEventListener("input", () => { if (name.value === was) name.value = d.name = model.value; was = model.value; });
    }
    const out = (r) => { state.test = r.ok ? (r.title ? `${r.title}${r.latency_s != null ? ` in ${r.latency_s} s` : ""}.` : `${r.models?.length ?? 0} models found.`) : `${String(r.title ?? r.error ?? "That did not work").replace(/\.$/, "")}. ${r.hint ?? ""}`; form.querySelector("#test-out").textContent = state.test; };
    form.querySelector("[data-find]")?.addEventListener("click", async () => {
      const res = await post("/api/models/list", { connection: d.params.connection ?? "ollama" });
      out(res.data); state.modelList = res.data.models ?? [];
      form.querySelector("#model-options").innerHTML = state.modelList.map((m) => `<option value="${esc(m)}">`).join("");
    });
    form.querySelector("[data-ptest]")?.addEventListener("click", async () => {
      if (!d.params.model?.trim()) return out({ ok: false, title: "Choose a model first", hint: "Use Find models, or type its name." });
      out((await post("/api/models/test", { connection: d.params.connection ?? "ollama", model: d.params.model })).data);
    });
    form.querySelector("[data-test]")?.addEventListener("click", () => {
      // No request is made: the zero-cost guard refuses provider calls server-side, so a test could only be refused.
      state.test = `Not sent. ${state.catalog.zero_cost ? "Zero-cost mode is on, so this server refuses every paid provider call before anything leaves the lab's server." : "Provider calls are not made from the Workbench."} No credentials were read or used.`;
      form.querySelector("#test-out").textContent = state.test;
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const editing = !arg.startsWith("new");
      const body = { name: d.name, kind: d.kind, notes: d.notes, params: d.params, ...(editing ? { id: arg } : {}) };
      const res = await post("/api/workbench/agents", body);
      if (res.ok) { state.agentDraft = null; state.test = null; location.hash = `#/agents/${res.data.id}`; if (editing) render(false); } else showError(res, "The agent could not be saved.");
    });
  }

  function wireUpload() {
    const take = async (file) => {
      if (!file) return;
      const lim = state.catalog.upload?.max_bytes ?? 48000;
      if (!/\.py$/i.test(file.name)) return showError({ data: { error: "Choose a Python file: its name must end in .py." } });
      if (file.size > lim) return showError({ data: { error: `That file is ${Math.ceil(file.size / 1000)} KB; the limit is ${Math.round(lim / 1000)} KB.` } });
      const source = await file.text();
      const res = await post("/api/workbench/uploads/inspect", { filename: file.name, source });
      if (!res.ok) return showError(res, "The file could not be inspected.");
      const stem = file.name.replace(/\.py$/i, "").replace(/[_-]+/g, " ").trim();
      state.upload = { filename: file.name, source, report: res.data, name: stem.charAt(0).toUpperCase() + stem.slice(1), class_name: res.data.compatible_classes[0], notes: "" };
      render(false);
    };
    const zone = main.querySelector("#dropzone");
    if (zone) {
      zone.querySelector("[data-file]").addEventListener("change", (e) => take(e.target.files[0]));
      zone.addEventListener("dragover", (e) => { e.preventDefault(); zone.classList.add("over"); });
      zone.addEventListener("dragleave", () => zone.classList.remove("over"));
      zone.addEventListener("drop", (e) => { e.preventDefault(); zone.classList.remove("over"); take(e.dataTransfer.files[0]); });
    }
    main.querySelector("[data-clear]")?.addEventListener("click", () => { state.upload = null; render(false); });
    const form = main.querySelector("#upload-form");
    if (!form) return;
    const u = state.upload;
    form.querySelectorAll("[data-u]").forEach((el) => el.addEventListener("input", () => { u[el.dataset.u] = el.value; }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const res = await post("/api/workbench/agents", { name: u.name, kind: state.catalog.local_features ? "python_file" : "python_upload", notes: u.notes, params: { class_name: u.class_name }, upload: { filename: u.filename, source: u.source } });
      if (res.ok) { state.upload = null; location.hash = `#/agents/${res.data.id}`; } else showError(res, "The agent could not be saved.");
    });
  }

  // ------------------------------------------------------------ resources (environments)
  async function environments(_s, arg) {
    try { await catalog(); } catch (e) { return unavailable(e.message); }
    if (arg) return envEditor(arg);
    const list = await getJSON("/api/workbench/environments");
    const chips = (e) => (e.source === "generated" ? `<span class="rchip">${e.categories} resources drawn per seed</span>`
      : Object.entries(e.resources).map(([c, q]) => `<span class="rchip"><b>${q}</b> ${esc(e.meta?.[c]?.unit ?? "")} ${esc(c)}</span>`).join(""));
    return `${top("Resources", "What your agents divide: the resources, how many units exist, and how much each agent privately values them. Templates are read-only; duplicate one or build your own.", `<a class="cta" href="#/environments/new">${icon("plus")}New resource set</a>`)}
      <ul class="ilist">${list.map((e) => `<li><a class="irow" href="#/environments/${e.id}">${avatar(e.name, "var(--accent)")}<span class="imain"><b>${esc(e.name)}</b>
        <span class="chips">${chips(e)}</span><span class="sub">${e.max_rounds} turns${e.builtin ? " · template" : ""}${e.history.length ? ` · used in ${e.history.length} experiment${e.history.length === 1 ? "" : "s"}` : ""}</span></span>${icon("chev-r")}</a></li>`).join("")}</ul>
      <p class="note" style="margin-top:14px">Your resource sets are separate from the research study. The study's environment, seeds and preferences are frozen and never change here.</p>${errorLine()}`;
  }

  const blankEnv = () => ({ name: "My resource set", description: "", source: "custom", categories: 3, max_rounds: 10, first_mover: "A",
    rows: [{ name: "materials", qty: 100, unit: "units", group: "", a: 50, b: 20 }, { name: "compute", qty: 50, unit: "hours", group: "", a: 30, b: 30 }, { name: "energy", qty: 20, unit: "MWh", group: "", a: 20, b: 50 }] });
  const envToDraft = (e) => ({ name: e.name, description: e.description || "", source: e.source, categories: e.categories ?? 3, max_rounds: e.max_rounds, first_mover: e.first_mover,
    rows: e.source === "custom" ? Object.entries(e.resources).map(([c, q]) => ({ name: c, qty: q, unit: e.meta?.[c]?.unit ?? "", group: e.meta?.[c]?.group ?? "", a: e.points.A[c], b: e.points.B[c] })) : blankEnv().rows });
  const draftToEnv = (d) => d.source === "generated"
    ? { name: d.name, description: d.description, source: "generated", categories: d.categories, max_rounds: d.max_rounds, first_mover: d.first_mover }
    : { name: d.name, description: d.description, source: "custom", max_rounds: d.max_rounds, first_mover: d.first_mover,
        resources: Object.fromEntries(d.rows.map((r) => [r.name, r.qty])),
        points: { A: Object.fromEntries(d.rows.map((r) => [r.name, r.a])), B: Object.fromEntries(d.rows.map((r) => [r.name, r.b])) },
        meta: Object.fromEntries(d.rows.map((r) => [r.name, { unit: r.unit, group: r.group }])) };

  // Who values what: A's points to the left, B's to the right of a shared axis.
  const butterfly = (rows) => `<div class="fly" aria-label="Private points per resource">${rows.map((r) => `<div class="fly-row"><span class="fa"><i style="--w:${(Number(r.a) || 0) / 100}"></i><b>${Number(r.a) || 0}</b></span>
    <span class="fn">${esc(r.name)}</span><span class="fb"><i style="--w:${(Number(r.b) || 0) / 100}"></i><b>${Number(r.b) || 0}</b></span></div>`).join("")}
    <div class="fly-key"><span><i class="sw" style="--c:var(--base)"></i>Agent A's points</span><span>Agent B's points<i class="sw" style="--c:var(--struct);margin:0 0 0 7px"></i></span></div></div>`;

  async function envEditor(arg) {
    let saved = null;
    if (arg !== "new") { try { saved = await getJSON(`/api/workbench/environments/${encodeURIComponent(arg)}`); } catch { return top("Resource set not found", "No resource set has this id.", "", backLink("#/environments", "Resources")); } }
    const env = saved?.environment;
    if (!state.envDraft || state.envDraft.for !== arg) state.envDraft = { for: arg, ...(env ? envToDraft(env) : blankEnv()) };
    const d = state.envDraft, ro = Boolean(env?.builtin) || (Boolean(env) && state.catalog.demo), lim = state.catalog.limits, dis = ro ? " disabled" : "";
    const sum = (s) => d.rows.reduce((t, r) => t + (Number(r[s]) || 0), 0);
    const total = (s) => `<span class="wb-sum ${sum(s) === 100 ? "ok" : ""}" id="sum-${s}">${icon(sum(s) === 100 ? "check" : "alert")}${sum(s)} / 100</span>`;
    const resCard = (r, i) => `<div class="rescard" data-row="${i}">
        <div class="rc-top"><label class="field grow"><span>Resource</span><input type="text" value="${esc(r.name)}" data-r="${i}" data-c="name" data-field="resources.${esc(r.name)}" aria-label="Resource ${i + 1} name"${dis}></label>
          <label class="field qty"><span>Quantity</span><input type="number" min="1" max="${lim.quantity}" step="1" value="${r.qty}" data-r="${i}" data-c="qty"${dis}></label>
          <label class="field unit"><span>Unit</span><input type="text" maxlength="24" value="${esc(r.unit)}" data-r="${i}" data-c="unit" placeholder="units" data-field="meta.${esc(r.name)}.unit"${dis}></label>
          <label class="field unit"><span>Category</span><input type="text" maxlength="24" value="${esc(r.group)}" data-r="${i}" data-c="group" placeholder="optional" data-field="meta.${esc(r.name)}.group"${dis}></label>
          ${ro || d.rows.length <= lim.resources[0] ? "" : `<button type="button" class="btn-x" data-rm="${i}" aria-label="Remove ${esc(r.name)}">${icon("trash")}</button>`}</div>
        <div class="rc-pts"><label class="field"><span>Agent A values it at</span><span class="numrange"><input type="range" min="0" max="100" step="1" value="${r.a}" data-r="${i}" data-c="a" aria-label="${esc(r.name)}: Agent A points"${dis}><input type="number" min="0" max="100" step="1" value="${r.a}" data-r="${i}" data-c="a" data-field="points.A.${esc(r.name)}" aria-label="${esc(r.name)}: Agent A points, exact"${dis}></span></label>
          <label class="field"><span>Agent B values it at</span><span class="numrange"><input type="range" min="0" max="100" step="1" value="${r.b}" data-r="${i}" data-c="b" aria-label="${esc(r.name)}: Agent B points"${dis}><input type="number" min="0" max="100" step="1" value="${r.b}" data-r="${i}" data-c="b" data-field="points.B.${esc(r.name)}" aria-label="${esc(r.name)}: Agent B points, exact"${dis}></span></label></div></div>`;
    const visual = `<div class="wb-grid2">
        <label class="field"><span>Name</span><input type="text" maxlength="60" value="${esc(d.name)}" data-e="name" data-field="name" placeholder="e.g. Supply chain"${dis}></label>
        <label class="field"><span>Turn limit</span><input type="number" min="${lim.rounds[0]}" max="${lim.rounds[1]}" step="1" value="${d.max_rounds}" data-e="max_rounds" data-field="max_rounds"${dis}><small class="note">The negotiation ends without agreement after this many turns.</small></label></div>
      <label class="field"><span>Description <span class="sub">(optional)</span></span><textarea rows="2" maxlength="500" data-e="description" data-field="description"${dis}>${esc(d.description)}</textarea></label>
      <div class="wb-grid2"><fieldset class="choice"><legend>Resources come from</legend>
          <label class="opt"><input type="radio" name="src" value="custom"${d.source === "custom" ? " checked" : ""}${dis}><span><b>Values you set</b><small>Fixed quantities and both agents' private values.</small></span></label>
          <label class="opt"><input type="radio" name="src" value="generated"${d.source === "generated" ? " checked" : ""}${dis}><span><b>The research generator</b><small>A new random instance for every seed, as in the study.</small></span></label></fieldset>
        <fieldset class="choice"><legend>Who opens</legend>${["A", "B"].map((s) => `<label class="opt"><input type="radio" name="first" value="${s}"${d.first_mover === s ? " checked" : ""}${dis}><span><b>Agent ${s} makes the first offer</b></span></label>`).join("")}
          <small class="note">An experiment can also swap this.</small></fieldset></div>
      ${d.source === "generated" ? `<label class="field"><span>Number of resources</span><input type="number" min="${lim.generated_resources[0]}" max="${lim.generated_resources[1]}" step="1" value="${d.categories}" data-e="categories" data-field="categories"${dis}></label>`
        : `<h3 class="wb-h3" style="margin-top:6px">Resources and private values</h3>
          <p class="note">Each agent spreads exactly 100 points over the resources: how much it cares about each. Neither agent sees the other's points.</p>
          <div class="rescards">${d.rows.map(resCard).join("")}</div>
          <div class="resfoot">${ro || d.rows.length >= lim.resources[1] ? "" : `<button type="button" class="btn" data-add>${icon("plus")}Add resource</button>`}
            <span class="sumline">Agent A ${total("a")}${ro ? "" : ' <button type="button" class="btn-link" data-norm="a">Balance to 100</button>'}</span>
            <span class="sumline">Agent B ${total("b")}${ro ? "" : ' <button type="button" class="btn-link" data-norm="b">Balance to 100</button>'}</span></div>
          <div id="fly">${butterfly(d.rows)}</div>`}`;
    const importer = `<div class="seg" role="group" aria-label="Import format">${["text", "json"].map((f) => `<button type="button" data-fmt="${f}" aria-pressed="${state.importFmt === f}">${f === "text" ? "Text" : "JSON"}</button>`).join("")}</div>
      <label class="field"><span>Paste a resource set</span><textarea id="import-text" rows="10" spellcheck="false" class="wb-code" data-field="format">${esc(state.importText || (state.importFmt === "text"
        ? "name: Field hospital\nrounds: 10\nfirst: A\nresources: food=20, water=12, medicine=6\nA: food=50, water=30, medicine=20\nB: food=10, water=30, medicine=60\n"
        : JSON.stringify(draftToEnv(d), null, 2)))}</textarea></label>
      <div class="actions" style="margin-top:0"><button type="button" class="btn" data-validate>Check and preview</button><button type="button" class="btn" data-load disabled>Use these values</button><label class="btn wb-file">${icon("upload")}Open a file…<input type="file" accept=".json,.txt,text/plain,application/json" data-file hidden></label></div>`;
    const actions = env ? `${!ro ? "" : `<button type="button" class="btn" data-envdup="${env.id}">${icon("copy")}Duplicate to edit</button>`}<a class="cta" href="#/simulations/new" data-use="${env.id}">${icon("play")}Use in an experiment</a>` : "";
    return `${top(env ? env.name : "New resource set", ro ? (env?.builtin ? "A read-only template. Duplicate it to change anything." : "Shared in the public demo, so it cannot be changed. Duplicate it to make your own version.") : "Set up the resources and how much each agent values them. The preview checks everything with the server as you type.", actions, backLink("#/environments", "Resources"))}
      <div class="wb-split"><form class="sheet" id="env-form" novalidate>
        ${ro ? "" : `<div class="seg" role="tablist" aria-label="Editor mode">${[["visual", "Builder"], ["import", "Import"]].map(([k, t]) => `<button type="button" role="tab" data-tab="${k}" aria-pressed="${state.envTab === k}" aria-selected="${state.envTab === k}">${t}</button>`).join("")}</div>`}
        ${ro || state.envTab === "visual" ? visual : importer}
        ${errorLine()}
        ${ro ? "" : `<div class="actions"><button type="submit" class="cta">${icon("check")}${env ? "Save changes" : "Save resource set"}</button><a class="btn" href="#/environments">Cancel</a>${env ? `<button type="button" class="btn" data-envdel="${env.id}">${icon("trash")}Delete</button>` : ""}</div>`}
      </form>
      <aside class="sheet wb-aside" id="env-preview" aria-live="polite"><p class="note">Preview loading…</p></aside></div>`;
  }

  function previewHtml(p) {
    const inst = p.instance, env = p.environment, res = Object.keys(inst.pool), meta = env.meta ?? {};
    const maxQ = Math.max(...Object.values(inst.pool));
    return `<h2 class="wb-h3">Preview${p.seed_dependent ? ` <span class="sub">seed ${inst.seed}</span>` : ""}</h2>
      ${p.seed_dependent ? '<p class="note">Generated: every seed draws a different instance. This is seed 0.</p>' : ""}
      <div class="qbars">${res.map((c) => `<div class="qrow"><span>${esc(c)}</span><span class="qbar"><i style="--w:${inst.pool[c] / maxQ}"></i></span><b>${inst.pool[c]} <small>${esc(meta[c]?.unit ?? "")}</small></b></div>`).join("")}</div>
      <h3 class="wb-h4">Best possible split</h3>
      <p class="note">The allocation that maximises both agents' combined value (W*). Agents never see it.</p>
      <div class="splits">${res.map((c) => { const a = inst.optimal_allocation.A[c], q = inst.pool[c];
        return `<div class="srow"><span>${esc(c)}</span><span class="sbar" role="img" aria-label="${esc(c)}: A ${a}, B ${q - a}"><i class="a" style="--w:${a / q}"></i></span><span class="sub">A ${a} · B ${q - a}</span></div>`; }).join("")}</div>
      <dl class="kvs"><dt>Best combined value</dt><dd>${fmt(inst.optimal_welfare, 1)} of 200 points</dd><dt>Turns</dt><dd>${env.max_rounds}, Agent ${env.first_mover} opens</dd></dl>
      <details class="more"><summary>How a negotiation ends</summary><ul class="plainlist">${p.rules.termination.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></details>`;
  }

  function wireEnvironments(arg) {
    main.querySelectorAll("[data-envdup]").forEach((b) => b.addEventListener("click", async () => {
      const e = (await getJSON(`/api/workbench/environments/${b.dataset.envdup}`)).environment;
      const { id, builtin, created, updated, ...rest } = e;
      const res = await post("/api/workbench/environments", { ...rest, name: `${e.name} (copy)`.slice(0, 60) });
      if (res.ok) { state.envDraft = null; location.hash = `#/environments/${res.data.id}`; } else showError(res, "Could not duplicate the resource set.");
    }));
    main.querySelectorAll("[data-envdel]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.innerHTML = `${icon("trash")}Confirm delete`; b.classList.add("danger"); return; }
      const res = await post(`/api/workbench/environments/${b.dataset.envdel}/delete`, {});
      if (res.ok) { state.envDraft = null; location.hash = "#/environments"; } else showError(res, "Could not delete the resource set.");
    }));
    main.querySelector("[data-use]")?.addEventListener("click", (e) => { state.simDraft = { ...(state.simDraft ?? newSim()), environment: e.currentTarget.dataset.use }; });
    const form = main.querySelector("#env-form");
    if (!form) return;
    const d = state.envDraft, pane = main.querySelector("#env-preview");
    let timer = null, parsed = null;
    const refresh = async (body = { format: "form", content: draftToEnv(d) }) => {
      const res = await post("/api/workbench/environments/preview", body);
      if (res.ok) { pane.innerHTML = previewHtml(res.data); main.querySelector("#wb-error").hidden = true; main.querySelectorAll("[aria-invalid]").forEach((x) => x.removeAttribute("aria-invalid")); return res.data; }
      pane.innerHTML = `<h2 class="wb-h3">Preview</h2><p class="note">Fix the highlighted field to see the preview.</p>`;
      showError(res, "The resource set is not valid yet.", false); // never steal focus while typing
      return null;
    };
    const soon = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };
    onCleanup(() => clearTimeout(timer));
    const ro = !form.querySelector("button[type=submit]");
    if (ro || state.envTab === "visual") refresh();
    else pane.innerHTML = '<h2 class="wb-h3">Preview</h2><p class="note">Paste or open a resource set, then check it.</p>';
    form.querySelectorAll("[data-tab]").forEach((b) => b.addEventListener("click", () => { state.envTab = b.dataset.tab; render(false); }));
    form.querySelectorAll("[data-e]").forEach((el) => el.addEventListener("input", () => { d[el.dataset.e] = el.type === "number" ? Number(el.value) : el.value; soon(); }));
    form.querySelectorAll('input[name="src"]').forEach((el) => el.addEventListener("change", () => { d.source = el.value; render(false); }));
    form.querySelectorAll('input[name="first"]').forEach((el) => el.addEventListener("change", () => { d.first_mover = el.value; soon(); }));
    const sums = () => {
      for (const s of ["a", "b"]) {
        const t = d.rows.reduce((x, r) => x + (Number(r[s]) || 0), 0), out = main.querySelector(`#sum-${s}`);
        out.innerHTML = `${icon(t === 100 ? "check" : "alert")}${t} / 100`; out.classList.toggle("ok", t === 100);
      }
      main.querySelector("#fly").innerHTML = butterfly(d.rows);
    };
    form.querySelectorAll("[data-r]").forEach((el) => el.addEventListener("input", () => {
      const i = +el.dataset.r, c = el.dataset.c, r = d.rows[i];
      r[c] = c === "name" ? el.value.trim().toLowerCase() : ["unit", "group"].includes(c) ? el.value : Number(el.value);
      if (c === "a" || c === "b") form.querySelectorAll(`[data-r="${i}"][data-c="${c}"]`).forEach((x) => { if (x !== el) x.value = el.value; });
      sums(); soon();
    }));
    form.querySelectorAll("[data-norm]").forEach((b) => b.addEventListener("click", () => {
      const s = b.dataset.norm, v = normalize100(d.rows.map((r) => Number(r[s])));
      d.rows.forEach((r, i) => { r[s] = v[i]; });
      render(false);
    }));
    form.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { d.rows.splice(+b.dataset.rm, 1); render(false); }));
    form.querySelector("[data-add]")?.addEventListener("click", () => {
      const name = ["labour", "storage", "transport", "land", "water", "data"].find((n) => !d.rows.some((r) => r.name === n)) ?? "extra";
      d.rows.push({ name, qty: 10, unit: "units", group: "", a: 0, b: 0 }); render(false);
    });
    const ta = form.querySelector("#import-text");
    form.querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", () => { state.importFmt = b.dataset.fmt; state.importText = ""; render(false); }));
    ta?.addEventListener("input", () => { state.importText = ta.value; form.querySelector("[data-load]").disabled = true; });
    form.querySelector("[data-validate]")?.addEventListener("click", async () => { parsed = await refresh({ format: state.importFmt, content: ta.value }); form.querySelector("[data-load]").disabled = !parsed; });
    form.querySelector("[data-load]")?.addEventListener("click", () => { if (!parsed) return; Object.assign(d, envToDraft(parsed.environment)); state.envTab = "visual"; render(false); });
    form.querySelector("[data-file]")?.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      if (file.size > 12 * 1024) { showError({ data: { error: "That file is larger than 12 KB, the most the server accepts in one request; a resource set is a few lines." } }); return; }
      state.importText = await file.text();
      state.importFmt = /\.json$/i.test(file.name) ? "json" : "text";
      render(false);
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const res = await post("/api/workbench/environments", { ...draftToEnv(d), ...(arg !== "new" ? { id: arg } : {}) });
      if (res.ok) { state.envDraft = null; location.hash = `#/environments/${res.data.id}`; if (arg === res.data.id) render(false); } else showError(res, "The resource set could not be saved.");
    });
  }

  // ------------------------------------------------------------ experiments: list and wizard
  const newSim = () => ({ name: "", environment: "builtin-research", agents: { X: "builtin-priority", Y: "builtin-concession" },
    conditions: ["baseline_v1", "structured_v1"], repetitions: 3, base_seed: 0, swap_seats: true, swap_first_mover: true, pace: "watch",
    extra_envs: [], events: [], personas: [], prices: {}, caps: {}, consented: [] });
  const STEPS = [["agents", "Agents"], ["resources", "Resources"], ["preferences", "Preferences"], ["settings", "Settings"], ["review", "Review"]];

  async function simulations(_s, arg) {
    try { await catalog(); } catch (e) { return unavailable(e.message); }
    if (arg?.startsWith("new")) return wizard(arg.split("/")[1]);
    const list = await getJSON("/api/workbench/simulations");
    return `${top("Experiments", "Each experiment runs every negotiation of a design on the unchanged engine, then analyses them automatically. Results are labelled by what actually ran.", `<a class="cta" href="#/simulations/new">${icon("plus")}New experiment</a>`)}
      ${list.length ? experimentList(list) : empty("No experiments yet", "An experiment pairs two agents on a resource set and runs every combination of the design you choose. The first one takes about a minute to set up.", `<a class="cta" href="#/simulations/new">${icon("plus")}Set up an experiment</a>`)}`;
  }
  const kindName = (k) => ({ simulated: "simulated", "local-llm": "local LLM" })[k] ?? "local agents";
  const experimentList = (list) => `<ul class="ilist">${list.map((s) => `<li><a class="irow" href="#/simulation/${s.id}"><span class="imain"><b>${esc(s.name)}</b>
      <span class="sub">${esc(s.agents.X)} vs ${esc(s.agents.Y)} · ${esc(s.environment)}${s.sweep ? " and more" : ""} · ${esc(sourceSummary(s))} · ${when(s.created)}</span></span>
      <span class="imeta"><span class="mini-prog" role="img" aria-label="${s.done} of ${s.planned} negotiations"><i style="--w:${s.planned ? s.done / s.planned : 0}"></i></span><span class="sub">${s.done}/${s.planned}${s.agreement_rate == null ? "" : ` · ${pct(s.agreement_rate)} agreed`}</span></span>
      ${statusBadge(s.status)}${icon("chev-r")}</a></li>`).join("")}</ul>`;

  async function wizard(stepKey = "agents") {
    const [agentList, envList] = await Promise.all([getJSON("/api/workbench/agents"), getJSON("/api/workbench/environments")]);
    const d = (state.simDraft ??= newSim()), lim = state.catalog.limits;
    const step = Math.max(0, STEPS.findIndex(([k]) => k === stepKey));
    const env = envList.find((e) => e.id === d.environment), n = designSize(d);
    const a = agentList.find((x) => x.id === d.agents.X), b = agentList.find((x) => x.id === d.agents.Y);
    const deaf = a && b && ![a, b].some((x) => kindOf(x.kind).reads_instructions);
    const simulated = a && b && [a, b].every((x) => ["mock", "python"].includes(x.category));
    const hasPrompt = [a, b].some((x) => x?.kind === "prompt"), llm = [a, b].some((x) => x?.category === "llm");
    if (!hasPrompt) d.personas = [];
    const est = step === STEPS.length - 1 && a?.executable && b?.executable ? await sbx.estimateFor(d) : null;
    const estimate = est?.ok ? est.data : null;
    const stepper = `<ol class="stepper" aria-label="Experiment setup">${STEPS.map(([k, t], i) => `<li class="${i < step ? "done" : i === step ? "now" : ""}"><a href="#/simulations/new/${k}"${i === step ? ' aria-current="step"' : ""}><i>${i < step ? icon("check") : i + 1}</i>${t}</a></li>`).join("")}</ol>`;
    const agentPick = (slot) => `<fieldset class="choice"><legend>Agent ${slot}</legend><div class="pickgrid">${agentList.filter((x) => x.executable).map((x) => `<label class="opt"><input type="radio" name="agent-${slot}" value="${x.id}"${x.id === d.agents[slot] ? " checked" : ""}>
        <span><b>${esc(x.name)}</b><small>${esc(x.notes || x.kind_label)}</small></span></label>`).join("")}</div></fieldset>`;
    let body;
    if (step === 0) body = `<h2>Who negotiates?</h2><p class="note">Pick a strategy for each side. Both can use the same one.</p>
      <div class="wb-grid2">${agentPick("X")}${agentPick("Y")}</div><p class="note"><a href="#/agents/new">Add your own agent</a></p>`;
    else if (step === 1) body = `<h2>What do they divide?</h2><p class="note">Each side privately spreads 100 points across the resources and never sees the other side's points.</p>
      <fieldset class="choice"><legend class="vh">Resource set</legend><div class="pickgrid wide">${envList.map((e) => `<label class="opt"><input type="radio" name="env" value="${e.id}"${e.id === d.environment ? " checked" : ""}>
        <span><b>${esc(e.name)}</b><small>${e.source === "generated" ? `${e.categories} resources, new per seed` : Object.entries(e.resources).map(([c, q]) => `${q} ${e.meta?.[c]?.unit ? `${e.meta[c].unit} of ` : ""}${c}`).join(" · ")}</small></span></label>`).join("")}</div></fieldset>
      ${sbx.sweepEditor(d, envList)}${sbx.eventEditor(d)}`;
    else if (step === 2) body = `<h2>What does each agent want?</h2><p class="note">Private preferences come from the resource set: each agent spreads 100 points over the resources, and never sees the other's.</p>
      ${env?.source === "generated" ? notice("info", "info", "This resource set uses the research generator, so preferences are drawn anew for every seed. Each repetition is a different situation.")
        : env ? `${butterfly(Object.keys(env.resources).map((c) => ({ name: c, a: env.points.A[c], b: env.points.B[c] })))}${env.builtin ? `<p class="note">A template. To change these values, duplicate it under <a href="#/environments/${env.id}">Resources</a>.</p>` : `<p class="note"><a href="#/environments/${env.id}">Edit these values</a></p>`}` : ""}
      <p class="note">With seats swapped (next step), each agent plays both A and B, so neither is favoured by the values it was dealt.</p>`;
    else if (step === 3) body = `<h2>How should it run?</h2>
      <fieldset class="choice"><legend>Instruction conditions</legend>
        ${state.catalog.conditions.map((c) => `<label class="opt"><input type="checkbox" data-cond="${c}"${d.conditions.includes(c) ? " checked" : ""} data-field="conditions"><span><b>${COND_NAME[c]} instructions</b><small><code>${c}</code>${deaf ? " · not read by these agents" : ""}</small></span></label>`).join("")}
        <small class="note">The research study's frozen instruction variants. Agents that read instructions receive them each turn.</small></fieldset>
      <div class="wb-grid2"><label class="field"><span>Repetitions</span><input type="number" min="1" max="${lim.repetitions}" step="1" value="${d.repetitions}" data-n="repetitions" data-field="repetitions"><small class="note">Each uses its own seed.</small></label>
        <label class="field"><span>First seed</span><input type="number" min="0" step="1" value="${d.base_seed}" data-n="base_seed" data-field="base_seed"><small class="note">Seeds count up from here.</small></label></div>
      <div class="wb-toggles"><label class="toggle"><input type="checkbox" data-b="swap_seats"${d.swap_seats ? " checked" : ""}> Swap seats, so each agent plays A and B</label>
        <label class="toggle"><input type="checkbox" data-b="swap_first_mover"${d.swap_first_mover ? " checked" : ""}> Swap who makes the first offer</label></div>
      <fieldset class="choice"><legend>Pace</legend><div class="seg" role="group" aria-label="Pace">${[["watch", "Watch live"], ["instant", "As fast as possible"]].map(([v, t]) => `<button type="button" data-pace="${v}" aria-pressed="${d.pace === v}">${t}</button>`).join("")}</div>
        <small class="note">${d.pace === "watch" ? "Turns are spaced at least 0.35 s apart so you can follow them." : "No pacing: rule-based agents finish in well under a second."}</small></fieldset>
      ${hasPrompt ? sbx.personaEditor(d) : ""}`;
    else body = `<h2>Review and launch</h2>
      <label class="field"><span>Experiment name <span class="sub">(optional)</span></span><input type="text" maxlength="80" value="${esc(d.name)}" data-s="name" data-field="name" placeholder="${esc(`${a?.name ?? "X"} vs ${b?.name ?? "Y"} · ${env?.name ?? ""}`)}"></label>
      <dl class="review"><div><dt>Agents</dt><dd>${esc(a?.name ?? "—")} vs ${esc(b?.name ?? "—")}</dd></div>
        <div><dt>Resources</dt><dd>${esc(env?.name ?? "—")}${d.extra_envs.length ? ` and ${d.extra_envs.length} more` : ""}<span class="sub">${env?.source === "generated" ? "new instance per seed" : "fixed values"} · ${env?.max_rounds ?? "—"} turns${d.events.length ? ` · ${d.events.length} resource event${d.events.length === 1 ? "" : "s"}` : ""}</span></dd></div>
        <div><dt>Design</dt><dd>${d.repetitions} repetition${d.repetitions === 1 ? "" : "s"} × ${d.conditions.length} condition${d.conditions.length === 1 ? "" : "s"} × ${d.swap_seats ? 2 : 1} seating${d.swap_seats ? "s" : ""} × ${d.swap_first_mover ? 2 : 1} opener${d.swap_first_mover ? "s" : ""}${d.extra_envs.length ? ` × ${d.extra_envs.length + 1} resource sets` : ""}${d.personas.length ? ` × ${d.personas.length} personas` : ""}<span class="sub">seeds ${d.base_seed}${d.repetitions > 1 ? `–${d.base_seed + d.repetitions - 1}` : ""} · ${d.pace === "watch" ? "watch live" : "fast"}</span></dd></div>
        <div><dt>Negotiations</dt><dd><b>${n}</b>${n > lim.negotiations ? ` ${badge("crit", "x", `over the ${lim.negotiations} limit`)}` : ""}</dd></div>
        <div><dt>Data source</dt><dd>${estimate ? esc(sourceSummary(estimate)) : "—"}<span class="sub">${estimate?.required.some((c) => c.id === "api_cost") ? "Uses your API key: you pay your provider" : "Free"}</span></dd></div></dl>
      ${estimate ? sbx.consentPanel(d, estimate) : ""}
      ${est && !est.ok ? `<p class="step-error" role="alert">${esc(est.data?.error ?? "This design cannot run.")}</p>` : ""}
      ${d.repetitions < 10 ? notice("info", "info", `<b>Small sample.</b> With ${d.repetitions} repetition${d.repetitions === 1 ? "" : "s"}, intervals will be wide. Results below 10 repetitions are flagged as a first look.`) : ""}
      ${env?.source === "custom" && a && b && kindOf(a.kind).deterministic && kindOf(b.kind).deterministic && d.repetitions > 1 ? notice("warn", "alert", "Fixed values and repeatable agents: every repetition will replay the first one exactly. Use the research generator for varied situations.") : ""}
      ${llm ? notice("info", "await", `<b>Local models take time.</b> A turn can take up to a minute on a laptop, so ${n} negotiations may run for a while. You can lower the repetitions under Advanced settings.`) : ""}
      ${deaf && d.conditions.length > 1 ? notice("info", "info", "Neither agent reads instructions, so both conditions will give identical results. That checks the pipeline; it is not a finding.") : ""}
      <details class="advanced" id="wiz-adv"${state.adv ? " open" : ""}><summary>Advanced settings</summary>
      <fieldset class="choice"><legend>Instruction conditions</legend>
        ${state.catalog.conditions.map((c) => `<label class="opt"><input type="checkbox" data-cond="${c}"${d.conditions.includes(c) ? " checked" : ""} data-field="conditions"><span><b>${COND_NAME[c]} instructions</b><small><code>${c}</code>${deaf ? " · not read by these agents" : ""}</small></span></label>`).join("")}
        <small class="note">The research study's frozen instruction variants. Agents that read instructions receive them each turn.</small></fieldset>
      <div class="wb-grid2"><label class="field"><span>Repetitions</span><input type="number" min="1" max="${lim.repetitions}" step="1" value="${d.repetitions}" data-n="repetitions" data-field="repetitions"><small class="note">Each uses its own seed.</small></label>
        <label class="field"><span>First seed</span><input type="number" min="0" step="1" value="${d.base_seed}" data-n="base_seed" data-field="base_seed"><small class="note">Seeds count up from here.</small></label></div>
      <div class="wb-toggles"><label class="toggle"><input type="checkbox" data-b="swap_seats"${d.swap_seats ? " checked" : ""}> Swap seats, so each agent plays A and B</label>
        <label class="toggle"><input type="checkbox" data-b="swap_first_mover"${d.swap_first_mover ? " checked" : ""}> Swap who makes the first offer</label></div>
      <fieldset class="choice"><legend>Pace</legend><div class="seg" role="group" aria-label="Pace">${[["watch", "Watch live"], ["instant", "As fast as possible"]].map(([v, t]) => `<button type="button" data-pace="${v}" aria-pressed="${d.pace === v}">${t}</button>`).join("")}</div>
        <small class="note">${d.pace === "watch" ? "Turns are spaced 0.35 s apart so you can follow them; the agents themselves answer instantly." : "No pacing: rule-based agents finish in well under a second."}</small></fieldset>
      </details>`;
    const blocker = step === 0 && !(a?.executable && b?.executable) ? "Choose two agents that can run." : step === 2 && !d.conditions.length ? "Choose at least one condition." : "";
    const nextBtn = step < STEPS.length - 1 ? `<a class="cta" href="#/simulations/new/${STEPS[step + 1][0]}"${blocker ? ' aria-disabled="true" data-block' : ""}>Continue${icon("chev-r")}</a>`
      : `<button type="button" class="cta" id="launch"${n > lim.negotiations || !d.conditions.length || !estimate || !sbx.consentReady(d, estimate) ? " disabled" : ""}>${icon("play")}${esc(estimate ? sbx.runLabel(estimate, n) : `Run ${n} negotiations`)}</button>`;
    state.estimate = estimate;
    const skip = step < STEPS.length - 1 && !blocker ? `<a class="linkbtn" href="#/simulations/new/review">Skip to review</a>` : "";
    return `${top("New experiment", "", "", backLink("#/simulations", "Experiments"))}${stepper}
      <div class="wb-split"><form class="sheet wizard" id="sim-form" novalidate>${body}${errorLine(blocker)}
        <div class="stepnav">${step ? `<a class="btn" href="#/simulations/new/${STEPS[step - 1][0]}">${icon("chev-l")}Back</a>` : '<span></span>'}<span class="stepnav-r">${skip}${nextBtn}</span></div></form>
      <aside class="sheet wb-aside"><h2 class="wb-h3">So far</h2><dl class="kvs"><dt>Agents</dt><dd>${esc(a?.name ?? "—")} · ${esc(b?.name ?? "—")}</dd><dt>Resources</dt><dd>${esc(env?.name ?? "—")}</dd>
        <dt>Negotiations</dt><dd>${n}</dd><dt>Cost</dt><dd>${hasPrompt ? "Shown at review" : "Free"}</dd></dl>
        <h2 class="wb-h3">Other ways to start</h2><ul class="plainlist"><li><a href="#/try">Try a single negotiation</a> <span class="sub">one negotiation, turn by turn</span></li><li><a href="#/lab">Agent Lab</a> <span class="sub">a balanced evaluation of two agents</span></li></ul></aside></div>`;
  }

  function wireWizard() {
    const form = main.querySelector("#sim-form");
    if (!form) return;
    const d = state.simDraft;
    form.querySelector("#wiz-adv")?.addEventListener("toggle", (e) => { state.adv = e.target.open; });
    const on = (sel, ev, fn) => form.querySelectorAll(sel).forEach((el) => el.addEventListener(ev, () => fn(el)));
    on('input[name^="agent-"]', "change", (el) => { d.agents[el.name.slice(-1)] = el.value; render(false); });
    on('input[name="env"]', "change", (el) => { d.environment = el.value; render(false); });
    on("[data-s=name]", "input", (el) => { d.name = el.value; });
    on("[data-cond]", "change", (el) => { d.conditions = state.catalog.conditions.filter((c) => (c === el.dataset.cond ? el.checked : d.conditions.includes(c))); render(false); });
    on("[data-n]", "change", (el) => { d[el.dataset.n] = el.value === "" ? NaN : Number(el.value); render(false); });
    on("[data-b]", "change", (el) => { d[el.dataset.b] = el.checked; render(false); });
    on("[data-pace]", "click", (el) => { d.pace = el.dataset.pace; render(false); });
    sbx.wireDesign(form, d);
    sbx.wireConsent(form, d);
    main.querySelectorAll("[data-block]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); main.querySelector("#wb-error").hidden = false; }));
    form.addEventListener("submit", (e) => e.preventDefault());
    main.querySelector("#launch")?.addEventListener("click", async (e) => {
      const btn = e.currentTarget;
      btn.disabled = true; btn.innerHTML = '<span class="spin" aria-hidden="true"></span>Starting…';
      const res = await post("/api/workbench/simulations", { ...simBody(d), consent: { token: state.estimate?.token, accepted: d.consented } });
      if (res.ok) { state.simTab = "live"; state.simDraft = null; location.hash = `#/simulation/${res.data.id}`; }
      else { btn.disabled = false; btn.innerHTML = `${icon("play")}Try again`; showError(res, "The experiment could not start."); }
    });
  }

  /** One-click first experiment from built-ins: real local negotiations, labelled simulated. */
  async function quickStart() {
    const res = await post("/api/workbench/simulations", { ...simBody(newSim()), name: "First experiment · Priority trader vs Steady conceder" });
    if (res.ok) { state.simTab = "live"; location.hash = `#/simulation/${res.data.id}`; }
    return res;
  }

  // ------------------------------------------------------------ experiment workspace
  const seatName = (doc, seats, seat) => `${seats[seat]} · ${doc.config.agents[seats[seat]].name}`;
  const allocText = (alloc, seat) => (alloc ? Object.entries(alloc[seat]).map(([c, q]) => `${esc(c)} ${q}`).join(", ") : "");
  function turnRow(t, doc, seats) {
    const color = AGENT_COLOR[seats[t.actor]];
    const what = t.action === "OFFER" ? `offers itself ${allocText(t.allocation, t.actor)}` : t.action === "ACCEPT" ? "accepts the standing offer" : t.action === "WALK_AWAY" ? "walks away" : `breaks the rules (${esc(t.reason ?? "")})`;
    const values = t.value_A != null ? `<span class="sub">worth A ${fmt(t.value_A, 1)} · B ${fmt(t.value_B, 1)} points${t.standing === false ? " · final-turn offer, never standing" : ""}</span>` : "";
    return `<li class="wb-turn ${t.action === "INVALID" ? "bad" : ""}" style="--c:${color}"><span class="wb-tn">${t.turn}</span><div><span class="act ${t.action}">${ACT[t.action] ?? t.action}</span> <b>${esc(seatName(doc, seats, t.actor))}</b> ${what}${values ? `<br>${values}` : ""}${t.message ? `<div class="wb-msg">“${esc(t.message)}”</div>` : ""}</div></li>`;
  }
  const elapsed = (doc) => { const s = Math.max(0, Math.round((doc.finished ?? Date.now() / 1000) - doc.created)); return s < 60 ? `${s} s` : `${Math.floor(s / 60)} min ${s % 60} s`; };

  async function simulation(_s, id) {
    let doc;
    try { doc = await getJSON(`/api/workbench/simulations/${encodeURIComponent(id)}`); } catch { return top("Experiment not found", "No experiment has this id on this server.", "", backLink("#/simulations", "Experiments")); }
    await catalog();
    state.doc = doc;
    const tabs = [["live", doc.status === "running" ? "Live" : "Summary"], ["analytics", "Analysis"], ["negotiations", `Negotiations (${doc.trials.length})`], ["export", "Export"]];
    if (!tabs.some(([k]) => k === state.simTab)) state.simTab = "live";
    const sum = summarize(doc.trials);
    return `${top(doc.config.name, `${esc(doc.config.agents.X.name)} vs ${esc(doc.config.agents.Y.name)} · ${esc((doc.config.environments ?? [doc.config.environment]).map((e) => e.name).join(", "))} · started <time data-ago="${doc.created}">${when(doc.created)}</time>`,
        `${statusBadge(doc.status)}<a class="btn" href="#/report/${doc.id}">${icon("doc")}Report</a>`, backLink("#/simulations", "Experiments"))}
      <p class="datalabel ${doc.config.data_kind}">${icon("info")}<span><b>${esc(doc.config.label.split(":")[0])}.</b>${esc(doc.config.label.slice(doc.config.label.indexOf(":") + 1))}</span></p>
      ${doc.status !== "running" ? "" : `<div class="runbar"><div class="wb-progress" role="progressbar" aria-label="Negotiations finished" aria-valuemin="0" aria-valuemax="${doc.plan_size}" aria-valuenow="${doc.trials.length}"><i style="transform:scaleX(${doc.trials.length / doc.plan_size})"></i></div>
        <span class="sub" id="progress-text">${doc.trials.length} of ${doc.plan_size} negotiations · ${elapsed(doc)}${sum.n ? ` · ${pct(sum.agreement_rate)} agreed` : ""}</span></div>`}
      ${doc.stop_reason ? notice("warn", "alert", `<b>Stopped early.</b> ${esc(doc.stop_reason)}`) : ""}
      ${keyNumbers(doc)}
      <nav class="tabs" aria-label="Experiment views" id="simtabs">${tabs.map(([k, t]) => `<a href="#/simulation/${doc.id}" data-simtab="${k}"${state.simTab === k ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</nav>
      <div id="simpanel">${simPanel(doc)}</div>`;
  }

  function simPanel(doc) {
    const tab = state.simTab;
    if (tab === "live") return livePanel(doc);
    if (tab === "negotiations") return trialsPanel(doc);
    if (tab === "analytics") return analyticsPanel(doc);
    return exportPanel(doc);
  }

  const tape = (doc) => `<div class="otape" aria-label="Finished negotiations by outcome">${doc.trials.map((t) => `<button type="button" class="ot ${t.status === "completed" ? t.outcome : "aborted"}" style="--c:${t.status === "completed" ? OUT_COLOR[t.outcome] : "var(--sunk)"}" data-goto="${t.index}" data-tip="${esc(`#${t.index + 1}: ${t.status === "completed" ? OUTCOME_LABEL[t.outcome] : t.status}${t.rounds ? ` after ${t.rounds} turns` : ""}`)}" aria-label="Negotiation ${t.index + 1}: ${esc(t.status === "completed" ? OUTCOME_LABEL[t.outcome] : t.status)}"></button>`).join("")}${Array.from({ length: Math.max(0, doc.plan_size - doc.trials.length) }, () => '<i class="ot pending"></i>').join("")}</div>`;
  const tapeLegend = (doc) => `<div class="legend ot-legend">${OUTCOMES.map((o) => `<span><i class="ot ${o}" style="--c:${OUT_COLOR[o]}"></i>${OUTCOME_LABEL[o]}</span>`).join("")}${doc.trials.some((t) => t.status !== "completed") ? '<span><i class="ot aborted"></i>Aborted (infrastructure)</span>' : ""}${doc.plan_size > doc.trials.length ? '<span><i class="ot pending"></i>Pending</span>' : ""}</div>`;
  /** The answer first: what happened, in words, from stored negotiations only. */
  function answer(doc) {
    const s = summarize(doc.trials), agreed = s.outcomes.agreed;
    if (!s.n) return "";
    const head = agreed === s.n && s.n > 1 ? `All ${s.n} negotiations reached a deal.` : agreed ? `${agreed} of ${s.n} negotiations reached a deal.` : `No deal in ${s.n === 1 ? "this negotiation" : `any of the ${s.n} negotiations`}.`;
    const byCondition = doc.config.conditions.map((c) => [c, summarize(doc.trials.filter((t) => t.condition === c))]).filter(([, x]) => x.n);
    return `<section class="answer"><h2>${head}</h2><p>On average they took ${fmt(s.rounds, 1)} turns and captured ${pct(s.rwe)} of the best combined value possible. The less satisfied side ended with ${Math.round(s.ew * 100)} of its 100 points.</p>
      ${byCondition.length > 1 ? `<ul class="findings">${byCondition.map(([c, x]) => `<li><b>${COND_NAME[c]} instructions:</b> ${pct(x.agreement_rate)} reached a deal, capturing ${pct(x.rwe)} of the value.</li>`).join("")}</ul>` : ""}
      <button type="button" class="link" data-findings>See what the data shows${icon("chev-r")}</button></section>`;
  }

  function liveStats(doc) {
    const s = summarize(doc.trials), c = actionCounts(doc.trials);
    const item = (k, v) => `<div><dt>${k}</dt><dd>${v}</dd></div>`;
    return `<dl class="factline" id="live-stats">${item("Finished", `${doc.trials.length} of ${doc.plan_size}`)}${item("Agreed", s.outcomes.agreed)}${item("Walked away", s.outcomes.walked_away)}
      ${item("Timed out", s.outcomes.timeout)}${item("Invalid", s.outcomes.invalid_action)}${item("Offers", `${c.offers} <span class="sub">(${c.rejected} rejected, ${c.accepted} accepted)</span>`)}${item("Elapsed", elapsed(doc))}</dl>`;
  }

  function livePanel(doc) {
    const cfg = doc.config, live = doc.live;
    const outcomesBlock = `<h3 class="wb-h4">${doc.status === "running" ? "Outcomes so far" : "How each negotiation ended"}</h3>${tape(doc)}${tapeLegend(doc)}`;
    if (doc.status !== "running" || !live) {
      return `${doc.error ? notice("warn", "alert", `<b>Stopped by an error.</b> ${esc(doc.error)}`) : ""}${answer(doc)}
        <div class="nextsteps"><button type="button" class="cta" data-simtab-go="analytics">${icon("chart")}Explore the analysis</button><button type="button" class="btn" data-simtab-go="negotiations">Read the negotiations</button><a class="btn" href="#/report/${doc.id}">${icon("doc")}Open the report</a></div>
        <section class="block">${outcomesBlock}</section>
        <details class="rundetails"><summary>Run details</summary><dl class="kvs wide"><dt>Agents</dt><dd>X ${esc(cfg.agents.X.name)} (${esc(kindOf(cfg.agents.X.kind)?.label ?? cfg.agents.X.kind)}) · Y ${esc(cfg.agents.Y.name)} (${esc(kindOf(cfg.agents.Y.kind)?.label ?? cfg.agents.Y.kind)})</dd>
        <dt>Design</dt><dd>${cfg.repetitions} repetition(s) from seed ${cfg.base_seed}; ${cfg.conditions.map((c) => COND_NAME[c]).join(" and ")}; seats swapped ${cfg.swap_seats ? "yes" : "no"}; opener swapped ${cfg.swap_first_mover ? "yes" : "no"}</dd>
        <dt>Provenance</dt><dd>protocol <code>${esc(doc.provenance.protocol_id)}</code> · code <code>${esc(String(doc.provenance.code_version).slice(0, 12))}</code> · zero-cost mode ${doc.provenance.zero_cost_mode ? "on" : "off"}</dd></dl>
        <h3 class="wb-h4">Log</h3><ol class="wb-log">${doc.log.map((l) => `<li class="${l.level}"><span class="mono">${l.t.toFixed(2)} s</span><span>${esc(l.text)}</span></li>`).join("")}</ol></details>`;
    }
    const sp = live.spec;
    return `${liveStats(doc)}${outcomesBlock}<section class="block live-now"><div class="block-head"><h2>Now: negotiation ${sp.index + 1} of ${doc.plan_size}</h2><span class="note">seed ${sp.seed} · ${COND_NAME[sp.condition]} · Agent ${sp.first_mover} opens</span></div>
      <div class="wb-split"><div><ol class="wb-turns" id="live-turns">${live.turns.map((t) => turnRow(t, doc, sp.seats)).join("")}</ol>
        <p class="wb-thinking" id="live-thinking" aria-live="polite">${live.thinking ? `<i></i>${esc(seatName(doc, sp.seats, live.thinking.seat))} is deciding turn ${live.thinking.round}…` : ""}</p></div>
      <aside class="sheet wb-aside"><h2 class="wb-h3">At the table</h2><dl class="kvs">${["A", "B"].map((s) => `<dt>Seat ${s}</dt><dd><i class="wb-dot" style="--c:${AGENT_COLOR[sp.seats[s]]}"></i>${esc(seatName(doc, sp.seats, s))}</dd>`).join("")}</dl>
        <h2 class="wb-h3">On the table</h2><dl class="kvs">${Object.entries(live.pool).map(([c, q]) => `<dt>${esc(c)}</dt><dd>${q} ${esc(cfg.environment.meta?.[c]?.unit ?? "units")}</dd>`).join("")}</dl>
        <button type="button" class="btn" id="cancel-sim">${icon("x")}Stop the experiment</button></aside></div></section>`;
  }

  function trialsPanel(doc) {
    if (!doc.trials.length) return empty("No negotiations finished yet", "They appear here as each one ends.");
    return `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th class="num">Seed</th><th>Condition</th><th>Seat A · Seat B</th><th>Opens</th><th>Outcome</th><th class="num">Turns</th><th class="num">Value created (RWE)</th><th class="num">Fairness (EW)</th></tr></thead><tbody>
      ${doc.trials.map((t) => `<tr class="link" data-trial="${t.index}" id="trial-${t.index}" tabindex="0" aria-expanded="${state.openTrial === t.index}"><td class="num">${t.index + 1}</td><td class="num">${t.seed}</td><td>${COND_NAME[t.condition] ?? esc(t.condition)}</td>
        <td>${t.seats.A} · ${t.seats.B}</td><td>${t.first_mover}</td><td>${t.status === "completed" ? `<span class="tag" style="--c:${OUT_COLOR[t.outcome]}">${esc(OUTCOME_LABEL[t.outcome] ?? t.outcome)}</span>${t.invalid_reason ? ` <span class="sub">${esc(t.invalid_reason)}</span>` : ""}` : `<span class="sub">${esc(t.status)}${t.reason ? `: ${esc(t.reason)}` : ""}</span>`}</td>
        <td class="num">${t.rounds ?? "—"}</td><td class="num">${t.rwe == null ? "—" : fmt(t.rwe)}</td><td class="num">${t.ew == null ? "—" : fmt(t.ew)}</td></tr>
        ${state.openTrial === t.index ? `<tr class="wb-detail"><td colspan="9">${trialDetail(doc, t)}</td></tr>` : ""}`).join("")}</tbody></table></div>
      <p class="note" style="margin-top:10px">Select a row to read its transcript. Value created (RWE) and fairness (EW) score failed negotiations as 0.</p>`;
  }

  function trialDetail(doc, t) {
    if (t.status !== "completed") return `<p>${esc(t.status === "aborted" ? `Aborted in turn ${t.round} (${t.reason}): ${t.detail}. Not an outcome; not re-run.` : "Cancelled before it finished. Not an outcome.")}</p>`;
    return `<div class="wb-split"><ol class="wb-turns">${t.events.map((e) => turnRow(e, doc, t.seats)).join("")}</ol>
      <dl class="kvs"><dt>Outcome</dt><dd>${esc(OUTCOME_LABEL[t.outcome] ?? t.outcome)} after ${t.rounds} turn(s)</dd>
        <dt>Final split</dt><dd>${t.final_allocation ? `A: ${allocText(t.final_allocation, "A")}<br>B: ${allocText(t.final_allocation, "B")}` : "None (no agreement)"}</dd>
        <dt>Points won</dt><dd>A ${fmt(t.utility_A, 1)} · B ${fmt(t.utility_B, 1)} of 100</dd><dt>Best combined</dt><dd>${fmt(t.optimal_welfare, 1)}, best split for A: ${allocText(t.optimal_allocation, "A")}</dd>
        <dt>Private points</dt><dd>A: ${Object.entries(t.points.A).map(([c, p]) => `${esc(c)} ${fmt(p, 0)}`).join(", ")}<br>B: ${Object.entries(t.points.B).map(([c, p]) => `${esc(c)} ${fmt(p, 0)}`).join(", ")}</dd></dl></div>`;
  }

  // ------------------------------------------------------------ analytics (one experiment)
  const KPI = {
    agreement_rate: ["Deals reached (agreement rate)", "Agreement rate: the share of negotiations that ended in an accepted offer before the deadline. All negotiations count."],
    rwe: ["Value created (RWE)", "How much of the best possible total value the two agents achieved. Relative welfare efficiency: combined value of the deal ÷ the best combined value possible (W*). Failed negotiations score 0."],
    ew: ["Fairness (EW)", "How well the worse-off agent did. Egalitarian welfare: the points of the less satisfied agent ÷ 100. Failed negotiations score 0."],
    rounds: ["Turns", "Mean number of turns before the negotiation ended."],
    walk_rate: ["Walk-aways", "Share of negotiations ended by an agent walking away."],
    invalid_rate: ["Invalid actions", "Share ended by an action that broke the rules (impossible split, malformed answer, illegal accept)."],
  };
  const kpiTile = (k, v) => `<div class="stat"><dt>${KPI[k][0]}<button type="button" class="hint" data-tip="${esc(KPI[k][1])}" aria-label="${esc(`${KPI[k][0]}: ${KPI[k][1]}`)}">${icon("info")}</button></dt><dd>${v}</dd></div>`;
  /** The three numbers a first-time reader needs, in plain words; the formal name is in the label and tooltip. */
  const keyNumbers = (doc) => { const s = summarize(doc.trials);
    return `<section class="keynums" id="keynums" aria-label="Key results${doc.status === "running" ? " so far" : ""}"><dl class="stats">${kpiTile("agreement_rate", s.n ? pct(s.agreement_rate) : "—")}${kpiTile("rwe", s.n ? fmt(s.rwe, 2) : "—")}${kpiTile("ew", s.n ? fmt(s.ew, 2) : "—")}</dl>
      <p class="note">${s.n ? `From ${s.n} finished negotiation${s.n === 1 ? "" : "s"}${doc.status === "running" ? " so far" : ""}. Value created and fairness run from 0 to 1, and a failed negotiation counts as 0.` : "Appears once the first negotiation finishes."}</p></section>`; };
  const kpis = (s) => `<dl class="stats">${kpiTile("agreement_rate", pct(s.agreement_rate))}${kpiTile("rwe", fmt(s.rwe, 2))}${kpiTile("ew", fmt(s.ew, 2))}${kpiTile("rounds", fmt(s.rounds, 1))}${kpiTile("walk_rate", pct(s.walk_rate))}${kpiTile("invalid_rate", pct(s.invalid_rate))}</dl>`;

  /** Charts built from stored trials; shared with the report. */
  function outcomeCharts(doc, trials, names) {
    const s = summarize(trials), done = completedTrials(trials), conds = doc.config.conditions;
    const parts = OUTCOMES.map((o) => ({ key: o, label: OUTCOME_LABEL[o], count: s.outcomes[o], color: OUT_COLOR[o] }));
    const rounds = Object.fromEntries([...Array(doc.config.environment.max_rounds)].map((_, i) => [i + 1, done.filter((t) => t.rounds === i + 1).length]));
    const scat = done.map((t) => ({ x: t.agents.X.utility, y: t.agents.Y.utility, s: t.condition, color: COND_COLOR[t.condition], trial: t.index,
      tip: `#${t.index + 1} · ${COND_NAME[t.condition]} · ${OUTCOME_LABEL[t.outcome]}\n${names.X} ${fmt(t.agents.X.utility * 100, 0)} · ${names.Y} ${fmt(t.agents.Y.utility * 100, 0)} points` }));
    const box = (key) => conds.map((c) => ({ key: c, label: COND_NAME[c], color: COND_COLOR[c], stats: boxStats(done.filter((t) => t.condition === c).map((t) => t[key])) }));
    const condLegend = conds.length > 1 ? toggleLegend(conds.map((c) => [c, COND_NAME[c], COND_COLOR[c]])) : "";
    return {
      donut: chartCard("How negotiations ended", "Share of outcomes. Select a segment to filter the other charts to that outcome.",
        `<div class="donutwrap">${donut(parts)}<ul class="dlegend">${parts.map((p) => `<li><i class="sw" style="--c:${p.color}"></i>${p.label}<b>${p.count}</b><span class="sub">${s.n ? pct(p.count / s.n) : "—"}</span></li>`).join("")}</ul></div>`,
        { table: dataTable(["Outcome", "Negotiations", "Share"], parts.map((p) => [p.label, p.count, s.n ? pct(p.count / s.n) : "—"])) }),
      rounds: chartCard("How long they took", "Number of negotiations ending at each turn. Late endings suggest hard bargaining or a deadline.", histogram(rounds, Object.keys(rounds), "var(--accent)", "Negotiations", "Turn the negotiation ended"),
        { table: dataTable(["Turn", "Negotiations"], Object.entries(rounds)) }),
      scatter: chartCard("Who got what", `Each dot is one negotiation: the points ${esc(names.X)} won against the points ${esc(names.Y)} won. Dots on the dashed line split value evenly; failures sit at 0, 0. ${conds.length > 1 ? `${COND_NAME[conds[1]]} is drawn as rings, so it shows where it coincides with ${COND_NAME[conds[0]]}. ` : ""}Select a dot to read it.`,
        scatter(scat, { xLabel: `${names.X} (share of its 100 points)`, yLabel: `${names.Y} (share)`, diagonal: "equal shares" }), { lg: condLegend,
          table: dataTable(["Negotiation", "Condition", names.X, names.Y], done.map((t) => [t.index + 1, COND_NAME[t.condition], fmt(t.agents.X.utility), fmt(t.agents.Y.utility)])) }),
      box: chartCard("Value created by condition", "Spread of value created (RWE). The box holds the middle half of negotiations, the line is the median, whiskers reach the rest; circles are outliers.",
        `${boxPlot(box("rwe"), { axis: "Value created (RWE)" })}${boxPlot(box("ew"), { axis: "Fairness (EW)" })}`,
        { table: dataTable(["Condition", "Measure", "n", "Median", "Mean", "Q1", "Q3"], ["rwe", "ew"].flatMap((m) => box(m).map((g) => [g.label, m.toUpperCase(), g.stats?.n ?? 0, fmt(g.stats?.median), fmt(g.stats?.mean), fmt(g.stats?.q1), fmt(g.stats?.q3)]))) }),
      area: chartCard("Outcomes as the experiment ran", "Running share of each outcome after each finished negotiation. A flat band means the pattern settled early.",
        stackedArea(cumulativeOutcomes(trials), OUTCOMES.map((o) => ({ key: o, label: OUTCOME_LABEL[o], color: OUT_COLOR[o] })), { xLabel: "Negotiations finished" }),
        { lg: toggleLegend(OUTCOMES.map((o) => [o, OUTCOME_LABEL[o], OUT_COLOR[o]])) }),
      sankey: chartCard("From condition to outcome", "Each band is a group of negotiations: which condition they ran under, which agent opened, and how they ended. Hover a band for its count.",
        sankey([conds.map((c) => ({ id: c, label: COND_NAME[c], color: COND_COLOR[c] })), ["X", "Y"].map((k) => ({ id: k, label: `${names[k]} opens`, color: AGENT_COLOR[k] })),
          OUTCOMES.map((o) => ({ id: o, label: OUTCOME_LABEL[o], color: OUT_COLOR[o] }))], flows(trials, [(t) => t.condition, (t) => t.seats[t.first_mover], (t) => t.outcome]), { label: "Condition, opener and outcome" }),
        { table: dataTable(["From", "To", "Negotiations"], flows(trials, [(t) => t.condition, (t) => t.seats[t.first_mover], (t) => t.outcome]).map((l) => [l.stage ? `${names[l.from]} opens` : COND_NAME[l.from], l.stage ? OUTCOME_LABEL[l.to] : `${names[l.to]} opens`, l.n])) }),
      timeline: chartCard("Turn by turn", `One row per negotiation (first 40), one block per turn. Solid blocks are offers, coloured by who made them; outlined blocks end it: accept, walk away or invalid. Select a row to read it.`,
        timeline(done.slice(0, 40).map((t) => ({ trial: t.index, label: `#${t.index + 1}`, cells: t.events.map((e) => ({ action: e.action, color: AGENT_COLOR[t.seats[e.actor]], tip: `#${t.index + 1}, turn ${e.turn}: ${names[t.seats[e.actor]]} — ${ACT[e.action] ?? e.action}` })) })), doc.config.environment.max_rounds),
        { wide: true, lg: `<div class="legend"><span><i class="sw" style="--c:var(--base)"></i>${esc(names.X)} offers</span><span><i class="sw" style="--c:var(--struct)"></i>${esc(names.Y)} offers</span><span><i class="sw ring" style="--c:var(--good)"></i>Accept</span><span><i class="sw ring" style="--c:var(--warning)"></i>Walk away</span><span><i class="sw ring" style="--c:var(--critical)"></i>Invalid</span></div>` }),
    };
  }

  /** Charts built from the server's per-agent analysis (all negotiations, not filtered). */
  function agentCharts(a, names) {
    const keys = Object.keys(a.agents), col = (k) => AGENT_COLOR[k];
    const dims = [{ key: "utility_mean", label: "Points won" }, { key: "opening_demand_mean", label: "Opening demand" }, { key: "total_concession_mean", label: "Concession" },
      { key: "concession_frequency_mean", label: "Concedes often" }, { key: "priority_alignment", label: "Asks for priorities" }];
    const series = keys.map((k) => ({ key: k, label: names[k], color: col(k), values: Object.fromEntries(dims.map((d) => [d.key, a.agents[k][d.key] == null ? null : Math.max(0, Math.min(1, a.agents[k][d.key]))])) }));
    const lines = keys.map((k) => ({ key: k, label: names[k], color: col(k), points: (a.behaviour?.[k]?.concession_curve?.points ?? []).map((p) => ({ x: p.k, y: p.mean_value, n: p.n })) }));
    const res = [...new Set(keys.flatMap((k) => Object.keys(a.agents[k].resources)))];
    const rows = keys.flatMap((k) => [{ k, label: `${names[k].slice(0, 16)} · values`, f: "points" }, { k, label: `${names[k].slice(0, 16)} · asks`, f: "claim" }]);
    const cell = (r, c) => {
      const x = a.agents[r.k].resources[c.id];
      if (!x) return { v: null, tip: "not observed" };
      return r.f === "points" ? { v: x.points_mean == null ? null : x.points_mean / 100, tip: `${names[r.k]} privately values ${c.label} at ${fmt(x.points_mean, 1)} of its 100 points (ground truth)` }
        : { v: x.opening_claim_mean, tip: `${names[r.k]} asked for ${pct(x.opening_claim_mean)} of the ${c.label} units in its first offer (observed, n = ${x.n_opening})` };
    };
    const edges = keys.flatMap((k) => res.map((c) => { const x = a.agents[k].resources[c]; return { agent: k, resource: c, w: x?.final_share_mean, tip: `${names[k]} ended with ${pct(x?.final_share_mean)} of ${c} on average (agreed negotiations, n = ${x?.n_final ?? 0})` }; }));
    const agentLegend = toggleLegend(keys.map((k) => [k, names[k], col(k)]));
    return {
      radar: chartCard("Behaviour profile", "Five behaviours on a 0–100% scale, each measured from the stored negotiations. Hover a point for its value. Dimensions an agent never showed are left out.",
        radar(dims, series), { lg: agentLegend, table: dataTable(["Dimension", ...keys.map((k) => names[k])], dims.map((d) => [d.label, ...series.map((s) => (s.values[d.key] == null ? "—" : pct(s.values[d.key])))])) }),
      concession: chartCard("Concession over offers", "The value (to itself) of each agent's 1st, 2nd, 3rd… offer, averaged over negotiations. A falling line means it gave ground as it went.",
        lines.some((l) => l.points.length) ? lineChart(lines, { xLabel: "The agent's nth offer", yLabel: "Own value of the offer" }) : '<p class="note">No offers were made.</p>',
        { lg: agentLegend, table: dataTable(["Agent", "Offer", "n", "Own value"], lines.flatMap((l) => l.points.map((p) => [l.label, p.x, p.n, fmt(p.y)]))) }),
      heatmap: chartCard("What they value vs. what they ask for", "For each resource: how much the agent privately values it (ground truth, share of its 100 points) and how much of it the agent asked for in its first offer (observed). Darker is more.",
        heatmap(rows, res.map((c) => ({ id: c, label: c })), cell, { label: "Private values and opening asks by resource" }), { wide: true }),
      network: chartCard("Who ends up with what", "Lines join each agent to each resource; thicker lines mean the agent kept more of it in agreed deals. Hover a line for the exact share.",
        network(keys.map((k) => ({ key: k, label: names[k], color: col(k) })), res.map((c) => ({ id: c, label: c })), edges),
        { lg: agentLegend, table: dataTable(["Agent", "Resource", "Mean final share", "n"], edges.map((e) => [names[e.agent], e.resource, pct(e.w), a.agents[e.agent].resources[e.resource]?.n_final ?? 0])) }),
    };
  }

  function analyticsPanel(doc) {
    const a = doc.analytics, f = state.af;
    if (!a.n_completed) return empty("Nothing to analyse yet", "Analytics appear once at least one negotiation has an outcome. Nothing is estimated in the meantime.");
    const names = Object.fromEntries(Object.entries(doc.config.agents).map(([k, d]) => [k, d.name]));
    const trials = doc.trials.filter((t) => (f.condition === "all" || t.condition === f.condition) && (f.outcome === "all" || t.outcome === f.outcome));
    const views = [...sbx.analysisViews(a.sandbox), ["outcomes", "Outcomes"], ["agents", "Agents"], ["behaviour", "Claims"], ["statistics", "Statistics"], ["observations", "Observations"]];
    if (!views.some(([k]) => k === f.view)) f.view = "profile";
    const chip = (k, label, on) => `<button type="button" class="chip-f" data-af="${k}" aria-pressed="${on}">${label}</button>`;
    const filters = `<div class="filterbar" role="group" aria-label="Filter negotiations">${chip("condition:all", "All conditions", f.condition === "all")}${doc.config.conditions.map((c) => chip(`condition:${c}`, COND_NAME[c], f.condition === c)).join("")}
      ${f.outcome !== "all" ? `<button type="button" class="chip-f on" data-af="outcome:all" aria-pressed="true">${esc(OUTCOME_LABEL[f.outcome])} only ${icon("x")}</button>` : ""}<span class="sub">${completedTrials(trials).length} of ${a.n_completed} negotiations</span></div>`;
    let body = "";
    if (["profile", "compare", "reaction"].includes(f.view)) body = sbx.analysisBody(f.view, doc, names);
    else if (f.view === "outcomes") {
      const c = outcomeCharts(doc, trials, names);
      body = `${filters}${kpis(summarize(trials))}<p class="note">Descriptive: these summarise what happened in this experiment and test nothing.</p>
        <div class="cgrid">${c.donut}${c.box}${c.scatter}${c.rounds}</div>
        <details class="more morecharts"><summary>More charts (3)</summary><div class="cgrid">${c.area}${c.sankey}${c.timeline}</div></details>`;
    } else if (f.view === "agents") {
      const c = agentCharts(a, names);
      body = `${notice("info", "info", "Agent measures use every negotiation in this experiment; the condition filter does not apply here. Rule-based agents follow fixed rules: differences come from the situations they met, not from learning.")}
        <div class="cgrid">${c.radar}${c.concession}${c.heatmap}${c.network}</div>
        <section class="block">${condTable(a)}</section>
        <section class="block"><div class="block-head"><h2>Agent by agent</h2></div><div class="grid g2">${Object.entries(a.agents).map(([k, p]) => profile(k, p, names)).join("")}</div></section>`;
    } else if (f.view === "behaviour") body = behaviourSection(a, names);
    else if (f.view === "statistics") body = inferenceSection(a.inference);
    else body = `<section class="block" style="margin-top:0"><div class="block-head"><h2>What the data shows</h2><span class="note">Generated from the records. Each says what it is based on and links to the negotiations behind it; none guesses why an agent acted.</span></div>
      <ul class="wb-obs">${a.observations.map((o) => `<li><span class="badge">${esc(o.basis)}</span><p>${esc(o.text)}</p>${obsEvidence(o)}</li>`).join("")}</ul>
      ${a.not_available.length ? `<h3 class="wb-h4">Not measured here</h3><ul class="plainlist">${a.not_available.map((x) => `<li class="note">${esc(x)}</li>`).join("")}</ul>` : ""}</section>`;
    return `${doc.status === "running" ? notice("info", "await", `<b>Still running.</b> ${a.n_completed} of ${doc.plan_size} negotiations so far; these values change until it finishes.`) : ""}
      ${a.not_outcomes && Object.keys(a.not_outcomes).length ? notice("warn", "alert", `${Object.entries(a.not_outcomes).map(([k, v]) => `${v} ${esc(k)}`).join(", ")} negotiation(s) are not outcomes and are excluded.`) : ""}
      <div class="seg viewseg" role="tablist" aria-label="Analysis view">${views.map(([k, t]) => `<button type="button" role="tab" data-view="${k}" aria-selected="${f.view === k}" aria-pressed="${f.view === k}">${t}</button>`).join("")}</div>
      <div id="aview">${body}</div>`;
  }
  const obsEvidence = (o) => (o.evidence.length ? `<details class="more"><summary>Evidence: ${o.n > o.evidence.length ? `first ${o.evidence.length} of ${o.n}` : `${o.evidence.length}`} negotiation${o.evidence.length === 1 ? "" : "s"}</summary><p>${o.evidence.map((e) => `<button type="button" class="btn-link" data-goto="${e.trial}">#${e.trial + 1}${e.turn ? `, turn ${e.turn}` : ""}</button>`).join(" ")}</p></details>` : `<span class="sub">n=${o.n}</span>`);

  const condTable = (a) => `<div class="block-head"><h2>By condition</h2><span class="note">Value created and fairness count failures as 0; deal quality, evenness and envy-free use agreed negotiations only.</span></div>
    <div class="table-wrap"><table><thead><tr><th>Condition</th><th class="num">n</th><th class="num">Deals reached</th><th class="num">Value created (RWE)</th><th class="num">Fairness (EW)</th><th class="num">Deal quality</th><th class="num">Evenness</th><th class="num">Envy-free</th><th class="num">Invalid</th><th class="num">Turns</th></tr></thead><tbody>
    ${Object.entries(a.by_condition).map(([c, v]) => `<tr><td>${COND_NAME[c] ?? esc(c)}</td><td class="num">${v.n}</td><td class="num">${pct(v.agreement_rate)}</td><td class="num">${fmt(v.rwe)}</td><td class="num">${fmt(v.ew)}</td>
      <td class="num">${fmt(v.swe)} <span class="sub">n=${v.n_agreed}</span></td><td class="num">${fmt(v.equitability)}</td><td class="num">${pct(v.envy_free_rate)}</td><td class="num">${pct(v.invalid_rate)}</td><td class="num">${fmt(v.mean_rounds, 1)}</td></tr>`).join("")}</tbody></table></div>`;

  const profile = (k, p, names) => `<div class="sheet wb-agent" style="--c:${AGENT_COLOR[k]}"><h3><i class="wb-dot"></i>${esc(names[k])}</h3>
    <dl class="kvs"><dt>Points won</dt><dd>${fmt(p.utility_mean)} <span class="sub">median ${fmt(p.utility_median)}, share of its 100</span></dd>
      <dt>Opening demand</dt><dd>${fmt(p.opening_demand_mean)} <span class="sub">value of its first offer to itself</span></dd>
      <dt>Total concession</dt><dd>${fmt(p.total_concession_mean)} <span class="sub">first to last offer</span></dd>
      <dt>Concedes</dt><dd>${pct(p.concession_frequency_mean)} <span class="sub">of consecutive offers</span></dd>
      <dt>Offers per negotiation</dt><dd>${fmt(p.offers_mean, 1)}</dd>
      <dt>Accepted · walked · invalid</dt><dd>${p.accepted.length} · ${p.walked_away.length} · ${p.invalid.length} <span class="sub">of ${p.negotiations}</span></dd>
      <dt>Asks for priorities</dt><dd>${pct(p.priority_alignment)} <span class="sub">resource pairs where it claimed strictly more of the one it values more${p.priority_ties == null ? "" : `; ${pct(p.priority_ties)} equal`}</span></dd></dl></div>`;

  const num = (v, d = 3) => fmt(v, d);
  function behaviourSection(a, names) {
    if (!a.behaviour || !Object.keys(a.behaviour).length) return '<p class="note">No behavioural measures for this experiment.</p>';
    const panel = (k, b) => {
      const sc = b.scarcity, vs = b.value_split, roles = b.by_role;
      const roleRows = [["Opened", roles.opens], ["Responded", roles.responds], ["Seat A", roles.seat_A], ["Seat B", roles.seat_B]]
        .map(([t, r]) => `<tr><td>${t}</td><td class="num">${r.n}</td><td class="num">${pct(r.agreement_rate)}</td><td class="num">${num(r.utility_mean)}</td></tr>`).join("");
      const condRows = Object.entries(b.by_condition).map(([c, r]) => `<tr><td>${COND_NAME[c] ?? esc(c)}</td><td class="num">${r.n}</td><td class="num">${num(r.opening_demand_mean)}</td><td class="num">${num(r.total_concession_mean)} <span class="sub">n=${r.n_concession}</span></td><td class="num">${num(r.utility_mean)}</td></tr>`).join("");
      return `<div class="sheet wb-agent" style="--c:${AGENT_COLOR[k]}"><h3><i class="wb-dot"></i>${esc(names[k])}</h3>
        <h4 class="wb-h4">Asks for what it values <span class="sub">observed vs. ground truth</span></h4><p class="note">${esc(vs.definition)}</p>
        ${vs.difference == null ? '<p class="note">Not measurable: no opening offers with unequal private points.</p>'
          : `<dl class="kvs"><dt>Resources it values most</dt><dd>${pct(vs.high_value_claim)} asked for</dd><dt>The rest</dt><dd>${pct(vs.low_value_claim)} asked for</dd><dt>Difference</dt><dd>${vs.difference >= 0 ? "+" : ""}${fmt(vs.difference, 3)} <span class="sub">n = ${vs.n_negotiations}</span></dd></dl>`}
        <h4 class="wb-h4">Scarcity <span class="sub">derived</span></h4><p class="note">${esc(sc.definition)}</p>
        ${sc.measurable ? `<div class="table-wrap"><table><thead><tr><th>Quantity tercile</th><th class="num">Units</th><th class="num">n</th><th class="num">Opening ask</th></tr></thead><tbody>
          ${Object.entries(sc.bins).map(([bin, v]) => `<tr><td>${bin}</td><td class="num">${v.quantity_range ? `${v.quantity_range[0]}–${v.quantity_range[1]}` : "—"}</td><td class="num">${v.n}</td><td class="num">${pct(v.opening_claim_mean)}</td></tr>`).join("")}
          </tbody></table></div><p class="note">Spearman (quantity, ask): ${sc.spearman_quantity_vs_claim == null ? "undefined (no variation)" : fmt(sc.spearman_quantity_vs_claim, 2)}, n = ${sc.n}.</p>` : `<p class="note">${esc(sc.reason)}</p>`}
        <h4 class="wb-h4">Position <span class="sub">observed; points won as a share of 100, failures 0</span></h4>
        <div class="table-wrap"><table><thead><tr><th>Role</th><th class="num">n</th><th class="num">Deals reached</th><th class="num">Points won</th></tr></thead><tbody>${roleRows}</tbody></table></div>
        <h4 class="wb-h4">By condition <span class="sub">observed</span></h4>
        <div class="table-wrap"><table><thead><tr><th>Condition</th><th class="num">n</th><th class="num">Opening demand</th><th class="num">Concession</th><th class="num">Points won</th></tr></thead><tbody>${condRows}</tbody></table></div></div>`;
    };
    return `<p class="note" style="margin:0 0 14px">Each measure states its definition and n. Ground truth is the private points each agent was given; observed is what it did; derived is a calculation over both. Motives are never inferred.</p>
      <div class="grid g2">${Object.entries(a.behaviour).map(([k, b]) => panel(k, b)).join("")}</div>`;
  }

  function inferenceSection(inf) {
    if (!inf) return '<p class="note">No statistical tests for this experiment.</p>';
    const head = `<div class="block-head"><h2>Exploratory statistics</h2><span class="note">The study's preregistered procedures (spec §8.9) applied to this experiment. Exploratory, never the confirmatory analysis.</span></div>`;
    if (!inf.available) return `${head}<p class="note">${esc(inf.reason)}</p>`;
    const r = inf.result, pv = (x) => (x == null ? "—" : x < 0.0001 ? "< 0.0001" : x.toFixed(4));
    const rows = Object.entries(r.primary).map(([hk, x]) => `<tr><td>${hk}</td><td>${esc(x.label)}</td><td class="num">${x.n_pairs}</td><td class="num">${x.mean_difference == null ? "—" : fmt(x.mean_difference, 4)}</td>
      <td class="num">${x.bootstrap.defined ? `[${fmt(x.bootstrap.low, 4)}, ${fmt(x.bootstrap.high, 4)}]` : "—"}</td><td class="num">${pv(x.permutation.p)}</td><td class="num">${pv(x.permutation.p_holm)}</td>
      <td>${!x.permutation.defined ? "Not testable" : x.permutation.reject ? "Significant (Holm)" : "Not significant (Holm)"}</td><td class="num">${x.wilcoxon.defined ? pv(x.wilcoxon.p) : `<span class="sub">${esc(x.wilcoxon.reason)}</span>`}</td></tr>`).join("");
    return `${head}${notice("warn", "alert", `<b>Scope.</b> ${esc(inf.scope)}`)}
      <div class="table-wrap"><table><thead><tr><th>Hypothesis</th><th>Measure</th><th class="num">Instance pairs</th><th class="num">Mean d</th><th class="num">95% bootstrap CI</th><th class="num">Permutation p</th><th class="num">Holm p</th><th>Decision at α = ${r.alpha}</th><th class="num">Wilcoxon p</th></tr></thead><tbody>${rows}</tbody></table></div>
      <p class="note" style="margin-top:10px">d = structured − baseline per instance; ${r.permutations.toLocaleString()} sign flips and ${r.bootstrap_resamples.toLocaleString()} bootstrap resamples, seed ${r.seed}. ${Object.values(r.primary).some((x) => x.unpaired.length) ? "Some instances lacked a condition and were not paired." : "Every instance was paired."}</p>`;
  }

  function exportPanel(doc) {
    const ext = (f) => f.split(".").pop().toUpperCase();
    return `<p class="note">Every file is built by the server from this experiment's stored records and carries its data label. For a printable summary with charts, open the <a href="#/report/${doc.id}">report</a> and save it as PDF.</p>
      <ul class="ilist">${Object.entries(state.catalog.exports).map(([f, what]) => `<li><div class="irow"><span class="filetag">${ext(f)}</span><span class="imain"><b><code>${esc(f)}</code></b><span class="sub">${esc(what)}</span></span>
        <button type="button" class="btn" data-export="${f}">${icon("download")}Download</button></div></li>`).join("")}</ul>${errorLine()}`;
  }

  function wireSimulation(id) {
    const panel = main.querySelector("#simpanel");
    if (!panel) return;
    h.field.busy(state.doc.status === "running"); onCleanup(() => h.field.busy(false));
    const paint = () => { panel.innerHTML = simPanel(state.doc); wirePanel(); fade([panel]); };
    const setTab = (k) => { state.simTab = k; main.querySelectorAll("[data-simtab]").forEach((x) => (x.dataset.simtab === k ? x.setAttribute("aria-current", "page") : x.removeAttribute("aria-current"))); paint(); };
    const openTrial = (i) => { state.openTrial = i; setTab("negotiations"); main.querySelector(`#trial-${i}`)?.scrollIntoView({ block: "center" }); main.querySelector(`#trial-${i}`)?.focus({ preventScroll: true }); };
    main.querySelectorAll("[data-simtab]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); setTab(a.dataset.simtab); }));
    function wirePanel() {
      panel.querySelectorAll("[data-simtab-go]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.simtabGo)));
      panel.querySelector("[data-findings]")?.addEventListener("click", () => { state.af.view = "observations"; setTab("analytics"); });
      panel.querySelector("#cancel-sim")?.addEventListener("click", async (e) => {
        const b = e.currentTarget; b.disabled = true; b.textContent = "Stopping…";
        const res = await post(`/api/workbench/simulations/${id}/cancel`, {});
        if (!res.ok) { b.disabled = false; b.textContent = res.data.error ?? "Could not stop"; }
      });
      const toggle = (row) => { const i = +row.dataset.trial; state.openTrial = state.openTrial === i ? null : i; paint(); main.querySelector(`#trial-${i}`)?.focus(); };
      panel.querySelectorAll("tr[data-trial]").forEach((row) => { row.addEventListener("click", () => toggle(row)); row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(row); } }); });
      panel.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => openTrial(+b.dataset.goto)));
      panel.querySelectorAll("svg [data-trial]").forEach((m) => { m.style.cursor = "pointer"; m.addEventListener("click", () => openTrial(+m.closest("[data-trial]").dataset.trial)); m.addEventListener("keydown", (e) => { if (e.key === "Enter") openTrial(+m.dataset.trial); }); });
      panel.querySelectorAll("[data-filter-outcome]").forEach((m) => { m.style.cursor = "pointer"; const go = () => { state.af.outcome = state.af.outcome === m.dataset.filterOutcome ? "all" : m.dataset.filterOutcome; paint(); }; m.addEventListener("click", go); m.addEventListener("keydown", (e) => { if (e.key === "Enter") go(); }); });
      panel.querySelectorAll("[data-af]").forEach((b) => b.addEventListener("click", () => { const [k, v] = b.dataset.af.split(":"); state.af[k] = v; paint(); }));
      panel.querySelectorAll("[data-view]").forEach((b) => b.addEventListener("click", () => { state.af.view = b.dataset.view; paint(); }));
      panel.querySelectorAll("[data-export]").forEach((b) => b.addEventListener("click", async () => {
        try { download(await getJSON(`/api/workbench/simulations/${id}/export/${b.dataset.export}`)); } catch (err) { showError({ data: { error: `Export failed: ${err.message}` } }); }
      }));
      h.animateCharts(panel);
    }
    wirePanel();
    // Keep relative times ("started 2 min ago") current while the page is open.
    const ago = setInterval(() => main.querySelectorAll("time[data-ago]").forEach((t) => (t.textContent = timeAgo(+t.dataset.ago))), 30000);
    onCleanup(() => clearInterval(ago));
    if (state.doc.status !== "running") return;
    // Poll while it runs; the view only ever shows what the engine reported.
    let seen = state.doc.live?.turns.length ?? 0, index = state.doc.live?.index;
    const timer = setInterval(async () => {
      let doc;
      try { doc = await getJSON(`/api/workbench/simulations/${id}`); } catch { return; }
      const prev = state.doc; state.doc = doc;
      const bar = main.querySelector(".wb-progress");
      bar.querySelector("i").style.transform = `scaleX(${doc.trials.length / doc.plan_size})`; bar.setAttribute("aria-valuenow", doc.trials.length);
      const tab = main.querySelector('[data-simtab="negotiations"]');
      if (tab) tab.textContent = `Negotiations (${doc.trials.length})`;
      const s = summarize(doc.trials);
      main.querySelector("#progress-text").textContent = `${doc.trials.length} of ${doc.plan_size} negotiations · ${elapsed(doc)}${s.n ? ` · ${pct(s.agreement_rate)} agreed` : ""}`;
      const keynums = main.querySelector("#keynums"); if (keynums) keynums.outerHTML = keyNumbers(doc);
      if (doc.status !== "running") { clearInterval(timer); h.field.refresh(); render(false).then(() => fade([...main.querySelectorAll(".answer > *, .nextsteps")])); return; }
      if (state.simTab !== "live") { if (doc.trials.length !== prev.trials.length && !["export", "analytics"].includes(state.simTab)) paint(); return; }
      const list = main.querySelector("#live-turns");
      if (!list || doc.live?.index !== index) { index = doc.live?.index; paint(); seen = doc.live?.turns.length ?? 0; return; }
      const fresh = doc.live.turns.slice(seen);
      if (fresh.length) { list.insertAdjacentHTML("beforeend", fresh.map((t) => turnRow(t, doc, doc.live.spec.seats)).join("")); fade([...list.children].slice(-fresh.length)); seen += fresh.length; }
      const th = doc.live.thinking;
      main.querySelector("#live-thinking").innerHTML = th ? `<i></i>${esc(seatName(doc, doc.live.spec.seats, th.seat))} is deciding turn ${th.round}…` : "";
      main.querySelector("#live-stats").outerHTML = liveStats(doc);
      if (doc.trials.length !== prev.trials.length) { main.querySelector(".otape").outerHTML = tape(doc); main.querySelector(".otape button:last-of-type")?.classList.add("pop"); main.querySelector(".ot-legend").outerHTML = tapeLegend(doc); main.querySelectorAll(".otape [data-goto]").forEach((b) => b.addEventListener("click", () => openTrial(+b.dataset.goto))); }
    }, 400);
    onCleanup(() => clearInterval(timer));
  }

  // ------------------------------------------------------------ wiring
  function wire(name, arg) {
    if (name === "agents") wireAgents(arg);
    if (name === "environments") wireEnvironments(arg);
    if (name === "simulations" && arg?.startsWith("new")) wireWizard();
    if (name === "simulation") wireSimulation(arg);
    sbx.wire(name);
  }

  return {
    routes: { agents, environments, simulations, simulation, ...sbx.routes },
    names: ["agents", "environments", "simulations", "simulation", ...sbx.names],
    wire,
    shared: { getJSON, post, badge, statusBadge, top, backLink, section, empty, chartCard, kpis, outcomeCharts, agentCharts, condTable, experimentList, kindName, download, catalog, quickStart, runBadge, CAT, when, sbx },
  };
}
