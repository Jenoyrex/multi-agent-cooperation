// Start (guided workflow), cross-experiment Analytics and Compare, Reports and Settings.
// Reads only /api/workbench/*; pooled numbers are re-described from stored negotiations (lib.js, unit-tested).
import { boxStats, completedTrials, esc, fmt, OUTCOME_LABEL, OUTCOMES, pct, sourceSummary, summarize } from "./lib.js";
import { dataTable, stackBars } from "./charts.js";
import { boxPlot, groupedBars, toggleLegend } from "./viz.js";
import { COND_NAME, OUT_COLOR } from "./workbench.js";

const MAX_POOLED = 30; // experiments read for pooled analytics (newest first)

export function createInsights({ main, h, render, wb, prefs }) {
  const { icon, notice } = h;
  const { getJSON, badge, statusBadge, top, backLink, section, empty, chartCard, kpis, outcomeCharts, agentCharts, condTable, experimentList, kindName, download, catalog, quickStart, when, sbx } = wb;
  const state = { cmp: { a: null, b: null }, docs: null };

  // ------------------------------------------------------------ start
  async function start() {
    let sims;
    try { sims = await getJSON("/api/workbench/simulations"); }
    catch (e) { return `${top("Cooperation Lab")}${notice("warn", "alert", `The server did not answer (${esc(e.message)}).`)}`; }
    const running = sims.find((s) => s.status === "running");
    return `<header class="welcome"><h1>See how two AI agents share resources.</h1>
        <p class="standfirst">Pick two agents, give them something to divide, and see who gets what.</p>
        <div class="welcome-actions"><button type="button" class="cta big" id="quick">${icon("play")}Run a sample</button><a class="btn" href="#/simulations/new">Set up your own</a></div>
        <p class="step-error" id="quick-error" role="alert" hidden></p></header>
      ${running ? `<a class="runningbar" href="#/simulation/${running.id}">${badge("accent", "await", "Running")}<b>${esc(running.name)}</b><span class="sub">${running.done} of ${running.planned} negotiations</span>${icon("chev-r")}</a>` : ""}
      ${sims.length ? section("Recent", '<a href="#/simulations">All experiments</a>', experimentList(sims.slice(0, 3))) : ""}`;
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
    const series = [["agreement_rate", "Deals reached", "var(--base)"], ["rwe", "Value created", "var(--struct)"], ["ew", "Fairness", "var(--claude)"]];
    const bars = groupedBars(rows.map(({ d, s }) => ({ label: short(d.config.name), bars: series.map(([k, l, c]) => ({ key: k, label: l, v: s[k], color: c, tip: `${d.config.name}\n${l}: ${k === "agreement_rate" ? pct(s[k]) : fmt(s[k], 2)} (n = ${s.n})` })) })));
    const mix = stackBars(rows.map(({ d, s }) => ({ label: short(d.config.name, 15), n: s.n, parts: OUTCOMES.map((o) => ({ key: o, count: s.outcomes[o], share: s.n ? s.outcomes[o] / s.n : 0, color: OUT_COLOR[o], ink: o === "agreed" || o === "invalid_action" ? "var(--panel)" : "var(--ink)" })) })));
    const box = boxPlot(rows.map(({ d }, i) => ({ key: d.id, label: short(d.config.name, 18), color: i % 2 ? "var(--struct)" : "var(--base)", stats: boxStats(completedTrials(d.trials).map((t) => t.rwe)) })), { axis: "Value created per negotiation" });
    const simulatedOnly = docs.every((d) => d.config.data_kind === "simulated");
    return `${notice("info", "info", `<b>Pooled and descriptive.</b> These totals mix experiments with different agents, resources and designs, so they describe your work so far; they are not a test of anything.${simulatedOnly ? " Every experiment here used rule-based agents: the data is simulated." : ""}${sims.filter((s) => s.done > 0).length > MAX_POOLED ? ` Showing the newest ${MAX_POOLED} experiments.` : ""}`)}
      <dl class="stats">${[["Experiments", docs.length], ["Negotiations", pooled.n]].map(([k, v]) => `<div class="stat"><dt>${k}</dt><dd>${v}</dd></div>`).join("")}</dl>
      <div style="margin-top:12px">${kpis(pooled)}</div>
      <div class="cgrid">${chartCard("Key measures by experiment", "Agreement rate, mean value created and mean fairness for each experiment. Use the legend to show or hide a measure.", bars,
        { wide: true, lg: toggleLegend(series.map(([k, l, c]) => [k, l, c])), table: dataTable(["Experiment", "n", "Deals reached", "Value created", "Fairness", "Turns"], rows.map(({ d, s }) => [d.config.name, s.n, pct(s.agreement_rate), fmt(s.rwe, 3), fmt(s.ew, 3), fmt(s.rounds, 1)])) })}
        ${chartCard("Outcome mix", "How each experiment's negotiations ended.", mix, { lg: `<div class="legend">${OUTCOMES.map((o) => `<span><i class="sw" style="--c:${OUT_COLOR[o]}"></i>${OUTCOME_LABEL[o]}</span>`).join("")}</div>` })}
        ${chartCard("Spread of value created", "Each row is one experiment: the box holds its middle half of negotiations, the line its median.", box)}</div>
      ${section("Experiments", "Select one for its full analysis.", `<div class="table-wrap"><table><thead><tr><th>Experiment</th><th>Data</th><th class="num">n</th><th class="num">Deals reached</th><th class="num">Value created</th><th class="num">Fairness</th><th class="num">Turns</th><th>Status</th></tr></thead><tbody>
        ${rows.map(({ d, s }) => `<tr class="link" data-href="#/simulation/${d.id}"><td><a href="#/simulation/${d.id}">${esc(d.config.name)}</a><div class="sub">${esc(d.config.agents.X.name)} vs ${esc(d.config.agents.Y.name)} · ${esc(d.config.environment.name)}</div></td>
          <td>${esc(sourceSummary(d))}</td><td class="num">${s.n}</td><td class="num">${pct(s.agreement_rate)}</td><td class="num">${fmt(s.rwe)}</td><td class="num">${fmt(s.ew)}</td><td class="num">${fmt(s.rounds, 1)}</td><td>${statusBadge(d.status)}</td></tr>`).join("")}</tbody></table></div>`)}`;
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
        ${row("Agreement", "agreement_rate", (v) => pct(v, 1))}${row("Value created (mean RWE)", "rwe", (v) => fmt(v, 3))}${row("Fairness (mean EW)", "ew", (v) => fmt(v, 3))}
        ${row("Turns (mean)", "rounds", (v) => fmt(v, 2))}${row("Walk-aways", "walk_rate", (v) => pct(v, 1))}${row("Invalid actions", "invalid_rate", (v) => pct(v, 1))}</tbody></table></div>
      <div class="cgrid">${chartCard("Value created", "Distribution per negotiation in each experiment.", box("rwe", "Value created"))}
        ${chartCard("Fairness", "Distribution per negotiation in each experiment.", box("ew", "Fairness"))}
        ${chartCard("Outcome mix", "How each experiment's negotiations ended.", mix, { lg: `<div class="legend">${OUTCOMES.map((o) => `<span><i class="sw" style="--c:${OUT_COLOR[o]}"></i>${OUTCOME_LABEL[o]}</span>`).join("")}</div>` })}</div>
      <p class="note" style="margin-top:16px">To compare the two instruction conditions or the two agents inside one experiment, open it: its analysis filters by condition and profiles each agent. <a href="#/simulation/${B.id}">Open B</a> · <a href="#/simulation/${A.id}">Open A</a></p>`;
  }

  // ------------------------------------------------------------ reports
  async function reports() {
    const sims = await getJSON("/api/workbench/simulations");
    const ready = sims.filter((s) => s.done > 0);
    return `${top("Reports", "A printable summary of each experiment: setup, results, charts, interpretation, limitations and provenance. Save one as PDF from your browser's print dialog, or download the data.")}
      ${ready.length ? `<ul class="ilist">${ready.map((s) => `<li><a class="irow" href="#/report/${s.id}"><span class="filetag">${icon("doc")}</span><span class="imain"><b>${esc(s.name)}</b><span class="sub">${s.done} negotiations · ${esc(sourceSummary(s))} · ${when(s.created)}</span></span>${statusBadge(s.status)}${icon("chev-r")}</a></li>`).join("")}</ul>`
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
      cfg.data_kind === "simulated" ? "Simulated data: both agents follow fixed rules. Nothing here is evidence about Claude, GPT, Gemini or any language model."
        : cfg.data_kind === "local-llm" ? "Local LLM: the results describe how these models answered on this computer. They are exploratory, not the preregistered study."
        : ["local-model", "api-model"].includes(cfg.data_kind) ? `Data source: ${sourceSummary(cfg)}. These results describe the named model with the instructions used here, in these runs. They are not the research study's results, and may differ between runs.`
        : "Your own agents: the results describe the programs that answered, not any named model, and are not a controlled experiment.",
      "Every statistic is descriptive unless marked exploratory; none is the preregistered confirmatory analysis.",
      env.source === "custom" ? "A fixed resource set is one situation however many repetitions ran; results may not carry over to other values." : "Each seed is a different situation; the number of seeds limits how far patterns generalise.",
      ...(Object.keys(a.not_outcomes ?? {}).length ? [`${Object.entries(a.not_outcomes).map(([k, v]) => `${v} ${k}`).join(", ")} negotiation(s) are not outcomes and are excluded.`] : []),
      ...a.not_available,
    ];
    const R = doc.readable ?? { headline: "", points: [], numbers: [], agents: [], caveats: [], glossary: [] };
    const words = (k) => esc(String(k).replace(/_/g, " "));
    const params = (p) => Object.entries(p).map(([k, v]) => `${words(k)}: ${typeof v === "number" ? fmt(v, 2) : typeof v === "string" && v.length > 40 ? `“${esc(v)}”` : esc(v)}`).join(" · ");
    const cond = Object.entries(a.by_condition ?? {});
    // What the pictures show, in one line each: the same numbers as the charts, so a reader knows what to look for.
    const takeaways = [
      R.headline,
      cond.length > 1 ? `Best result captured, by instructions: ${cond.map(([c, v]) => `${esc(COND_NAME[c])} ${v.rwe == null ? "—" : `${Math.round(v.rwe * 100)}%`}`).join(" · ")}.` : "",
      R.points.find((p) => p.text.startsWith("When they made a deal"))?.text ?? "",
      R.points.find((p) => p.text.startsWith("A negotiation took"))?.text ?? "",
    ].filter(Boolean);
    const plain = `<section class="report-plain"><h2>The short version</h2>
        <p class="lead">${esc(R.headline)}</p>
        ${R.points.length ? `<ul class="plainpoints">${R.points.map((p) => `<li><span>${esc(p.text)}</span>${p.detail ? `<span class="sub">${esc(p.detail)}</span>` : ""}</li>`).join("")}</ul>` : ""}</section>
      ${R.numbers.length ? `<section><h2>Key numbers</h2><dl class="keynums-grid">${R.numbers.map((k) => `<div><dt>${esc(k.label)}</dt><dd class="big">${esc(k.value)}</dd><dd class="meaning">${esc(k.meaning)}</dd><dd class="sub">${esc(k.exact)}</dd></div>`).join("")}</dl></section>` : ""}
      ${R.agents.length ? `<section><h2>Each agent, in plain words</h2><div class="agentcards">${R.agents.map((g) => `<div class="sheet agentcard"><h3><i class="wb-dot" style="--c:${g.label === "X" ? "var(--base)" : "var(--struct)"}"></i>${esc(g.name)}</h3>${g.source ? `<p class="sub">${esc(g.source)}</p>` : ""}<ul class="plainlist">${g.lines.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></div>`).join("")}</div></section>` : ""}`;
    const pictures = oc || ac ? `<section><h2>The results in pictures</h2>
        <div class="readhint"><b>What to look for:</b><ul class="plainlist">${takeaways.map((t) => `<li>${esc(t)}</li>`).join("")}</ul><p class="sub">Each chart says how to read it under its title. Hover or select a mark for its exact value.</p></div>
        ${oc ? `<div class="cgrid">${oc.donut}${oc.box}${oc.scatter}${oc.rounds}</div>` : ""}${ac ? `<div class="cgrid">${ac.radar}${ac.concession}</div>` : ""}</section>` : "";
    const details = `<details class="fulldetails" id="fulldetails"><summary><h2>Full details</h2><span class="sub">Every table, definition, limitation and record behind the summary above. Opened automatically when you save as PDF.</span></summary>
        <section><h3>How the experiment was set up</h3><dl class="kvs wide">
          <dt>Agent X</dt><dd><b>${esc(names.X)}</b> · ${esc(kindLabel(cfg.agents.X.kind))}<br><span class="sub">${params(cfg.agents.X.params)}</span></dd>
          <dt>Agent Y</dt><dd><b>${esc(names.Y)}</b> · ${esc(kindLabel(cfg.agents.Y.kind))}<br><span class="sub">${params(cfg.agents.Y.params)}</span></dd>
          <dt>Instructions given</dt><dd>${cfg.conditions.map((c) => `${COND_NAME[c]} (<code>${c}</code>)`).join(", ")}</dd>
          <dt>Design</dt><dd>${cfg.repetitions} repetition(s) from seed ${cfg.base_seed}; seats swapped ${cfg.swap_seats ? "yes" : "no"}; opener swapped ${cfg.swap_first_mover ? "yes" : "no"}; ${env.max_rounds} turns at most</dd></dl>
          <h4>What they divided: ${esc(env.name)}</h4>${resTable}</section>
        ${s.n ? `<section><h3>All key measures</h3>${kpis(s)}<p class="note">“Value created” and “Fairness” run from 0 to 1 (0.63 means 63%). Definitions are under “Words used in this report”.</p></section>` : ""}
        ${oc ? `<section><h3>Results by instructions</h3>${condTable(a)}</section>` : ""}
        ${a.n_completed ? `<section class="report-sandbox"><h3>Behaviour profile, with likely ranges</h3>${sbx.analysisBody("profile", doc, names)}</section>` : ""}
        ${a.sandbox?.sweep ? `<section class="report-sandbox"><h3>Comparison between groups</h3>${sbx.analysisBody("compare", doc, names)}</section>` : ""}
        ${a.sandbox?.reactions ? `<section class="report-sandbox"><h3>Reaction to resource events</h3>${sbx.analysisBody("reaction", doc, names)}</section>` : ""}
        <section><h3>Everything the lab noticed</h3>${a.observations.length ? `<ul class="wb-obs">${a.observations.map((o) => `<li><span class="badge">${esc(o.basis)}</span><p>${esc(o.text)}</p><span class="sub">n = ${o.n}</span></li>`).join("")}</ul>` : '<p class="note">No observations: too few negotiations.</p>'}
          ${a.inference?.available ? `<p class="note">Exploratory tests (spec §8.9 procedures, not confirmatory): ${Object.entries(a.inference.result.primary).map(([k, x]) => `${k} ${x.permutation.defined ? (x.permutation.reject ? "significant" : "not significant") : "not testable"} after Holm`).join("; ")}.</p>` : ""}</section>
        <section><h3>Limitations</h3><ul class="plainlist">${limits.map((l) => `<li>${esc(l)}</li>`).join("")}</ul></section>
        <section><h3>Record of this run</h3><dl class="kvs wide"><dt>Experiment id</dt><dd><code>${doc.id}</code></dd><dt>Engine</dt><dd>${esc(doc.provenance.engine)}</dd><dt>Protocol</dt><dd><code>${esc(doc.provenance.protocol_id)}</code> · spec ${esc(doc.provenance.spec_version)}</dd>
          <dt>Prompts</dt><dd><code>${esc(String(doc.provenance.prompt_hash).slice(0, 16))}</code></dd><dt>Code</dt><dd><code>${esc(String(doc.provenance.code_version).slice(0, 16))}</code></dd><dt>Zero-cost mode</dt><dd>${doc.provenance.zero_cost_mode ? "On: no paid or external model calls" : "Off for this experiment: it used an API key or a remote agent"}</dd>
          <dt>Started · finished</dt><dd>${new Date(doc.created * 1000).toLocaleString()} · ${doc.finished ? new Date(doc.finished * 1000).toLocaleString() : "—"}</dd></dl></section>
      </details>`;
    return `<div class="report-tools noprint">${backLink("#/reports", "Reports")}<div class="head-actions"><button type="button" class="cta" id="print">${icon("doc")}Save as PDF</button>${["csv", "json", "md"].map((x) => { const f = Object.keys(cat.exports).find((n) => n.endsWith(`.${x}`)); return f ? `<button type="button" class="btn" data-export="${f}">${icon("download")}${x.toUpperCase()}</button>` : ""; }).join("")}</div></div>
      <article class="report">
        <header><p class="sub">Cooperation Lab · experiment report · ${new Date(doc.created * 1000).toLocaleDateString(undefined, { year: "numeric", month: "long", day: "numeric" })}</p>
          <h1>${esc(cfg.name)}</h1><p class="standfirst">${esc(names.X)} and ${esc(names.Y)} negotiated over ${esc(env.name)} in ${s.n} completed negotiation${s.n === 1 ? "" : "s"} (${doc.trials.length} of ${doc.plan_size} planned, ${esc(doc.status)}).</p>
          <p class="datalabel ${cfg.data_kind}">${icon("info")}<span><b>${esc(cfg.label.split(":")[0])}.</b>${esc(cfg.label.slice(cfg.label.indexOf(":") + 1))}</span></p></header>
        ${plain}
        ${pictures}
        ${R.caveats.length ? `<section><h2>Good to know before you quote these numbers</h2><ul class="plainlist">${R.caveats.map((c) => `<li>${esc(c)}</li>`).join("")}</ul></section>` : ""}
        ${details}
        ${R.glossary.length ? `<section><h2>Words used in this report</h2><dl class="helpdl">${R.glossary.map(([t, d]) => `<div><dt>${esc(t)}</dt><dd>${esc(d)}</dd></div>`).join("")}</dl></section>` : ""}
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
        ${row(cat.local_features ? "Research study's zero-cost lock" : "Zero-cost mode", cat.local_features ? "The research study's runner refuses paid model calls. Sandbox agents are separate: a local model is free, and an API key is yours, used only after you confirm an estimate and set a cap." : "This public demo calls no model at all. It cannot be switched off from this page.", cat.zero_cost ? badge("good", "lock", "On") : badge("warn", "alert", "Off"))}
        ${cat.kinds.filter((k) => k.category === "provider").map((k) => row(esc(k.label), esc(k.reason), badge(k.id === "gemini" ? "" : "warn", k.id === "gemini" ? "x" : "lock", k.id === "gemini" ? "No adapter" : "Locked"))).join("")}
        ${cat.local_features ? row("API keys", `Added under <a href="#/models">Model connections</a>, or read from your environment or a local .env. Kept in memory, shown masked, and never saved to the database, exports, reports or logs.`, badge("", "lock", "Never saved"))
          : row("API keys", `This public demo takes no keys. <a href="#/run-locally">Run the app on your computer</a> to use your own.`, badge("", "lock", "None here"))}</ul></section>
      <section class="block"><div class="block-head"><h2>Your data</h2></div><ul class="ilist settings">
        ${row("Agents, resources and experiments", "Stored in the app's own database, separate from the research study.", badge("", "info", "Local"))}
        ${row("Uploaded Python files", `Stored with the agent and checked by reading the code (up to ${Math.round((cat.upload?.max_bytes ?? 48000) / 1000)} KB). ${cat.local_features ? "A file runs only after you confirm, in its own process on this computer." : "This public demo never executes them."}`, badge("", "lock", cat.local_features ? "Asks first" : "Never executed"))}
        ${row("Research study data", "The preregistered study's records in <code>results/</code> are read-only here and never changed by the Workbench.", badge("", "lock", "Read-only"))}
        ${cat.demo ? row("Public demo", `This is a shared presentation server. Everything created here is visible to every visitor, cannot be edited or deleted, and is erased when the server restarts. Experiments run at most ${cat.limits.negotiations} negotiations; local HTTP agents are off.`, badge("warn", "alert", "Shared")) : ""}</ul></section>`;
  }

  function wire(name, arg) {
    main.querySelectorAll("tr.link[data-href]").forEach((tr) => tr.addEventListener("click", (e) => { if (!e.target.closest("a, button")) location.hash = tr.dataset.href; }));
    if (name === "") main.querySelector("#quick")?.addEventListener("click", async (e) => {
      const b = e.currentTarget; b.disabled = true; b.innerHTML = '<span class="spin" aria-hidden="true"></span>Starting…';
      const res = await quickStart();
      if (!res.ok) { b.disabled = false; b.innerHTML = `${icon("play")}Run a sample`; const err = main.querySelector("#quick-error"); err.textContent = res.data.error ?? "The experiment could not start."; err.hidden = false; }
    });
    if (name === "analytics") main.querySelectorAll("[data-cmp]").forEach((el) => el.addEventListener("change", () => { state.cmp[el.dataset.cmp] = el.value; render(false); }));
    if (name === "report") {
      // The full details are part of the PDF: open them before printing (the button, or the browser's own print command).
      const openAll = () => main.querySelectorAll(".report details").forEach((d) => { d.open = true; });
      main.querySelector("#print")?.addEventListener("click", () => { openAll(); print(); });
      addEventListener("beforeprint", openAll, { once: true });
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
