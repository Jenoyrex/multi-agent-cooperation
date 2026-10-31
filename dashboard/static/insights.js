// Start (guided workflow), cross-experiment Analytics and Compare, Reports and Settings.
// Reads only /api/workbench/*; pooled numbers are re-described from stored negotiations (lib.js, unit-tested).
import { boxStats, completedTrials, esc, fmt, OUTCOME_LABEL, OUTCOMES, pct, summarize } from "./lib.js";
import { dataTable, stackBars } from "./charts.js";
import { boxPlot, groupedBars, toggleLegend } from "./viz.js";
import { COND_NAME, OUT_COLOR } from "./workbench.js";

const MAX_POOLED = 30; // experiments read for pooled analytics (newest first)

export function createInsights({ main, h, render, wb, prefs }) {
  const { icon, notice } = h;
  const { getJSON, badge, statusBadge, top, backLink, section, empty, chartCard, kpis, outcomeCharts, agentCharts, condTable, experimentList, download, catalog, quickStart, when } = wb;
  const state = { cmp: { a: null, b: null }, docs: null };

  // ------------------------------------------------------------ start
  async function start() {
    let agents, envs, sims;
    try { [agents, envs, sims] = await Promise.all([getJSON("/api/workbench/agents"), getJSON("/api/workbench/environments"), getJSON("/api/workbench/simulations")]); }
    catch (e) { return `${top("Cooperation Lab")}${notice("warn", "alert", `The server did not answer (${esc(e.message)}).`)}`; }
    const running = sims.find((s) => s.status === "running");
    const steps = [["Choose two agents and what they divide", `${agents.filter((a) => a.executable).length} agents and ${envs.length} resource sets ready to use`],
      ["Run the negotiations", "Watch them live, or let them finish in seconds"],
      ["Read the results", "Value created, fairness, deals reached, and every transcript"]];
    return `<header class="welcome"><h1>See how two negotiating agents share resources when neither knows what the other wants.</h1>
        <p class="standfirst">Run a sample experiment to see it work. Everything runs on the lab's server at no cost.</p>
        <div class="welcome-actions"><button type="button" class="cta big" id="quick">${icon("play")}Run a sample experiment</button><a class="btn" href="#/simulations/new">Set up your own</a></div>
        <p class="note">The sample runs 24 negotiations between two built-in rule-based agents. Results are labelled <b>simulated</b>: they show how the lab measures behaviour, not how any AI model behaves.</p>
        <p class="step-error" id="quick-error" role="alert" hidden></p></header>
      ${running ? `<a class="runningbar" href="#/simulation/${running.id}">${badge("accent", "await", "Running")}<b>${esc(running.name)}</b><span class="sub">${running.done} of ${running.planned} negotiations</span>${icon("chev-r")}</a>` : ""}
      <section class="block"><h2 class="vh">How it works</h2><ol class="howstrip">${steps.map(([t, sub], i) => `<li><span class="jmark" aria-hidden="true">${i + 1}</span><div><b>${t}</b><span class="sub">${sub}</span></div></li>`).join("")}</ol></section>
      ${section("Recent experiments", sims.length ? '<a href="#/simulations">All experiments</a>' : "", sims.length ? experimentList(sims.slice(0, 5)) : empty("No experiments yet", "Run the sample experiment above, or set up your own. They will appear here with their progress and results."))}
      <section class="block study-link"><a class="irow" href="#/study"><span class="imain"><b>The research study</b><span class="sub">The preregistered Claude vs GPT experiment this lab was built for. It has not been run yet, so there are no results.</span></span>${icon("chev-r")}</a></section>`;
  }

  // ------------------------------------------------------------ analytics hub
  async function loadDocs() {
    const sims = await getJSON("/api/workbench/simulations");
    const use = sims.filter((s) => s.done > 0).slice(0, MAX_POOLED);
    const docs = await Promise.all(use.map((s) => getJSON(`/api/workbench/simulations/${s.id}`)));
    return { sims, docs };
  }
  const short = (s, n = 26) => (s.length > n ? `${s.slice(0, n - 1)}…` : s);

  async function analytics(_s, arg) {
    await catalog();
    let data;
    try { data = await loadDocs(); } catch (e) { return `${top("Analytics")}${notice("warn", "alert", `The server did not answer (${esc(e.message)}).`)}`; }
    state.docs = data.docs;
    const tabs = `<nav class="tabs" aria-label="Analytics views"><a href="#/analytics"${arg ? "" : ' aria-current="page"'}>Across experiments</a><a href="#/analytics/compare"${arg === "compare" ? ' aria-current="page"' : ""}>Compare two</a></nav>`;
    const head = top("Analytics", "Results across your experiments, computed from every stored negotiation. Open an experiment for its full interactive analysis.");
    if (!data.docs.length) return `${head}${tabs}${empty("No results yet", "Analytics appear once an experiment has finished at least one negotiation.", `<a class="cta" href="#/simulations/new">${icon("plus")}Set up an experiment</a>`)}`;
    return `${head}${tabs}${arg === "compare" ? compareView(data.docs) : hubView(data)}`;
  }

  function hubView({ sims, docs }) {
    const all = docs.flatMap((d) => d.trials), pooled = summarize(all);
    const rows = docs.map((d) => ({ d, s: summarize(d.trials) }));
    const series = [["agreement_rate", "Agreement", "var(--base)"], ["rwe", "Value captured", "var(--struct)"], ["ew", "Worse-off share", "var(--claude)"]];
    const bars = groupedBars(rows.map(({ d, s }) => ({ label: short(d.config.name), bars: series.map(([k, l, c]) => ({ key: k, label: l, v: s[k], color: c, tip: `${d.config.name}\n${l}: ${k === "agreement_rate" ? pct(s[k]) : fmt(s[k], 2)} (n = ${s.n})` })) })));
    const mix = stackBars(rows.map(({ d, s }) => ({ label: short(d.config.name, 15), n: s.n, parts: OUTCOMES.map((o) => ({ key: o, count: s.outcomes[o], share: s.n ? s.outcomes[o] / s.n : 0, color: OUT_COLOR[o], ink: o === "agreed" || o === "invalid_action" ? "var(--panel)" : "var(--ink)" })) })));
    const box = boxPlot(rows.map(({ d }, i) => ({ key: d.id, label: short(d.config.name, 18), color: i % 2 ? "var(--struct)" : "var(--base)", stats: boxStats(completedTrials(d.trials).map((t) => t.rwe)) })), { axis: "Value captured (RWE) per negotiation" });
    const simulatedOnly = docs.every((d) => d.config.data_kind === "simulated");
    return `${notice("info", "info", `<b>Pooled and descriptive.</b> These totals mix experiments with different agents, resources and designs, so they describe your work so far; they are not a test of anything.${simulatedOnly ? " Every experiment here used rule-based agents: the data is simulated." : ""}${sims.filter((s) => s.done > 0).length > MAX_POOLED ? ` Showing the newest ${MAX_POOLED} experiments.` : ""}`)}
      <dl class="stats">${[["Experiments", docs.length], ["Negotiations", pooled.n]].map(([k, v]) => `<div class="stat"><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
      <div style="margin-top:12px">${kpis(pooled)}</div>
      <div class="cgrid">${chartCard("Key measures by experiment", "Agreement rate, mean value captured and mean worse-off share for each experiment. Use the legend to show or hide a measure.", bars,
        { wide: true, lg: toggleLegend(series.map(([k, l, c]) => [k, l, c])), table: dataTable(["Experiment", "n", "Agreement", "Value captured", "Worse-off share", "Turns"], rows.map(({ d, s }) => [d.config.name, s.n, pct(s.agreement_rate), fmt(s.rwe, 3), fmt(s.ew, 3), fmt(s.rounds, 1)])) })}
        ${chartCard("Outcome mix", "How each experiment's negotiations ended.", mix, { lg: `<div class="legend">${OUTCOMES.map((o) => `<span><i class="sw" style="--c:${OUT_COLOR[o]}"></i>${OUTCOME_LABEL[o]}</span>`).join("")}</div>` })}
        ${chartCard("Spread of value captured", "Each row is one experiment: the box holds its middle half of negotiations, the line its median.", box)}</div>
      ${section("Experiments", "Select one for its full analysis.", `<div class="table-wrap"><table><thead><tr><th>Experiment</th><th>Data</th><th class="num">n</th><th class="num">Agreement</th><th class="num">Value captured</th><th class="num">Worse-off share</th><th class="num">Turns</th><th>Status</th></tr></thead><tbody>
        ${rows.map(({ d, s }) => `<tr class="link" data-href="#/simulation/${d.id}"><td><a href="#/simulation/${d.id}">${esc(d.config.name)}</a><div class="sub">${esc(d.config.agents.X.name)} vs ${esc(d.config.agents.Y.name)} · ${esc(d.config.environment.name)}</div></td>
          <td>${d.config.data_kind === "simulated" ? "Simulated" : "Local agents"}</td><td class="num">${s.n}</td><td class="num">${pct(s.agreement_rate)}</td><td class="num">${fmt(s.rwe)}</td><td class="num">${fmt(s.ew)}</td><td class="num">${fmt(s.rounds, 1)}</td><td>${statusBadge(d.status)}</td></tr>`).join("")}</tbody></table></div>`)}`;
  }

  function compareView(docs) {
    const byId = Object.fromEntries(docs.map((d) => [d.id, d]));
    if (!byId[state.cmp.a]) state.cmp.a = docs[1]?.id ?? docs[0].id;
    if (!byId[state.cmp.b]) state.cmp.b = docs[0].id;
    const A = byId[state.cmp.a], B = byId[state.cmp.b], sa = summarize(A.trials), sb = summarize(B.trials);
    const pick = (slot, cur) => `<label class="field"><span>Experiment ${slot.toUpperCase()}</span><select data-cmp="${slot}">${docs.map((d) => `<option value="${d.id}"${d.id === cur ? " selected" : ""}>${esc(d.config.name)} · ${when(d.created)}</option>`).join("")}</select></label>`;
    const diffs = [];
    const name = (d, k) => d.config.agents[k].name;
    if (name(A, "X") !== name(B, "X") || name(A, "Y") !== name(B, "Y")) diffs.push("the agents");
    if (A.config.environment.name !== B.config.environment.name) diffs.push("the resources");
    if (A.config.conditions.join() !== B.config.conditions.join()) diffs.push("the conditions");
    if (A.config.repetitions !== B.config.repetitions || A.config.base_seed !== B.config.base_seed) diffs.push("the seeds or repetitions");
    const row = (label, key, f) => { const a = sa[key], b = sb[key]; return `<tr><td>${label}</td><td class="num">${f(a)}</td><td class="num">${f(b)}</td><td class="num">${a == null || b == null ? "—" : `${b - a > 0 ? "+" : b - a < 0 ? "−" : "±"}${f(Math.abs(b - a))}`}</td></tr>`; };
    const box = (key, axis) => boxPlot([{ key: "a", label: "A", color: "var(--base)", stats: boxStats(completedTrials(A.trials).map((t) => t[key])) }, { key: "b", label: "B", color: "var(--struct)", stats: boxStats(completedTrials(B.trials).map((t) => t[key])) }], { axis });
    const mix = stackBars([[A, sa, "A"], [B, sb, "B"]].map(([, s, l]) => ({ label: `Experiment ${l}`, n: s.n, parts: OUTCOMES.map((o) => ({ key: o, count: s.outcomes[o], share: s.n ? s.outcomes[o] / s.n : 0, color: OUT_COLOR[o], ink: o === "agreed" || o === "invalid_action" ? "var(--panel)" : "var(--ink)" })) })));
    return `<div class="sheet cmp-pick"><div class="wb-grid2">${pick("a", A.id)}${pick("b", B.id)}</div></div>
      ${notice("warn", "alert", `<b>Exploratory comparison.</b> ${A.id === B.id ? "Both sides are the same experiment." : diffs.length ? `These experiments differ in ${diffs.join(", ")}, so a difference below may come from any of them.` : "Same agents, resources, conditions and seeds."} Differences are descriptive; no test is run, and nothing here says why they differ.`)}
      <div class="table-wrap"><table><thead><tr><th>Measure</th><th class="num">A · ${esc(short(A.config.name, 22))}</th><th class="num">B · ${esc(short(B.config.name, 22))}</th><th class="num">B − A</th></tr></thead><tbody>
        <tr><td>Negotiations</td><td class="num">${sa.n}</td><td class="num">${sb.n}</td><td class="num">—</td></tr>
        ${row("Agreement", "agreement_rate", (v) => pct(v, 1))}${row("Value captured (mean RWE)", "rwe", (v) => fmt(v, 3))}${row("Worse-off share (mean EW)", "ew", (v) => fmt(v, 3))}
        ${row("Turns (mean)", "rounds", (v) => fmt(v, 2))}${row("Walk-aways", "walk_rate", (v) => pct(v, 1))}${row("Invalid actions", "invalid_rate", (v) => pct(v, 1))}</tbody></table></div>
      <div class="cgrid">${chartCard("Value captured", "Distribution per negotiation in each experiment.", box("rwe", "Value captured (RWE)"))}
        ${chartCard("Worse-off share", "Distribution per negotiation in each experiment.", box("ew", "Worse-off share (EW)"))}
        ${chartCard("Outcome mix", "How each experiment's negotiations ended.", mix, { lg: `<div class="legend">${OUTCOMES.map((o) => `<span><i class="sw" style="--c:${OUT_COLOR[o]}"></i>${OUTCOME_LABEL[o]}</span>`).join("")}</div>` })}</div>
      <p class="note" style="margin-top:16px">To compare the two instruction conditions or the two agents inside one experiment, open it: its analysis filters by condition and profiles each agent. <a href="#/simulation/${B.id}">Open B</a> · <a href="#/simulation/${A.id}">Open A</a></p>`;
  }

  // ------------------------------------------------------------ reports
  async function reports() {
    const sims = await getJSON("/api/workbench/simulations");
    const ready = sims.filter((s) => s.done > 0);
    return `${top("Reports", "A printable summary of each experiment: setup, results, charts, interpretation, limitations and provenance. Save one as PDF from your browser's print dialog, or download the data.")}
      ${ready.length ? `<ul class="ilist">${ready.map((s) => `<li><a class="irow" href="#/report/${s.id}"><span class="filetag">${icon("doc")}</span><span class="imain"><b>${esc(s.name)}</b><span class="sub">${s.done} negotiations · ${s.data_kind === "simulated" ? "simulated" : "local agents"} · ${when(s.created)}</span></span>${statusBadge(s.status)}${icon("chev-r")}</a></li>`).join("")}</ul>`
        : empty("No reports yet", "A report is ready as soon as an experiment has finished negotiations.", `<a class="cta" href="#/simulations/new">${icon("plus")}Set up an experiment</a>`)}`;
  }

  async function report(_s, id) {
    let doc;
    try { [doc] = await Promise.all([getJSON(`/api/workbench/simulations/${encodeURIComponent(id)}`), catalog()]); } catch { return top("Report not found", "No experiment has this id on this server.", "", backLink("#/reports", "Reports")); }
    const cfg = doc.config, a = doc.analytics, s = summarize(doc.trials), names = { X: cfg.agents.X.name, Y: cfg.agents.Y.name };
    const env = cfg.environment, cat = await catalog(), kinds = cat.kinds;
    const kindLabel = (k) => kinds.find((x) => x.id === k)?.label ?? k;
    const oc = s.n ? outcomeCharts(doc, doc.trials, names) : null, ac = a.n_completed ? agentCharts(a, names) : null;
    const resTable = env.source === "generated" ? `<p>Generated by the research generator: ${env.categories} resources with quantities and private values drawn anew for each seed (${cfg.base_seed}–${cfg.base_seed + cfg.repetitions - 1}).</p>`
      : `<div class="table-wrap"><table><thead><tr><th>Resource</th><th>Category</th><th class="num">Quantity</th><th class="num">${esc(names.X)} / A points</th><th class="num">B points</th></tr></thead><tbody>${Object.entries(env.resources).map(([c, q]) => `<tr><td>${esc(c)}</td><td>${esc(env.meta?.[c]?.group ?? "—")}</td><td class="num">${q} ${esc(env.meta?.[c]?.unit ?? "")}</td><td class="num">${env.points.A[c]}</td><td class="num">${env.points.B[c]}</td></tr>`).join("")}</tbody></table></div><p class="note">Points are seat values: with seats swapped, each agent held both A's and B's.</p>`;
    const limits = [
      cfg.data_kind === "simulated" ? "Simulated data: both agents follow fixed rules. Nothing here is evidence about Claude, GPT, Gemini or any language model." : "Local agents: the results describe the programs that answered on the lab's server, not any named model, and are not a controlled experiment.",
      "Every statistic is descriptive unless marked exploratory; none is the preregistered confirmatory analysis.",
      env.source === "custom" ? "A fixed resource set is one situation however many repetitions ran; results may not carry over to other values." : "Each seed is a different situation; the number of seeds limits how far patterns generalise.",
      ...(Object.keys(a.not_outcomes ?? {}).length ? [`${Object.entries(a.not_outcomes).map(([k, v]) => `${v} ${k}`).join(", ")} negotiation(s) are not outcomes and are excluded.`] : []),
      ...a.not_available,
    ];
    return `<div class="report-tools noprint">${backLink("#/reports", "Reports")}<div class="head-actions"><button type="button" class="cta" id="print">${icon("doc")}Save as PDF</button>${["csv", "json", "md"].map((x) => { const f = Object.keys(cat.exports).find((n) => n.endsWith(`.${x}`)); return f ? `<button type="button" class="btn" data-export="${f}">${icon("download")}${x.toUpperCase()}</button>` : ""; }).join("")}</div></div>
      <article class="report">
        <header><p class="sub">Cooperation Lab · experiment report · ${new Date(doc.created * 1000).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}</p>
          <h1>${esc(cfg.name)}</h1><p class="standfirst">${esc(names.X)} and ${esc(names.Y)} negotiated over ${esc(env.name)} in ${s.n} completed negotiation${s.n === 1 ? "" : "s"} (${doc.trials.length} of ${doc.plan_size} planned, ${esc(doc.status)}).</p>
          <p class="datalabel ${cfg.data_kind}">${icon("info")}<span><b>${esc(cfg.label.split(":")[0])}.</b>${esc(cfg.label.slice(cfg.label.indexOf(":") + 1))}</span></p></header>
        <section><h2>Summary</h2>${s.n ? kpis(s) : '<p class="note">No completed negotiations.</p>'}
          ${a.observations.length ? `<ul class="plainlist" style="margin-top:14px">${a.observations.slice(0, 4).map((o) => `<li>${esc(o.text)} <span class="sub">(${esc(o.basis)})</span></li>`).join("")}</ul>` : ""}</section>
        <section><h2>Setup</h2><dl class="kvs wide">
          <dt>Agent X</dt><dd>${esc(names.X)} · ${esc(kindLabel(cfg.agents.X.kind))} · ${Object.entries(cfg.agents.X.params).map(([k, v]) => `${esc(k)} ${typeof v === "number" ? fmt(v, 2) : esc(v)}`).join(", ")}</dd>
          <dt>Agent Y</dt><dd>${esc(names.Y)} · ${esc(kindLabel(cfg.agents.Y.kind))} · ${Object.entries(cfg.agents.Y.params).map(([k, v]) => `${esc(k)} ${typeof v === "number" ? fmt(v, 2) : esc(v)}`).join(", ")}</dd>
          <dt>Conditions</dt><dd>${cfg.conditions.map((c) => `${COND_NAME[c]} (<code>${c}</code>)`).join(", ")}</dd>
          <dt>Design</dt><dd>${cfg.repetitions} repetition(s) from seed ${cfg.base_seed}; seats swapped ${cfg.swap_seats ? "yes" : "no"}; opener swapped ${cfg.swap_first_mover ? "yes" : "no"}; ${env.max_rounds} turns at most</dd></dl>
          <h3>Resources: ${esc(env.name)}</h3>${resTable}</section>
        ${oc ? `<section><h2>Results</h2><div class="cgrid">${oc.donut}${oc.box}${oc.scatter}${oc.rounds}</div>${condTable(a)}</section>` : ""}
        ${ac ? `<section><h2>Agent behaviour</h2><div class="cgrid">${ac.radar}${ac.concession}</div></section>` : ""}
        <section><h2>Interpretation</h2>${a.observations.length ? `<ul class="wb-obs">${a.observations.map((o) => `<li><span class="badge">${esc(o.basis)}</span><p>${esc(o.text)}</p><span class="sub">n = ${o.n}</span></li>`).join("")}</ul>` : '<p class="note">No observations: too few negotiations.</p>'}
          ${a.inference?.available ? `<p class="note">Exploratory tests (spec §8.9 procedures, not confirmatory): ${Object.entries(a.inference.result.primary).map(([k, x]) => `${k} ${x.permutation.defined ? (x.permutation.reject ? "significant" : "not significant") : "not testable"} after Holm`).join("; ")}.</p>` : ""}</section>
        <section><h2>Limitations</h2><ul class="plainlist">${limits.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></section>
        <section><h2>Provenance</h2><dl class="kvs wide"><dt>Experiment id</dt><dd><code>${doc.id}</code></dd><dt>Engine</dt><dd>${esc(doc.provenance.engine)}</dd><dt>Protocol</dt><dd><code>${esc(doc.provenance.protocol_id)}</code> · spec ${esc(doc.provenance.spec_version)}</dd>
          <dt>Prompts</dt><dd><code>${esc(String(doc.provenance.prompt_hash).slice(0, 16))}</code></dd><dt>Code</dt><dd><code>${esc(String(doc.provenance.code_version).slice(0, 16))}</code></dd><dt>Zero-cost mode</dt><dd>${doc.provenance.zero_cost_mode ? "On: no paid or external model calls" : "Off"}</dd>
          <dt>Started · finished</dt><dd>${new Date(doc.created * 1000).toLocaleString()} · ${doc.finished ? new Date(doc.finished * 1000).toLocaleString() : "—"}</dd></dl></section>
      </article>${'<p class="step-error" id="wb-error" role="alert" hidden></p>'}`;
  }

  // ------------------------------------------------------------ settings
  async function settings() {
    const cat = await catalog();
    const seg = (name, value, opts) => `<div class="seg" role="radiogroup" aria-label="${name}">${opts.map(([v, t]) => `<button type="button" role="radio" data-pref="${name}" data-value="${v}" aria-checked="${value === v}" aria-pressed="${value === v}">${t}</button>`).join("")}</div>`;
    const row = (title, text, control) => `<li><div class="srow-t"><b>${title}</b><span class="sub">${text}</span></div>${control}</li>`;
    return `${top("Settings", "How the lab looks, and what it will and will not do.")}
      <section class="block" style="margin-top:0"><div class="block-head"><h2>Appearance</h2></div><ul class="ilist settings">
        ${row("Theme", "System follows your device's light or dark setting.", seg("theme", prefs.theme(), [["system", "System"], ["light", "Light"], ["dark", "Dark"]]))}
        ${row("Motion", "Reduce turns off movement and transitions, whatever your device says.", seg("motion", prefs.motion(), [["system", "System"], ["reduce", "Reduce"]]))}</ul></section>
      <section class="block"><div class="block-head"><h2>Cost and AI providers</h2></div><ul class="ilist settings">
        ${row("Zero-cost mode", "The server refuses every paid or external model call before anything is sent. It cannot be switched off from this page.", cat.zero_cost ? badge("good", "lock", "On") : badge("warn", "alert", "Off"))}
        ${cat.kinds.filter((k) => k.category === "provider").map((k) => row(esc(k.label), esc(k.reason), badge(k.id === "gemini" ? "" : "warn", k.id === "gemini" ? "x" : "lock", k.id === "gemini" ? "No adapter" : "Locked"))).join("")}
        ${row("API keys", "Never entered in the browser. The server reads them only from its own .env file, and does not use them while zero-cost mode is on.", badge("", "lock", "Server only"))}</ul></section>
      <section class="block"><div class="block-head"><h2>Your data</h2></div><ul class="ilist settings">
        ${row("Agents, resources and experiments", "Stored on the lab's server in <code>sandbox_runs/workbench.db</code>, separate from the research study.", badge("", "info", "Local"))}
        ${row("Uploaded Python files", `Stored with the agent and checked by reading the code (up to ${Math.round((cat.upload?.max_bytes ?? 48000) / 1000)} KB). This server never executes them.`, badge("", "lock", "Never executed"))}
        ${row("Research study data", "The preregistered study's records in <code>results/</code> are read-only here and never changed by the Workbench.", badge("", "lock", "Read-only"))}
        ${cat.demo ? row("Public demo", `This is a shared presentation server. Everything created here is visible to every visitor, cannot be edited or deleted, and is erased when the server restarts. Experiments run at most ${cat.limits.negotiations} negotiations; local HTTP agents are off.`, badge("warn", "alert", "Shared")) : ""}</ul></section>`;
  }

  function wire(name, arg) {
    main.querySelectorAll("tr.link[data-href]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("a, button")) location.hash = tr.dataset.href; }));
    if (name === "") main.querySelector("#quick")?.addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true; b.textContent = "Starting…";
      const res = await quickStart();
      if (!res.ok) { b.disabled = false; b.innerHTML = `${icon("play")}Run a sample experiment`; const err = main.querySelector("#quick-error"); err.textContent = res.data.error ?? "The experiment could not start."; err.hidden = false; }
    });
    if (name === "analytics") main.querySelectorAll("[data-cmp]").forEach((el) => el.addEventListener("change", () => { state.cmp[el.dataset.cmp] = el.value; render(false); }));
    if (name === "report") {
      main.querySelector("#print")?.addEventListener("click", () => print());
      main.querySelectorAll("[data-export]").forEach((b) => b.addEventListener("click", async () => {
        try { download(await getJSON(`/api/workbench/simulations/${arg}/export/${b.dataset.export}`)); }
        catch (err) { const el = main.querySelector("#wb-error"); el.textContent = `Export failed: ${err.message}`; el.hidden = false; }
      }));
    }
    if (name === "settings") main.querySelectorAll("[data-pref]").forEach((b) => b.addEventListener("click", () => { prefs.set(b.dataset.pref, b.dataset.value); render(false); }));
    h.animateCharts(main);
  }

  return { routes: { "": start, analytics, reports, report, settings }, names: ["analytics", "reports", "report", "settings"], wire };
}
