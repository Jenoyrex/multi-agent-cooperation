// Workbench: agent library, environment builder, simulations, live workspace,
// behavioural analytics and export. Every value shown comes from /api/workbench/*;
// nothing is estimated in the browser except the size of a design before it starts.
import { esc, fmt, OUTCOME_LABEL, pct, timeAgo } from "./lib.js";
import { dataTable, dotPlot, figure, legend, stackBars } from "./charts.js";

const OUT_COLOR = { agreed: "var(--o-agreed)", walked_away: "var(--o-walked)", timeout: "var(--o-timeout)", invalid_action: "var(--o-invalid)" };
const OUT_INK = { agreed: "var(--ground)", walked_away: "var(--ink)", timeout: "var(--ink)", invalid_action: "var(--ground)" };
const AGENT_COLOR = { X: "var(--base)", Y: "var(--struct)" };
const STATUS = { running: ["", "await", "Running"], completed: ["good", "check", "Completed"], cancelled: ["warn", "alert", "Cancelled"],
  failed: ["crit", "x", "Failed"], interrupted: ["warn", "alert", "Interrupted"] };
const CAT_LABEL = { mock: "Rule-based", python: "Python", http: "Local HTTP", provider: "Provider" };

export function createWorkbench({ main, h, render, onCleanup, motion }) {
  const { icon, head, blockHead, notice, readout } = h;
  const state = { catalog: null, agentDraft: null, envDraft: null, envTab: "visual", importFmt: "text", importText: "",
    simDraft: null, simTab: "live", openTrial: null };

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
  const runnable = (a) => (a.executable ? badge("good", "ready", "Runs at $0") : a.kind === "gemini" ? badge("", "x", "Not implemented") : badge("warn", "lock", "Locked: zero-cost"));
  const subnav = (active) => `<nav class="subnav" aria-label="Workbench"><a href="#/workbench"${active === "workbench" ? ' aria-current="page"' : ""}>Overview</a>
    <a href="#/agents"${active === "agents" ? ' aria-current="page"' : ""}>Agents</a><a href="#/environments"${active === "environments" ? ' aria-current="page"' : ""}>Environments</a>
    <a href="#/simulations"${active === "simulations" ? ' aria-current="page"' : ""}>Simulations</a></nav>`;
  const zeroCost = () => notice("info", "lock", "<b>Zero-cost mode.</b> Rule-based and Python agents run on this machine at no cost. Claude, GPT and Gemini can be configured here but never run: real provider calls cost money, and the server refuses them before anything is sent.");
  const when = (t) => (t ? timeAgo(t) : "—");
  const errorLine = (msg, field) => `<p class="step-error" id="wb-error" role="alert"${msg ? "" : " hidden"}>${msg ? esc(msg) : ""}${field ? ` <span class="sub">(${esc(field)})</span>` : ""}</p>`;
  function showError(res, fallback, focus = true) {
    const el = main.querySelector("#wb-error");
    const msg = res.data?.error ?? fallback;
    if (el) { el.innerHTML = `${esc(msg)}${res.data?.field ? ` <span class="sub">(${esc(res.data.field)})</span>` : ""}`; el.hidden = false; }
    main.querySelectorAll("[aria-invalid]").forEach((x) => x.removeAttribute("aria-invalid"));
    const f = res.data?.field && main.querySelector(`[data-field="${CSS.escape(res.data.field)}"]`);
    if (f) { f.setAttribute("aria-invalid", "true"); if (focus) f.focus(); }
  }
  const fade = (els) => { if (motion.on() && els?.length) motion.M.animate(els, { opacity: [0, 1], transform: ["translateY(6px)", "none"] }, { duration: 0.35, ease: motion.EASE, delay: motion.M.stagger(0.03) }); };
  function download(file) {
    const url = URL.createObjectURL(new Blob([file.content], { type: file.content_type }));
    const a = Object.assign(document.createElement("a"), { href: url, download: file.filename });
    document.body.append(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }
  const unavailable = (msg) => `${head("Workbench unavailable", esc(msg))}<a class="cta" href="#/">Home${icon("chev-r")}</a>`;

  // ------------------------------------------------------------ overview
  async function overview() {
    let o;
    try { o = await getJSON("/api/workbench/overview"); state.catalog = o.catalog; } catch (e) { return unavailable(`The server did not answer (${e.message}).`); }
    const kinds = o.catalog.kinds;
    const rows = o.simulations.map((s) => `<tr class="link" data-href="#/simulation/${s.id}"><td><a href="#/simulation/${s.id}">${esc(s.name)}</a><div class="sub">${esc(s.environment)}</div></td>
      <td>${statusBadge(s.status)}</td><td class="num">${s.done}/${s.planned}</td><td class="num">${s.agreement_rate == null ? "—" : pct(s.agreement_rate)}</td><td>${when(s.created)}</td></tr>`).join("");
    return `${subnav("workbench")}${head("Workbench", "Configure agents, define the resources they share, run controlled simulations and study how each agent behaves. Every run uses the same negotiation engine as the research experiment, unchanged.")}
      ${zeroCost()}
      <dl class="readouts">${readout("Agents in the library", `${o.agents}`, "including built-ins")}${readout("Environments", `${o.environments}`, "including templates")}
        ${readout("Simulations", `${o.simulation_count}`, o.running ? "one running now" : "none running")}${readout("Runnable agent types", `${kinds.filter((k) => k.executable).length} of ${kinds.length}`, "providers are locked")}</dl>
      <div class="actions"><a class="cta" href="#/simulations/new">New simulation${icon("chev-r")}</a><a class="btn" href="#/agents/new">Add an agent</a><a class="btn" href="#/environments/new">Build an environment</a></div>
      <section class="block">${blockHead("Recent simulations", "Newest first. Open one to follow it live or read its analysis.")}
        ${rows ? `<div class="table-wrap"><table><thead><tr><th>Simulation</th><th>Status</th><th class="num">Negotiations</th><th class="num">Agreement</th><th>Started</th></tr></thead><tbody>${rows}</tbody></table></div>`
          : `<div class="empty"><h3>No simulations yet</h3><p>Start with a built-in pair: the Priority trader against the Steady conceder on the Field hospital supplies environment takes a few seconds.</p><a class="cta" href="#/simulations/new">Set up a simulation${icon("chev-r")}</a></div>`}</section>
      <section class="block">${blockHead("What runs here", "Capabilities on this server, as the server reports them.")}
        <div class="table-wrap"><table><thead><tr><th>Agent type</th><th>Category</th><th>Status</th><th>Reads instructions</th><th>Deterministic</th></tr></thead><tbody>
        ${kinds.map((k) => `<tr><td>${esc(k.label)}</td><td>${CAT_LABEL[k.category]}</td><td>${runnable({ ...k, kind: k.id })}</td><td>${k.reads_instructions ? "Yes" : "No"}</td><td>${k.deterministic ? "Yes" : "No"}</td></tr>`).join("")}
        </tbody></table></div><p class="note" style="margin-top:10px">Imports: JSON and a plain-text format. YAML and Python scenario modules are not supported: this server does not execute uploaded code.</p></section>`;
  }

  // ------------------------------------------------------------ agents
  async function agents(_s, arg) {
    try { await catalog(); } catch (e) { return unavailable(e.message); }
    if (arg) return agentEditor(arg);
    const list = await getJSON("/api/workbench/agents");
    return `${subnav("agents")}${head("Agent library", "Every agent the workbench knows. Rule-based and Python agents run at no cost; provider agents can be configured but are locked. Built-ins are read-only: duplicate one to adjust it.")}
      <div class="listbar"><p class="count note">${list.length} agents</p><a class="cta" href="#/agents/new">New agent${icon("chev-r")}</a></div>
      <div class="table-wrap"><table class="wb-list"><thead><tr><th>Agent</th><th>Type</th><th>Status</th><th class="num">Simulations</th><th><span class="vh">Actions</span></th></tr></thead><tbody>
      ${list.map((a) => `<tr><td><a href="#/agents/${a.id}">${esc(a.name)}</a>${a.builtin ? ' <span class="sub">built-in</span>' : ""}<div class="sub">${esc(a.notes || "")}</div></td>
        <td>${esc(a.kind_label)}<div class="sub">${CAT_LABEL[a.category]}</div></td><td>${runnable(a)}</td><td class="num">${a.history.length}</td>
        <td class="wb-rowacts"><button type="button" class="btn" data-dup="${a.id}">Duplicate</button>${a.builtin ? "" : `<button type="button" class="btn" data-del="${a.id}" data-name="${esc(a.name)}">Delete</button>`}</td></tr>`).join("")}
      </tbody></table></div>${errorLine()}`;
  }

  async function agentEditor(arg) {
    let saved = null;
    if (arg !== "new") { try { saved = await getJSON(`/api/workbench/agents/${encodeURIComponent(arg)}`); } catch { return `${subnav("agents")}${head("Agent not found", "No agent has this id.")}<a class="btn" href="#/agents">${icon("chev-l")}Agent library</a>`; } }
    const d = state.agentDraft && state.agentDraft.for === arg ? state.agentDraft
      : (state.agentDraft = saved ? { for: arg, name: saved.name, kind: saved.kind, notes: saved.notes, params: { ...saved.params } } : { for: arg, name: "", kind: "priority", notes: "", params: {} });
    const kind = kindOf(d.kind), ro = Boolean(saved?.builtin);
    const field = (p) => {
      const v = d.params[p.name] ?? p.default;
      const input = p.type === "choice" ? `<select data-param="${p.name}" data-field="params.${p.name}"${ro ? " disabled" : ""}>${p.choices.map((c) => `<option${c === v ? " selected" : ""}>${esc(c)}</option>`).join("")}</select>`
        : `<input type="${p.type === "number" ? "number" : "text"}" ${p.type === "number" ? `step="0.01" min="${p.min}" max="${p.max}"` : 'spellcheck="false"'} value="${esc(v)}" data-param="${p.name}" data-field="params.${p.name}"${ro ? " disabled" : ""}>`;
      return `<label class="field"><span>${esc(p.label)}</span>${input}<small class="note">${esc(p.help)}</small></label>`;
    };
    return `${subnav("agents")}<a class="back" href="#/agents">${icon("chev-l")}Agent library</a>
      ${head(saved ? saved.name : "New agent", ro ? "A built-in agent. Duplicate it to change its parameters." : "Choose a type, then set its parameters. The server validates everything again when you save.")}
      <div class="wb-split"><form class="stepcard" id="agent-form" novalidate>
        <label class="field"><span>Name</span><input type="text" maxlength="60" value="${esc(d.name)}" data-k="name" data-field="name" required${ro ? " disabled" : ""}></label>
        <label class="field"><span>Type</span><select data-k="kind" data-field="kind"${ro || saved ? " disabled" : ""}>${state.catalog.kinds.map((k) => `<option value="${k.id}"${k.id === d.kind ? " selected" : ""}>${esc(k.label)}${k.executable ? "" : k.id === "gemini" ? " — not implemented" : " — locked"}</option>`).join("")}</select>
          ${saved && !ro ? '<small class="note">The type of a saved agent is fixed; duplicate it to change type.</small>' : ""}</label>
        ${kind.executable ? "" : notice("warn", "lock", `<b>This agent cannot run.</b> ${esc(kind.reason)}`)}
        <p class="note">${esc(kind.summary)}</p>
        <div class="wb-params">${kind.params.map(field).join("")}</div>
        <label class="field"><span>Role and notes</span><textarea rows="3" maxlength="500" data-k="notes" data-field="notes"${ro ? " disabled" : ""}>${esc(d.notes)}</textarea>
          <small class="note">${kind.reads_instructions ? "Each turn sends this agent the frozen instruction strategy of the simulation's condition; custom system prompts are not sent." : "This agent does not read instructions; notes are for you."}</small></label>
        ${errorLine()}
        <div class="actions">${ro ? `<button type="button" class="cta" data-dup="${saved.id}">Duplicate to edit${icon("chev-r")}</button>`
          : `<button type="submit" class="cta">${saved ? "Save changes" : "Create agent"}${icon("check")}</button><a class="btn" href="#/agents">Cancel</a>`}
          ${saved && !ro ? `<button type="button" class="btn" data-del="${saved.id}" data-name="${esc(saved.name)}">Delete</button>` : ""}</div>
      </form>
      <aside class="panel wb-aside"><h2 class="wb-h3">Capabilities</h2><dl class="wb-dl">
        <dt>Category</dt><dd>${CAT_LABEL[kind.category]}</dd><dt>Actions</dt><dd>${kind.actions.join(" · ")}</dd>
        <dt>Deterministic</dt><dd>${kind.deterministic ? "Yes: the same inputs give the same moves" : "No"}</dd><dt>Cost</dt><dd>${kind.cost === "free" ? "Free, runs locally" : "Paid provider (never called here)"}</dd></dl>
        ${saved ? `<h2 class="wb-h3">Simulation history</h2>${saved.history.length ? `<ul class="plainlist">${saved.history.map((s) => `<li><a href="#/simulation/${s.id}">${esc(s.name)}</a> <span class="sub">${esc(s.status)} · ${when(s.created)}</span></li>`).join("")}</ul>` : '<p class="note">Not used in a simulation yet.</p>'}` : ""}
      </aside></div>`;
  }

  function wireAgents(arg) {
    main.querySelectorAll("[data-dup]").forEach((b) => b.addEventListener("click", async () => {
      const a = await getJSON(`/api/workbench/agents/${b.dataset.dup}`);
      const res = await post("/api/workbench/agents", { name: `${a.name} (copy)`.slice(0, 60), kind: a.kind, params: a.params, notes: a.notes });
      if (res.ok) { state.agentDraft = null; location.hash = `#/agents/${res.data.id}`; } else showError(res, "Could not duplicate the agent.");
    }));
    main.querySelectorAll("[data-del]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = `Confirm delete`; b.classList.add("danger"); return; } // two-step, no modal
      const res = await post(`/api/workbench/agents/${b.dataset.del}/delete`, {});
      if (res.ok) { state.agentDraft = null; location.hash = "#/agents"; render(false); } else showError(res, "Could not delete the agent.");
    }));
    const form = main.querySelector("#agent-form");
    if (!form) return;
    const d = state.agentDraft;
    form.querySelectorAll("[data-k]").forEach((el) => el.addEventListener(el.tagName === "SELECT" ? "change" : "input", () => {
      d[el.dataset.k] = el.value;
      if (el.dataset.k === "kind") { d.params = {}; render(false); }
    }));
    form.querySelectorAll("[data-param]").forEach((el) => el.addEventListener("input", () => { d.params[el.dataset.param] = el.type === "number" ? (el.value === "" ? null : Number(el.value)) : el.value; }));
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const body = { name: d.name, kind: d.kind, notes: d.notes, params: d.params, ...(arg !== "new" ? { id: arg } : {}) };
      const res = await post("/api/workbench/agents", body);
      if (res.ok) { state.agentDraft = null; location.hash = `#/agents/${res.data.id}`; if (arg === res.data.id) render(false); } else showError(res, "The agent could not be saved.");
    });
  }

  // ------------------------------------------------------------ environments
  async function environments(_s, arg) {
    try { await catalog(); } catch (e) { return unavailable(e.message); }
    if (arg) return envEditor(arg);
    const list = await getJSON("/api/workbench/environments");
    return `${subnav("environments")}${head("Environments", "The resources two agents divide, their quantities, each agent's private points and the round limit. Templates are read-only; duplicate one, or build your own.")}
      <div class="listbar"><p class="count note">${list.length} environments</p><a class="cta" href="#/environments/new">New environment${icon("chev-r")}</a></div>
      <div class="table-wrap"><table class="wb-list"><thead><tr><th>Environment</th><th>Resources</th><th class="num">Rounds</th><th class="num">Simulations</th><th><span class="vh">Actions</span></th></tr></thead><tbody>
      ${list.map((e) => `<tr><td><a href="#/environments/${e.id}">${esc(e.name)}</a>${e.builtin ? ' <span class="sub">template</span>' : ""}<div class="sub wb-clip">${esc(e.description || "")}</div></td>
        <td>${e.source === "generated" ? `${e.categories} drawn from each seed` : Object.entries(e.resources).map(([c, q]) => `${esc(c)} ${q}`).join(" · ")}</td>
        <td class="num">${e.max_rounds}</td><td class="num">${e.history.length}</td>
        <td class="wb-rowacts"><button type="button" class="btn" data-envdup="${e.id}">Duplicate</button>${e.builtin ? "" : `<button type="button" class="btn" data-envdel="${e.id}">Delete</button>`}</td></tr>`).join("")}
      </tbody></table></div>${errorLine()}`;
  }

  const blankEnv = () => ({ name: "", description: "", source: "custom", categories: 3, max_rounds: 10, first_mover: "A",
    rows: [{ name: "food", qty: 20, a: 50, b: 20 }, { name: "water", qty: 12, a: 30, b: 20 }, { name: "medicine", qty: 6, a: 20, b: 60 }] });
  const envToDraft = (e) => ({ name: e.name, description: e.description || "", source: e.source, categories: e.categories ?? 3, max_rounds: e.max_rounds, first_mover: e.first_mover,
    rows: e.source === "custom" ? Object.entries(e.resources).map(([c, q]) => ({ name: c, qty: q, a: e.points.A[c], b: e.points.B[c] })) : blankEnv().rows });
  const draftToEnv = (d) => d.source === "generated"
    ? { name: d.name, description: d.description, source: "generated", categories: d.categories, max_rounds: d.max_rounds, first_mover: d.first_mover }
    : { name: d.name, description: d.description, source: "custom", max_rounds: d.max_rounds, first_mover: d.first_mover,
        resources: Object.fromEntries(d.rows.map((r) => [r.name, r.qty])),
        points: { A: Object.fromEntries(d.rows.map((r) => [r.name, r.a])), B: Object.fromEntries(d.rows.map((r) => [r.name, r.b])) } };

  async function envEditor(arg) {
    let saved = null;
    if (arg !== "new") { try { saved = await getJSON(`/api/workbench/environments/${encodeURIComponent(arg)}`); } catch { return `${subnav("environments")}${head("Environment not found", "No environment has this id.")}<a class="btn" href="#/environments">${icon("chev-l")}Environments</a>`; } }
    const env = saved?.environment;
    if (!state.envDraft || state.envDraft.for !== arg) state.envDraft = { for: arg, ...(env ? envToDraft(env) : blankEnv()) };
    const d = state.envDraft, ro = Boolean(env?.builtin), lim = state.catalog.limits;
    const sumA = d.rows.reduce((t, r) => t + (Number(r.a) || 0), 0), sumB = d.rows.reduce((t, r) => t + (Number(r.b) || 0), 0);
    const dis = ro ? " disabled" : "";
    const visual = `<div class="wb-grid2">
        <label class="field"><span>Name</span><input type="text" maxlength="60" value="${esc(d.name)}" data-e="name" data-field="name"${dis}></label>
        <label class="field"><span>Round limit</span><input type="number" min="${lim.rounds[0]}" max="${lim.rounds[1]}" step="1" value="${d.max_rounds}" data-e="max_rounds" data-field="max_rounds"${dis}></label></div>
      <label class="field"><span>Description</span><textarea rows="2" maxlength="500" data-e="description" data-field="description"${dis}>${esc(d.description)}</textarea></label>
      <div class="wb-grid2"><fieldset class="choice"><legend>Resources come from</legend>
          <label class="opt"><input type="radio" name="src" value="custom"${d.source === "custom" ? " checked" : ""}${dis}><span><b>Fixed values</b><small class="sub">You set quantities and both agents' points.</small></span></label>
          <label class="opt"><input type="radio" name="src" value="generated"${d.source === "generated" ? " checked" : ""}${dis}><span><b>The research generator</b><small class="sub">Each seed draws a new instance (spec §1–§2).</small></span></label></fieldset>
        <fieldset class="choice"><legend>Opening seat</legend>${["A", "B"].map((s) => `<label class="opt"><input type="radio" name="first" value="${s}"${d.first_mover === s ? " checked" : ""}${dis}><span><b>Seat ${s} opens</b></span></label>`).join("")}
          <small class="note">A simulation can also swap this by design.</small></fieldset></div>
      ${d.source === "generated" ? `<label class="field"><span>Number of resources</span><input type="number" min="${lim.generated_resources[0]}" max="${lim.generated_resources[1]}" step="1" value="${d.categories}" data-e="categories" data-field="categories"${dis}></label>`
        : `<div class="table-wrap"><table class="wb-res"><caption class="vh">Resources and private points</caption><thead><tr><th>Resource</th><th class="num">Quantity</th><th class="num">Agent A points</th><th class="num">Agent B points</th><th><span class="vh">Remove</span></th></tr></thead><tbody>
          ${d.rows.map((r, i) => `<tr><td><input type="text" aria-label="Resource ${i + 1} name" value="${esc(r.name)}" data-r="${i}" data-c="name" data-field="resources.${esc(r.name)}"${dis}></td>
            <td class="num"><input type="number" aria-label="${esc(r.name)} quantity" min="1" max="${lim.quantity}" step="1" value="${r.qty}" data-r="${i}" data-c="qty"${dis}></td>
            <td class="num"><input type="number" aria-label="${esc(r.name)}: Agent A points" min="0" max="100" step="1" value="${r.a}" data-r="${i}" data-c="a" data-field="points.A.${esc(r.name)}"${dis}></td>
            <td class="num"><input type="number" aria-label="${esc(r.name)}: Agent B points" min="0" max="100" step="1" value="${r.b}" data-r="${i}" data-c="b" data-field="points.B.${esc(r.name)}"${dis}></td>
            <td>${ro || d.rows.length <= lim.resources[0] ? "" : `<button type="button" class="btn-x" data-rm="${i}" aria-label="Remove ${esc(r.name)}">${icon("x")}</button>`}</td></tr>`).join("")}
          </tbody><tfoot><tr><td>${ro || d.rows.length >= lim.resources[1] ? "" : '<button type="button" class="btn" data-add>Add resource</button>'}</td><td></td>
            <td class="num" data-field="points.A"><span class="wb-sum ${sumA === 100 ? "ok" : ""}" id="sum-a">${sumA} / 100</span></td><td class="num" data-field="points.B"><span class="wb-sum ${sumB === 100 ? "ok" : ""}" id="sum-b">${sumB} / 100</span></td><td></td></tr></tfoot></table></div>
          <p class="note">Each agent spreads exactly 100 private points over the resources; a resource's per-unit value is its points divided by its quantity, as in the research environment.</p>`}`;
    const importer = `<div class="seg" role="group" aria-label="Import format">${["text", "json"].map((f) => `<button type="button" data-fmt="${f}" aria-pressed="${state.importFmt === f}">${f === "text" ? "Text" : "JSON"}</button>`).join("")}<button type="button" disabled title="This server does not execute uploaded code">Python (unavailable)</button></div>
      <label class="field"><span>Scenario ${state.importFmt === "text" ? "text" : "JSON"}</span><textarea id="import-text" rows="10" spellcheck="false" class="wb-code" data-field="format">${esc(state.importText || (state.importFmt === "text"
        ? "name: Field hospital\nrounds: 10\nfirst: A\nresources: food=20, water=12, medicine=6\nA: food=50, water=30, medicine=20\nB: food=10, water=30, medicine=60\n"
        : JSON.stringify(draftToEnv(d), null, 2)))}</textarea></label>
      <div class="actions" style="margin-top:0"><button type="button" class="btn" data-validate>Validate and preview</button><button type="button" class="btn" data-load disabled>Load into the editor</button><label class="btn wb-file">Open a file…<input type="file" accept=".json,.txt,text/plain,application/json" data-file hidden></label></div>`;
    return `${subnav("environments")}<a class="back" href="#/environments">${icon("chev-l")}Environments</a>
      ${head(env ? env.name : "New environment", ro ? "A read-only template. Duplicate it to change anything." : "Define resources and private points, or import a scenario. The preview updates as you type, from the server's own validation.")}
      <div class="wb-split"><form class="stepcard" id="env-form" novalidate>
        ${ro ? "" : `<nav class="subnav" aria-label="Editor mode" style="margin:0"><a href="#/environments/${esc(arg)}" data-tab="visual"${state.envTab === "visual" ? ' aria-current="page"' : ""}>Visual editor</a><a href="#/environments/${esc(arg)}" data-tab="import"${state.envTab === "import" ? ' aria-current="page"' : ""}>Import</a></nav>`}
        ${ro || state.envTab === "visual" ? visual : importer}
        ${errorLine()}
        <div class="actions">${ro ? `<button type="button" class="cta" data-envdup="${env.id}">Duplicate to edit${icon("chev-r")}</button>` : `<button type="submit" class="cta">${env ? "Save changes" : "Save environment"}${icon("check")}</button><a class="btn" href="#/environments">Cancel</a>`}
          <a class="btn" href="#/simulations/new" data-use="${env ? env.id : ""}"${env ? "" : " hidden"}>Use in a simulation</a></div>
      </form>
      <aside class="panel wb-aside" id="env-preview" aria-live="polite"><p class="note">Preview loading…</p></aside></div>`;
  }

  function previewHtml(p) {
    const inst = p.instance, env = p.environment, res = Object.keys(inst.pool);
    const pts = (s, c) => fmt(inst.points[s][c], 0);
    return `<h2 class="wb-h3">Preview${p.seed_dependent ? ` <span class="sub">seed ${inst.seed}</span>` : ""}</h2>
      ${p.seed_dependent ? '<p class="note">Generated: each seed in a simulation draws a different instance. This is seed 0.</p>' : ""}
      <div class="table-wrap"><table><thead><tr><th>Resource</th><th class="num">Qty</th><th class="num">A pts</th><th class="num">B pts</th><th class="num">Best split A · B</th></tr></thead><tbody>
      ${res.map((c) => `<tr><td>${esc(c)}</td><td class="num">${inst.pool[c]}</td><td class="num">${pts("A", c)}</td><td class="num">${pts("B", c)}</td><td class="num">${inst.optimal_allocation.A[c]} · ${inst.optimal_allocation.B[c]}</td></tr>`).join("")}
      </tbody></table></div>
      <dl class="wb-dl"><dt>Best total value (W*)</dt><dd>${fmt(inst.optimal_welfare, 1)} points</dd><dt>Rounds</dt><dd>${env.max_rounds}, seat ${env.first_mover} opens</dd>
        <dt>Actions</dt><dd>${p.rules.actions.join(" · ")}</dd><dt>Agents</dt><dd>${p.rules.agents}</dd></dl>
      <details class="more"><summary>How a negotiation ends</summary><ul class="plainlist">${p.rules.termination.map((t) => `<li>${esc(t)}</li>`).join("")}</ul></details>`;
  }

  function wireEnvironments(arg) {
    main.querySelectorAll("[data-envdup]").forEach((b) => b.addEventListener("click", async () => {
      const e = (await getJSON(`/api/workbench/environments/${b.dataset.envdup}`)).environment;
      const { id, builtin, created, updated, ...rest } = e;
      const res = await post("/api/workbench/environments", { ...rest, name: `${e.name} (copy)`.slice(0, 60) });
      if (res.ok) { state.envDraft = null; location.hash = `#/environments/${res.data.id}`; } else showError(res, "Could not duplicate the environment.");
    }));
    main.querySelectorAll("[data-envdel]").forEach((b) => b.addEventListener("click", async () => {
      if (b.dataset.confirm !== "1") { b.dataset.confirm = "1"; b.textContent = "Confirm delete"; b.classList.add("danger"); return; }
      const res = await post(`/api/workbench/environments/${b.dataset.envdel}/delete`, {});
      if (res.ok) render(false); else showError(res, "Could not delete the environment.");
    }));
    const form = main.querySelector("#env-form");
    if (!form) return;
    const d = state.envDraft, pane = main.querySelector("#env-preview");
    let timer = null, parsed = null;
    const refresh = async (body = { format: "form", content: draftToEnv(d) }) => {
      const res = await post("/api/workbench/environments/preview", body);
      if (res.ok) { pane.innerHTML = previewHtml(res.data); fade([pane]); main.querySelector("#wb-error").hidden = true; main.querySelectorAll("[aria-invalid]").forEach((x) => x.removeAttribute("aria-invalid")); return res.data; }
      pane.innerHTML = `<h2 class="wb-h3">Preview</h2><p class="note">Fix the highlighted field to see the preview.</p>`;
      showError(res, "The environment is not valid yet.", false); // never steal focus while typing
      return null;
    };
    const soon = () => { clearTimeout(timer); timer = setTimeout(refresh, 250); };
    onCleanup(() => clearTimeout(timer));
    const ro = form.querySelector("input:disabled, textarea:disabled") && !form.querySelector("button[type=submit]");
    if (ro || state.envTab === "visual") refresh();
    else pane.innerHTML = '<h2 class="wb-h3">Preview</h2><p class="note">Paste or open a scenario, then validate it.</p>';
    form.querySelectorAll("[data-tab]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); state.envTab = a.dataset.tab; render(false); }));
    form.querySelectorAll("[data-e]").forEach((el) => el.addEventListener("input", () => { d[el.dataset.e] = el.type === "number" ? Number(el.value) : el.value; soon(); }));
    form.querySelectorAll('input[name="src"]').forEach((el) => el.addEventListener("change", () => { d.source = el.value; render(false); }));
    form.querySelectorAll('input[name="first"]').forEach((el) => el.addEventListener("change", () => { d.first_mover = el.value; soon(); }));
    form.querySelectorAll("[data-r]").forEach((el) => el.addEventListener("input", () => {
      const r = d.rows[+el.dataset.r];
      r[el.dataset.c] = el.dataset.c === "name" ? el.value.trim().toLowerCase() : Number(el.value);
      for (const s of ["a", "b"]) { const total = d.rows.reduce((t, x) => t + (Number(x[s]) || 0), 0), out = main.querySelector(`#sum-${s}`); out.textContent = `${total} / 100`; out.classList.toggle("ok", total === 100); }
      soon();
    }));
    form.querySelectorAll("[data-rm]").forEach((b) => b.addEventListener("click", () => { d.rows.splice(+b.dataset.rm, 1); render(false); }));
    form.querySelector("[data-add]")?.addEventListener("click", () => { d.rows.push({ name: `resource-${d.rows.length + 1}`.replace(/\d/g, (n) => "abcdefghij"[n]), qty: 10, a: 0, b: 0 }); render(false); });
    // Import tab
    const ta = form.querySelector("#import-text");
    form.querySelectorAll("[data-fmt]").forEach((b) => b.addEventListener("click", () => { state.importFmt = b.dataset.fmt; state.importText = ""; render(false); }));
    ta?.addEventListener("input", () => { state.importText = ta.value; form.querySelector("[data-load]").disabled = true; });
    form.querySelector("[data-validate]")?.addEventListener("click", async () => {
      parsed = await refresh({ format: state.importFmt, content: ta.value });
      form.querySelector("[data-load]").disabled = !parsed;
    });
    form.querySelector("[data-load]")?.addEventListener("click", () => { if (!parsed) return; Object.assign(d, envToDraft(parsed.environment)); state.envTab = "visual"; render(false); });
    form.querySelector("[data-file]")?.addEventListener("change", async (e) => {
      const file = e.target.files[0];
      if (!file) return;
      if (file.size > 12 * 1024) { showError({ data: { error: "That file is larger than 12 KB, the most the server accepts in one request; a scenario is a few lines." } }); return; }
      state.importText = await file.text();
      state.importFmt = /\.json$/i.test(file.name) ? "json" : "text";
      render(false);
    });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const res = await post("/api/workbench/environments", { ...draftToEnv(d), ...(arg !== "new" ? { id: arg } : {}) });
      if (res.ok) { state.envDraft = null; location.hash = `#/environments/${res.data.id}`; if (arg === res.data.id) render(false); } else showError(res, "The environment could not be saved.");
    });
    main.querySelector("[data-use]")?.addEventListener("click", () => { state.simDraft = { ...(state.simDraft ?? newSim()), environment: main.querySelector("[data-use]").dataset.use }; });
  }

  // ------------------------------------------------------------ simulations
  const newSim = () => ({ name: "", environment: "builtin-research", agents: { X: "builtin-priority", Y: "builtin-concession" },
    conditions: ["baseline_v1"], repetitions: 3, base_seed: 0, swap_seats: true, swap_first_mover: true, pace: "watch" });
  const designSize = (d) => d.repetitions * d.conditions.length * (d.swap_seats ? 2 : 1) * (d.swap_first_mover ? 2 : 1);

  async function simulations(_s, arg) {
    try { await catalog(); } catch (e) { return unavailable(e.message); }
    if (arg === "new") return simBuilder();
    const list = await getJSON("/api/workbench/simulations");
    return `${subnav("simulations")}${head("Simulations", "Each simulation runs a design: repetitions × conditions × seatings, with the unchanged negotiation engine. Results are labelled by what actually ran.")}
      <div class="listbar"><p class="count note">${list.length} simulations</p><a class="cta" href="#/simulations/new">New simulation${icon("chev-r")}</a></div>
      ${list.length ? `<div class="table-wrap"><table><thead><tr><th>Simulation</th><th>Agents</th><th>Status</th><th class="num">Negotiations</th><th class="num">Agreement</th><th>Started</th></tr></thead><tbody>
        ${list.map((s) => `<tr class="link" data-href="#/simulation/${s.id}"><td><a href="#/simulation/${s.id}">${esc(s.name)}</a><div class="sub">${esc(s.environment)} · ${s.data_kind === "simulated" ? "simulated" : "local agents"}</div></td>
          <td>${esc(s.agents.X)} <span class="sub">vs</span> ${esc(s.agents.Y)}</td><td>${statusBadge(s.status)}</td><td class="num">${s.done}/${s.planned}</td>
          <td class="num">${s.agreement_rate == null ? "—" : pct(s.agreement_rate)}</td><td>${when(s.created)}</td></tr>`).join("")}</tbody></table></div>`
        : `<div class="empty"><h3>No simulations yet</h3><p>A simulation pairs two agents on an environment and runs every combination of the design you choose.</p><a class="cta" href="#/simulations/new">Set one up${icon("chev-r")}</a></div>`}`;
  }

  async function simBuilder() {
    const [agentList, envList] = await Promise.all([getJSON("/api/workbench/agents"), getJSON("/api/workbench/environments")]);
    const d = (state.simDraft ??= newSim());
    const agentSel = (slot) => `<label class="field"><span>Agent ${slot}</span><select data-agent="${slot}" data-field="agents.${slot}">
        ${agentList.map((a) => `<option value="${a.id}"${a.id === d.agents[slot] ? " selected" : ""}${a.executable ? "" : " disabled"}>${esc(a.name)} · ${esc(a.kind_label)}${a.executable ? "" : a.kind === "gemini" ? " (not implemented)" : " (locked)"}</option>`).join("")}</select></label>`;
    const env = envList.find((e) => e.id === d.environment), n = designSize(d), lim = state.catalog.limits;
    const a = agentList.find((x) => x.id === d.agents.X), b = agentList.find((x) => x.id === d.agents.Y);
    const deaf = a && b && ![a, b].some((x) => kindOf(x.kind).reads_instructions);
    const simulated = a && b && [a, b].every((x) => ["mock", "python"].includes(x.category));
    return `${subnav("simulations")}<a class="back" href="#/simulations">${icon("chev-l")}Simulations</a>
      ${head("New simulation", "Choose who negotiates, where, and under which design. The summary shows exactly what will run before anything starts.")}
      <div class="wb-split"><form class="stepcard" id="sim-form" novalidate>
        <label class="field"><span>Environment</span><select data-s="environment" data-field="environment">${envList.map((e) => `<option value="${e.id}"${e.id === d.environment ? " selected" : ""}>${esc(e.name)}${e.builtin ? " (template)" : ""}</option>`).join("")}</select></label>
        <div class="wb-grid2">${agentSel("X")}${agentSel("Y")}</div>
        <p class="note">Locked and unimplemented provider agents are listed but cannot be chosen. X and Y may be the same agent (self-play).</p>
        <fieldset class="choice"><legend>Conditions</legend>
          ${state.catalog.conditions.map((c) => `<label class="opt"><input type="checkbox" data-cond="${c}"${d.conditions.includes(c) ? " checked" : ""} data-field="conditions"><span><b>${c === "baseline_v1" ? "Baseline instructions" : "Structured instructions"}</b><small class="sub"><code>${c}</code>${deaf ? " · not read by these agents" : ""}</small></span></label>`).join("")}</fieldset>
        <div class="wb-grid2"><label class="field"><span>Repetitions</span><input type="number" min="1" max="${lim.repetitions}" step="1" value="${d.repetitions}" data-n="repetitions" data-field="repetitions"><small class="note">One instance seed each.</small></label>
          <label class="field"><span>First seed</span><input type="number" min="0" step="1" value="${d.base_seed}" data-n="base_seed" data-field="base_seed"><small class="note">Seeds run from here upwards.</small></label></div>
        <div class="wb-toggles"><label class="toggle"><input type="checkbox" data-b="swap_seats"${d.swap_seats ? " checked" : ""}> Swap seats, so each agent plays A and B</label>
          <label class="toggle"><input type="checkbox" data-b="swap_first_mover"${d.swap_first_mover ? " checked" : ""}> Swap the opening seat</label></div>
        <fieldset class="choice"><legend>Pace</legend><div class="seg" role="group" aria-label="Pace">${[["watch", "Watch it live"], ["instant", "As fast as possible"]].map(([v, t]) => `<button type="button" data-pace="${v}" aria-pressed="${d.pace === v}">${t}</button>`).join("")}</div>
          <small class="note">${d.pace === "watch" ? "Turns are spaced 0.35 s apart so you can follow them; the agents themselves answer instantly." : "No pacing: rule-based agents finish in a fraction of a second."}</small></fieldset>
        <label class="field"><span>Name <span class="sub">(optional)</span></span><input type="text" maxlength="80" value="${esc(d.name)}" data-s="name" data-field="name" placeholder="${esc(`${a?.name ?? "X"} vs ${b?.name ?? "Y"} · ${env?.name ?? ""}`)}"></label>
        ${errorLine()}
        <div class="actions"><button type="submit" class="cta"${n > lim.negotiations || !d.conditions.length ? " disabled" : ""}>Run ${n} negotiation${n === 1 ? "" : "s"}${icon("chev-r")}</button><a class="btn" href="#/simulations">Cancel</a></div>
      </form>
      <aside class="panel wb-aside"><h2 class="wb-h3">Summary</h2><dl class="wb-dl">
        <dt>Environment</dt><dd>${esc(env?.name ?? "—")}${env?.source === "generated" ? " (a new instance per seed)" : env ? " (fixed values)" : ""}</dd>
        <dt>Agents</dt><dd>X ${esc(a?.name ?? "—")} · Y ${esc(b?.name ?? "—")}</dd>
        <dt>Design</dt><dd>${d.repetitions} repetition${d.repetitions === 1 ? "" : "s"} × ${d.conditions.length} condition${d.conditions.length === 1 ? "" : "s"} × ${d.swap_seats ? 2 : 1} seating${d.swap_seats ? "s" : ""} × ${d.swap_first_mover ? 2 : 1} opening seat${d.swap_first_mover ? "s" : ""}</dd>
        <dt>Negotiations</dt><dd><b>${n}</b>${n > lim.negotiations ? ` <span class="badge crit">over the ${lim.negotiations} limit</span>` : ""}</dd>
        <dt>Seeds</dt><dd>${d.base_seed}${d.repetitions > 1 ? `–${d.base_seed + d.repetitions - 1}` : ""}</dd>
        <dt>Rounds</dt><dd>${env?.max_rounds ?? "—"}</dd><dt>Cost</dt><dd>$0: ${simulated ? "rule-based agents, labelled simulated" : "local agents on this machine, labelled local agents"}</dd></dl>
        ${env?.source === "custom" && a && b && kindOf(a.kind).deterministic && kindOf(b.kind).deterministic && d.repetitions > 1 ? notice("warn", "alert", "Fixed environment and deterministic agents: every repetition will repeat the first exactly. Use the research generator for varied instances.") : ""}
        ${deaf && d.conditions.length > 1 ? notice("info", "info", "Neither agent reads instructions, so both conditions will give identical results. That is a check on the pipeline, not a finding.") : ""}
      </aside></div>`;
  }

  function wireSimBuilder() {
    const form = main.querySelector("#sim-form");
    if (!form) return;
    const d = state.simDraft;
    const on = (sel, ev, fn) => form.querySelectorAll(sel).forEach((el) => el.addEventListener(ev, () => fn(el)));
    on("[data-s]", "change", (el) => { d[el.dataset.s] = el.value; if (el.dataset.s !== "name") render(false); });
    on("[data-s=name]", "input", (el) => { d.name = el.value; });
    on("[data-agent]", "change", (el) => { d.agents[el.dataset.agent] = el.value; render(false); });
    on("[data-cond]", "change", (el) => { d.conditions = state.catalog.conditions.filter((c) => (c === el.dataset.cond ? el.checked : d.conditions.includes(c))); render(false); });
    on("[data-n]", "change", (el) => { d[el.dataset.n] = el.value === "" ? NaN : Number(el.value); render(false); });
    on("[data-b]", "change", (el) => { d[el.dataset.b] = el.checked; render(false); });
    on("[data-pace]", "click", (el) => { d.pace = el.dataset.pace; render(false); });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      const btn = form.querySelector("button[type=submit]");
      btn.disabled = true;
      const res = await post("/api/workbench/simulations", d);
      if (res.ok) { state.simTab = "live"; location.hash = `#/simulation/${res.data.id}`; } else { btn.disabled = false; showError(res, "The simulation could not start."); }
    });
  }

  // ------------------------------------------------------------ simulation workspace
  const seatName = (doc, trial, seat) => { const label = trial.seats[seat]; return `${label} · ${doc.config.agents[label].name}`; };
  const allocText = (alloc, seat) => (alloc ? Object.entries(alloc[seat]).map(([c, q]) => `${esc(c)} ${q}`).join(", ") : "");
  function turnRow(t, doc, trial) {
    const who = seatName(doc, trial, t.actor), color = AGENT_COLOR[trial.seats[t.actor]];
    const what = t.action === "OFFER" ? `offers: ${t.actor} gets ${allocText(t.allocation, t.actor)}` : t.action === "ACCEPT" ? "accepts the standing offer" : t.action === "WALK_AWAY" ? "walks away" : `invalid action (${esc(t.reason ?? "")})`;
    const values = t.value_A != null ? `<span class="sub">worth A ${fmt(t.value_A, 1)} · B ${fmt(t.value_B, 1)} pts${t.standing === false ? " · final-turn offer, never standing" : ""}</span>` : "";
    return `<li class="wb-turn ${t.action === "INVALID" ? "bad" : ""}" style="--c:${color}"><span class="wb-tn">${t.turn}</span><div><b>Seat ${t.actor}</b> <span class="sub">${esc(who)}</span> ${what}${values ? `<br>${values}` : ""}${t.message ? `<div class="wb-msg">“${esc(t.message)}”</div>` : ""}</div></li>`;
  }

  async function simulation(_s, id) {
    let doc;
    try { doc = await getJSON(`/api/workbench/simulations/${encodeURIComponent(id)}`); } catch { return `${subnav("simulations")}${head("Simulation not found", "No simulation has this id on this server.")}<a class="btn" href="#/simulations">${icon("chev-l")}Simulations</a>`; }
    state.doc = doc;
    const tabs = [["live", doc.status === "running" ? "Live" : "Overview"], ["negotiations", `Negotiations (${doc.trials.length})`], ["analytics", "Analytics"], ["log", "Log"], ["export", "Export"]];
    if (!tabs.some(([k]) => k === state.simTab)) state.simTab = "live";
    return `${subnav("simulations")}<a class="back" href="#/simulations">${icon("chev-l")}Simulations</a>
      ${head(doc.config.name, "", `<div class="wb-headline">${statusBadge(doc.status)}<span class="sub">${esc(doc.config.environment.name)} · started ${when(doc.created)} · <code>${doc.id}</code></span></div>`)}
      ${notice(doc.config.data_kind === "simulated" ? "info" : "warn", "info", `<b>${esc(doc.config.label.split(":")[0])}.</b>${esc(doc.config.label.slice(doc.config.label.indexOf(":") + 1))}`)}
      <div class="wb-progress" role="progressbar" aria-label="Negotiations finished" aria-valuemin="0" aria-valuemax="${doc.plan_size}" aria-valuenow="${doc.trials.length}"><i style="transform:scaleX(${doc.trials.length / doc.plan_size})"></i></div>
      <p class="note" id="progress-text">${doc.trials.length} of ${doc.plan_size} negotiations finished${doc.status === "running" ? "" : "."}</p>
      <nav class="subnav" aria-label="Simulation views" id="simtabs">${tabs.map(([k, t]) => `<a href="#/simulation/${doc.id}" data-simtab="${k}"${state.simTab === k ? ' aria-current="page"' : ""}>${t}</a>`).join("")}</nav>
      <div id="simpanel">${simPanel(doc)}</div>`;
  }

  function simPanel(doc) {
    const tab = state.simTab;
    if (tab === "live") return livePanel(doc);
    if (tab === "negotiations") return trialsPanel(doc);
    if (tab === "analytics") return analyticsPanel(doc);
    if (tab === "log") return `<ol class="wb-log">${doc.log.map((l) => `<li class="${l.level}"><span class="mono">${l.t.toFixed(2)} s</span><span>${esc(l.text)}</span></li>`).join("")}</ol>`;
    return exportPanel(doc);
  }

  function livePanel(doc) {
    const cfg = doc.config, live = doc.live;
    const design = `<dl class="readouts">${readout("Negotiations", `${doc.trials.length}<small> / ${doc.plan_size}</small>`)}${readout("Agreed", `${doc.trials.filter((t) => t.outcome === "agreed").length}`)}
      ${readout("Other outcomes", `${doc.trials.filter((t) => t.status === "completed" && t.outcome !== "agreed").length}`)}${readout("Not outcomes", `${doc.trials.filter((t) => t.status !== "completed").length}`, "aborted or cancelled")}</dl>`;
    if (doc.status !== "running" || !live) {
      return `${design}<section class="block">${blockHead("Configuration")}<dl class="wb-dl wide"><dt>Agents</dt><dd>X ${esc(cfg.agents.X.name)} (${esc(cfg.agents.X.kind)}) · Y ${esc(cfg.agents.Y.name)} (${esc(cfg.agents.Y.kind)})</dd>
        <dt>Design</dt><dd>${cfg.repetitions} repetition(s) from seed ${cfg.base_seed}; ${cfg.conditions.join(", ")}; seats swapped ${cfg.swap_seats ? "yes" : "no"}; opening seat swapped ${cfg.swap_first_mover ? "yes" : "no"}</dd>
        <dt>Provenance</dt><dd>protocol <code>${esc(doc.provenance.protocol_id)}</code> · code <code>${esc(String(doc.provenance.code_version).slice(0, 12))}</code> · zero-cost mode</dd></dl>
        ${doc.error ? notice("warn", "alert", `<b>Stopped by an error.</b> ${esc(doc.error)}`) : ""}
        <div class="actions"><button type="button" class="cta" data-simtab-go="analytics">Read the analysis${icon("chev-r")}</button><button type="button" class="btn" data-simtab-go="negotiations">Inspect negotiations</button><button type="button" class="btn" data-simtab-go="export">Export</button></div></section>`;
    }
    const sp = live.spec;
    return `${design}<section class="block wb-live">${blockHead(`Negotiation ${sp.index + 1} of ${doc.plan_size}`, `seed ${sp.seed} · <code>${esc(sp.condition)}</code> · seat ${sp.first_mover} opens`)}
      <div class="wb-split"><div><ol class="wb-turns" id="live-turns">${live.turns.map((t) => turnRow(t, doc, { seats: sp.seats })).join("")}</ol>
        <p class="wb-thinking" id="live-thinking">${live.thinking ? `Seat ${live.thinking.seat} (${esc(seatName(doc, { seats: sp.seats }, live.thinking.seat))}) is deciding turn ${live.thinking.round}…` : ""}</p></div>
      <aside class="panel wb-aside"><h2 class="wb-h3">Seats</h2><dl class="wb-dl">${["A", "B"].map((s) => `<dt>Seat ${s}</dt><dd><i class="wb-dot" style="--c:${AGENT_COLOR[sp.seats[s]]}"></i>${esc(seatName(doc, { seats: sp.seats }, s))}</dd>`).join("")}</dl>
        <h2 class="wb-h3">Inventory</h2><dl class="wb-dl">${Object.entries(live.pool).map(([c, q]) => `<dt>${esc(c)}</dt><dd>${q} units</dd>`).join("")}</dl>
        <button type="button" class="btn" id="cancel-sim">Cancel the simulation</button></aside></div></section>`;
  }

  function trialsPanel(doc) {
    if (!doc.trials.length) return `<div class="empty"><h3>No negotiations finished yet</h3><p>They appear here as soon as each one ends.</p></div>`;
    return `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th class="num">Seed</th><th>Condition</th><th>Seat A · Seat B</th><th>Opens</th><th>Outcome</th><th class="num">Turns</th><th class="num">RWE</th><th class="num">EW</th></tr></thead><tbody>
      ${doc.trials.map((t) => `<tr class="link" data-trial="${t.index}" id="trial-${t.index}" tabindex="0" aria-expanded="${state.openTrial === t.index}"><td class="num">${t.index + 1}</td><td class="num">${t.seed}</td><td><code>${esc(t.condition)}</code></td>
        <td>${t.seats.A} · ${t.seats.B}</td><td>${t.first_mover}</td><td>${t.status === "completed" ? `<span class="tag" style="--c:${OUT_COLOR[t.outcome]}">${esc(OUTCOME_LABEL[t.outcome] ?? t.outcome)}</span>${t.invalid_reason ? ` <span class="sub">${esc(t.invalid_reason)}</span>` : ""}` : `<span class="sub">${esc(t.status)}${t.reason ? `: ${esc(t.reason)}` : ""}</span>`}</td>
        <td class="num">${t.rounds ?? "—"}</td><td class="num">${t.rwe == null ? "—" : fmt(t.rwe)}</td><td class="num">${t.ew == null ? "—" : fmt(t.ew)}</td></tr>
        ${state.openTrial === t.index ? `<tr class="wb-detail"><td colspan="9">${trialDetail(doc, t)}</td></tr>` : ""}`).join("")}</tbody></table></div>
      <p class="note" style="margin-top:10px">Select a row to read its transcript. RWE and EW score failed negotiations as 0.</p>`;
  }

  function trialDetail(doc, t) {
    if (t.status !== "completed") return `<p>${esc(t.status === "aborted" ? `Aborted in turn ${t.round} (${t.reason}): ${t.detail}. Not an outcome; not re-run.` : "Cancelled before it finished. Not an outcome.")}</p>`;
    return `<div class="wb-split"><ol class="wb-turns">${t.events.map((e) => turnRow(e, doc, t)).join("")}</ol>
      <dl class="wb-dl"><dt>Outcome</dt><dd>${esc(OUTCOME_LABEL[t.outcome] ?? t.outcome)} after ${t.rounds} turn(s)</dd>
        <dt>Final allocation</dt><dd>${t.final_allocation ? `A: ${allocText(t.final_allocation, "A")}<br>B: ${allocText(t.final_allocation, "B")}` : "None (no agreement)"}</dd>
        <dt>Utilities</dt><dd>A ${fmt(t.utility_A, 1)} · B ${fmt(t.utility_B, 1)} of 100</dd><dt>Best total (W*)</dt><dd>${fmt(t.optimal_welfare, 1)}, split A: ${allocText(t.optimal_allocation, "A")}</dd>
        <dt>Private points</dt><dd>A: ${Object.entries(t.points.A).map(([c, p]) => `${esc(c)} ${fmt(p, 0)}`).join(", ")}<br>B: ${Object.entries(t.points.B).map(([c, p]) => `${esc(c)} ${fmt(p, 0)}`).join(", ")}</dd></dl></div>`;
  }

  function analyticsPanel(doc) {
    const a = doc.analytics;
    if (!a.n_completed) return `<div class="empty"><h3>Nothing to analyse yet</h3><p>Analytics appear once at least one negotiation has an outcome. Nothing is estimated in the meantime.</p></div>`;
    const names = Object.fromEntries(Object.entries(doc.config.agents).map(([k, d]) => [k, d.name]));
    const conds = Object.entries(a.by_condition);
    const condTable = `<div class="table-wrap"><table><thead><tr><th>Condition</th><th class="num">n</th><th class="num">Agreement</th><th class="num">Mean RWE</th><th class="num">Mean EW</th><th class="num">SWE (agreed)</th><th class="num">Equitability (agreed)</th><th class="num">Envy-free</th><th class="num">Invalid</th><th class="num">Turns</th></tr></thead><tbody>
      ${conds.map(([c, v]) => `<tr><td><code>${esc(c)}</code></td><td class="num">${v.n}</td><td class="num">${v.agreement_rate == null ? "—" : pct(v.agreement_rate)}</td><td class="num">${v.rwe == null ? "—" : fmt(v.rwe)}</td><td class="num">${v.ew == null ? "—" : fmt(v.ew)}</td>
        <td class="num">${v.swe == null ? "—" : fmt(v.swe)} <span class="sub">n=${v.n_agreed}</span></td><td class="num">${v.equitability == null ? "—" : fmt(v.equitability)}</td><td class="num">${v.envy_free_rate == null ? "—" : pct(v.envy_free_rate)}</td>
        <td class="num">${v.invalid_rate == null ? "—" : pct(v.invalid_rate)}</td><td class="num">${v.mean_rounds == null ? "—" : fmt(v.mean_rounds, 1)}</td></tr>`).join("")}</tbody></table></div>`;
    const outcomes = stackBars(conds.map(([c, v]) => ({ label: c, n: v.n, parts: Object.entries(v.outcomes).map(([k, count]) => ({ key: k, count, share: v.n ? count / v.n : 0, color: OUT_COLOR[k], ink: OUT_INK[k] })) })));
    const dots = dotPlot(Object.entries(a.agents).map(([k, p]) => ({ label: `${k} ${names[k]}`.slice(0, 26), color: AGENT_COLOR[k], mean: p.utility_mean,
      points: p.utilities.map((u) => ({ v: u.utility, tip: `Negotiation ${u.trial + 1} (${u.condition}): ${k} realised ${fmt(u.utility * 100, 1)} of 100` })) })), { axis: "Realised share of its own maximum" });
    const prof = (k, p) => `<div class="panel wb-agent" style="--c:${AGENT_COLOR[k]}"><h3><i class="wb-dot"></i>${esc(k)} · ${esc(names[k])}</h3>
      <dl class="wb-dl"><dt>Mean utility</dt><dd>${fmt(p.utility_mean)} <span class="sub">median ${fmt(p.utility_median)}</span></dd>
        <dt>Opening demand</dt><dd>${p.opening_demand_mean == null ? "—" : fmt(p.opening_demand_mean)} <span class="sub">of its own maximum</span></dd>
        <dt>Total concession</dt><dd>${p.total_concession_mean == null ? "—" : fmt(p.total_concession_mean)} <span class="sub">first to last offer</span></dd>
        <dt>Concession frequency</dt><dd>${p.concession_frequency_mean == null ? "—" : pct(p.concession_frequency_mean)} <span class="sub">of consecutive offers</span></dd>
        <dt>Offers per negotiation</dt><dd>${p.offers_mean == null ? "—" : fmt(p.offers_mean, 1)}</dd>
        <dt>Accepted · walked away · invalid</dt><dd>${p.accepted.length} · ${p.walked_away.length} · ${p.invalid.length} <span class="sub">of ${p.negotiations}</span></dd>
        <dt>Priority alignment</dt><dd>${p.priority_alignment == null ? "—" : pct(p.priority_alignment)} <span class="sub">opening claims vs private points</span></dd></dl></div>`;
    const priorities = (k, p) => `<div class="table-wrap"><table class="wb-prio"><caption class="vh">${esc(names[k])}: resource priorities</caption><thead><tr><th>${esc(k)} · ${esc(names[k])}</th><th class="num">Private points <span class="sub">ground truth, mean over seats held</span></th><th>Opening claim <span class="sub">observed</span></th><th>Final share <span class="sub">agreed only</span></th></tr></thead><tbody>
      ${Object.entries(p.resources).map(([c, r]) => `<tr><td>${esc(c)}</td><td class="num">${r.points_mean == null ? "—" : fmt(r.points_mean, 1)}</td>
        <td>${bar(r.opening_claim_mean, AGENT_COLOR[k])} <span class="sub">n=${r.n_opening}</span></td><td>${bar(r.final_share_mean, AGENT_COLOR[k])} <span class="sub">n=${r.n_final}</span></td></tr>`).join("")}</tbody></table></div>`;
    const ev = (o) => (o.evidence.length ? `<details class="more"><summary>Evidence: ${o.n > o.evidence.length ? `first ${o.evidence.length} of ${o.n}` : `${o.evidence.length}`} negotiation${o.evidence.length === 1 ? "" : "s"}</summary><p>${o.evidence.map((e) => `<button type="button" class="btn-link" data-goto="${e.trial}">#${e.trial + 1}${e.turn ? `, turn ${e.turn}` : ""}</button>`).join(" ")}</p></details>` : `<span class="sub">n=${o.n}</span>`);
    return `${doc.status === "running" ? notice("info", "await", `<b>Still running.</b> ${a.n_completed} of ${doc.plan_size} negotiations so far; these values change until it finishes.`) : ""}
      ${a.not_outcomes && Object.keys(a.not_outcomes).length ? notice("warn", "alert", `${Object.entries(a.not_outcomes).map(([k, v]) => `${v} ${esc(k)}`).join(", ")} negotiation(s) are not outcomes and are excluded.`) : ""}
      <section class="block" style="margin-top:8px">${blockHead("Cooperation and fairness by condition", "Descriptive only; no test is run. RWE and EW use every negotiation, failures scoring 0; SWE, equitability and envy-freeness use agreed ones.")}${condTable}
        ${figure(1, "Outcomes by condition", "Share of negotiations ending in each outcome.", outcomes, legend(Object.entries(OUT_COLOR).map(([k, c]) => [OUTCOME_LABEL[k] ?? k, c])),
          dataTable(["Condition", "Agreed", "Walked away", "Timeout", "Invalid"], conds.map(([c, v]) => [c, v.outcomes.agreed, v.outcomes.walked_away, v.outcomes.timeout, v.outcomes.invalid_action])))}</section>
      <section class="block">${blockHead("Agent comparison", "Each agent across every seat it held. Utilities are its own points / 100.")}<div class="grid g2">${Object.entries(a.agents).map(([k, p]) => prof(k, p)).join("")}</div>
        ${figure(2, "Utility per negotiation", "One dot per negotiation; the tick marks the mean. Failed negotiations sit at 0.", dots, "",
          dataTable(["Negotiation", "Agent", "Utility"], Object.entries(a.agents).flatMap(([k, p]) => p.utilities.map((u) => [u.trial + 1, k, fmt(u.utility)]))))}</section>
      <section class="block">${blockHead("Resource priorities", "Ground truth is the private points each agent was given; with swapped seats it held both seats' points, so the mean mixes them. Observed is the share of each resource it claimed in its first offer, and what it ended with when agreed.")}
        <div class="grid">${Object.entries(a.agents).map(([k, p]) => priorities(k, p)).join("")}</div></section>
      <section class="block">${blockHead("Observations", "Generated from the records above. Each says what it is based on; none infers why an agent acted.")}
        <ul class="wb-obs">${a.observations.map((o) => `<li><span class="badge">${esc(o.basis)}</span><p>${esc(o.text)}</p>${ev(o)}</li>`).join("")}</ul>
        <ul class="plainlist" style="margin-top:14px">${a.not_available.map((x) => `<li class="note">${esc(x)}</li>`).join("")}</ul></section>`;
  }
  const bar = (v, color) => (v == null ? "—" : `<span class="wb-bar" role="img" aria-label="${pct(v)}"><i style="width:${Math.round(v * 100)}%;background:${color}"></i></span> ${pct(v)}`);

  function exportPanel(doc) {
    return `<p class="note">Every file is built by the server from this simulation's stored records and carries its data label.</p>
      <div class="table-wrap"><table><thead><tr><th>File</th><th>Contents</th><th><span class="vh">Download</span></th></tr></thead><tbody>
      ${Object.entries(state.catalog.exports).map(([f, what]) => `<tr><td><code>${esc(f)}</code></td><td>${esc(what)}</td><td><button type="button" class="btn" data-export="${f}">Download</button></td></tr>`).join("")}</tbody></table></div>${errorLine()}`;
  }

  function wireSimulation(id) {
    const panel = main.querySelector("#simpanel");
    if (!panel) return;
    const paint = () => { panel.innerHTML = simPanel(state.doc); wirePanel(); fade([panel]); };
    const setTab = (k) => { state.simTab = k; main.querySelectorAll("[data-simtab]").forEach((x) => (x.dataset.simtab === k ? x.setAttribute("aria-current", "page") : x.removeAttribute("aria-current"))); paint(); };
    main.querySelectorAll("[data-simtab]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); setTab(a.dataset.simtab); }));
    function wirePanel() {
      panel.querySelectorAll("[data-simtab-go]").forEach((b) => b.addEventListener("click", () => setTab(b.dataset.simtabGo)));
      panel.querySelector("#cancel-sim")?.addEventListener("click", async (e) => {
        e.target.disabled = true; e.target.textContent = "Cancelling…";
        const res = await post(`/api/workbench/simulations/${id}/cancel`, {});
        if (!res.ok) { e.target.disabled = false; e.target.textContent = res.data.error ?? "Could not cancel"; }
      });
      const toggle = (row) => { const i = +row.dataset.trial; state.openTrial = state.openTrial === i ? null : i; paint(); main.querySelector(`#trial-${i}`)?.focus(); };
      panel.querySelectorAll("tr[data-trial]").forEach((row) => { row.addEventListener("click", () => toggle(row)); row.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(row); } }); });
      panel.querySelectorAll("[data-goto]").forEach((b) => b.addEventListener("click", () => { state.openTrial = +b.dataset.goto; setTab("negotiations"); main.querySelector(`#trial-${b.dataset.goto}`)?.scrollIntoView({ block: "center" }); }));
      panel.querySelectorAll("[data-export]").forEach((b) => b.addEventListener("click", async () => {
        try { download(await getJSON(`/api/workbench/simulations/${id}/export/${b.dataset.export}`)); } catch (err) { showError({ data: { error: `Export failed: ${err.message}` } }); }
      }));
      panel.querySelectorAll("details.more").forEach((d) => d.addEventListener("toggle", () => { if (d.open) fade([...d.children].filter((c) => c.tagName !== "SUMMARY")); }));
    }
    wirePanel();
    if (state.doc.status !== "running") return;
    // Poll the server while it runs; the view only ever shows what the engine reported.
    let seen = state.doc.live?.turns.length ?? 0, index = state.doc.live?.index;
    const timer = setInterval(async () => {
      let doc;
      try { doc = await getJSON(`/api/workbench/simulations/${id}`); } catch { return; }
      const prev = state.doc; state.doc = doc;
      const bar = main.querySelector(".wb-progress");
      bar.querySelector("i").style.transform = `scaleX(${doc.trials.length / doc.plan_size})`; bar.setAttribute("aria-valuenow", doc.trials.length);
      const tab = main.querySelector('[data-simtab="negotiations"]');
      if (tab) tab.textContent = `Negotiations (${doc.trials.length})`;
      main.querySelector("#progress-text").textContent = `${doc.trials.length} of ${doc.plan_size} negotiations finished${doc.status === "running" ? "" : "."}`;
      if (doc.status !== "running") { clearInterval(timer); render(false); return; }
      if (state.simTab !== "live") { if (doc.trials.length !== prev.trials.length && state.simTab !== "export") paint(); return; }
      const list = main.querySelector("#live-turns");
      if (!list || doc.live?.index !== index) { index = doc.live?.index; seen = 0; paint(); seen = doc.live?.turns.length ?? 0; return; }
      const fresh = doc.live.turns.slice(seen);
      if (fresh.length) {
        list.insertAdjacentHTML("beforeend", fresh.map((t) => turnRow(t, doc, { seats: doc.live.spec.seats })).join(""));
        fade([...list.children].slice(-fresh.length)); seen += fresh.length;
      }
      const th = doc.live.thinking;
      main.querySelector("#live-thinking").textContent = th ? `Seat ${th.seat} (${seatName(doc, { seats: doc.live.spec.seats }, th.seat)}) is deciding turn ${th.round}…` : "";
      const ro = main.querySelectorAll(".readouts dd");
      if (ro.length >= 4) { ro[0].innerHTML = `${doc.trials.length}<small> / ${doc.plan_size}</small>`; ro[1].textContent = doc.trials.filter((t) => t.outcome === "agreed").length; ro[2].textContent = doc.trials.filter((t) => t.status === "completed" && t.outcome !== "agreed").length; ro[3].textContent = doc.trials.filter((t) => t.status !== "completed").length; }
    }, 400);
    onCleanup(() => clearInterval(timer));
  }

  // ------------------------------------------------------------ wiring
  function wire(name, arg) {
    main.querySelectorAll("tr.link[data-href]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("a, button")) location.hash = tr.dataset.href; }));
    if (name === "agents") wireAgents(arg);
    if (name === "environments") wireEnvironments(arg);
    if (name === "simulations" && arg === "new") wireSimBuilder();
    if (name === "simulation") wireSimulation(arg);
  }

  return {
    routes: { workbench: overview, agents, environments, simulations, simulation },
    nav: { simulation: "simulations" },
    names: ["workbench", "agents", "environments", "simulations", "simulation"],
    wire,
  };
}
