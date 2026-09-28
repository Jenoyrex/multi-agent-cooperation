import { CONDITIONS, esc, filterRows, fmt, labStages, mean, modelLabel, OUTCOME_LABEL, OUTCOMES, outcomeShares, pct, phase, registerSlots, short, signed, timeAgo, uniq } from "./lib.js";
import { dataTable, dotPlot, figure, histogram, legend, stackBars } from "./charts.js";
import { createField } from "./field.js";

// Motion's vanilla build (vendor/motion.js, loaded as a classic script) exposes window.Motion.
// Everything below degrades to the static page if it is missing or motion is reduced.
const M = window.Motion;
const reducedMotion = () => matchMedia("(prefers-reduced-motion: reduce)").matches;
const motionOn = () => Boolean(M) && !reducedMotion() && !document.hidden;
const EASE = [0.16, 1, 0.3, 1]; // exponential ease-out, no overshoot
const field = createField(document.getElementById("field"));
let cleanups = []; // stop inView/scroll observers and timers of the previous view
const later = (fn, ms) => { const id = setTimeout(fn, ms); cleanups.push(() => clearTimeout(id)); };

// Every measure explained the same way (spec §8.6-8.9 terminology): what it measures, why it matters,
// how to read it, and which negotiations count. Shown through info buttons, table notes and the glossary.
const DEF = {
  rwe: { name: "Relative welfare efficiency", plain: "How much of the best possible total value did they achieve?", what: "How close the agreed allocation came to the best total value achievable for this pool (W*), computed from both private valuations.", why: "Confirmatory measure for H1: did the agents capture the available gains from trade?", read: "0 = no value realised (every failed negotiation scores 0); 1 = the theoretical welfare optimum.", basis: "Unconditional: every negotiation counts." },
  ew: { name: "Egalitarian welfare", plain: "How well did the less-satisfied agent do?", what: "The normalized utility of the less-satisfied agent: min(u_A, u_B) / 100.", why: "Confirmatory measure for H2: does the worse-off side also benefit, not just the total?", read: "0 to 1; higher means a larger minimum realised utility. Failures score 0.", basis: "Unconditional: every negotiation counts." },
  agreement_rate: { name: "Agreement rate", plain: "How often did they agree before the deadline?", what: "The proportion of negotiations that reached a valid agreement before the round limit.", why: "Confirmatory measure for H3: does cooperation happen at all?", read: "0% to 100%.", basis: "All negotiations." },
  equitability: { name: "Equitability", plain: "How evenly were the benefits split between the two agents?", what: "How evenly the two agents' realised shares of their own maximum matched: 1 − |u_A − u_B| / 100.", why: "Describes the fairness of agreed splits, separately from the total value created.", read: "0 to 1; 1 means both realised the same share.", basis: "Agreement-only secondary, descriptive measure. Failed negotiations are excluded, not scored 0; not tested." },
  swe: { name: "Conditional social welfare efficiency", plain: "When they did agree, how much of the best total did the deal capture?", what: "Social welfare efficiency (SW / W*) among agreed negotiations only.", why: "Separates the quality of deals from how often deals happen.", read: "0 to 1; 1 is the welfare optimum.", basis: "Agreement-only secondary, descriptive measure; not tested." },
  imbalance: { name: "Model imbalance", plain: "Did one model come out ahead of the other?", what: "u_Claude/100 − u_GPT/100: how much more of its own maximum Claude realised than GPT, whichever seat each held.", why: "Diagnostic: checks whether one model systematically extracts more value. Seats are swapped by design, so this is not a seat effect.", read: "−1 to +1; positive favours Claude, 0 is balanced.", basis: "Agreement-only diagnostic; not tested." },
  first_mover_gap: { name: "First-mover gap", plain: "Did making the first offer help?", what: "u_first/100 − u_second/100: how much more the agent that moved first realised than the one that moved second.", why: "Diagnostic: detects a first-mover advantage. Who moves first is swapped by design, so it can be separated from model effects.", read: "−1 to +1; positive favours the first mover.", basis: "Agreement-only diagnostic; not tested." },
  envy_free_rate: { name: "Envy-free rate", plain: "How often did neither agent prefer the other's share?", what: "Share of agreements in which neither agent values the other's bundle above its own, judged by its own private valuation.", why: "A diagnostic fairness check that needs both hidden valuations. It is not a confirmatory outcome.", read: "0% to 100%; higher means fewer envious agents.", basis: "Agreement-only diagnostic; not tested." },
  invalid_action_rate: { name: "Invalid-action rate", plain: "How often did a model break the rules or the answer format?", what: "Share of negotiations ended by malformed output, an impossible allocation or an illegal accept.", why: "Separates protocol and output faults from a strategic failure to agree.", read: "0% to 100%.", basis: "All negotiations; diagnostic outcome rate." },
  utility: { name: "Utility", plain: "How much an agent got, judged by its own secret values", what: "Points an agent realised from its final bundle, by its own private valuation.", why: "The quantity every welfare measure is built from.", read: "0 to 100 per agent; 100 means it received everything it valued.", basis: "0 whenever there is no agreement." },
  rounds: { name: "Rounds", plain: "How many turns the negotiation took", what: "Turns taken before the negotiation ended.", why: "Process efficiency: how quickly agreement or failure came.", read: "1 to 10; turn 10 is the deadline.", basis: "Diagnostic; reported, not tested." },
};
/** The three confirmatory hypotheses (spec §8.8), each asking whether its measure differs between conditions. */
const HYP = [["H1", "rwe", "Does the pair capture a different share of the available value?"], ["H2", "ew", "Does the worse-off agent fare differently?"], ["H3", "agreement_rate", "Do they reach agreement at a different rate?"]];
/** The four-part definition as one readable block (tooltip text and accessible label). */
const defText = (d) => `${d.plain ? `${d.plain}
` : ""}What it measures: ${d.what}\nWhy it matters: ${d.why}\nHow to read it: ${d.read}\nWhich negotiations count: ${d.basis}`;

const main = document.getElementById("main");
const COND_COLOR = { baseline_v1: "var(--base)", structured_v1: "var(--struct)" };
// seatA/seatB: neutral seat colours when both seats hold the same kind of agent (e.g. a sandbox Claude vs Claude).
const FAM_COLOR = { claude: "var(--claude)", openai: "var(--openai)", seatA: "var(--ink-2)", seatB: "var(--scale)" };
const OUT_COLOR = { agreed: "var(--o-agreed)", walked_away: "var(--o-walked)", timeout: "var(--o-timeout)", invalid_action: "var(--o-invalid)" };
// Segment label ink chosen per fill so counts stay legible on dark and light fills.
const OUT_INK = { agreed: "var(--ground)", walked_away: "var(--ink)", timeout: "var(--ink)", invalid_action: "var(--ground)" };
const famColor = (f) => FAM_COLOR[f] ?? "var(--muted)";
const st = { draft: null, sbx: null, cmp: null, snap: null, raw: "", filters: {}, dataset: "pilot", evaluator: false, showMeasures: false, phaseKey: null };
const RQ = "Does a structured negotiation instruction strategy change cooperation outcomes when LLM agents negotiate over shared resources with private preferences?";
let figN = 0;
const nextFig = () => ++figN;

// ------------------------------------------------------------------ data
async function load() {
  field.loading(true);
  let raw;
  try {
    const res = await fetch("/api/snapshot", { cache: "no-store" });
    if (!res.ok) throw new Error(`snapshot ${res.status}`);
    raw = await res.text();
  } finally { field.loading(false); }
  const changed = raw !== st.raw;
  if (changed && st.raw) field.refresh(); // real data changed on a refresh
  st.raw = raw;
  st.snap = JSON.parse(raw);
  const g = labStages(st.snap);
  field.base(st.snap.runs.some((r) => r.status === "running") ? "running" : g.full === "complete" ? "complete" : "idle");
  const p = st.snap.progress;
  const stage = p.full.done || p.pilot.done >= p.pilot.target ? p.full : p.pilot; // the stage currently filling
  document.getElementById("nav-status").textContent = `${stage.done}/${stage.target}`;
  return changed;
}

// ------------------------------------------------------------------ router
const routes = { "": home, try: tryit, live: liveView, result: resultView, compare, lab: labView, example,
  experiments, negotiation: detail, results, structured: comparison, methodology, configuration: setup };
// Which navigation entry a route belongs to.
const NAV_OF = { "": "home", example: "home", live: "try", result: "try", negotiation: "experiments", structured: "results" };
const notFound = () => `${head("Page not found", `There is no page at <code>${esc(location.hash)}</code> in this lab.`)}<a class="cta" href="#/">Go to Home${icon("chev-r")}</a>`;
function parse() {
  const [, name = "", ...rest] = location.hash.replace(/^#/, "").split("/");
  return { name, arg: decodeURIComponent(rest.join("/")) };
}
async function render(animate = true) {
  const { name, arg } = parse();
  const view = Object.hasOwn(routes, name) ? routes[name] : notFound;
  const navKey = NAV_OF[name] ?? name;
  document.querySelectorAll(".rail nav a").forEach((a) => (a.dataset.route === navKey ? a.setAttribute("aria-current", "page") : a.removeAttribute("aria-current")));
  figN = 0;
  cleanups.forEach((f) => f()); cleanups = [];
  field.page(name === "" ? "overview" : ["live", "result", "negotiation"].includes(name) ? "negotiation" : name);
  if (name === "negotiation") { main.innerHTML = skeleton(); field.loading(true); }
  const html = await view(st.snap, arg);
  field.loading(false);
  const swap = () => { main.innerHTML = html; wire(name); animateView(name); };
  if (animate && document.startViewTransition && !document.hidden && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
    const t = document.startViewTransition(swap);
    t.ready.catch(() => {}); // aborted transitions still run swap
  } else swap();
}
window.addEventListener("hashchange", async () => { await render(); window.scrollTo(0, 0); main.focus({ preventScroll: true }); });

// ------------------------------------------------------------------ shared bits
const icon = (name) => `<svg class="icon" aria-hidden="true"><use href="#i-${name}"/></svg>`;
/** A keyboard-reachable definition: the tooltip shows on hover and focus, the label is read aloud. */
const info = (key, text = "") => { const d = DEF[key]; const name = d ? d.name : "About this", body = d ? defText(d) : text; return `<button type="button" class="hint" data-tip="${esc(body)}" aria-label="${esc(`${name}. ${body}`)}">${icon("info")}</button>`; };
/** Plain-language question first, the formal research name second. */
const term = (key) => `<span class="term"><b>${esc(DEF[key].plain)}</b><span class="formal">${esc(DEF[key].name)}</span></span>${info(key)}`;
const badge = (p, extra = "") => `<span class="badge ${p.tone === "muted" ? "" : p.tone} ${extra}" data-phase="${p.key}">${icon(p.tone === "good" ? "ready" : p.tone === "warn" ? "alert" : "await")}${esc(p.label)}</span>`;
const cond = (c) => `<span class="tag c-${esc(c)}"><code>${esc(c)}</code></span>`;
const fam = (model) => { const f = String(model ?? "").split(":")[0]; return `<span class="tag m-${esc(f)}">${esc(modelLabel(model))}</span>`; };
const head = (title, standfirst = "", extra = "") => `<header class="page-head"><h1>${esc(title)}</h1>${standfirst ? `<p class="standfirst">${standfirst}</p>` : ""}${extra}</header>`;
const blockHead = (title, note = "") => `<div class="block-head"><h2>${esc(title)}</h2>${note ? `<span class="note">${note}</span>` : ""}</div>`;
const notice = (kind, iconName, html) => `<p class="notice ${kind}">${icon(iconName)}<span>${html}</span></p>`;
const readoutDef = (key, value, basis = "") => `<div class="readout"><dt>${term(key)}</dt><dd>${value}</dd>${basis ? `<div class="basis">${basis}</div>` : ""}</div>`;
const readout = (label, value, basis = "") => `<div class="readout"><dt>${esc(label)}</dt><dd>${value}</dd>${basis ? `<div class="basis">${basis}</div>` : ""}</div>`;
const datasetSeg = (active) => `<div class="seg" role="group" aria-label="Dataset">${["pilot", "full"].map((d) => `<button type="button" data-dataset="${d}" aria-pressed="${d === active}">${d === "pilot" ? "Pilot" : "Full experiment"}</button>`).join("")}</div>`;
/** Display names for the two fixed participants; any other id is shown as recorded. */
const MODEL_NAME = { "claude-sonnet-4-6": "Claude Sonnet 4.6", "gpt-4.1-2025-04-14": "GPT-4.1" };
const modelName = (id) => MODEL_NAME[id] ?? id;
const COND_NAME = { baseline_v1: "Baseline", structured_v1: "Structured" };
/** One-line experiment state for the hero chip, from recorded data only. */
function statusLine(s) {
  const { pilot, full } = s.progress;
  return labStages(s).empty ? "not run yet, 0 negotiations recorded" : `${phase(s).label.toLowerCase()}, ${pilot.done}/${pilot.target} pilot and ${full.done}/${full.target} full-run negotiations`;
}
/** The step that follows this page on the first-visit route. */
const next = (href, label, why) => `<nav class="next" aria-label="Next step"><a href="${href}">Next: ${esc(label)}${icon("chev-r")}</a><span>${why}</span></nav>`;
const pilotNotice = notice("info", "info", "<b>Operational pilot (spec §8.4).</b> Three instances across the eight design cells. Pilot negotiations are excluded from the confirmatory dataset, so treat these values as checks on the harness, not findings.");

function register(rows, seeds, kind) {
  const filled = registerSlots(rows, seeds);
  let k = 0;
  return `<div class="register ${kind}" role="img" aria-label="${filled.size} of ${seeds.length * 8} negotiations recorded">${Array.from({ length: seeds.length * 8 }, (_, i) => `<i${filled.has(i) ? ` class="on" style="--i:${k++}"` : ""}></i>`).join("")}</div>`;
}

/** The instrument: pilot -> full run -> analysis, from recorded data only. */
function labStatus(s, compact = false, headline = null) {
  const g = labStages(s), { pilot, full } = s.progress, c = s.setup;
  const pilotRows = s.negotiations.filter((r) => r.mode === "pilot");
  const fullRows = s.negotiations.filter((r) => r.mode === "full");
  const fullSeeds = Array.from({ length: c.full.instances }, (_, i) => c.full.seeds[0] + i);
  const p = phase(s);
  const PILOT = { ready: ["await", "Not run yet"], running: ["ready", "Running"], partial: ["alert", `Stopped at ${pilot.done} of ${pilot.target}`], complete: ["check", "Complete"] }[g.pilot];
  const FULL = { locked: ["await", "Not started"], running: ["ready", `Running, ${full.done} of ${full.target}`], partial: ["lock", `${full.done} of ${full.target} recorded`], complete: ["check", "Complete, results unsealed"] }[g.full];
  // Readiness comes from the setup flags, so nothing implies code that does not exist yet.
  const ANALYSIS = !c.analysis_implemented ? ["await", "Not written yet"]
    : { awaiting: ["await", "Waiting for data"], sealed: ["lock", "Sealed until the full run completes"], pending: ["info", "Ready to run, not run yet"] }[g.analysis];
  const top = headline ?? { title: g.empty ? "Ready, not run yet" : p.label, lead: g.empty ? `The lab is ready, but the controlled experiment has not been executed yet: 0 of ${pilot.target} pilot and 0 of ${full.target} full-run negotiations recorded. This is the expected state before the first run, not an error. There are no results and no conclusions yet.` : `${pilot.done} of ${pilot.target} pilot and ${full.done} of ${full.target} full-run negotiations recorded.` };
  return `<div class="lab${compact ? " compact" : ""}">
    <div class="lab-head"><div><h2>${esc(top.title)}</h2>
      <p>${top.lead}</p>${top.why ? `<ul class="why">${top.why.map((w) => `<li>${w}</li>`).join("")}</ul>` : ""}</div>
      ${g.empty ? "" : badge(p)}</div>
    <div class="stages">
      <div class="stage"><h3>Pilot <small>${pilot.target} negotiations</small></h3><span class="role">Operational check — excluded from confirmatory analysis</span>
        <div class="state ${g.pilot}">${icon(PILOT[0])}${esc(PILOT[1])}</div>
        ${register(pilotRows, c.pilot.seeds, "pilot")}
        ${compact ? "" : `<div class="register-key"><span>8 design cells × 3 seeds${info("reg", "Each square is one preregistered negotiation: one seed in one design cell. It fills when that negotiation is stored. It shows completion only, never results.")}</span></div>`}
        <p>Checks that the experiment software and API pipeline work with the real models. Its values are never findings.</p></div>
      <div class="stage"><h3>Full experiment <small>${full.target} negotiations</small></h3><span class="role">Confirmatory dataset — used for the research analysis</span>
        <div class="state ${g.full}">${icon(FULL[0])}${esc(FULL[1])}</div>
        ${register(fullRows, fullSeeds, "full")}
        ${compact ? "" : `<div class="register-key"><span>${c.full.instances} instances × 8 cells${info("reg", "Each square is one preregistered negotiation: one instance in one design cell. It fills when stored, and shows completion only, never results.")}</span><span>seeds ${c.full.seeds[0]}–${c.full.seeds[1]}</span></div>`}
        <p>Produces the dataset used for the preregistered research analysis. It starts after the pilot, and results stay sealed until all ${full.target} are recorded (spec §8.9).${c.full.driver_implemented ? "" : " The script that runs it is not written yet."}</p></div>
      <div class="stage"><h3>Analysis</h3><span class="role">Statistical evaluation</span>
        <div class="state ${c.analysis_implemented ? g.analysis : "awaiting"}">${icon(ANALYSIS[0])}${esc(ANALYSIS[1])}</div>
        <p>Applies the preregistered statistical tests to the completed dataset: paired sign-flip permutation tests, bootstrap intervals and Holm correction across H1–H3, fixed before any data (spec §8.9).${c.analysis_implemented ? "" : " This code is not written yet; it runs only after the full experiment."}</p></div>
    </div></div>`;
}

// ------------------------------------------------------------------ 1. home
const heroMedia = () => `<div class="hero-media" aria-hidden="true">
    <video class="hero-video" muted loop playsinline disablepictureinpicture disableremoteplayback tabindex="-1" preload="${reducedMotion() ? "auto" : "metadata"}"${reducedMotion() ? "" : " autoplay"}>
      <source src="${HERO_VIDEO}" type="video/mp4"></video>
    <div class="hero-scrim"></div></div>`;

function home(s) {
  const flow = [
    ["Private preferences", "Each agent secretly values the resources differently."],
    ["Two AI agents", "Claude Sonnet 4.6 and GPT-4.1, one in each seat."],
    ["Negotiation", "They take turns making offers, for up to 10 turns."],
    ["Resource split", "An accepted offer decides who gets what."],
    ["Outcome", "Each side's value, and how close the total came to the best possible."],
  ];
  return `${heroMedia()}
  <header class="hero">
    <div class="hero-copy">
      <p class="eyebrow">Cooperation Lab · an AI negotiation laboratory</p>
      <h1 class="hero-q">Can AI agents cooperate when they don't know what the other agent wants?</h1>
      <p class="hero-sub">Watch two AI agents negotiate over shared resources with private preferences. Then test whether different negotiation instructions change the outcome.</p>
      <div class="hero-actions">
        <a class="cta" href="#/try">Try a negotiation${icon("chev-r")}</a>
        <a class="chip" href="#/experiments">Explore the research</a>
      </div>
    </div>
  </header>

  <section class="block" id="how">${blockHead("How it works")}
    <ol class="flow">${flow.map(([t, d]) => `<li><b>${t}</b><span>${d}</span></li>`).join("")}</ol>
    <p class="lead">Two agents want different things. Neither knows exactly what the other values. They must negotiate a division of the shared resources, and nothing is shared out unless they agree.</p>
    <div class="actions"><a class="cta" href="#/try">Try it${icon("chev-r")}</a><a class="btn" href="#/example">Read a full illustrative walkthrough</a></div>
  </section>

  <section class="block">${blockHead("What a negotiation looks like")}${preview()}</section>

  <section class="block">${blockHead("Three ways to use the lab")}
    <div class="modes">
      <a class="mode" href="#/try"><span class="kicker">Try it · sandbox</span><h3>Run your own negotiation</h3>
        <p>Choose the resources, each agent's private preferences and the negotiation instructions, then watch the two models negotiate live.</p>
        <p class="note">Sandbox experiments are interactive explorations and are not included in the reported research results.</p>
        <span class="go">Try a negotiation${icon("chev-r")}</span></a>
      <a class="mode" href="#/lab"><span class="kicker">Agent Lab · bring your agents</span><h3>Evaluate two agents</h3>
        <p>Connect your own agent over HTTP, or use a built-in one, and run it against another through the same scenarios, with seats and first move balanced. Every score is computed by the evaluator.</p>
        <p class="note">Exploratory: stored apart from the research results, and never ranks agents.</p>
        <span class="go">Open the Agent Lab${icon("chev-r")}</span></a>
      <a class="mode" href="#/experiments"><span class="kicker">Research · controlled experiment</span><h3>See the controlled experiment</h3>
        <p>The same kind of negotiation, repeated under a locked configuration, to test whether structured negotiation instructions change the outcome.</p>
        <p class="note">Status: ${esc(statusLine(s))}.</p>
        <span class="go">Explore the research${icon("chev-r")}</span></a>
    </div>
  </section>`;
}

/** Hand-written preview of what the lab shows. Never produced by a model, and labelled so. */
const PREVIEW = {
  pool: { books: 5, tickets: 8, apples: 4 },
  turns: [
    ["A", "Opening offer", "I'm willing to give up 3 books if I can receive 5 tickets.", { books: 2, tickets: 5, apples: 2 }],
    ["B", "Counter-offer", "I can accept 2 books, but I need at least 4 tickets.", { books: 3, tickets: 4, apples: 1 }],
    ["A", "Counter-offer", "Four tickets each works for me if I take all the apples.", { books: 3, tickets: 4, apples: 4 }],
    ["B", "Accepts", "Agreed.", null],
  ],
};
function preview() {
  const who = { A: ["Agent A", "Claude Sonnet 4.6", "claude"], B: ["Agent B", "GPT-4.1", "openai"] };
  const split = (a) => Object.entries(PREVIEW.pool).map(([c, q]) => `<span>${esc(c)}: A <b>${a[c]}</b> · B <b>${q - a[c]}</b></span>`).join("");
  return `<div class="preview" aria-label="Illustrative example, not experimental data">
    <div class="ex-banner" role="note">${icon("alert")}Illustrative example — not experimental data</div>
    <div class="pv-body">
      <p class="note">Shared pool: ${Object.entries(PREVIEW.pool).map(([c, q]) => `${q} ${esc(c)}`).join(", ")}. Written for this page to show the idea; no model produced these messages.</p>
      <ol class="chat">${PREVIEW.turns.map(([r, act, msg, a]) => { const [seat, name, f] = who[r]; return `<li class="bubble ${r} m-${f}">
        <div class="bh"><b>${seat}</b><span class="tag m-${f}">${name}</span><span class="act${a ? "" : " ACCEPT"}">${act}</span></div>
        <p>“${esc(msg)}”</p>${a ? `<div class="pv-split" aria-label="Proposed split, Agent A first">${split(a)}</div>` : ""}</li>`; }).join("")}</ol>
    </div></div>`;
}

// ------------------------------------------------------------------ 2. try a negotiation (sandbox)
const STEPS = ["Agents", "Resources", "Preferences", "Strategy", "Review"];
const STRAT = {
  baseline_v1: ["Baseline", "Agents negotiate using the baseline instructions: the rules of the game and their own private values, nothing more."],
  structured_v1: ["Structured", "Agents receive additional structured guidance for preference revelation, integrative trades, and disagreement/deadline reasoning."],
};
const RES_NAME = /^[a-z][a-z-]{0,19}$/;
const newDraft = () => ({ mode: "ai", agents: { A: "claude", B: "openai" }, first: "A", strategy: "structured_v1",
  pool: [["apples", 10], ["books", 8], ["tickets", 6]], points: { A: [50, 30, 20], B: [10, 30, 60] } });
const getJSON = async (url) => { const r = await fetch(url, { cache: "no-store" }); if (!r.ok) throw new Error(`${url} ${r.status}`); return r.json(); };
const seatKind = (d, r) => (d.mode === "scripted" ? "scripted" : d.agents[r]);
const seatLabel = (d, r) => (d.mode === "scripted" ? `Scripted test agent ${r}` : st.sbx?.models.find((m) => m.id === d.agents[r])?.label ?? d.agents[r]);
const sum = (xs) => xs.reduce((a, b) => a + b, 0);
const isInt = (v, lo, hi) => Number.isInteger(v) && v >= lo && v <= hi;
const sandboxNote = notice("", "info", "<b>Sandbox.</b> Sandbox experiments are interactive explorations and are not included in the reported research results.");

/** The first problem on a step, in words, or null. Mirrors dashboard/sandbox.py validate(). */
function stepProblem(d, step) {
  const lim = st.sbx.limits;
  if (step === 2) {
    if (d.pool.length < lim.min_resources || d.pool.length > lim.max_resources) return `Use between ${lim.min_resources} and ${lim.max_resources} resources.`;
    const names = d.pool.map(([n]) => n.trim().toLowerCase());
    const bad = names.find((n) => !RES_NAME.test(n));
    if (bad !== undefined) return `Resource name “${bad || "(empty)"}”: use 1–20 lowercase letters or hyphens.`;
    if (new Set(names).size !== names.length) return "Each resource needs a different name.";
    const q = d.pool.find(([, n]) => !isInt(n, 1, lim.max_quantity));
    if (q) return `“${q[0]}” needs a whole-number quantity from 1 to ${lim.max_quantity}.`;
  }
  if (step === 3) for (const r of "AB") {
    if (d.points[r].some((v) => !isInt(v, 0, 100))) return `Agent ${r}'s points must be whole numbers from 0 to 100.`;
    if (sum(d.points[r]) !== 100) return `Agent ${r}'s points add up to ${sum(d.points[r])}; they must add up to exactly 100.`;
  }
  return null;
}
/** Why Start is unavailable, or null. */
function startBlocker(d) {
  if (st.sbx.busy) return "Another sandbox negotiation is still running on this server. Wait for it to finish, then start yours.";
  if (d.mode === "ai") {
    const missing = [...new Set("AB".split("").map((r) => st.sbx.models.find((m) => m.id === d.agents[r])).filter((m) => !m.ready).map((m) => m.key_name))];
    if (missing.length) return `The server is missing ${missing.map((k) => `<code>${k}</code>`).join(" and ")}. Add ${missing.length > 1 ? "them" : "it"} to <code>.env</code> and restart the server, or choose <b>Scripted test agents</b> in step 1 to try the flow offline.`;
  }
  return null;
}

async function tryit(s, arg) {
  st.sbx = await getJSON("/api/sandbox/info");
  const d = (st.draft ??= newDraft());
  let step = Math.min(5, Math.max(1, Number.parseInt(arg, 10) || 1));
  const stuck = [2, 3].find((k) => k < step && stepProblem(d, k));
  if (stuck) step = stuck; // no skipping past a step that still needs fixing
  const body = [null, tryAgents, tryResources, tryPreferences, tryStrategy, tryReview][step](d, s);
  const blocker = step === 5 ? startBlocker(d) : null;
  return `${head("Try a negotiation", "Set up one negotiation between two agents, then watch it happen live.")}
  ${sandboxNote}
  <ol class="stepper" aria-label="Setup progress">${STEPS.map((t, i) => { const k = i + 1, cls = k < step ? "done" : k === step ? "now" : "";
    return `<li class="${cls}">${k < step ? `<a href="#/try/${k}">` : "<span>"}<i>${k < step ? icon("check") : k}</i>${t}${k < step ? "</a>" : "</span>"}</li>`; }).join("")}</ol>
  <section class="stepcard" aria-labelledby="step-title">
    <p class="stepno">Step ${step} of ${STEPS.length}</p>
    <h2 id="step-title">${["", "Who negotiates?", "What are the agents dividing?", "What does each agent want?", "How should the agents negotiate?", "Review and start"][step]}</h2>
    ${stuck ? notice("warn", "alert", "Fix this step before moving on.") : ""}
    ${body}
    ${blocker ? notice("warn", "alert", blocker) : ""}
    <p class="step-error" id="step-error" role="alert" hidden></p>
    <div class="stepnav">${step > 1 ? `<a class="btn" href="#/try/${step - 1}">${icon("chev-l")}Back</a>` : "<span></span>"}
      ${step < 5 ? `<button class="cta" type="button" data-step="${step}">Next: ${STEPS[step]}${icon("chev-r")}</button>`
        : `<button class="cta" type="button" id="start"${blocker ? " disabled" : ""}>Start negotiation${icon("chev-r")}</button>`}</div>
  </section>`;
}

function tryAgents(d) {
  const opt = (r) => st.sbx.models.map((m) => `<option value="${m.id}"${d.agents[r] === m.id ? " selected" : ""}>${esc(m.label)}</option>`).join("");
  const seat = (r) => { const m = st.sbx.models.find((x) => x.id === d.agents[r]);
    return `<div class="seatcard m-${esc(d.mode === "scripted" ? "scripted" : d.agents[r])}"><div class="sc-head"><b>Agent ${r}</b>${d.first === r ? `<span class="badge">makes the first offer</span>` : ""}</div>
      ${d.mode === "scripted" ? `<p class="sc-model">Scripted test agent ${r}</p><p class="note">Rule-based: asks for a large share, concedes a little each turn, accepts once an offer is good enough for it.</p>`
        : `<label class="field"><span>Model</span><select data-agent="${r}">${opt(r)}</select></label>
      <p class="note">${esc(m.provider)} · <code>${esc(m.model)}</code></p>
      <p class="keystate ${m.ready ? "ok" : "no"}">${icon(m.ready ? "check" : "alert")}${m.ready ? "Ready on this server" : `Needs ${esc(m.key_name)} in .env`}</p>`}</div>`; };
  return `<p class="lead">Agent A and Agent B are negotiation seats. A model can occupy either seat.</p>
    <fieldset class="choice"><legend>What should negotiate?</legend>
      <label class="opt"><input type="radio" name="mode" value="ai"${d.mode === "ai" ? " checked" : ""}><span><b>AI models</b><small>Claude Sonnet 4.6 and GPT-4.1, the two models of the research experiment, with its locked settings. Runs on the server with its API keys; at most $${st.sbx.max_cost_usd.toFixed(2)} per negotiation.</small></span></label>
      <label class="opt"><input type="radio" name="mode" value="scripted"${d.mode === "scripted" ? " checked" : ""}><span><b>Scripted test agents</b><small>An offline check of the flow: simple rule-based agents, not AI. Same engine and rules, no API keys, no cost.</small></span></label>
    </fieldset>
    <div class="seats2">${seat("A")}<div class="vs" aria-hidden="true">${icon("swap")}</div>${seat("B")}</div>
    <fieldset class="choice inline"><legend>Who makes the first offer?</legend>
      ${["A", "B"].map((r) => `<label class="opt"><input type="radio" name="first" value="${r}"${d.first === r ? " checked" : ""}><span><b>Agent ${r}</b></span></label>`).join("")}</fieldset>
    <p class="note">These are the agents this lab supports. Importing or configuring other agents isn't available: model settings stay locked to the experiment's.</p>`;
}

function tryResources(d) {
  const total = sum(d.pool.map(([, q]) => (Number.isInteger(q) ? q : 0)));
  return `<p class="lead">These resources are shared. The agents must negotiate how to split them: every unit goes to one agent or the other.</p>
    <div class="reslist"><div class="rhead" aria-hidden="true"><span>Resource</span><span>Quantity</span><span></span></div>
    ${d.pool.map(([n, q], i) => `<div class="resrow">
      <input type="text" data-rname="${i}" value="${esc(n)}" aria-label="Resource ${i + 1} name" maxlength="20" autocomplete="off" spellcheck="false">
      <input type="number" data-rqty="${i}" value="${Number.isInteger(q) ? q : ""}" min="1" max="${st.sbx.limits.max_quantity}" step="1" aria-label="${esc(n)} quantity">
      <button class="btn-x" type="button" data-remove="${i}" aria-label="Remove ${esc(n)}"${d.pool.length <= st.sbx.limits.min_resources ? " disabled" : ""}>${icon("x")}</button></div>`).join("")}</div>
    <div class="resfoot"><button class="btn" type="button" data-add${d.pool.length >= st.sbx.limits.max_resources ? " disabled" : ""}>Add a resource</button>
      <span class="pooltotal" id="pooltotal" aria-live="polite">${total} units in the pool</span></div>
    <p class="note">${st.sbx.limits.min_resources} to ${st.sbx.limits.max_resources} resources, 1 to ${st.sbx.limits.max_quantity} whole units each. Names: lowercase letters or hyphens.</p>`;
}

const pointsTotal = (d, r) => { const t = sum(d.points[r].map((v) => (Number.isInteger(v) ? v : 0))); return `<span class="${t === 100 ? "ok" : "no"}">${icon(t === 100 ? "check" : "alert")}${t} / 100 points</span>`; };
function tryPreferences(d) {
  const card = (r) => `<div class="prefcard"><div class="pc-head"><b>Agent ${r}</b><span class="sub">${esc(seatLabel(d, r))}</span>
      <span class="private">${icon("lock")}Private · hidden from Agent ${r === "A" ? "B" : "A"}</span></div>
    ${d.pool.map(([n], i) => `<label class="prow"><span class="pn">${esc(n)}</span>
      <input type="number" data-pt="${r}" data-i="${i}" value="${Number.isInteger(d.points[r][i]) ? d.points[r][i] : ""}" min="0" max="100" step="1" aria-label="Agent ${r} points for ${esc(n)}">
      <span class="pbar" aria-hidden="true"><i data-pbar="${r}${i}" style="--w:${(d.points[r][i] || 0) / 100}"></i></span></label>`).join("")}
    <p class="ptotal" id="total-${r}" aria-live="polite">${pointsTotal(d, r)}</p></div>`;
  return `<p class="lead">Each agent knows what it values, but not the other agent's private valuation. Give each agent 100 points to spread over the resources: more points means it cares more about that resource.</p>
    <div class="prefs">${card("A")}${card("B")}</div>
    <p class="note">You set both sides because you run this negotiation. During it, each agent only ever sees its own points; the other side's stay hidden until the result.</p>
    <p class="note">Tip: agents that care about different things can both do well by trading, so a split that suits both exists.</p>`;
}

function tryStrategy(d, s) {
  return `<fieldset class="choice cards"><legend class="vh">Negotiation instructions</legend>
    ${Object.entries(STRAT).map(([k, [name, text]]) => `<label class="opt c-${k}"><input type="radio" name="strategy" value="${k}"${d.strategy === k ? " checked" : ""}><span><b>${name}</b><small>${text}</small></span></label>`).join("")}
    </fieldset>
    <p class="lead">The negotiation rules stay the same. Only the instructions given to the agents change.</p>
    ${d.mode === "scripted" ? notice("warn", "alert", "Scripted test agents follow fixed rules and never read instructions, so the strategy can&#39;t change what they do. Choose <b>AI models</b> in step 1 to see its effect.") : ""}
    <details class="more"><summary>Show the exact instructions the structured strategy adds</summary><pre class="prompt">${esc(s.setup.structured_block)}</pre></details>`;
}

function tryReview(d) {
  const ai = d.mode === "ai";
  return `<dl class="review">
      <div><dt>Agents</dt><dd><b>A</b> ${esc(seatLabel(d, "A"))} <span class="vs">vs</span> <b>B</b> ${esc(seatLabel(d, "B"))}<span class="sub">Agent ${d.first} makes the first offer</span></dd></div>
      <div><dt>Resources</dt><dd>${d.pool.map(([n, q]) => `${q} ${esc(n)}`).join(" · ")}</dd></div>
      <div><dt>Private preferences</dt><dd>${icon("check")} Configured, and hidden from the other agent
        <span class="sub">A: ${d.pool.map(([n], i) => `${esc(n)} ${d.points.A[i]}`).join(", ")} · B: ${d.pool.map(([n], i) => `${esc(n)} ${d.points.B[i]}`).join(", ")}</span></dd></div>
      <div><dt>Strategy</dt><dd>${STRAT[d.strategy][0]}<span class="sub">${d.strategy === "structured_v1" ? "Structured negotiation instruction strategy" : "Baseline instructions"}</span></dd></div>
      <div><dt>Rounds</dt><dd>Up to ${st.sbx.max_rounds} turns<span class="sub">Locked, as in the experiment</span></dd></div>
      <div><dt>Cost</dt><dd>${ai ? `At most $${st.sbx.max_cost_usd.toFixed(2)}` : "None"}<span class="sub">${ai ? "Hard cap per sandbox run, charged to the server's API keys" : "Scripted agents make no API calls"}</span></dd></div>
    </dl>
    ${notice("info", "info", "This sandbox run is exploratory and is not included in the reported research experiment.")}`;
}

// ------------------------------------------------------------------ 3. live negotiation
const runN = (run, r = run.result ?? {}) => ({ ...r, ...(run.config.agents.A === run.config.agents.B ? { family_A: "seatA", family_B: "seatB" } : { family_A: run.config.agents.A, family_B: run.config.agents.B }),
  model_A: run.labels.A, model_B: run.labels.B, max_rounds: run.max_rounds, resource_pool: run.config.pool });
/** Live turns as timeline events: an offer before the deadline stands until the next offer. */
function liveEvents(run) {
  let standing = null;
  return run.turns.map((t) => {
    const e = { ...t, value_A: null, value_B: null };
    if (t.action === "OFFER") { e.standing = t.turn < run.max_rounds; if (e.standing) standing = { actor: t.actor, turn: t.turn }; else e.note = "Final-turn offer: can't be answered"; }
    if (t.action === "ACCEPT") e.accepted = standing;
    return e;
  });
}
/** The engine's invalid_reason codes (src/negotiation/protocol.py), in words. */
const INVALID_TEXT = {
  malformed_output: "its reply could not be read as a valid action",
  invalid_allocation: "it made an offer that doesn't divide the resources correctly",
  illegal_accept: "it tried to accept when there was no offer from the other agent to accept",
};
const invalidText = (code) => INVALID_TEXT[code] ?? "its turn broke the negotiation rules";
const OUT_HEAD = { agreed: "Agreement reached", timeout: "No agreement: the deadline passed", walked_away: "No agreement: an agent walked away", invalid_action: "Stopped: an agent broke the rules" };

async function liveView(s, id) {
  const res = await fetch(`/api/sandbox/runs/${encodeURIComponent(id)}`, { cache: "no-store" });
  if (!res.ok) return `${head("Negotiation not found", "This sandbox negotiation doesn't exist on this server.")}<a class="cta" href="#/try">Try a negotiation${icon("chev-r")}</a>`;
  const run = await res.json();
  if (run.status === "finished") return resultView(s, id, run);
  st.liveRun = run;
  const n = runN(run), seat = (r) => { const k = run.config.agents[r];
    return `<div class="lagent m-${esc(k)}" id="agent-${r}"><div class="who"><b>Agent ${r}</b><span class="tag m-${esc(k)}">${esc(run.labels[r])}</span></div>
      ${run.config.first_mover === r ? `<span class="badge">made the first offer</span>` : ""}
      <p class="private">${icon("lock")}Private preferences · hidden from Agent ${r === "A" ? "B" : "A"}</p>
      <details class="reveal"><summary>Show (you set these)</summary><ul>${Object.keys(run.config.pool).map((c) => `<li>${esc(c)} <b>${run.config.points[r][c]}</b> pts</li>`).join("")}</ul></details>
      <p class="speaking" aria-hidden="true"><i></i>Thinking…</p></div>`; };
  return `<a class="back" href="#/try/5">${icon("chev-l")}Setup</a>
  <header class="livebar" aria-live="polite">
    <div><span class="lb-k">Negotiation</span><b id="lb-round">Round 0 / ${run.max_rounds}</b></div>
    <div><span class="lb-k">Status</span><b id="lb-state" class="lb-state running"><i></i>In progress</b></div>
    <div class="lb-now"><span class="lb-k">Now</span><b id="lb-now">Connecting to Agent ${run.config.first_mover}…</b></div>
    <div><span class="lb-k">Strategy</span><b>${STRAT[run.config.strategy][0]}</b></div>
    <div class="lb-prop"><span class="lb-k">On the table · A–B</span><b id="lb-prop">No offer yet</b></div>
  </header>
  <p class="note">${icon("info")} Sandbox run: exploratory, not part of the reported research. Messages appear when each agent's full turn arrives; nothing is streamed or invented in between.</p>
  <div class="livegrid">
    <div class="live-main"><h2 class="vh">Transcript</h2><div class="tl" id="live-tl"></div><div id="live-end"></div></div>
    <aside class="live-side">
      ${seat("A")}${seat("B")}
      <section class="proposal" aria-labelledby="prop-h"><h3 id="prop-h">Current proposal</h3><p class="note" id="prop-note">No offer on the table yet.</p>
        <div class="prop-head" aria-hidden="true"><span></span><span class="qa">A</span><span></span><span class="qb">B</span></div>
        ${Object.entries(run.config.pool).map(([c, q]) => `<div class="prop-row" data-cat="${esc(c)}"><span class="cat">${esc(c)}</span><span class="qa">–</span>
          <div class="bar" style="--ca:${famColor(n.family_A)};--cb:${famColor(n.family_B)}" role="img" aria-label="${esc(c)}: no offer yet"><div class="a" style="--to:.5"></div></div><span class="qb">–</span></div>`).join("")}
      </section>
    </aside>
  </div>`;
}

function pollLive(id) {
  let shown = 0, stopped = false;
  cleanups.push(() => { stopped = true; });
  const tick = async () => {
    if (stopped) return;
    let run;
    try { run = await getJSON(`/api/sandbox/runs/${encodeURIComponent(id)}`); }
    catch { document.getElementById("lb-now").textContent = "Connection to the server lost. Retrying…"; return later(tick, 2000); }
    if (stopped || !document.getElementById("live-tl")) return; // the live view has been left
    shown = paintLive(run, shown);
    if (run.status === "running") later(tick, 700);
    else finishLive(run);
  };
  tick();
}

/** Brings the live screen up to date with the run; returns how many turns are on screen. */
function paintLive(run, shown) {
  const n = runN(run), evs = liveEvents(run), tl = document.getElementById("live-tl");
  const offers = evs.filter((e) => e.action === "OFFER");
  for (let i = shown; i < evs.length; i++) {
    const prev = [...evs.slice(0, i)].reverse().find((e) => e.action === "OFFER")?.allocation ?? null;
    tl.insertAdjacentHTML("beforeend", turnRow(evs[i], n, i, prev));
    const row = tl.lastElementChild;
    if (motionOn()) {
      M.animate(row, { opacity: [0, 1], transform: ["translateY(10px)", "none"] }, { duration: 0.45, ease: EASE });
      row.querySelectorAll(".bar").forEach((bar) => M.animate(bar.querySelector(".a"), { transform: [`scaleX(${+bar.style.getPropertyValue("--from")})`, `scaleX(${+bar.style.getPropertyValue("--to")})`] }, { duration: 0.7, delay: 0.1, ease: EASE }));
    }
    field.turn(evs[i].actor);
    row.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "nearest" });
  }
  const last = evs.at(-1), round = run.thinking?.round ?? last?.turn ?? 0;
  document.getElementById("lb-round").textContent = `Round ${round} / ${run.max_rounds}`;
  const now = document.getElementById("lb-now");
  if (run.thinking) {
    const r = run.thinking.actor, secs = Math.max(0, Math.round(Date.now() / 1000 - run.thinking.since));
    now.textContent = `Agent ${r} (${run.labels[r]}) is ${evs.length ? "responding" : "thinking"}… ${secs} s · ${run.max_rounds - run.thinking.round} turns left after this`;
  } else if (run.status === "running") now.textContent = evs.length ? "Updating allocation…" : `Connecting to Agent ${run.config.first_mover}…`;
  "AB".split("").forEach((r) => document.getElementById(`agent-${r}`)?.classList.toggle("active", run.thinking?.actor === r));
  // The allocation on the table: the latest standing offer (or the accepted one).
  const table = [...offers].reverse().find((e) => e.standing);
  if (table) {
    document.getElementById("prop-note").textContent = last?.action === "ACCEPT" ? `Accepted: Agent ${table.actor}'s offer from round ${table.turn} is the final split.` : `Agent ${table.actor}'s offer from round ${table.turn} is on the table.`;
    document.getElementById("lb-prop").textContent = `${last?.action === "ACCEPT" ? "Accepted: " : ""}Agent ${table.actor}'s offer, round ${table.turn}: ${Object.keys(run.config.pool).map((c) => `${c} ${table.allocation?.A?.[c] ?? "–"}–${table.allocation?.B?.[c] ?? "–"}`).join(" · ")}`;
    document.querySelectorAll(".prop-row").forEach((row) => {
      const c = row.dataset.cat, q = run.config.pool[c], a = table.allocation?.A?.[c], b = table.allocation?.B?.[c];
      row.querySelector(".qa").textContent = a ?? "–"; row.querySelector(".qb").textContent = b ?? "–";
      const bar = row.querySelector(".bar"); bar.setAttribute("aria-label", `${c}: Agent A ${a}, Agent B ${b} of ${q}`);
      bar.querySelector(".a").style.setProperty("--to", q ? (a ?? 0) / q : 0);
    });
  }
  return evs.length;
}

function finishLive(run) {
  "AB".split("").forEach((r) => document.getElementById(`agent-${r}`)?.classList.remove("active"));
  const state = document.getElementById("lb-state"), end = document.getElementById("live-end");
  state.classList.remove("running");
  if (run.status === "finished") {
    const o = run.result.outcome;
    state.textContent = OUTCOME_LABEL[o].toUpperCase();
    document.getElementById("lb-now").textContent = OUT_HEAD[o];
    end.innerHTML = `<div class="live-done">${icon(o === "agreed" ? "check" : "x")}<div><b>${OUT_HEAD[o]}</b><p>Opening the result…</p></div><a class="cta" href="#/result/${run.id}">See the result${icon("chev-r")}</a></div>`;
    field.outcome(o);
    later(() => { if (location.hash === `#/live/${run.id}`) location.replace(`#/result/${run.id}`); }, 1800);
  } else {
    state.textContent = "STOPPED";
    document.getElementById("lb-now").textContent = run.error?.title ?? "Stopped";
    end.innerHTML = `<div class="live-error" role="alert">${icon("alert")}<div><b>${esc(run.error?.title ?? "Stopped")}</b><p>${esc(run.error?.message ?? "")}</p>
      ${run.error?.detail ? `<details class="diag"><summary>Technical detail</summary><pre>${esc(run.error.detail)}</pre></details>` : ""}
      <div class="actions"><a class="cta" href="#/try/5">Try again${icon("chev-r")}</a><a class="btn" href="#/try/1">Change the setup</a></div></div></div>`;
  }
}

// ------------------------------------------------------------------ 4. result
async function resultView(s, id, run = null) {
  if (!run) {
    const res = await fetch(`/api/sandbox/runs/${encodeURIComponent(id)}`, { cache: "no-store" });
    if (!res.ok) return `${head("Result not found", "This sandbox negotiation doesn't exist on this server.")}<a class="cta" href="#/try">Try a negotiation${icon("chev-r")}</a>`;
    run = await res.json();
  }
  if (run.status !== "finished") return liveView(s, id);
  st.resultRun = run;
  const r = run.result, n = runN(run), agreed = r.outcome === "agreed", pool = run.config.pool;
  const other = run.config.strategy === "structured_v1" ? "baseline_v1" : "structured_v1";
  const best = (c) => { const a = run.config.points.A[c], b = run.config.points.B[c]; return a === b ? "either agent" : a > b ? "A" : "B"; };
  const bundle = (seat) => `<div class="bundle m-${esc(run.config.agents[seat])}"><div class="who"><b>Agent ${seat}</b><span class="tag m-${esc(run.config.agents[seat])}">${esc(run.labels[seat])}</span></div>
    <ul>${Object.keys(pool).map((c) => `<li><span>${esc(c)}</span><b>${r.final_allocation[seat][c]}</b><span class="sub">of ${pool[c]}</span></li>`).join("")}</ul>
    <p class="score">${fmt(seat === "A" ? r.utility_A : r.utility_B, 1)}<small> / 100 points by its own preferences</small></p></div>`;
  let prev = null;
  const turns = r.events.map((e, i) => { const h = turnRow(e, n, i, prev); if (e.action === "OFFER" && e.allocation) prev = e.allocation; return h; }).join("");
  return `<a class="back" href="#/try">${icon("chev-l")}Try a negotiation</a>
  <header class="result-head ${agreed ? "ok" : "no"}"><span class="res-icon">${icon(agreed ? "check" : "x")}</span>
    <div><p class="eyebrow">Sandbox result · ${STRAT[run.config.strategy][0]} instructions</p><h1>${OUT_HEAD[r.outcome]}</h1>
    <p class="standfirst">${agreed ? `After ${r.rounds} of ${r.max_rounds} turns, Agent ${r.events.at(-1).actor} accepted the offer on the table.`
      : r.outcome === "timeout" ? `No offer was accepted within ${r.max_rounds} turns, so nothing was divided and both agents score 0.`
      : r.outcome === "walked_away" ? `Agent ${r.events.at(-1).actor} walked away on turn ${r.rounds}, so nothing was divided and both agents score 0.`
      : `On turn ${r.rounds}, ${r.events.at(-1)?.action === "INVALID" ? `Agent ${esc(r.events.at(-1).actor)}` : "an agent"} broke the rules: ${invalidText(r.invalid_reason)}. The engine never repairs or retries a turn, so the negotiation ended with nothing divided and both agents score 0.`}</p></div></header>

  ${agreed ? `<section class="block">${blockHead("Final allocation")}<div class="bundles">${bundle("A")}${bundle("B")}</div></section>` : ""}

  <section class="block">${blockHead("How did they do?")}
    <dl class="readouts">
      ${readout("Agreement", agreed ? `${icon("check")} Reached` : `${icon("x")} Not reached`)}
      ${readout("Turns used", `${r.rounds}<small> / ${r.max_rounds}</small>`)}
      ${readout("Agent A's score", `${fmt(r.utility_A, 1)}<small> / 100</small>`, "by A's own private points")}
      ${readout("Agent B's score", `${fmt(r.utility_B, 1)}<small> / 100</small>`, "by B's own private points")}
      ${readout("Combined value", `${fmt(r.utility_A + r.utility_B, 1)}<small> / ${fmt(r.optimal_welfare, 1)}</small>`, "vs the best possible split")}
      ${readout("Efficiency", pct(r.rwe, 1), "how much of the best possible total they reached")}
    </dl>
    <p class="note" style="margin-top:10px">The best possible split gives each resource to the agent that values it more; here that is ${Object.keys(pool).map((c) => `${esc(c)} → ${best(c)}`).join(", ")}.</p>
    <details class="more"><summary>Research metrics, with their formal names</summary>
      <dl class="readouts" style="margin-top:12px">
        ${readoutDef("rwe", fmt(r.rwe))}${readoutDef("ew", fmt(r.ew))}
        ${readoutDef("equitability", fmt(r.equitability), agreed ? "" : "undefined without agreement")}
        ${readoutDef("envy_free_rate", r.envy_free === null || r.envy_free === undefined ? "—" : r.envy_free ? "Envy-free" : "Envy", agreed ? "neither prefers the other's share?" : "undefined without agreement")}
      </dl></details>
  </section>

  <section class="block">${blockHead("What each agent privately wanted", "Revealed now; during the negotiation each agent saw only its own points")}
    <div class="table-wrap"><table><thead><tr><th>Resource</th><th class="num">In the pool</th><th class="num">Agent A's points</th><th class="num">Agent B's points</th></tr></thead><tbody>
      ${Object.keys(pool).map((c) => `<tr><td>${esc(c)}</td><td class="num">${pool[c]}</td><td class="num">${run.config.points.A[c]}</td><td class="num">${run.config.points.B[c]}</td></tr>`).join("")}
    </tbody></table></div></section>

  <section class="block">${blockHead("The negotiation, turn by turn")}
    <div class="tl" id="timeline" data-outcome="${esc(r.outcome)}">${turns}${verdictRow(n, r.events.length)}</div></section>

  <nav class="whatnext" aria-label="What next">
    <a class="cta" href="#/try/1" data-again>Run another negotiation${icon("chev-r")}</a>
    <a class="btn" href="#/try/5" data-flip="${other}">Try the ${STRAT[other][0].toLowerCase()} strategy on the same setup</a>
    <a class="btn" href="#/compare">Compare results</a>
    <a class="btn" href="#/experiments">Explore the research</a>
  </nav>
  ${notice("", "info", "Sandbox result: an exploratory run, not part of the reported research. One negotiation says little on its own, because model answers vary from run to run.")}`
    .replaceAll(" data-eval hidden", " data-eval"); // the hidden values are revealed once it is over
}

/** Load a finished run's setup back into the Try-it draft. */
function draftFrom(run, strategy = run.config.strategy) {
  const c = run.config, names = Object.keys(c.pool), scripted = c.agents.A === "scripted";
  st.draft = { mode: scripted ? "scripted" : "ai", agents: scripted ? { A: "claude", B: "openai" } : { ...c.agents }, first: c.first_mover, strategy,
    pool: names.map((k) => [k, c.pool[k]]), points: { A: names.map((k) => c.points.A[k]), B: names.map((k) => c.points.B[k]) } };
}

// ------------------------------------------------------------------ 5. compare (sandbox runs)
async function compare() {
  const runs = (await getJSON("/api/sandbox/runs")).filter((r) => r.status === "finished" && r.result);
  const top = head("Compare", "Put two of your sandbox negotiations side by side, for example the same setup under the baseline and the structured instructions.");
  if (!runs.length) return `${top}<div class="empty"><h2>No sandbox negotiations yet</h2><p>Run one, then run the same setup with the other strategy, and compare them here.</p><a class="cta" href="#/try">Try a negotiation${icon("chev-r")}</a>
    <p class="note">Looking for the research comparison? <a href="#/structured">Structured vs baseline</a> shows the controlled experiment, once it has data.</p></div>`;
  const byId = (st.cmpRuns = new Map(runs.map((r) => [r.id, r])));
  if (!st.cmp || !byId.has(st.cmp[0]) || (st.cmp[1] && !byId.has(st.cmp[1]))) {
    const b = runs.find((r) => r.config.strategy === "baseline_v1"), s2 = runs.find((r) => r.config.strategy === "structured_v1");
    st.cmp = b && s2 ? [b.id, s2.id] : [runs[0].id, runs[1]?.id ?? null];
  }
  const [L, R] = st.cmp.map((id) => byId.get(id) ?? null);
  const scripted = [L, R].some((r) => r?.config.agents.A === "scripted");
  const when = (t) => new Date(t * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
  const name = (r) => `${STRAT[r.config.strategy][0]} · ${r.labels.A} vs ${r.labels.B} · ${OUTCOME_LABEL[r.result.outcome]} · ${when(r.created)}`;
  const pick = (k) => `<label class="field"><span>${k ? "Right" : "Left"}</span><select data-cmp="${k}">${runs.map((r) => `<option value="${r.id}"${st.cmp[k] === r.id ? " selected" : ""}>${esc(name(r))}</option>`).join("")}</select></label>`;
  const same = L && R && JSON.stringify([L.config.pool, L.config.points, L.config.agents, L.config.first_mover]) === JSON.stringify([R.config.pool, R.config.points, R.config.agents, R.config.first_mover]);
  const v = (r) => r.result, row = (label, f, bar) => `<tr><th scope="row">${label}</th>${[L, R].map((r) => `<td class="num">${r ? f(v(r)) : "—"}${r && bar ? `<span class="cbar" aria-hidden="true"><i style="--w:${Math.max(0, Math.min(1, bar(v(r))))}"></i></span>` : ""}</td>`).join("")}</tr>`;
  return `${top}
  ${notice("warn", "alert", "<b>Exploratory sandbox comparison.</b> These are actual sandbox runs, not controlled research findings. One run per strategy can't show that a strategy works better: model answers vary from run to run.")}
  <div class="filters">${pick(0)}${runs.length > 1 ? pick(1) : ""}</div>
  ${!R ? notice("info", "info", `Only one finished run so far. <a href="#/try/5" data-flip-id="${L.id}">Run the same setup with the ${STRAT[L.config.strategy === "structured_v1" ? "baseline_v1" : "structured_v1"][0].toLowerCase()} strategy</a> to compare.`) : ""}
  ${scripted ? notice("warn", "alert", "<b>Scripted test agents never read the instructions.</b> A scripted run's strategy is only a label on its configuration, so this comparison is not evidence that one strategy performs better. Use AI models for that.") : ""}
  ${L && R ? same ? notice("info", "check", L.config.strategy === R.config.strategy ? "Same setup and same strategy: any difference comes from the models' variability."
      : scripted ? "Same agents, resources and preferences. The configurations differ only in the instruction setting, which these scripted agents ignore."
      : "Same agents, resources and preferences: only the instructions differ.")
    : notice("warn", "alert", "These runs used different agents, resources, preferences or opening seat, so a difference isn't only due to the strategy.") : ""}
  <div class="table-wrap"><table class="cmp"><thead><tr><th></th>${[L, R].map((r) => `<th class="num">${r ? `<span class="tag c-${r.config.strategy}">${STRAT[r.config.strategy][0]}</span>` : "—"}</th>`).join("")}</tr></thead><tbody>
    ${row("Agreement", (x) => (x.outcome === "agreed" ? `${icon("check")} Reached` : `${icon("x")} ${OUTCOME_LABEL[x.outcome]}`))}
    ${row("Turns used", (x) => `${x.rounds} / ${x.max_rounds}`, (x) => x.rounds / x.max_rounds)}
    ${row("Agent A's score", (x) => fmt(x.utility_A, 1), (x) => x.utility_A / 100)}
    ${row("Agent B's score", (x) => fmt(x.utility_B, 1), (x) => x.utility_B / 100)}
    ${row("Combined value", (x) => `${fmt(x.utility_A + x.utility_B, 1)} <span class="sub">of ${fmt(x.optimal_welfare, 1)}</span>`, (x) => (x.utility_A + x.utility_B) / (x.optimal_welfare || 1))}
    ${row("Efficiency", (x) => pct(x.rwe, 1), (x) => x.rwe)}
    ${row("How the worse-off agent did", (x) => fmt(x.ew), (x) => x.ew)}
  </tbody></table></div>
  <div class="actions">${[L, R].filter(Boolean).map((r) => `<a class="btn" href="#/result/${r.id}">Open the ${STRAT[r.config.strategy][0].toLowerCase()} run</a>`).join("")}<a class="cta" href="#/try">Run another${icon("chev-r")}</a></div>
  <p class="note" style="margin-top:18px">For evidence, see the controlled experiment: <a href="#/structured">Structured vs baseline</a> compares the two strategies over hundreds of preregistered negotiations once they are recorded.</p>`;
}

// ------------------------------------------------------------------ 6. agent lab (repeated evaluation)
const LAB_KINDS = [["scripted", "Scripted test agent"], ["claude", "Claude Sonnet 4.6 (server key)"], ["openai", "GPT-4.1 (server key)"],
  ["http", "Custom HTTP endpoint"], ["gemini", "Google Gemini (not available yet)"], ["ollama", "Ollama (not available yet)"]];
const labAgent = () => ({ kind: "scripted", url: "", label: "", token: "", timeout_s: 20 });
const newLab = () => ({ agents: { X: labAgent(), Y: labAgent() }, blocks: 3, categories: 3, strategy: "baseline_v1", probe: {} });
const pct0 = (x) => (x === null || x === undefined ? "—" : pct(x, 0));
/** A distribution in words: median, and the IQR only when there are enough observations. */
const distText = (d, f = (x) => fmt(x, 2)) => !d || !d.n ? `<span class="sub">not observed</span>`
  : `${f(d.median)}<span class="sub dist">${d.q1 !== undefined ? `IQR ${f(d.q1)}–${f(d.q3)} · ` : ""}n=${d.n}${d.spread ? " · spread: insufficient observations" : ""}</span>`;
const labName = (a) => esc(a?.label ?? "—");
/** Two agents of one kind share a label; suffix X and Y so every table stays unambiguous. */
const labAgents = (ag) => (ag?.X && ag.X.label === ag.Y.label ? { X: { ...ag.X, label: `${ag.X.label} X` }, Y: { ...ag.Y, label: `${ag.Y.label} Y` } } : ag);
const ABORT_LABEL = { agent_failure: "agent failure", provider_failure: "provider failure", budget_exhausted: "cost cap" };
const SEALED_NOTE = "While an evaluation runs, the lab shares only progress: no scenarios, scores, transcripts or endpoints, for any evaluation. Evaluator-private details (valuations, seeds, utilities, the running evaluation's private state) stay sealed until it ends; evaluation ids, agent labels and progress counts remain visible.";

function labProblem(d, info) {
  if (!info.ready) return esc(info.error);
  for (const id of "XY") {
    const a = d.agents[id];
    if (a.kind === "gemini" || a.kind === "ollama") return `Agent ${id}: this provider has no native adapter yet. Connect it through a Custom HTTP endpoint instead.`;
    if (a.kind === "http" && !/^https?:\/\/\S+$/.test(a.url.trim())) return `Agent ${id}: enter the endpoint's http:// or https:// URL.`;
    if (a.kind === "http" && !/^[A-Za-z0-9 ._-]{1,40}$/.test(a.label.trim())) return `Agent ${id}: give it a name (letters, digits, spaces, dots, dashes or underscores).`;
    const m = st.sbx.models.find((x) => x.id === a.kind);
    if (m && !m.ready) return `Agent ${id} needs <code>${esc(m.key_name)}</code> in the server's <code>.env</code>.`;
  }
  if (!isInt(d.blocks, 1, info.max_blocks)) return `Choose 1 to ${info.max_blocks} blocks.`;
  if (!isInt(d.categories, 2, 5)) return "Choose 2 to 5 resource types.";
  return null;
}
const labBody = (d) => ({ agents: Object.fromEntries(["X", "Y"].map((id) => { const a = d.agents[id];
    return [id, a.kind === "http" ? { kind: "http", url: a.url.trim(), label: a.label.trim(), timeout_s: a.timeout_s, ...(a.token ? { token: a.token } : {}) } : { kind: a.kind }]; })),
  scenario: { blocks: d.blocks, categories: d.categories }, strategy: d.strategy });

async function labView(s, arg) {
  if (arg) return labReport(arg);
  st.sbx = await getJSON("/api/sandbox/info");
  const [info, past] = await Promise.all([getJSON("/api/lab/info"), getJSON("/api/lab/evaluations")]);
  const d = (st.lab ??= newLab());
  const problem = labProblem(d, info), n = d.blocks * 4, scripted = ["X", "Y"].every((id) => d.agents[id].kind === "scripted");
  const card = (id) => { const a = d.agents[id], p = d.probe[id];
    return `<div class="seatcard m-${id === "X" ? "seatA" : "seatB"}"><div class="sc-head"><b>Agent ${id}</b></div>
      <label class="field"><span>Agent</span><select data-lab-kind="${id}">${LAB_KINDS.map(([k, t]) => `<option value="${k}"${a.kind === k ? " selected" : ""}${k === "gemini" || k === "ollama" ? " disabled" : ""}>${esc(t)}</option>`).join("")}</select></label>
      ${a.kind === "scripted" ? `<p class="note">Rule-based test agent: asks for a large share, concedes a little each turn. Ignores instructions; not AI.</p>` : ""}
      ${["claude", "openai"].includes(a.kind) ? (() => { const m = st.sbx.models.find((x) => x.id === a.kind);
        return `<p class="note">${esc(m.provider)} · <code>${esc(m.model)}</code>, locked research settings. Called by the server with its own key; calls stop once $2 is reached for the evaluation (the last call may go slightly over).</p>
        <p class="keystate ${m.ready ? "ok" : "no"}">${icon(m.ready ? "check" : "alert")}${m.ready ? "Ready on this server" : `Needs ${esc(m.key_name)} in .env`}</p>`; })() : ""}
      ${a.kind === "http" ? `<label class="field"><span>Endpoint URL</span><input type="url" data-lab="${id}.url" value="${esc(a.url)}" placeholder="http://127.0.0.1:8900/" autocomplete="off" spellcheck="false"></label>
        <label class="field"><span>Name shown in the report</span><input type="text" data-lab="${id}.label" value="${esc(a.label)}" maxlength="40" autocomplete="off"></label>
        <label class="field"><span>Bearer token (optional)</span><input type="password" data-lab="${id}.token" value="${esc(a.token)}" autocomplete="off"></label>
        <p class="note">Used for this evaluation only: kept in server memory, never stored, never shown again, never sent to the other agent. Over an <code>http://</code> address to another machine it travels unencrypted.</p>
        <div class="actions"><button class="btn" type="button" data-probe="${id}">Test connection</button></div>
        ${p ? `<p class="keystate ${p.ok ? "ok" : "no"}" role="status">${icon(p.ok ? "check" : "alert")}${p.ok ? `Answered ${esc(p.action)} in ${fmt(p.latency_s, 2)} s to a synthetic test turn.` : `${esc(p.title)}: ${esc(p.message)}`}</p>` : ""}` : ""}</div>`; };
  return `${head("Agent Lab", "Bring two agents, run them through controlled hidden scenarios with seats and first move balanced, and read what the evaluator measured.")}
  ${notice("warn", "alert", "<b>Exploratory evaluation.</b> Agent Lab runs are stored apart from the research data and are never part of the controlled experiment's results. The lab measures; it does not rank agents.")}
  ${info.sealed ? notice("", "info", `<b>An evaluation is running.</b> ${SEALED_NOTE}`) : ""}
  <section class="block">${blockHead("Who knows what")}
    <div class="knows"><div><h3>Each agent is sent</h3><p>The shared pool, its <b>own</b> private points, the public transcript, the round and the deadline. Nothing else.</p></div>
    <div><h3>Kept from every agent</h3><p>The other agent's points and the best possible split. Scenarios come from seeds keyed with the server's secret, so they cannot be reconstructed, and no agent ever negotiates the same scenario twice.</p></div>
    <div><h3>The evaluator knows</h3><p>Everything: both private valuations and the best possible split, so every score below is computed, not judged.</p></div></div>
    <details class="more"><summary>What the lab cannot prevent</summary>
      <p class="note">An agent running on this computer could read the lab's files and settings: run agents you do not trust on another machine. Each agent meets the same opponent in every negotiation, so an adaptive agent can learn how its opponent behaves; the negotiations are not independent for such agents. Two endpoints run by one operator could coordinate outside the lab.</p></details></section>

  <section class="stepcard" aria-labelledby="lab-agents"><h2 id="lab-agents">1 · Agents</h2>
    <div class="seats2">${card("X")}<div class="vs" aria-hidden="true">${icon("swap")}</div>${card("Y")}</div>
    <details class="more"><summary>Connecting your own agent: the HTTP contract</summary>
      <p class="note">Each turn the server POSTs your agent's observation as JSON (<code>contract: negotiation-lab/1</code>): <code>role</code>, <code>resource_pool</code>, <code>own_valuation</code>, <code>round</code>, <code>max_rounds</code>, <code>rounds_remaining</code>, <code>is_final_round</code>, <code>allowed_actions</code>, <code>standing_offer</code>, <code>transcript</code>, and the exact <code>instructions</code> and <code>prompt</code> the research models receive. The whole reply must arrive within 20 s and be at most 64 KiB:</p>
      <pre class="prompt">{"action_type": "OFFER" | "ACCEPT" | "WALK_AWAY",
 "allocation": {"widgets": 12, "gadgets": 5, "components": 30},   // units YOU get, OFFER only
 "message": "short public message"}</pre>
      <p class="note">The engine validates every reply: an unreadable reply or an impossible split ends that negotiation as an invalid action. No reply (network error, timeout, HTTP error) is an <b>agent failure</b>: counted against that agent, never turned into a negotiation outcome, and its block is left out of every aggregate. A reference agent ships with the lab: <code>python -m lab.example_http_agent --port 8900</code>. Only a URL is accepted: no code is uploaded or run.</p></details>
  </section>

  <section class="stepcard" aria-labelledby="lab-env"><h2 id="lab-env">2 · Environment</h2>
    <div class="labgrid">
      <label class="field"><span>Blocks</span><input type="number" data-lab-num="blocks" value="${esc(d.blocks)}" min="1" max="${info.max_blocks}" step="1"></label>
      <label class="field"><span>Resource types</span><input type="number" data-lab-num="categories" value="${esc(d.categories)}" min="2" max="5" step="1"></label></div>
    <p class="note">A block is 4 negotiations, each on its own hidden scenario drawn by the server with the research generators: each agent sits in seat A twice and in seat B twice, and each seat moves first once. The four run in a random order. Rules are the research rules: up to ${st.sbx.max_rounds} turns, nothing is divided without agreement.</p>
    <p class="lead" id="lab-count">${n} negotiations on ${n} different hidden scenarios (${d.blocks} block${d.blocks === 1 ? "" : "s"} of 4).</p>
  </section>

  <section class="stepcard" aria-labelledby="lab-strat"><h2 id="lab-strat">3 · Instructions</h2>
    <fieldset class="choice cards"><legend class="vh">Negotiation instructions</legend>
    ${Object.entries(STRAT).map(([k, [name, text]]) => `<label class="opt c-${k}"><input type="radio" name="lab-strategy" value="${k}"${d.strategy === k ? " checked" : ""}><span><b>${name}</b><small>${text}</small></span></label>`).join("")}</fieldset>
    <p class="note">The negotiation rules stay the same. Only the instructions given to the agents change. AI models receive them as their instructions; HTTP endpoints receive the same text in the observation and may use it or not; scripted agents ignore it.</p>
    ${scripted ? notice("warn", "alert", "Both agents are scripted test agents: this checks the pipeline, and says nothing about how AI agents behave.") : ""}
  </section>

  <section class="stepcard" aria-labelledby="lab-run"><h2 id="lab-run">4 · Run</h2>
    ${problem ? notice("warn", "alert", problem) : ""}
    <p class="step-error" id="step-error" role="alert" hidden></p>
    <div class="stepnav"><span></span><button class="cta" type="button" id="lab-start"${problem ? " disabled" : ""}>Run ${n} negotiations${icon("chev-r")}</button></div>
  </section>

  <section class="block">${blockHead("Past evaluations", "Exploratory; stored under sandbox_runs/lab/")}
    ${past.length ? `<div class="table-wrap"><table><thead><tr><th>When</th><th>Agent X</th><th>Agent Y</th><th class="num">Negotiations</th><th class="num">Complete blocks</th><th class="num">Agreed</th><th>Status</th></tr></thead><tbody>
      ${past.map((e) => `<tr><td><a href="#/lab/${e.id}">${new Date(e.created * 1000).toLocaleString([], { dateStyle: "medium", timeStyle: "short" })}</a></td><td>${labName(labAgents(e.agents)?.X)}</td><td>${labName(labAgents(e.agents)?.Y)}</td>
        <td class="num">${e.finished}${e.aborted ? ` <span class="sub">+${e.aborted} aborted</span>` : ""} / ${e.planned}</td><td class="num">${e.complete_blocks ?? "—"} / ${e.blocks ?? "—"}</td><td class="num">${e.sealed ? "sealed" : pct0(e.agreed)}</td><td>${esc(e.status)}</td></tr>`).join("")}</tbody></table></div>`
      : `<div class="empty"><h3>No evaluations yet</h3><p>Choose two agents above and run them. Results appear here.</p></div>`}</section>`;
}

async function labReport(id) {
  const res = await fetch(`/api/lab/evaluations/${encodeURIComponent(id)}`, { cache: "no-store" });
  if (!res.ok) return `${head("Evaluation not found", "No Agent Lab evaluation has this ID on this server.")}<a class="cta" href="#/lab">Agent Lab${icon("chev-r")}</a>`;
  const ev = await res.json();
  st.labEval = ev;
  const A = labAgents(ev.agents), cfg = ev.config ?? {};
  const strategy = ev.strategy ?? cfg.strategy, blocks = ev.blocks ?? cfg.scenario?.blocks, k = ev.categories ?? cfg.scenario?.categories;
  const top = `<a class="back" href="#/lab">${icon("chev-l")}Agent Lab</a>
  <header class="page-head"><p class="eyebrow">Agent Lab · exploratory evaluation · ${esc(STRAT[strategy]?.[0] ?? strategy)} instructions</p><h1>${esc(`${A.X.label} vs ${A.Y.label}`)}</h1>
    <p class="standfirst">${blocks} block${blocks === 1 ? "" : "s"} × 4 negotiations = ${ev.planned}, each on its own hidden scenario (${k} resource types), with seats and first move balanced in every block.</p></header>`;
  if (ev.sealed && !ev.trials) return `${top}
    <div class="empty"><h2>Sealed while another evaluation runs</h2><p>${SEALED_NOTE}</p><p>This report opens as soon as that evaluation ends.</p></div>`;
  const outcomeCell = (t) => (t.status === "aborted" ? `<span class="badge warn">${esc(ABORT_LABEL[t.abort?.class] ?? "aborted")}${t.abort?.attributed ? ` · Agent ${esc(t.abort.agent)}` : ""}</span>` : esc(OUTCOME_LABEL[t.outcome] ?? t.outcome ?? "…"));
  if (ev.sealed) return `${top}
    <section class="block"><div class="livebar" aria-live="polite"><div><span class="lb-k">Progress</span><b id="lab-progress">${ev.trials.length} / ${ev.planned}</b></div>
      <div><span class="lb-k">Status</span><b class="lb-state running"><i></i>Running</b></div>
      <div class="lb-now"><span class="lb-k">Now</span><b>Negotiation ${(ev.current ?? ev.trials.length) + 1} of ${ev.planned}</b></div></div>
    <div class="bar lab-bar" role="progressbar" aria-valuemin="0" aria-valuemax="${ev.planned}" aria-valuenow="${ev.trials.length}"><div class="a" style="--to:${ev.trials.length / ev.planned}"></div></div>
    ${notice("", "info", `${SEALED_NOTE} Scores and transcripts appear here when it ends.`)}</section>
    <section class="block">${blockHead("Negotiations so far")}${ev.trials.length ? `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th class="num">Block</th><th>Seat A vs seat B</th><th>First move</th><th>Outcome</th><th class="num">Turns</th></tr></thead><tbody>
      ${ev.trials.map((t) => `<tr><td class="num">${t.position + 1}</td><td class="num">${t.block + 1}</td><td>${labName(A[t.seats.A])} <span class="sub">vs ${labName(A[t.seats.B])}</span></td><td>Agent ${t.first_mover}</td><td>${t.status === "aborted" ? `<span class="badge warn">aborted</span>` : esc(OUTCOME_LABEL[t.outcome] ?? "…")}</td><td class="num">${t.rounds ?? "—"}</td></tr>`).join("")}</tbody></table></div>` : `<p class="note">Waiting for the first negotiation…</p>`}</section>`;

  const sm = ev.summary, bl = sm.blocks, used = new Set(bl.complete), o = sm.outcomes, pa = sm.per_agent, f = sm.failures;
  const scripted = ["X", "Y"].some((a) => A[a].kind === "scripted");
  const row = (label, fn, help = "") => `<tr><th scope="row">${label}${help ? info("", help) : ""}</th><td class="num">${fn("X")}</td><td class="num">${fn("Y")}</td></tr>`;
  const pts = (x) => fmt(x * 100, 1);
  const trialTable = `<div class="table-wrap"><table><thead><tr><th class="num">#</th><th class="num">Block</th><th>Cell</th><th>Seat A vs seat B</th><th>First move</th><th>Outcome</th><th class="num">Turns</th>
    <th class="num">${labName(A.X)} score</th><th class="num">${labName(A.Y)} score</th><th class="num">Efficiency</th><th>In aggregates</th></tr></thead><tbody>
    ${ev.trials.map((t) => `<tr><td class="num">${t.position + 1}</td><td class="num">${t.block + 1}</td><td>${esc(t.cell)}</td><td>${labName(A[t.seats.A])} <span class="sub">vs ${labName(A[t.seats.B])}</span></td><td>Agent ${t.first_mover}</td>
      <td>${outcomeCell(t)}</td><td class="num">${t.rounds ?? "—"}</td><td class="num">${t.per_agent ? pts(t.per_agent.X.utility) : "—"}</td><td class="num">${t.per_agent ? pts(t.per_agent.Y.utility) : "—"}</td>
      <td class="num">${t.rwe !== undefined ? pct(t.rwe, 0) : "—"}</td><td>${used.has(t.block) ? "yes" : `<span class="sub">no, block excluded</span>`}</td></tr>`).join("")}</tbody></table></div>`;
  const p = ev.provenance ?? {};
  return `${top}
  ${notice("warn", "alert", `<b>Exploratory evaluation, not research evidence.</b> Under these hidden scenarios and these controlled conditions, the observed measurements were as below. The lab does not declare a better agent, and no significance test is run.`)}
  ${scripted ? notice("warn", "alert", "A scripted test agent took part. It follows fixed rules and ignores instructions, so its numbers describe the rule, not an AI agent.") : ""}
  ${ev.error ? notice("warn", "alert", `<b>${esc(ev.error.title)}.</b> ${esc(ev.error.message)}`) : ""}
  ${sm.exclusion_warning ? notice("warn", "alert", `<b>${bl.excluded.length} block${bl.excluded.length === 1 ? "" : "s"} excluded.</b> Aggregates use complete blocks only, so every agent keeps equal seat and first-move exposure. If a failure depended on how a negotiation was going, the remaining blocks may not represent the full sample.`) : ""}

  <section class="block">${blockHead("Blocks and failures", "Failures are infrastructure events, never negotiation outcomes")}
    <dl class="readouts">
      ${readout("Complete blocks", `${bl.complete.length}<small> / ${bl.planned}</small>`, "used for every aggregate")}
      ${readout("Excluded blocks", String(bl.excluded.length), bl.excluded.map((b) => `block ${b.block + 1}: ${esc(b.reasons.map((r) => ABORT_LABEL[r] ?? r).join(", "))}`).join("<br>"))}
      ${readout("Blocks not run", String(bl.not_run))}
      ${readout("Negotiations", `${sm.used}<small> used / ${sm.attempted} attempted</small>`)}
    </dl>
    <div class="table-wrap" style="margin-top:12px"><table class="cmp"><thead><tr><th></th><th class="num">${labName(A.X)}</th><th class="num">${labName(A.Y)}</th></tr></thead><tbody>
      ${row("Negotiations attempted", (a) => f[a].attempted)}
      ${row("Agent failures", (a) => `${f[a].agent_failures}<span class="sub dist">${pct0(f[a].rate)} of attempted · seat A ${f[a].by_seat.A} · seat B ${f[a].by_seat.B}</span>`, "The agent's endpoint did not reply (network error, timeout or HTTP error). Counted against the agent; not a negotiation outcome.")}
    </tbody></table></div>
    <p class="note" style="margin-top:10px">Provider failures (not attributed to an agent): ${sm.aborts_by_class.provider_failure} · cost-cap stops: ${sm.aborts_by_class.budget_exhausted}. Scenario balance check, mean best possible total per cell: ${Object.entries(sm.cell_balance).map(([c, d]) => `${esc(c)} ${d.n ? fmt(d.mean, 1) : "—"}`).join(" · ")}.</p></section>

  ${!sm.used ? `<div class="empty"><h2>No balanced block completed</h2><p>No aggregate is reported: pooling the negotiations that did finish would give the agents unequal seat and first-move exposure. See the negotiations below for what happened.</p></div>` : `
  <section class="block">${blockHead("Outcomes", `${sm.used} negotiations from ${bl.complete.length} complete block${bl.complete.length === 1 ? "" : "s"}`)}
    <dl class="readouts">
      ${readout("Agreement", pct0(o.agreed), `${Math.round(o.agreed * sm.used)} of ${sm.used}`)}
      ${readout("Walk-away", pct0(o.walked_away))}${readout("Deadline passed", pct0(o.timeout))}${readout("Invalid action", pct0(o.invalid_action))}
      ${readout("Median turns", distText(sm.rounds, (x) => fmt(x, 1)))}
    </dl>
    <dl class="readouts" style="margin-top:12px">
      ${readoutDef("rwe", distText(sm.rwe, (x) => pct(x, 0)), "median over complete blocks; no agreement counts as 0")}
      ${readoutDef("ew", distText(sm.ew))}
      ${readoutDef("equitability", distText(sm.equitability), "agreements only")}
      ${readout("Envy-free agreements", sm.envy_free_rate === null ? "—" : pct(sm.envy_free_rate, 0), "agreements only")}
    </dl>
    <p class="note" style="margin-top:10px">Efficiency and fairness are reported separately on purpose: the split with the largest total is not always the most even one.</p></section>

  <section class="block">${blockHead("Each agent", "Scores are points out of 100 by that agent's own private values; medians, with IQR once n ≥ 5")}
    <div class="table-wrap"><table class="cmp"><thead><tr><th></th><th class="num"><span class="tag m-seatA">X</span> ${labName(A.X)}</th><th class="num"><span class="tag m-seatB">Y</span> ${labName(A.Y)}</th></tr></thead><tbody>
      ${row("Score", (a) => distText(pa[a].utility, pts), "No agreement scores 0.")}
      ${row("Score in seat A", (a) => distText(pa[a].utility_by_seat.A, pts))}
      ${row("Score in seat B", (a) => distText(pa[a].utility_by_seat.B, pts))}
      ${row("Walk-aways it caused", (a) => pa[a].walk_aways)}
      ${row("Invalid actions it caused", (a) => pa[a].invalid_actions)}
    </tbody></table></div></section>

  <section class="block">${blockHead("Behavior", "Computed from the transcripts and each agent's own values; not judged by a model")}
    <div class="table-wrap"><table class="cmp"><thead><tr><th></th><th class="num">${labName(A.X)}</th><th class="num">${labName(A.Y)}</th></tr></thead><tbody>
      ${row("Opening demand", (a) => distText(pa[a].opening_demand, (x) => pct(x, 0)), "Value of the agent's first offer to itself, as a share of its maximum (100 points).")}
      ${row("Total concession", (a) => distText(pa[a].total_concession, (x) => pct(x, 0)), "Value of its first offer to itself minus value of its last offer to itself. Negative means it asked for more over time. Needs at least 2 offers.")}
      ${row("Concession frequency", (a) => distText(pa[a].concession_frequency, (x) => pct(x, 0)), "Share of its consecutive offers in which it asked for less. Direction only, not size. Needs at least 2 offers.")}
      ${row("Value it accepted", (a) => distText(pa[a].accepted_value, (x) => pct(x, 0)), "Value to itself of the offer it accepted. Observed only when this agent accepted; not a reservation value.")}
    </tbody></table></div>
    <p class="note" style="margin-top:10px">Definitions, formulas and limitations: <code>docs/phase2-agent-lab-plan.md</code> §8.2.</p></section>`}

  <section class="block">${blockHead("Every negotiation", "Open one to read its transcript")}${trialTable}
    <div class="lab-trials">${ev.trials.map((t, i) => t.status === "aborted"
      ? `<details class="more"><summary>#${t.position + 1} · block ${t.block + 1} · ${esc(t.cell)} · ${esc(ABORT_LABEL[t.abort.class] ?? "aborted")}: ${esc(t.abort.title)}</summary><p class="note">${esc(t.abort.message)}</p>${t.abort.detail ? `<pre class="prompt">${esc(t.abort.detail)}</pre>` : ""}</details>`
      : `<details class="more" data-trial="${i}"><summary>#${t.position + 1} · block ${t.block + 1} · ${esc(t.cell)} · ${labName(A[t.seats.A])} (A) vs ${labName(A[t.seats.B])} (B) · ${esc(OUTCOME_LABEL[t.outcome])} in ${t.rounds} turns</summary><div class="tl" data-slot></div></details>`).join("")}</div></section>

  <section class="block">${blockHead("Provenance")}<dl class="kv">
    <dt>Design</dt><dd class="mono">${esc(p.design)}</dd><dt>Protocol</dt><dd class="mono">${esc(p.protocol_id)}</dd>
    <dt>Code</dt><dd class="mono">${esc(p.code_version)}</dd><dt>Prompts</dt><dd class="mono">${esc(short(p.prompt_hash ?? "", 16))}</dd>
    <dt>Agent contract</dt><dd class="mono">${esc(p.contract)}</dd>
    <dt>Scenarios</dt><dd>${esc((p.environment?.category_names ?? []).join(", "))} · key ${esc(p.key_id)} · set <span class="mono">${esc(p.set_id)}</span></dd>
    <dt>Agent X</dt><dd>${esc(A.X.detail)}${A.X.auth ? ` · ${esc(A.X.auth)}` : ""}</dd><dt>Agent Y</dt><dd>${esc(A.Y.detail)}${A.Y.auth ? ` · ${esc(A.Y.auth)}` : ""}</dd>
    <dt>Evaluation ID</dt><dd class="mono">${esc(ev.id)}</dd></dl></section>
  <div class="actions"><a class="cta" href="#/lab" data-lab-again>Run again with changes${icon("chev-r")}</a><a class="btn" href="#/experiments">Research Mode</a></div>`;
}

/** Renders one trial's transcript on first open (transcripts can be long). */
function labTrialTimeline(t, ev) {
  const lab = (seat) => labAgents(ev.agents)[t.seats[seat]].label;
  const n = { family_A: t.seats.A === "X" ? "seatA" : "seatB", family_B: t.seats.B === "X" ? "seatA" : "seatB", model_A: lab("A"), model_B: lab("B"),
    max_rounds: t.max_rounds, resource_pool: t.resource_pool, outcome: t.outcome, invalid_reason: t.invalid_reason, utility_A: t.utility_A, utility_B: t.utility_B,
    optimal_welfare: t.optimal_welfare, final_allocation: t.final_allocation };
  let prev = null;
  return t.events.map((e, i) => { const h = turnRow(e, n, i, prev); if (e.action === "OFFER" && e.allocation) prev = e.allocation; return h; }).join("") + verdictRow(n, t.events.length);
}

/** Re-renders when the sealed state, the status or the progress changes. */
function pollLab(id) {
  let stopped = false;
  cleanups.push(() => { stopped = true; });
  const tick = async () => {
    if (stopped) return;
    let ev;
    try { ev = await getJSON(`/api/lab/evaluations/${encodeURIComponent(id)}`); } catch { return later(tick, 2000); }
    if (stopped || !location.hash.startsWith(`#/lab/${id}`)) return;
    const was = st.labEval;
    if (ev.sealed !== was.sealed || ev.status !== was.status || (ev.trials?.length ?? 0) !== (was.trials?.length ?? 0)) return render(false);
    later(tick, 1000);
  };
  later(tick, 1000);
}

function wireLab(arg) {
  if (arg) {
    if (st.labEval?.status === "running" || st.labEval?.sealed) pollLab(arg);
    main.querySelectorAll("details[data-trial]").forEach((dt) => dt.addEventListener("toggle", () => {
      const slot = dt.querySelector("[data-slot]");
      if (dt.open && !slot.childElementCount) slot.innerHTML = labTrialTimeline(st.labEval.trials[+dt.dataset.trial], st.labEval).replaceAll(" data-eval hidden", " data-eval");
    }));
    main.querySelector("[data-lab-again]")?.addEventListener("click", () => {
      const c = st.labEval.config;
      st.lab = { ...newLab(), blocks: c.scenario.blocks, categories: c.scenario.categories, strategy: c.strategy,
        agents: Object.fromEntries(["X", "Y"].map((k) => [k, { ...labAgent(), ...c.agents[k], label: c.agents[k].label ?? "", url: c.agents[k].url ?? "" }])) };
    });
    return;
  }
  const d = st.lab, err = main.querySelector("#step-error");
  const say = (m) => { err.innerHTML = m ?? ""; err.hidden = !m; };
  main.querySelectorAll("[data-lab-kind]").forEach((el) => el.addEventListener("change", () => { d.agents[el.dataset.labKind] = { ...labAgent(), kind: el.value }; delete d.probe[el.dataset.labKind]; render(false); }));
  main.querySelectorAll("[data-lab]").forEach((el) => el.addEventListener("input", () => { const [id, k] = el.dataset.lab.split("."); d.agents[id][k] = el.value; delete d.probe[id]; say(null); }));
  main.querySelectorAll("[data-lab]").forEach((el) => el.addEventListener("change", () => render(false)));
  main.querySelectorAll("[data-lab-num]").forEach((el) => el.addEventListener("change", () => { d[el.dataset.labNum] = el.value.trim() === "" ? NaN : Number(el.value); render(false); }));
  main.querySelectorAll('input[name="lab-strategy"]').forEach((el) => el.addEventListener("change", () => { d.strategy = el.value; render(false); }));
  main.querySelectorAll("[data-probe]").forEach((b) => b.addEventListener("click", async () => {
    const id = b.dataset.probe; b.disabled = true; b.textContent = "Testing…";
    try {
      const r = await fetch("/api/lab/probe", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ agent: labBody(d).agents[id] }) });
      const out = await r.json().catch(() => ({}));
      d.probe[id] = r.ok ? out : { ok: false, title: "Not tested", message: out.error ?? `The server refused the request (${r.status}).` };
    } catch { d.probe[id] = { ok: false, title: "Not tested", message: "The server could not be reached." }; }
    render(false);
  }));
  const start = main.querySelector("#lab-start");
  start?.addEventListener("click", async () => {
    start.disabled = true; start.textContent = "Starting…"; say(null);
    try {
      const r = await fetch("/api/lab/evaluations", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(labBody(d)) });
      const out = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(out.error ?? `The server refused the request (${r.status}).`);
      for (const id of "XY") d.agents[id].token = ""; // the server holds the only copy now
      location.hash = `#/lab/${out.id}`;
    } catch (e) {
      say(esc(e.message === "Failed to fetch" ? "The server could not be reached. Check that it is still running, then try again." : e.message));
      start.disabled = false; start.innerHTML = `Run ${d.blocks * 4} negotiations${icon("chev-r")}`;
    }
  });
}

// ------------------------------------------------------------------ 1b. illustrative example
// Made-up numbers that follow the real rules (spec §1-3): each agent's per-unit values sum to
// 100 points over the whole pool, utility = Σ units × value. Never stored, never mixed with data.
const EX = {
  pool: { widgets: 20, gadgets: 12, components: 30 },
  vA: { widgets: 2.5, gadgets: 2.5, components: 20 / 30 },   // 50 + 30 + 20 points
  vB: { widgets: 0.75, gadgets: 25 / 12, components: 2 },     // 15 + 25 + 60 points
  turns: [
    ["A", "OFFER", { widgets: 18, gadgets: 10, components: 12 }, "I'd like most of the widgets and gadgets. You can have more of the components.", "A opens by asking for most of what it values. It can't see that B cares most about components."],
    ["B", "OFFER", { widgets: 16, gadgets: 4, components: 4 }, "Components matter most to me. I'll give you most of the widgets if I get nearly all the components.", "B counters. It reveals its priority and gives up widgets, which it values little."],
    ["A", "OFFER", { widgets: 18, gadgets: 8, components: 4 }, "Deal on components. I need more gadgets, and I'll take two more widgets.", "A concedes on components, which it values least, and asks for gadgets back. Both sides now trade what they value less for what they value more."],
    ["B", "ACCEPT", null, "Agreed.", "B accepts A's standing offer. It becomes the final allocation."],
  ],
};
const exWorth = (alloc, v) => Object.keys(v).reduce((t, c) => t + alloc[c] * v[c], 0);
const exSplit = (a) => ({ A: a, B: Object.fromEntries(Object.entries(EX.pool).map(([c, q]) => [c, q - a[c]])) });

function example(s) {
  const n = { family_A: "example", family_B: "example", model_A: "illustrative", model_B: "illustrative", max_rounds: s.setup.max_rounds, resource_pool: EX.pool, outcome: "agreed" };
  let prev = null, standing = null;
  const rows = EX.turns.map(([actor, action, a, message, why], i) => {
    const turn = i + 1, e = { turn, actor, action, message, allocation: null, value_A: null, value_B: null, call: null };
    if (a) { e.allocation = exSplit(a); e.value_A = exWorth(e.allocation.A, EX.vA); e.value_B = exWorth(e.allocation.B, EX.vB); e.standing = true; }
    if (action === "ACCEPT") e.accepted = standing;
    const html = turnRow(e, n, i, prev) + `<p class="ex-why"><b>What's happening:</b> ${esc(why)}</p>`;
    if (a) { prev = e.allocation; standing = { actor, turn }; n.final_allocation = e.allocation; }
    return html;
  }).join("");
  n.utility_A = exWorth(n.final_allocation.A, EX.vA); n.utility_B = exWorth(n.final_allocation.B, EX.vB);
  n.optimal_welfare = Object.entries(EX.pool).reduce((t, [c, q]) => t + q * Math.max(EX.vA[c], EX.vB[c]), 0);
  const vals = (v) => Object.entries(v).map(([c, x]) => `<li>${esc(c)} <b>${fmt(x * EX.pool[c], 0)} pts</b> <span class="sub">${fmt(x, 2)} per unit</span></li>`).join("");
  const banner = `<div class="ex-banner" role="note">${icon("alert")}Illustrative example — not experimental data</div>`;
  // Stays on screen while the walkthrough scrolls, so no single turn can be read out of context as a real transcript.
  const pinned = `<div class="ex-banner pinned" role="note">${icon("alert")}<span><b>Illustrative walkthrough</b> · Illustrative example — not experimental data</span></div>`;
  const recorded = s.negotiations.find((r) => r.outcome === "agreed") ?? s.negotiations[0];
  return `<a class="back" href="#/">${icon("chev-l")}Home</a>
  ${head("How a negotiation works", "A short demonstration with made-up numbers that follows the real rules, so you can see how private values, offers and the final split fit together. It is not a model transcript: every message below was written for this example. Recorded negotiations use exactly this layout.")}
  ${recorded ? notice("info", "info", `<b>Research negotiations have been recorded.</b> <a href="#/negotiation/${encodeURIComponent(recorded.key)}">Open a recorded one</a>, or browse them all under <a href="#/experiments">Experiments</a>.`) : ""}
  <div class="example" aria-label="Illustrative example, not experimental data">
    ${pinned}
    <div class="ex-body">
      <dl class="outcomes exterms">
        <div><dt>Shared resources</dt><dd>A pool of items both agents must split: here widgets, gadgets and components.</dd></div>
        <div><dt>Secret values</dt><dd>What each agent privately cares about. A and B value the items differently and can't see each other's values.</dd></div>
        <div><dt>Offer</dt><dd>A proposed split of every item in the pool. A <b>counter-offer</b> is a new offer made in reply.</dd></div>
        <div><dt>Agreement</dt><dd>One agent accepts the other's latest offer. That split becomes final. No agreement means both get nothing.</dd></div>
      </dl>
      <p class="pool">Pool: ${Object.entries(EX.pool).map(([c, q]) => `${esc(c)} ${q}`).join(", ")}. Agent A and Agent B are seats; in the experiment one is Claude and the other GPT. No model is named here, because nothing here was produced by a model.</p>
      <div class="agents">
        <div class="agent"><div class="who"><b>Agent A</b><span class="badge">moves first</span></div><p class="note">Secret values (only A sees these)</p><ul class="exvals">${vals(EX.vA)}</ul></div>
        <div class="agent"><div class="who"><b>Agent B</b></div><p class="note">Secret values (only B sees these)</p><ul class="exvals">${vals(EX.vB)}</ul></div>
      </div>
      <p class="note">Each agent spreads 100 points over the whole pool. An offer's worth to an agent is the sum of its units times its own per-unit values, shown under each offer as the evaluator sees it.</p>
      <div class="tl">${rows}${verdictRow(n, EX.turns.length)}</div>
      <p class="ex-why"><b>What gets measured:</b> in the experiment, the evaluator turns scores like these into the preregistered measures, for example how close the combined score came to the best achievable total. No measures are computed for this example.</p>
    </div>
    ${banner}
  </div>
  ${next("#/try", "Try a negotiation", "Set up your own negotiation and watch two AI models play it out live, in this same layout.")}`
    .replaceAll(" data-eval hidden", " data-eval"); // this walkthrough always shows the evaluator's view
}

// ------------------------------------------------------------------ 2. setup
function setup(s) {
  const c = s.setup, sci = c.scientific, op = c.operational;
  const recorded = s.runs.filter((r) => r.mode === "pilot" || r.mode === "full").map((r) => ({ mode: r.mode, ...r.config?.experiment }));
  const caps = [...new Set(recorded.map((b) => `${b.mode}|${b.budget_max_total_tokens}|${b.budget_max_cost_usd}|${b.budget_max_input_tokens_per_call}`))];
  const row = (label, a, b) => `<tr><td>${esc(label)}</td><td>${a}</td><td>${b}</td></tr>`;
  const seatName = (seat, x) => (x.claude_seat === seat ? "Claude" : "GPT");
  const runtime = `<section class="block">${blockHead("Models and runtime settings", "Spec §8.15, <code>APPROVED_SCIENTIFIC</code> and <code>APPROVED_OPERATIONAL</code>. Read-only")}
    <div class="table-wrap"><table><thead><tr><th>Setting</th><th>Claude</th><th>GPT</th></tr></thead><tbody>
      ${row("Model", esc(modelName(sci.ClaudeAgent.model)), esc(modelName(sci.OpenAIAgent.model)))}
      ${row("Model version", `<code>${esc(sci.ClaudeAgent.model)}</code>`, `<code>${esc(sci.OpenAIAgent.model)}</code>`)}
      ${row("Temperature", sci.ClaudeAgent.temperature, sci.OpenAIAgent.temperature)}
      ${row("Max output tokens", sci.ClaudeAgent.max_output_tokens, sci.OpenAIAgent.max_output_tokens)}
      ${row("Thinking or reasoning", esc(c.claude_thinking), "none (non-reasoning model)")}
      ${row("Effort", esc(sci.ClaudeAgent.effort ?? "—"), esc(sci.OpenAIAgent.effort ?? "not applicable"))}
      ${row("Request timeout", `${op.timeout_s} s`, `${op.timeout_s} s`)}
      ${row("In-turn retries", `${op.max_retries}, backoff ${op.retry_backoff_s} s`, `${op.max_retries}, backoff ${op.retry_backoff_s} s`)}
      ${row("Price, USD per 1M tokens (in, out)", (c.prices[sci.ClaudeAgent.model] ?? []).join(", "), (c.prices[sci.OpenAIAgent.model] ?? []).join(", "))}
      ${row("Round limit", `${c.max_rounds} turns, shared`, `${c.max_rounds} turns, shared`)}
      ${row("Conditions", "baseline_v1 and structured_v1, same for both", "baseline_v1 and structured_v1, same for both")}
    </tbody></table></div></section>`;
  return `${head("Research configuration", "This page documents the configuration used for the controlled experiment. It is intentionally read-only so the reported experiment remains reproducible. The values are read from the modules that enforce them, and every pilot and full run is checked by <code>check_approved_runtime</code> before any API call and refuses to start if one differs. The Try-it sandbox uses the same model settings and round limit.")}

  ${runtime}

  <section class="block">${blockHead("Design cells", "Seat × first mover × condition, 8 negotiations per instance (spec §8.3)")}
    <div class="matrix" role="table" aria-label="Design cells">
      <div class="h" role="columnheader">Condition</div>
      ${[["A", "A"], ["A", "B"], ["B", "A"], ["B", "B"]].map(([seat, fm]) => `<div class="h" role="columnheader">Claude in seat ${seat}<br>Seat ${fm} opens</div>`).join("")}
      ${CONDITIONS.map((cd) => `<div class="r" role="rowheader">${cond(cd)}</div>${c.pilot.cells.filter((x) => x.condition === cd).map((x) => `<div class="cell" role="cell"><b>Cell ${x.index}</b><small>A ${seatName("A", x)}, B ${seatName("B", x)}</small></div>`).join("")}`).join("")}
    </div></section>

  <section class="block"><div class="grid g2">
    <div class="panel"><h3>Datasets and seeds</h3><dl class="kv">
      <dt>Pilot</dt><dd>${c.pilot.total} negotiations, seeds ${c.pilot.seeds.join(", ")}, 8 batches of 3</dd>
      <dt>Full run</dt><dd>${c.full.total} negotiations, ${c.full.instances} instances, seeds ${c.full.seeds[0]}–${c.full.seeds[1]}</dd>
      <dt>Recorded</dt><dd>Pilot ${s.progress.pilot.done}/${s.progress.pilot.target}, full ${s.progress.full.done}/${s.progress.full.target}</dd>
      <dt>Source</dt><dd class="note">${esc(c.full.source)}</dd></dl></div>
    <div class="panel"><h3>Negotiation rules and environment</h3><dl class="kv">
      <dt>Rounds</dt><dd>${c.max_rounds} turns; the last is response-only</dd>
      <dt>First mover</dt><dd>Fixed per batch (A or B), independent of the seed</dd>
      <dt>Categories</dt><dd>${c.environment.category_names.map(esc).join(", ")}, ${c.environment.min_qty}–${c.environment.max_qty} units each</dd>
      <dt>Valuations</dt><dd>Private Dirichlet(α = ${c.environment.dirichlet_alpha}) over ${c.environment.total_points} points</dd>
      <dt>Reruns</dt><dd>${c.max_transport_reruns} clean reruns after a transport failure</dd></dl></div>
  </div></section>

  <section class="block">${blockHead("Budget", "Hard caps supplied at launch, checked before every call")}
    ${caps.length ? `<div class="table-wrap"><table><thead><tr><th>Dataset</th><th class="num">Token cap</th><th class="num">Dollar cap</th><th class="num">Input reserve per call${info("r", "Worst-case input tokens set aside before each call, so the budget can never be overshot by one request.")}</th></tr></thead><tbody>
      ${caps.map((k) => { const [m, t, d, r] = k.split("|"); const n = (v) => (v === "null" || v === "undefined" ? "—" : v); return `<tr><td>${esc(m)}</td><td class="num">${esc(n(t))}</td><td class="num">${n(d) === "—" ? "—" : `$${esc(d)}`}</td><td class="num">${esc(n(r))}</td></tr>`; }).join("")}
    </tbody></table></div>`
    : notice("", "info", "<b>No budget recorded yet.</b> Caps have no defaults; they are passed to <code>scripts/run_pilot.py</code> at launch and stored with each run. The documented approved pilot budget is <code>--max-cost-usd 20 --max-input-tokens-per-call 20000</code>, with no token cap.")}
  </section>

  <section class="block">${blockHead("Instructions", "<code>src/agents/prompting.py</code>, verbatim")}
    <div class="grid g2">
      <details class="panel"><summary><b>System instructions</b>, both conditions</summary><pre class="prompt">${esc(c.system_instructions)}</pre></details>
      <details class="panel"><summary><b>structured_v1 block</b>, appended</summary><pre class="prompt">${esc(c.structured_block)}</pre></details>
    </div></section>
  ${next("#/try", "Back to the lab", "Set up your own negotiation and watch it live.")}`;
}

// ------------------------------------------------------------------ recorded negotiations (research)
function negList(s) {
  const rows = s.negotiations;
  const f = st.filters;
  const opt = (name, label, values, labels = {}, help = "") => `<label class="field"><span>${esc(label)}${help ? info(name, help) : ""}</span><select data-filter="${name}"><option value="all">All</option>${values.map((v) => `<option value="${esc(v)}"${f[name] === v ? " selected" : ""}>${esc(labels[v] ?? v)}</option>`).join("")}</select></label>`;
  const fams = { claude: "Claude", openai: "GPT" };
  const m = st.showMeasures ? "" : " hide-measures";
  return `  <div class="filters" role="search">
    ${opt("mode", "Dataset", uniq(rows, "mode"), {}, "Pilot is the operational check; full is the confirmatory data.")}
    ${opt("condition", "Condition", CONDITIONS, {}, "Which negotiation instruction strategy both agents received: baseline_v1 or structured_v1.")}
    ${opt("modelA", "Seat A", uniq(rows, "family_A"), fams, "Which model sat in seat A. Models swap seats across the design.")}
    ${opt("modelB", "Seat B", uniq(rows, "family_B"), fams, "Which model sat in seat B.")}
    ${opt("firstMover", "Opens", ["A", "B"], { A: "Seat A", B: "Seat B" }, "Which seat made the opening offer. Swapped across the design.")}
    ${opt("outcome", "Outcome", OUTCOMES, OUTCOME_LABEL, "How it ended: agreement, walk-away, timeout at the deadline, or invalid action.")}
    <label class="field"><span>Seed or ID${info("seedhelp", "The instance seed fixes the pool and both private valuations; the ID identifies one stored negotiation.")}</span><input type="search" data-filter="seed" value="${esc(f.seed ?? "")}" placeholder="30001"></label>
    <button class="btn-link" type="button" data-reset>Clear filters</button>
  </div>
  <div class="listbar"><p class="count" aria-live="polite" id="count"></p><label class="toggle"><input type="checkbox" id="measuretoggle"${st.showMeasures ? " checked" : ""}> Show measure columns</label></div>
  <div class="table-wrap"><table class="negs${m}"><thead><tr><th>ID</th><th>Dataset</th><th>Condition</th><th>Seat A</th><th>Seat B</th><th>Opens</th><th class="num">Seed</th><th>Outcome</th><th class="num">Rounds${info("rounds")}</th><th class="num">Score A${info("utility")}</th><th class="num">Score B</th><th class="num m">RWE${info("rwe")}</th><th class="num m">EW${info("ew")}</th><th class="num m">Equit.${info("equitability")}</th></tr></thead>
  <tbody id="rows"></tbody></table></div>`;
}
function negRows() {
  const rows = filterRows(st.snap.negotiations, st.filters);
  document.getElementById("count").textContent = `Showing ${rows.length} of ${st.snap.negotiations.length} negotiations`;
  document.getElementById("rows").innerHTML = rows.length ? rows.map((r, i) => `<tr class="link" style="--i:${i}" data-href="#/negotiation/${encodeURIComponent(r.key)}">
    <td class="mono"><a href="#/negotiation/${encodeURIComponent(r.key)}">${esc(r.key)}</a></td><td>${esc(r.mode)}</td><td>${cond(r.condition)}</td>
    <td>${fam(r.model_A)}</td><td>${fam(r.model_B)}</td><td>${esc(r.first_mover)}</td><td class="num">${r.seed}</td>
    <td>${esc(OUTCOME_LABEL[r.outcome] ?? r.outcome)}${r.invalid_reason ? `<div class="sub">${esc(r.invalid_reason)}</div>` : ""}</td>
    <td class="num">${r.rounds}</td><td class="num">${fmt(r.utility_A, 1)}</td><td class="num">${fmt(r.utility_B, 1)}</td>
    <td class="num m">${fmt(r.rwe)}</td><td class="num m">${fmt(r.ew)}</td><td class="num m">${fmt(r.equitability)}</td></tr>`).join("")
    : `<tr><td colspan="14">${notice("", "info", "<b>No negotiations match these filters.</b> Change a filter or clear them all.")}</td></tr>`;
  if (motionOn()) M.animate([...document.querySelectorAll("#rows tr")].slice(0, 24), { opacity: [0, 1] }, { duration: 0.3, delay: M.stagger(0.018), ease: EASE }); // opacity only: no scrollbar flash
}

// ------------------------------------------------------------------ 4. negotiation timeline
async function detail(s, key) {
  const res = await fetch(`/api/negotiation?key=${encodeURIComponent(key)}`, { cache: "no-store" });
  if (!res.ok) return `<a class="back" href="#/experiments">${icon("chev-l")}Experiments</a>${head("Negotiation not found", `No stored negotiation has the ID <code>${esc(key)}</code>. It may belong to a data file that is no longer in <code>results/</code>.`)}`;
  const n = await res.json();
  const agreed = n.outcome === "agreed";
  const seat = (r) => `<div class="agent m-${esc(r === "A" ? n.family_A : n.family_B)}"><div class="who"><b>Agent ${r}</b>${fam(r === "A" ? n.model_A : n.model_B)}${r === n.first_mover ? `<span class="badge">moves first</span>` : ""}</div>
    <div class="mono note">${esc(r === "A" ? n.model_A : n.model_B)}</div>
    <div class="vals" data-eval ${st.evaluator ? "" : "hidden"}>Private value per unit: ${Object.entries(r === "A" ? n.valuation_A : n.valuation_B).map(([c, v]) => `${esc(c)} ${fmt(v, 2)}`).join(", ")}</div></div>`;
  let prev = null;
  const turns = n.events.map((e, i) => { const html = turnRow(e, n, i, prev); if (e.action === "OFFER" && e.allocation) prev = e.allocation; return html; }).join("");
  const outBadge = { agreed: "good", walked_away: "warn", timeout: "warn", invalid_action: "crit" }[n.outcome];
  const siblings = s.negotiations.filter((r) => r.seed === n.seed && r.mode === n.mode && r.key !== n.key);
  return `<a class="back" href="#/experiments">${icon("chev-l")}Experiments</a>
  <header class="page-head"><p class="eyebrow">Research negotiation · ${esc(n.mode)}</p><h1>${esc(modelLabel(n.model_A))} (A) vs ${esc(modelLabel(n.model_B))} (B)</h1>
    <p class="standfirst">${esc(OUTCOME_LABEL[n.outcome])} in ${n.rounds} of ${n.max_rounds} rounds, under the ${esc(COND_NAME[n.condition] ?? n.condition)} instructions. ${n.mode === "pilot" ? "A pilot negotiation: it checks the software and is not part of the tested data." : ""}</p>
    <div class="conds" style="margin-top:14px"><span class="badge ${outBadge}">${icon(agreed ? "check" : "x")}${esc(OUTCOME_LABEL[n.outcome])}</span>${cond(n.condition)}<span class="badge">${esc(n.mode)}</span><span class="note mono">seed ${n.seed} · run ${esc(short(n.run_id, 10))}, #${n.index}</span></div>
    <p class="note" style="margin-top:12px">New to this view? <a href="#/example">See the illustrative walkthrough</a> first.</p></header>
  <div class="agents">${seat("A")}${seat("B")}</div>
  <div class="tl-head"><h2>Negotiation timeline</h2><div class="tl-tools">
    <label class="toggle" data-tip="Shows each agent's hidden per-unit values and what every offer was worth to each side. The agents never saw these."><input type="checkbox" id="evaltoggle" ${st.evaluator ? "checked" : ""}> Show hidden values</label>
    <button class="btn" type="button" id="replay" data-tip="Plays the stored rounds again in order.">${icon("replay")}Replay rounds</button></div></div>
  <p class="pool">Each row is one recorded turn: who acted, what they did, and for offers, how each category of the pool would be split. The last row is the outcome. Pool: ${Object.entries(n.resource_pool).map(([c, q]) => `${esc(c)} ${q}`).join(", ")}.</p>
  <div class="key" aria-hidden="true"><span class="k"><i class="mini" style="--claude:${famColor(n.family_A)};--openai:${famColor(n.family_B)}"></i>Split of one category: Agent A from the left, Agent B from the right</span><span class="k"><i class="mini ghost"></i>Previous offer</span><span class="k"><i class="mini dead"></i>Final-turn offer, never standing</span></div>
  <nav class="tape" aria-label="Rounds">${n.events.map((e) => `<a href="#/negotiation/${encodeURIComponent(n.key)}" data-turn="${e.turn}" class="${e.action === "OFFER" && !e.standing ? "dead" : ""}" style="--c:${e.action === "INVALID" ? "var(--critical)" : famColor(e.actor === "A" ? n.family_A : n.family_B)}"><b>${e.turn}</b><span>${esc(e.actor)} ${esc({ OFFER: "offer", ACCEPT: "accept", WALK_AWAY: "walk away", INVALID: "invalid" }[e.action] ?? e.action)}</span></a>`).join("")}</nav>
  <div class="tl" id="timeline" data-outcome="${esc(n.outcome)}">${turns}${verdictRow(n, n.events.length)}</div>
  <section class="block">${blockHead("How this negotiation scored", "Computed by the evaluator from both hidden valuations; the agents never saw these")}
  <dl class="readouts">
    ${readoutDef("rwe", fmt(n.rwe), "0 when not agreed")}
    ${readoutDef("ew", fmt(n.ew), "0 when not agreed")}
    ${readoutDef("equitability", fmt(n.equitability), agreed ? "agreed" : "undefined without agreement")}
    ${readoutDef("imbalance", signed(n.imbalance), agreed ? "Claude − GPT" : "undefined without agreement")}
    ${readoutDef("first_mover_gap", signed(n.first_mover_gap), agreed ? "first − second" : "undefined without agreement")}
  </dl>
  ${siblings.length ? `<p class="note" style="margin-top:12px">Same seed, other seatings and condition: ${siblings.map((r) => `<a href="#/negotiation/${encodeURIComponent(r.key)}">${esc(r.key)}</a> <span class="sub">(${esc(COND_NAME[r.condition] ?? r.condition)}, Claude in ${esc(r.claude_seat ?? "?")}, ${esc(r.first_mover)} opens)</span>`).join(" · ")}</p>` : ""}</section>
  <details class="usage more"><summary>Call usage, as reported by the providers (not a billing figure)</summary>
    <dl class="readouts">
      ${readout("API calls", n.usage.api_calls, `${n.usage.api_attempts} attempts`)}
      ${readout("Tokens in", n.usage.input_tokens ?? "—")}
      ${readout("Tokens out", n.usage.output_tokens ?? "—")}
      ${readout("Latency", n.usage.latency_s === null ? "—" : `${fmt(n.usage.latency_s, 1)}<small> s</small>`)}
      ${readout("Cost", n.usage.cost_usd === null ? "—" : `$${fmt(n.usage.cost_usd, 4)}`)}
    </dl></details>
  ${next("#/results", "Results", "See how negotiations like this one add up across each condition.")}`;
}

function tracks(alloc, pool, prev, i, n) {
  return `<div class="tracks" style="--ca:${famColor(n.family_A)};--cb:${famColor(n.family_B)}"><div class="track thead" aria-hidden="true"><span></span><span class="qa">A gets</span><span></span><span class="qb">B gets</span><span></span></div>${Object.keys(pool).map((c) => {
    const q = pool[c], a = alloc.A[c] ?? 0, b = alloc.B[c] ?? 0, to = q ? a / q : 0;
    const pa = prev ? prev.A[c] ?? 0 : a, from = q ? pa / q : 0, d = a - pa;
    return `<div class="track"><span class="cat">${esc(c)}</span><span class="qa">${a}</span>
      <div class="bar" role="img" aria-label="${esc(c)}: Agent A ${a}, Agent B ${b} of ${q}${prev && d ? `, A ${d > 0 ? "+" : ""}${d} versus previous offer` : ""}" style="--from:${from};--to:${to};--i:${i}"><div class="a"></div>${prev && d ? `<i class="ghost"></i>` : ""}<i class="edge"></i></div>
      <span class="qb">${b}</span><span class="delta">${prev ? (d ? `A ${d > 0 ? "+" : "−"}${Math.abs(d)}` : "no change") : "opening"}</span></div>`;
  }).join("")}</div>`;
}

function turnRow(e, n, i, prev) {
  const f = e.actor === "A" ? n.family_A : n.family_B;
  const who = `Agent ${e.actor}, ${modelLabel(e.actor === "A" ? n.model_A : n.model_B)}`;
  const deadline = e.action === "OFFER" && !e.standing;
  // Plain-language move: opening offer, counter-offer, final offer, accept, walk away, invalid action.
  const label = e.action === "OFFER" ? (deadline ? "Final offer" : prev ? "Counter-offer" : "Opening offer")
    : { ACCEPT: "Accepts", WALK_AWAY: "Walks away", INVALID: "Invalid action" }[e.action] ?? e.action;
  const cls = ["turn", e.action === "ACCEPT" ? "accept" : "", deadline ? "deadline" : "", e.action === "INVALID" ? "invalid" : ""].join(" ");
  let state = "", body = "";
  if (e.action === "OFFER") {
    state = e.standing ? `<span class="state on">${icon("ready")}Standing offer</span>` : `<span class="state">${icon("clock")}${esc(e.note ?? "Not standing")}</span>`;
    body = tracks(e.allocation, n.resource_pool, prev, i, n);
  } else if (e.action === "ACCEPT") {
    state = e.accepted ? `<span class="state on">${icon("check")}Accepts Agent ${esc(e.accepted.actor)}'s offer from round ${e.accepted.turn}</span>` : "";
  } else if (e.action === "WALK_AWAY") {
    state = `<span class="state">${icon("walk")}Leaves without agreement</span>`;
  } else if (e.action === "INVALID") {
    body = `<div class="fault"><b>Rule broken:</b> ${invalidText(e.reason)}. The engine rejected this turn; nothing was repaired or retried.
      <details class="diag"><summary>Technical detail</summary><pre>${esc(e.reason)}${e.detail ? `: ${esc(e.detail)}` : ""}</pre></details></div>`;
  }
  const call = e.call;
  return `<div class="${cls}" id="turn-${e.turn}" data-actor="${e.actor}" style="--i:${i};--c:${famColor(f)}">
    <div class="round" aria-hidden="true">${e.turn}${e.turn === n.max_rounds ? "<small>deadline</small>" : ""}</div>
    <div class="turn-body">
      <div class="turn-top"><span class="rlabel">Round ${e.turn}</span><span class="who">${esc(who)}</span><span class="act ${e.action}">${label}</span>${state}</div>
      ${e.message ? `<p class="msg">${esc(e.message)}</p>` : e.action === "INVALID" ? "" : `<p class="msg none">No message</p>`}
      ${body}
      ${e.value_A !== null && e.value_A !== undefined ? `<div class="evalv" data-eval ${st.evaluator ? "" : "hidden"}>Offer worth ${fmt(e.value_A, 1)} to A and ${fmt(e.value_B, 1)} to B, out of 100 each</div>` : ""}
      ${call ? `<details class="diag"><summary>Call diagnostics</summary><dl class="kv"><dt>Model</dt><dd class="mono">${esc(call.response_model ?? call.model ?? "—")}</dd><dt>Tokens</dt><dd>${call.input_tokens ?? "—"} in, ${call.output_tokens ?? "—"} out</dd><dt>Latency</dt><dd>${fmt(call.latency_s, 2)} s, ${call.attempts} attempt(s)</dd><dt>Stop reason</dt><dd>${esc(call.stop_reason ?? "—")}</dd>${call.error ? `<dt>Error</dt><dd>${esc(call.error)}</dd>` : ""}</dl>${call.raw_output ? `<pre>${esc(call.raw_output)}</pre>` : ""}</details>` : ""}
    </div></div>`;
}

function verdictRow(n, i) {
  const agreed = n.outcome === "agreed";
  const text = agreed ? `Agent A receives ${fmt(n.utility_A, 1)} and Agent B ${fmt(n.utility_B, 1)} of their 100 possible points; the best achievable total was ${fmt(n.optimal_welfare, 1)}.`
    : n.outcome === "invalid_action" ? `An agent broke the rules: ${invalidText(n.invalid_reason)}. There is no allocation; both agents score 0.`
    : n.outcome === "timeout" ? "The deadline passed without an accepted offer. There is no allocation; both agents score 0."
    : "An agent walked away. There is no allocation; both agents score 0.";
  return `<div class="verdict" style="--i:${i}"><div class="round" aria-hidden="true">${icon(agreed ? "check" : "x")}</div>
    <div class="card"><div class="big">${esc(OUTCOME_LABEL[n.outcome])}</div><p>${text}</p>
    ${agreed ? tracks(n.final_allocation, n.resource_pool, null, i, n) : ""}</div></div>`;
}

// ------------------------------------------------------------------ 5. results
/** Why a dataset has nothing to show yet, or null when it can be shown. Never repeats the status panel. */
function blocked(s, ds, after) {
  const v = s.results[ds];
  if (v.locked) return `${notice("", "lock", `<b>Sealed until the full experiment is complete (${v.n} of ${v.target}).</b> Spec §8.9: outcomes are not compared by condition before all ${v.target} full-run negotiations are stored, and the sample size never depends on an observed result.`)}${after}`;
  if (!v.n) return `${notice("", "await", ds === "pilot"
    ? `<b>No pilot negotiations recorded yet, so there is nothing to show.</b> When the pilot runs, its values appear here labelled as pilot: checks on the software, never findings. Nothing is estimated or simulated in the meantime. <a href="#/experiments">See the status</a>.`
    : `<b>No full-experiment negotiations recorded yet.</b> Confirmatory results come only from the full experiment (${v.target} negotiations), and conditions are not compared until all of it exists (spec §8.9). <a href="#/experiments">See the status</a>.`)}${after}`;
  return null;
}
/** "Can this answer the research question yet?", from recorded data and the setup flags only. */
function answerPanel(s) {
  const { pilot, full } = s.progress, complete = full.done >= full.target, tests = s.setup.analysis_implemented;
  const state = !pilot.done && !full.done ? "Not ready — no experimental data" : !complete ? "Not ready — full experiment incomplete" : "Not ready — analysis not run";
  const verdict = !pilot.done && !full.done ? "Not yet. No negotiations have been recorded."
    : !complete ? `Not yet. The full experiment has ${full.done} of ${full.target} negotiations; anything below is descriptive.`
    : !tests ? "Not yet. The data is complete, but the preregistered analysis has not been run."
    : `The data is complete. The preregistered tests belong on Structured vs baseline.`;
  return `<div class="answer panel"><h2>Can this answer the research question yet?</h2>
    <p class="readiness">${icon("await")}${esc(state)}</p>
    <dl class="readouts">
      ${readout("Pilot", `${pilot.done}<small> / ${pilot.target}</small>`, "operational check")}
      ${readout("Full experiment", `${full.done}<small> / ${full.target}</small>`, "research dataset")}
      ${readout("Preregistered analysis", "Not yet", tests ? "written, not run (§8.9)" : "code not written yet (§8.9)")}
      ${readout("Research conclusion", "Not yet", "appears on Structured vs baseline")}
    </dl>
    <p class="note" style="margin-top:12px">Pilot = operational check · Full experiment = research dataset · Analysis = statistical evaluation of the complete dataset.</p>
    <p class="verdict-line">${icon(complete && tests ? "info" : "await")}<b>${esc(verdict)}</b></p></div>`;
}
/** Where the answer to H1–H3 will appear. Status only: no test result exists in code. */
function hypCards(s) {
  const { full } = s.progress, tests = s.setup.analysis_implemented;
  const why = full.done < full.target ? `Sealed until all ${full.target} full-run negotiations are recorded (now ${full.done}).${tests ? "" : " The analysis code is not written yet."}`
    : tests ? "The data is complete; the preregistered analysis has not been run." : "The data is complete; the preregistered analysis code is not written yet.";
  return `<div class="hyp-cards">${HYP.map(([h, k, q]) => `<div class="panel"><span class="cite">${h} · ${esc(DEF[k].name)}${info(k)}</span><h3>${esc(q)}</h3>
    <div class="state">${icon(full.done < full.target ? "lock" : "await")}Not tested yet</div><p class="note">${esc(why)}</p></div>`).join("")}</div>`;
}
const datasetBar = (ds) => `<div class="dsbar">${datasetSeg(ds)}<span class="note">${ds === "pilot" ? "<b>Pilot</b>: operational check, 24 negotiations, excluded from confirmatory analysis. Never findings." : "<b>Full experiment</b>: confirmatory dataset, 480 negotiations. The hypotheses are tested on this."}</span></div>`;
function results(s) {
  const ds = st.dataset, v = s.results[ds];
  const nxt = next("#/structured", "Structured vs baseline", "The page where the answer to the research question will appear.");
  const top = `<nav class="subnav" aria-label="Research results"><a href="#/results" aria-current="page">By condition</a><a href="#/structured">Structured vs baseline</a></nav>${head("Results", "Did the structured strategy help? This page shows what happened in each condition, measure by measure, once the controlled experiment has data. Descriptive only: this page does not test whether the conditions differ. Each value traces back to the negotiations behind it, and each negotiation to its turn-by-turn transcript.")}${answerPanel(s)}${datasetBar(ds)}`;
  const stop = blocked(s, ds, nxt);
  if (stop) return top + stop;
  const rows = s.negotiations.filter((r) => r.mode === ds);
  const by = v.by_condition, g = (c) => rows.filter((r) => r.condition === c);
  const dots = (metric, agreedOnly = false) => CONDITIONS.map((c) => {
    const pts = g(c).filter((r) => r[metric] !== null && (!agreedOnly || r.outcome === "agreed"));
    return { label: c, color: COND_COLOR[c], mean: mean(pts.map((r) => r[metric])), points: pts.map((r) => ({ v: r[metric], tip: `${r.key}, seed ${r.seed}, ${OUTCOME_LABEL[r.outcome]}: ${fmt(r[metric])}` })) };
  });
  const condLegend = legend(CONDITIONS.map((c) => [c, COND_COLOR[c]]));
  const outLegend = legend(OUTCOMES.map((o) => [OUTCOME_LABEL[o], OUT_COLOR[o]]));
  const tableFor = (metric) => dataTable(["Negotiation", "Condition", "Seed", metric], rows.filter((r) => r[metric] !== null).map((r) => [r.key, r.condition, r.seed, fmt(r[metric])]));
  const signedDots = { domain: [-1, 1], zero: true, bin: 0.04, ticks: 4, fmtTick: (x) => signed(x, 1), meanFmt: (x) => signed(x) };
  return `${top}${ds === "pilot" ? pilotNotice : ""}
  ${glossary()}
  <section class="block">${blockHead("Confirmatory measures", "H1–H3, over all negotiations in each condition (spec §8.6–8.8)")}
    <div class="table-wrap"><table><thead><tr><th>Measure</th>${CONDITIONS.map((c) => `<th class="num">${cond(c)}</th>`).join("")}</tr></thead><tbody>
      ${[["Relative welfare efficiency", "rwe", fmt], ["Egalitarian welfare", "ew", fmt], ["Agreement rate", "agreement_rate", pct]].map(([l, k, f]) => `<tr><td><b>${esc(DEF[k].plain)}</b> · ${l}${info(k)}<span class="def">${esc(DEF[k].what)} ${esc(DEF[k].why.split(":")[0])}. ${esc(DEF[k].basis)}</span></td>${CONDITIONS.map((c) => `<td class="num">${by[c] ? f(by[c][k]) : "—"} <span class="sub">n = ${by[c]?.n ?? 0}</span></td>`).join("")}</tr>`).join("")}
      <tr><td class="note">The negotiations behind these values</td>${CONDITIONS.map((c) => `<td class="num">${by[c]?.n ? `<a href="#/experiments" data-show="${ds}|${c}">View ${by[c].n}${icon("chev-r")}</a>` : "—"}</td>`).join("")}</tr>
    </tbody><caption>Table 1. Condition means, n = negotiations in each condition. Presented side by side; no condition is ranked, and no inferential test has been run.</caption></table></div>
    <div class="grid g2" style="margin-top:20px">
      <div class="panel">${figure(nextFig(), "Relative welfare efficiency per negotiation", `${info("rwe")} SW / W* when agreed, 0 otherwise. One dot per negotiation; the black tick is the condition mean.`, dotPlot(dots("rwe"), { axis: "RWE (unconditional)" }), condLegend, tableFor("rwe"))}</div>
      <div class="panel">${figure(nextFig(), "Egalitarian welfare per negotiation", `${info("ew")} min(u<sub>A</sub>, u<sub>B</sub>) / 100 when agreed, 0 otherwise.`, dotPlot(dots("ew"), { axis: "Egalitarian welfare (unconditional)" }), condLegend, tableFor("ew"))}</div>
    </div></section>

  <section class="block">${blockHead("Outcomes", "Rates over all negotiations")}
    <div class="grid g2">
      <div class="panel">${figure(nextFig(), "Outcome distribution by condition", "Share of negotiations ending in each of the four strategic outcomes.", stackBars(CONDITIONS.map((c) => ({ label: c, n: by[c]?.n ?? 0, parts: outcomeShares(by[c]?.outcomes, by[c]?.n).map((p) => ({ ...p, key: p.outcome, color: OUT_COLOR[p.outcome], ink: OUT_INK[p.outcome] })) }))), outLegend,
        dataTable(["Condition", ...OUTCOMES.map((o) => OUTCOME_LABEL[o]), "n"], CONDITIONS.map((c) => [c, ...OUTCOMES.map((o) => by[c]?.outcomes[o] ?? 0), by[c]?.n ?? 0])))}</div>
      <div class="panel">${figure(nextFig(), "Outcome by first mover", "A-first and B-first cells, both conditions pooled.", stackBars(["A", "B"].map((fm) => { const o = v.pooled.outcomes_by_first_mover[fm], n = Object.values(o).reduce((a, b) => a + b, 0); return { label: `Agent ${fm} first`, n, parts: outcomeShares(o, n).map((p) => ({ ...p, key: p.outcome, color: OUT_COLOR[p.outcome], ink: OUT_INK[p.outcome] })) }; })), outLegend)}</div>
    </div>
    <div class="grid g2" style="margin-top:16px">${CONDITIONS.map((c) => `<div class="panel">${figure(nextFig(), `Rounds used, ${c}`, `All outcomes. Mean rounds to agreement ${fmt(by[c]?.mean_rounds_to_agreement, 1)}; invalid-action rate ${pct(by[c]?.invalid_action_rate)}${Object.keys(by[c]?.invalid_reasons ?? {}).length ? ` (${Object.entries(by[c].invalid_reasons).map(([k, m]) => `${esc(k)} ${m}`).join(", ")})` : ""}.`,
      histogram(by[c]?.rounds ?? {}, Array.from({ length: s.setup.max_rounds }, (_, i) => i + 1), COND_COLOR[c], c, "Round at which the negotiation ended"), "",
      dataTable(["Round", "Negotiations"], Array.from({ length: s.setup.max_rounds }, (_, i) => [i + 1, by[c]?.rounds?.[i + 1] ?? 0])))}</div>`).join("")}</div>
  </section>

  <section class="block">${blockHead("Secondary measures", "Descriptive, agreement only, not tested. Condition can change which negotiations agree, so these are not clean treatment effects (spec §8.12).")}
    <div class="grid g2">
      <div class="panel">${figure(nextFig(), "Equitability", `${info("equitability")} 1 − |u<sub>A</sub> − u<sub>B</sub>| / 100, agreed negotiations only.`, dotPlot(dots("equitability", true), { axis: "Equitability" }), condLegend, tableFor("equitability"))}</div>
      <div class="panel">${figure(nextFig(), "Conditional social welfare efficiency", `${info("swe")} SW / W*, agreed negotiations only.`, dotPlot(dots("swe", true), { axis: "Conditional SWE" }), condLegend, tableFor("swe"))}</div>
    </div></section>

  <section class="block">${blockHead("Diagnostics", "Agreement only, not tested")}
    <div class="grid g2">
      <div class="panel">${figure(nextFig(), "Model imbalance", `${info("imbalance")} u<sub>Claude</sub>/100 − u<sub>GPT</sub>/100, whichever seat each model held. Right of the dashed line, Claude realised more of its maximum.`, dotPlot(dots("imbalance", true), { ...signedDots, axis: "Claude − GPT" }), condLegend, tableFor("imbalance"))}</div>
      <div class="panel">${figure(nextFig(), "First-mover gap", `${info("first_mover_gap")} u<sub>first</sub>/100 − u<sub>second</sub>/100. Right of the dashed line, the first mover realised more.`, dotPlot(dots("first_mover_gap", true), { ...signedDots, axis: "First − second mover" }), condLegend, tableFor("first_mover_gap"))}</div>
    </div>
    <div class="table-wrap" style="margin-top:16px"><table><thead><tr><th>Condition</th><th class="num">Agreed</th><th class="num">Envy-free${info("envy_free_rate")}</th><th class="num">A envious</th><th class="num">B envious</th><th class="num">Mean imbalance${info("imbalance")}</th><th class="num">Mean first-mover gap${info("first_mover_gap")}</th></tr></thead><tbody>
      ${CONDITIONS.map((c) => { const b = by[c]; return `<tr><td>${cond(c)}</td><td class="num">${b?.n_agreed ?? 0}</td><td class="num">${pct(b?.envy_free_rate)}</td><td class="num">${pct(b?.envious_A_rate)}</td><td class="num">${pct(b?.envious_B_rate)}</td><td class="num">${signed(b?.imbalance)}</td><td class="num">${signed(b?.first_mover_gap)}</td></tr>`; }).join("")}
    </tbody><caption>Table 2. Envy and imbalance diagnostics, agreed negotiations only.</caption></table></div></section>
  ${nxt}`;
}

function glossary() {
  const rows = ["rwe", "ew", "agreement_rate", "equitability", "swe", "imbalance", "first_mover_gap", "envy_free_rate", "invalid_action_rate", "utility", "rounds"];
  return `<details class="glossary"><summary>How to read this page: what each measure means</summary><dl>${rows.map((k) => { const d = DEF[k]; return `<div><dt>${esc(d.plain)}<span class="formal">${esc(d.name)}</span></dt><dd>${esc(d.what)} <span>${esc(d.why)}</span><em>${esc(d.read)} ${esc(d.basis)}</em></dd></div>`; }).join("")}</dl></details>`;
}

// ------------------------------------------------------------------ 6. comparison
function comparison(s) {
  const ds = st.dataset, v = s.results[ds];
  const nxt = next("#/methodology", "Methodology", "Check how the experiment was designed, and why the answer is sealed until the end.");
  const top = `<nav class="subnav" aria-label="Research results"><a href="#/results">By condition</a><a href="#/structured" aria-current="page">Structured vs baseline</a></nav>${head("Structured vs baseline", "Did the instructions change the outcome? This is where the answer will appear: does each preregistered measure differ between the baseline and the structured negotiation instructions? Same models, same negotiation engine; only the instructions differ.")}
  ${hypCards(s)}
  <section class="block">${blockHead("Descriptive paired differences", "Per-instance d<sub>i</sub> = m<sub>i,structured</sub> − m<sub>i,baseline</sub>, where m is the mean over that instance's negotiations in each condition (spec §8.9). Not a test; neither condition is ranked")}
  ${datasetBar(ds)}`;
  const stop = blocked(s, ds, `</section>${nxt}`);
  if (stop) return top + stop;
  const rows = s.negotiations.filter((r) => r.mode === ds), by = v.by_condition, paired = v.paired;
  const narrow = innerWidth < 600; // short labels keep the plot area usable on phones
  const METRICS = [["rwe", "Δ RWE"], ["ew", narrow ? "Δ EW" : "Δ Egalitarian welfare"], ["agreement_rate", narrow ? "Δ Agreement" : "Δ Agreement rate"]];
  const diffs = figure(nextFig(), "Paired differences per instance", `One dot per instance, n = ${paired.length}; the tick is mean d. Orange dots, right of the dashed zero line, scored higher under <code>structured_v1</code>; blue dots, left of it, higher under <code>baseline_v1</code>. No interval is drawn because the preregistered bootstrap has not been run.`,
    dotPlot(METRICS.map(([m, label]) => ({ label, color: "var(--ink-2)", mean: mean(paired.map((p) => p[m])), points: paired.map((p) => ({ v: p[m], color: p[m] > 0 ? "var(--struct)" : p[m] < 0 ? "var(--base)" : "var(--ink-2)", tip: `${label}, seed ${p.seed}: d = ${signed(p[m])}, ${p[m] > 0 ? "higher under structured_v1" : p[m] < 0 ? "higher under baseline_v1" : "no difference"} (${p.n_structured} structured, ${p.n_baseline} baseline)` })) })),
      { domain: [-1, 1], zero: true, bin: 0.04, ticks: 4, width: innerWidth < 600 ? 360 : 760, fmtTick: (x) => signed(x, 1), meanFmt: (x) => signed(x), axis: "d = structured − baseline" }), "",
    dataTable(["Instance seed", ...METRICS.map(([, l]) => l), "n structured", "n baseline"], paired.map((p) => [p.seed, ...METRICS.map(([m]) => signed(p[m])), p.n_structured, p.n_baseline])));
  const rate = (xs) => (xs.length ? pct(xs.filter((r) => r.outcome === "agreed").length / xs.length) : "—");
  const cellRate = (g, pred) => { const xs = g.filter(pred); return `${rate(xs)} <span class="sub">n = ${xs.length}</span>`; };
  return `${top}${ds === "pilot" ? pilotNotice : ""}
  ${notice("warn", "alert", `<b>No inferential statistics.</b> The preregistered sign-flip permutation test, 95% bootstrap interval, Wilcoxon sensitivity check and Holm correction (§8.9) are ${s.setup.analysis_implemented ? "not run yet" : "not implemented yet"}, so no intervals or p-values are shown.`)}
  <ul class="howto"><li><b>One dot is one instance</b> (one seed): its average under Structured minus its average under Baseline.</li><li><b>Right of the dashed zero line</b>, that instance scored higher under Structured; left of it, higher under Baseline.</li><li><b>No error bars</b>: the preregistered bootstrap interval has not been computed, so no difference here is a finding.</li></ul>
    ${paired.length ? `<div class="panel">${diffs}</div>` : notice("", "await", "<b>No paired instances yet.</b> An instance appears once it has negotiations stored under both conditions.")}</section>
  <section class="block">${blockHead("Secondary measures side by side", "Agreement-only and diagnostic rates. The three confirmatory measures per condition are on <a href=\"#/results\">Results</a>")}
    <div class="table-wrap"><table><thead><tr><th>Measure</th>${CONDITIONS.map((c) => `<th class="num">${cond(c)}</th>`).join("")}<th>Basis</th></tr></thead><tbody>
      ${[["Equitability", "equitability", fmt, "agreed"], ["Conditional SWE", "swe", fmt, "agreed"], ["Envy-free rate", "envy_free_rate", pct, "agreed"], ["Invalid-action rate", "invalid_action_rate", pct, "all"]]
        .map(([l, k, f, basis]) => `<tr><td>${l}${info(k)}</td>${CONDITIONS.map((c) => `<td class="num">${by[c] ? f(by[c][k]) : "—"} <span class="sub">n = ${basis === "all" ? by[c]?.n ?? 0 : by[c]?.n_agreed ?? 0}</span></td>`).join("")}<td class="note">${basis === "all" ? "all negotiations" : "agreed only"}</td></tr>`).join("")}
    </tbody></table></div></section>
  <section class="block">${blockHead("Seat and first-mover diagnostics", "Agreement rate per design cell; imbalance over agreed negotiations")}
    <div class="table-wrap"><table><thead><tr><th>Condition</th><th class="num">Claude in A</th><th class="num">Claude in B</th><th class="num">A first</th><th class="num">B first</th><th class="num">Claude − GPT</th></tr></thead><tbody>
      ${CONDITIONS.map((c) => { const gr = rows.filter((r) => r.condition === c); return `<tr><td>${cond(c)}</td><td class="num">${cellRate(gr, (r) => r.claude_seat === "A")}</td><td class="num">${cellRate(gr, (r) => r.claude_seat === "B")}</td><td class="num">${cellRate(gr, (r) => r.first_mover === "A")}</td><td class="num">${cellRate(gr, (r) => r.first_mover === "B")}</td><td class="num">${signed(by[c]?.imbalance)} <span class="sub">n = ${by[c]?.n_imbalance ?? 0}</span></td></tr>`; }).join("")}
    </tbody></table></div></section>
  ${nxt}`;
}

// ------------------------------------------------------------------ 7. methodology
function turnDiagram(max) {
  const W = 640, slot = (W - 20) / max, y = 34;
  let out = "";
  for (let t = 1; t <= max; t++) {
    const x = 10 + (t - 1) * slot, who = t % 2 ? "A" : "B", dead = t === max;
    out += `<rect class="slot ${dead ? "dead" : who}" x="${x + 3}" y="${y}" width="${slot - 6}" height="40" rx="3"/><text class="num" x="${x + slot / 2}" y="${y + 25}" text-anchor="middle">${t}</text><text x="${x + slot / 2}" y="${y + 58}" text-anchor="middle">${who}</text>`;
  }
  out += `<text class="ann" x="10" y="16">First mover (A here), fixed per batch</text>`;
  out += `<text class="ann" x="${W - 10}" y="16" text-anchor="end">Deadline: accept or walk away; an offer here is a timeout</text>`;
  out += `<line class="line" x1="${W - 10 - slot / 2}" x2="${W - 10 - slot / 2}" y1="21" y2="${y - 2}"/><line class="line" x1="${10 + slot / 2}" x2="${10 + slot / 2}" y1="21" y2="${y - 2}"/>`;
  return `<div class="diagram"><svg viewBox="0 0 ${W} ${y + 66}" role="img" aria-label="${max} alternating turns; the last turn is the deadline">${out}</svg></div>`;
}
function methodology(s) {
  const c = s.setup, env = c.environment, sci = c.scientific, pv = s.provenance;
  const more = (html) => `<details class="more"><summary>Technical detail</summary>${html}</details>`;
  const secs = [
    ["question", "§8.1", "Research question", `<p class="lead">${esc(RQ)}</p><p class="note">Stated non-directionally, as preregistered: the hypotheses ask whether outcomes <em>differ</em> between conditions, in either direction.</p>`],
    ["conditions", "§8.2", "Baseline vs structured", `<p class="lead">The same negotiation engine is used in both conditions. The experiment changes only the instructions given to the models; everything else is identical.</p><div class="compare"><div class="shared">Shared by both: the engine, turn structure, action schema, user-turn prompt, information and deadline.</div>
      <div class="c-baseline_v1"><h3><i class="sw"></i>baseline_v1</h3><p class="none">System instructions only. Nothing appended.</p></div>
      <div class="c-structured_v1"><h3><i class="sw"></i>structured_v1</h3><div class="add">Appended block, delivered as one package:<ol><li>Preference ranking and revelation</li><li>Integrative trade guidance</li><li>Disagreement-point and deadline reasoning</li></ol>No fairness instruction.</div></div></div>`],
    ["agents", "§8.3, §8.15", "Experimental design", `<p class="lead">Two fixed participants, ${esc(modelName(sci.ClaudeAgent.model))} and ${esc(modelName(sci.OpenAIAgent.model))}, negotiate against each other. Agent A and Agent B are seats, not separate agents: each model plays both seats and both first-mover roles, so model, seat and turn order can be separated.</p>
      <ul><li><b>Claude</b>: <code>${esc(sci.ClaudeAgent.model)}</code>. <b>GPT</b>: <code>${esc(sci.OpenAIAgent.model)}</code>. Settings are identical in every cell and condition.</li><li>Each turn, an agent returns one structured action (offer, accept or walk away) and an optional short message the other agent sees.</li></ul>`],
    ["private", "§2, §3.4", "What the agents know", `<p class="lead">Each agent knows the pool and its own values, never the other agent's.</p>
      <div class="compare"><div><h3>An agent sees</h3><ul><li>The resource pool</li><li>Its own private valuation</li><li>The transcript so far</li><li>The round number and rounds left</li></ul></div>
      <div><h3>An agent never sees</h3><ul><li>The other agent's valuation</li><li>The best achievable allocation</li><li>Any metric, mid-negotiation</li><li>Other negotiations</li></ul></div></div>
      ${more(`<p>${env.num_categories} categories (${env.category_names.map(esc).join(", ")}), each with ${env.min_qty}–${env.max_qty} indivisible units drawn from the instance seed. Each agent spreads ${env.total_points} importance points over the categories (Dirichlet, α = ${env.dirichlet_alpha}), seeded from (instance, role) with SHA-256, so its best possible utility is exactly 100. The separation is structural: the object an agent's code receives has no field for the other valuation.</p>`)}`],
    ["rules", "§3, §8.5", "How a negotiation works", `<p class="lead">Two agents alternate for up to ${c.max_rounds} turns. Each turn is one action: offer an allocation, accept the other's standing offer, or walk away.</p>${turnDiagram(c.max_rounds)}
      <div class="semantics">
      <div><b>Agreement</b><p>An accept of a valid standing offer from the opponent. The accepted offer is the final allocation.</p></div>
      <div><b>Walk-away</b><p>Any walk-away action, on any turn.</p></div>
      <div><b>Timeout</b><p>A valid offer on the final turn. It is recorded but never standing, and yields no allocation.</p></div>
      <div><b>Invalid action</b><p>Malformed output, an invalid allocation, or an illegal accept. Never retried.</p></div></div>
      ${more(`<ul><li>Accepting needs a valid standing offer from the <b>opponent</b>; every offer is re-validated and never repaired. Disagreement point: both score 0.</li><li><b>Infrastructure failures</b> (transport errors, budget stops) are not outcomes. They are stored separately and the negotiation is re-run from turn 1, up to ${c.max_transport_reruns} times.</li><li><b>Strategic outcomes</b> are never retried, and a negotiation is never re-run because of its result.</li></ul>`)}`],
    ["metrics", "§8.6–8.7", "What we measure", `<p class="lead">Three confirmatory measures are tested; secondary and diagnostic measures are reported to describe and explain results. All are computed by the evaluator from the hidden valuations; agents never report their own scores.</p>
      <div class="measures">
        <div><h3>Confirmatory</h3><p>Every negotiation counts.</p><ul><li>${term("rwe")}</li><li>${term("ew")}</li><li>${term("agreement_rate")}</li></ul></div>
        <div><h3>Secondary</h3><p>Agreed negotiations only.</p><ul><li>${term("equitability")}</li><li>${term("swe")}</li></ul></div>
        <div><h3>Diagnostics</h3><p>Not tested.</p><ul><li>${term("envy_free_rate")}</li><li>${term("imbalance")}</li><li>${term("first_mover_gap")}</li></ul></div></div>
      ${more(`<div class="formula">
      <div class="n">RWE</div><div class="f">SW / W* if agreed, else 0</div><div class="t">confirmatory</div>
      <div class="n">Egalitarian welfare</div><div class="f">min(u_A, u_B) / 100 if agreed, else 0</div><div class="t">confirmatory</div>
      <div class="n">Agreement rate</div><div class="f">agreed / n</div><div class="t">confirmatory</div>
      <div class="n">Equitability</div><div class="f">1 − |u_A − u_B| / 100</div><div class="t">secondary, agreed only</div>
      <div class="n">Conditional SWE</div><div class="f">SW / W*</div><div class="t">secondary, agreed only</div>
      <div class="n">Model imbalance</div><div class="f">u_Claude / 100 − u_GPT / 100</div><div class="t">diagnostic</div>
      <div class="n">First-mover gap</div><div class="f">u_first / 100 − u_second / 100</div><div class="t">diagnostic</div>
      <div class="n">W*</div><div class="f">Σ_c q_c · max(v_A[c], v_B[c])</div><div class="t">ground truth</div></div>`)}`],
    ["hypotheses", "§8.8", "Confirmatory hypotheses", `<ol class="hyps"><li><b>H1</b><span>Relative welfare efficiency differs between baseline_v1 and structured_v1.</span></li><li><b>H2</b><span>Egalitarian welfare differs between baseline_v1 and structured_v1.</span></li><li><b>H3</b><span>Agreement rate differs between baseline_v1 and structured_v1.</span></li><li class="note">Each null hypothesis is no difference.</li></ol>`],
    ["analysis", "§8.9, §8.3", "Statistical analysis", `<p class="lead">${c.full.instances} instances × 8 negotiations = ${c.full.total}. For each instance, the two conditions are compared as a pair, and the pairs are tested together. Nothing is compared until all ${c.full.total} exist.</p>
      <div class="readouts" style="margin-top:14px"><div class="readout"><dt>Full experiment</dt><dd>${c.full.total}</dd><div class="basis">${c.full.instances} instances × 8, seeds ${c.full.seeds[0]}–${c.full.seeds[1]}</div></div>
      <div class="readout"><dt>Pilot</dt><dd>${c.pilot.total}</dd><div class="basis">3 instances × 8, operational only</div></div>
      <div class="readout"><dt>Cells per instance</dt><dd>8</dd><div class="basis">2 seats × 2 first movers × 2 conditions</div></div></div>
      ${more(`<ol><li>Per instance and condition, average each measure over the 4 negotiations.</li><li>Take the paired difference d<sub>i</sub> = structured − baseline for each of the ${c.full.instances} instances.</li><li>Test mean(d<sub>i</sub>) with a two-sided sign-flip permutation test, 10,000 permutations.</li><li>Report a 95% percentile bootstrap interval, 10,000 resamples of instances.</li><li>Check with a Wilcoxon signed-rank test; apply Holm correction across H1–H3 at α = 0.05.</li><li>Sensitivity analysis: H1–H3 recomputed with invalid-action negotiations removed (§8.11).</li></ol>`)}`],
    ["runtime", "§8.15", "Runtime configuration", `<p class="lead">Model settings were fixed before the first pilot call and every run is checked against them; a run with different settings refuses to start.</p>
      <ul><li>Temperature ${sci.ClaudeAgent.temperature} for both models, ${sci.ClaudeAgent.max_output_tokens} max output tokens, ${c.max_rounds} rounds, ${c.max_transport_reruns} clean reruns after a transport failure.</li><li>The full table, prices and budget are on <a href="#/configuration">Configuration</a>.</li></ul>`],
    ["limits", "§8.12", "Limitations", `<ul><li><b>Bundled intervention</b>: an effect belongs to the whole three-part package.</li><li><b>Output-schema asymmetry</b>: Claude answers through a forced tool call, GPT through strict JSON schema.</li><li><b>One model pairing</b>, stochastic outputs, and possible model drift behind fixed identifiers.</li><li><b>Linear utilities</b> make efficiency and fairness diverge by construction.</li><li><b>Selection</b> in agreement-only measures, and correlated confirmatory outcomes.</li></ul>`],
    ["provenance", "§8.15", "Version and provenance", `<p class="lead">Every run records these fingerprints, so any result can be traced to the exact code, prompts and specification it ran under.</p>
      <dl class="titleblock">
        <div><dt>Protocol${info("p", "Identifier of the frozen negotiation rules. Every run stores it.")}</dt><dd>${esc(pv.protocol_id)}</dd></div>
        <div><dt>Specification${info("s", "SHA-256 fingerprint of docs/spec.md. Each run records the fingerprint it ran under, so a changed preregistration is detectable.")}</dt><dd class="mono" title="${esc(pv.spec_version)}">${esc(short(pv.spec_version, 12))}</dd></div>
        <div><dt>Code${info("c", "Git commit of the working tree. Uncommitted means local edits exist; runs record this too.")}</dt><dd class="mono">${esc(short(pv.code_version, 8))}${pv.code_version.endsWith("+dirty") ? " (uncommitted)" : ""}</dd></div>
        <div><dt>Prompts${info("h", "Fingerprint of the prompt module and output schemas both models receive.")}</dt><dd class="mono" title="${esc(pv.prompt_hash)}">${esc(short(pv.prompt_hash, 12))}</dd></div>
        <div><dt>Models</dt><dd>${esc(sci.ClaudeAgent.model)}, ${esc(sci.OpenAIAgent.model)}</dd></div>
      </dl>`],
  ];
  const ORDER = ["question", "private", "rules", "conditions", "metrics", "hypotheses", "agents", "analysis", "limits", "runtime", "provenance"];
  secs.sort((a, b) => ORDER.indexOf(a[0]) - ORDER.indexOf(b[0]));
  return `${head("Methodology", "How was the experiment designed and tested? The preregistered design, readable in a minute: each section opens with the short version, and the technical detail is folded underneath. <code>docs/spec.md</code> is the source of truth; its section numbers are given throughout.")}
  ${notice("", "lock", "<b>Everything on this page was fixed before any data was collected</b>, in the preregistration (<code>docs/spec.md</code>). None of it can be changed from this site.")}
  <div class="protocol"><nav class="toc" aria-label="Protocol sections">${secs.map(([id, ref, t]) => `<a href="#/methodology" data-jump="${id}"><span>${ref.split(",")[0]}</span>${t}</a>`).join("")}</nav>
  <div>${secs.map(([id, ref, t, body]) => `<section id="m-${id}"><header><h2>${t}</h2><span class="cite">${ref}</span></header>${body}</section>`).join("")}</div></div>
  ${next("#/configuration", "Configuration", "The exact model settings, budget and prompts every run is checked against.")}`;
}

// ------------------------------------------------------------------ 6. research: experiments
function experiments(s) {
  const rows = s.negotiations, sci = s.setup.scientific, env = s.setup.environment;
  const claude = modelName(sci.ClaudeAgent.model), gpt = modelName(sci.OpenAIAgent.model);
  return `${head("Experiments", "The controlled experiment behind the lab: the same kind of negotiation, run hundreds of times under a locked configuration. Has it run yet, and what was recorded?")}
  ${notice("", "lock", "<b>Research configuration is locked to preserve experimental validity.</b> Sandbox runs from Try it are never shown here and never count towards the research results.")}
  ${labStatus(s)}
  <section class="block" id="recorded">${blockHead("Recorded negotiations", rows.length ? "Each row is one negotiation of the controlled experiment; open it to replay it turn by turn" : "")}
    ${rows.length ? negList(s) : `<div class="empty"><h3>No controlled research runs have been recorded yet.</h3>
      <p>For the interactive experience, start a sandbox negotiation.</p><a class="cta" href="#/try">Try a negotiation${icon("chev-r")}</a>
      <p class="note">Reported research runs will appear here once data collection begins: first the pilot (${s.progress.pilot.target} negotiations), then the full experiment (${s.progress.full.target}).</p></div>`}
  </section>

  <section class="block">${blockHead("About the controlled experiment", "What is held fixed, and what changes")}
    <div class="grid g2">
      <figure class="seatflow" aria-label="Diagram: Claude and GPT each take a seat, A or B; the seats exchange offers over a shared pool of resources, which ends in one final outcome">
        <div class="sf-models"><span class="tag m-claude">${esc(claude)}</span><span class="tag m-openai">${esc(gpt)}</span></div>
        <div class="sf-down" aria-hidden="true"><i></i><i></i></div>
        <div class="sf-seats"><b>Seat A</b><span class="sf-offers" aria-hidden="true">◄ offers ►</span><b>Seat B</b></div>
        <div class="sf-down one" aria-hidden="true"><i></i></div>
        <div class="sf-box">Shared resources <small>${env.category_names.map(esc).join(", ")}, drawn from a seed</small></div>
        <div class="sf-down one" aria-hidden="true"><i></i></div>
        <div class="sf-box">Final outcome <small>agreement and a split, or no deal</small></div>
        <figcaption>Which model sits in which seat, and which seat opens, is varied evenly.</figcaption>
      </figure>
      <ul class="plainlist">
        <li><b>Two fixed models</b>, ${esc(claude)} and ${esc(gpt)}, with identical locked settings. Agent A and Agent B are seats, not extra models.</li>
        <li><b>Private preferences are drawn at random</b> from a seed for each instance, so every scenario is reproducible.</li>
        <li><b>One thing changes:</b> the baseline instructions or the structured negotiation instruction strategy. The negotiation engine and rules are the same in both.</li>
        <li><b>Two stages:</b> a ${s.progress.pilot.target}-negotiation pilot checks the software; the ${s.progress.full.target}-negotiation full experiment is the data the hypotheses are tested on.</li>
        <li>Details: <a href="#/methodology">Methodology</a> and <a href="#/configuration">Configuration</a>.</li>
      </ul>
    </div>
  </section>

  <details class="more records"><summary>Run records, budget and data files (for researchers)</summary>${runRecords(s)}</details>
  ${next("#/results", "Results", "What happened in each condition, once there is data.")}`;
}

// ------------------------------------------------------------------ research: run records
function runRecords(s) {
  const spent = (mode) => { const rs = s.runs.filter((r) => r.mode === mode && r.totals); if (!rs.length) return null; const c = rs.map((r) => r.totals.cost_usd); return c.some((x) => x === null) ? null : c.reduce((a, b) => a + b, 0); };
  const cap = (mode) => s.runs.find((r) => r.mode === mode)?.config?.experiment?.budget_max_cost_usd ?? null;
  const tone = { completed: "good", running: "good", incomplete: "warn", budget_exhausted: "warn", failed: "crit" };
  const cur = s.provenance;
  const { pilot, full } = s.progress, done = full.done >= full.target;
  return `  <section class="block">${blockHead("What you can rely on right now")}
    <dl class="outcomes rely">
      <div><dt>Design, prompts and model settings</dt><dd><b>Frozen.</b> Fixed before the first pilot call. Every run is checked against them and records their versions.</dd></div>
      <div><dt>Pilot values</dt><dd><b>${pilot.done ? "Software checks only." : "None yet."}</b> The pilot checks that the harness works with the real models. Its values are never findings.</dd></div>
      <div><dt>Full-experiment data</dt><dd><b>${full.done ? `${full.done} of ${full.target} recorded.` : "None yet."}</b> ${done ? "Complete: condition results are unsealed." : "Condition results stay sealed until all of it exists."}</dd></div>
      <div><dt>Results and research conclusion</dt><dd><b>None yet.</b> It will appear on <a href="#/structured">Structured vs baseline</a> once the full experiment is complete and the preregistered analysis has run.</dd></div>
    </dl></section>
  <section class="block">${blockHead("Budget", "Spend is the provider-reported usage stored with each run, priced with the approved per-token rates; it is not a bill")}
    <dl class="readouts">${["pilot", "full"].map((m) => { const sp = spent(m), cp = cap(m); return readout(m === "pilot" ? "Pilot spend" : "Full-run spend", sp === null ? "—" : `$${fmt(sp, 4)}`, cp ? `of $${cp} cap` : "no cap recorded"); }).join("")}
    ${readout("Aborted attempts", s.progress.aborted_attempts, "infrastructure only")}${readout("Runs recorded", s.runs.length, s.runs.length ? `last ${esc(timeAgo(s.runs.at(-1).started_at))}` : "none yet")}</dl></section>
  <section class="block">${blockHead("Runs", "One row per batch of negotiations")}
    ${s.runs.length ? `<div class="table-wrap"><table><thead><tr><th>Run</th><th>Status</th><th>Dataset</th><th>Condition</th><th>Started</th><th class="num">Done</th><th class="num">Aborted</th><th class="num">Tokens in / out</th><th class="num">Cost</th><th>Code</th></tr></thead><tbody>
    ${s.runs.map((r) => { const t = r.totals ?? {}; const stale = r.code_version !== cur.code_version.replace("+dirty", "") && r.code_version !== cur.code_version;
      return `<tr><td class="mono">${esc(short(r.run_id, 10))}</td><td><span class="badge ${tone[r.status] ?? ""}">${esc(r.status)}</span></td><td>${esc(r.mode)}</td><td>${cond(r.method)}</td>
      <td title="${esc(new Date(r.started_at * 1000).toISOString())}">${esc(timeAgo(r.started_at))}</td><td class="num">${t.completed_negotiations ?? s.negotiations.filter((n) => n.run_id === r.run_id).length} / ${r.num_negotiations}</td>
      <td class="num">${t.aborted_attempts ?? s.aborted.filter((a) => a.run_id === r.run_id).length}</td><td class="num">${t.input_tokens ?? "—"} / ${t.output_tokens ?? "—"}</td><td class="num">${t.cost_usd === undefined || t.cost_usd === null ? "—" : `$${fmt(t.cost_usd, 4)}`}</td>
      <td class="mono">${esc(short(r.code_version, 8))}${stale ? ` <span class="badge warn" data-tip="This run was recorded with a different code commit than the one checked out now." tabindex="0">differs</span>` : ""}</td></tr>`; }).join("")}
    </tbody></table></div>` : notice("", "await", "<b>No runs recorded.</b> The pilot is started from the command line with <code>scripts/run_pilot.py</code>, which creates <code>results/pilot.db</code> on its first run.")}</section>
  <section class="block">${blockHead("Failures and aborts", "Infrastructure failures only; these are never negotiation outcomes")}
    ${s.aborted.length ? `<div class="table-wrap"><table><thead><tr><th>Run</th><th>#</th><th>Attempt</th><th>Reason</th><th>Actor, round</th><th>Rerun</th><th>Detail</th></tr></thead><tbody>
      ${s.aborted.map((a) => `<tr><td class="mono">${esc(short(a.run_id, 10))}</td><td>${a.negotiation_index}</td><td>${a.attempt_number}</td><td><span class="badge ${a.reason === "budget_exhausted" ? "warn" : "crit"}">${esc(a.reason)}</span></td><td>${esc(a.actor)}, ${a.round_number}</td><td>${a.will_rerun ? "yes" : "no"}</td><td style="white-space:normal;min-width:260px">${esc(a.detail)}</td></tr>`).join("")}
    </tbody></table></div>` : notice("", "check", "<b>No aborted attempts.</b> Transport failures and budget stops are listed here when they happen.")}
    ${s.negotiations.some((n) => n.outcome === "invalid_action") ? `<p class="note" style="margin-top:12px">Invalid actions are strategic outcomes, not harness failures; filter by outcome under <a href="#/experiments">Recorded negotiations</a>.</p>` : ""}</section>
  <section class="block">${blockHead("Data files", "Opened read-only from <code>results/*.db</code>")}
    ${s.databases.length ? `<div class="table-wrap"><table><thead><tr><th>File</th><th>Format</th><th>Path</th></tr></thead><tbody>${s.databases.map((d) => `<tr><td class="mono">${esc(d.name)}</td><td>${d.ok ? `<span class="badge good">${icon("check")}version 2</span>` : `<span class="badge crit">${icon("alert")}${esc(d.error)}</span>`}</td><td class="mono" style="white-space:normal">${esc(d.path)}</td></tr>`).join("")}</tbody></table></div>`
    : notice("", "await", "<b>No data file yet.</b> Nothing has been written to <code>results/</code>.")}</section>
  <section class="block">${blockHead("Current version")}<dl class="kv">
    <dt>Code</dt><dd class="mono">${esc(cur.code_version)}</dd><dt>Protocol</dt><dd class="mono">${esc(cur.protocol_id)}</dd>
    <dt>Specification</dt><dd class="mono">${esc(cur.spec_version)}</dd><dt>Prompts</dt><dd class="mono">${esc(cur.prompt_hash)}</dd></dl></section>`;
}

// ------------------------------------------------------------------ hero video
const HERO_VIDEO = "assets/live%20wally.mp4";

/** Sizes the hero video layer to the hero and plays it only while it can be seen:
 *  paused when scrolled away or the tab is hidden, never played under reduced motion
 *  (the first frame is shown instead), and removed if the file cannot be decoded. */
function heroVideo(media) {
  const video = media.querySelector("video"), hero = main.querySelector(".hero");
  let inView = true;
  const fit = () => {
    // Span from main's left edge to the viewport's right edge; end a little below the hero.
    media.style.width = `${document.documentElement.clientWidth - main.getBoundingClientRect().left}px`;
    if (hero) media.style.height = `${Math.round(hero.getBoundingClientRect().bottom - media.getBoundingClientRect().top + 40)}px`;
  };
  const sync = () => {
    if (reducedMotion()) { video.pause(); if (video.currentTime) video.currentTime = 0; return; }
    if (inView && !document.hidden) video.play()?.catch(() => media.classList.add("still")); // autoplay refused: keep the still
    else video.pause();
  };
  video.addEventListener("error", () => media.classList.add("failed"), true);
  video.querySelector("source").addEventListener("error", () => media.classList.add("failed"));
  const io = new IntersectionObserver(([en]) => { inView = en.isIntersecting; sync(); });
  const ro = new ResizeObserver(fit);
  const mq = matchMedia("(prefers-reduced-motion: reduce)");
  fit(); io.observe(media); if (hero) ro.observe(hero);
  // The video belongs to the hero: full at the top, ~0.8 midway, gone as the hero leaves (scroll-linked).
  // Always wired (not an entrance); the slow parallax drop is skipped under reduced motion.
  let unlink = () => {};
  if (M && hero) unlink = M.scroll(M.animate(media, reducedMotion() ? { opacity: [1, 0.8, 0] } : { opacity: [1, 0.8, 0], transform: ["none", "translateY(60px)"] }, { ease: "linear" }), { target: media, offset: ["start start", "end start"] }); // reaches 0 exactly as the layer leaves
  addEventListener("resize", fit);
  document.addEventListener("visibilitychange", sync);
  mq.addEventListener("change", sync);
  sync();
  return () => {
    unlink(); io.disconnect(); ro.disconnect(); removeEventListener("resize", fit);
    document.removeEventListener("visibilitychange", sync); mq.removeEventListener("change", sync);
    video.pause(); video.removeAttribute("src"); video.querySelector("source")?.remove(); video.load(); // stop decoding
  };
}

// ------------------------------------------------------------------ motion layer
function skeleton() {
  return `<a class="back" href="#/experiments">${icon("chev-l")}Experiments</a><header class="page-head"><h1>Reading negotiation…</h1></header>
    <div class="scanner" style="margin-bottom:22px"></div>
    <div class="skel">${[0, 1, 2].map(() => `<div class="row"><i class="dot"></i><div class="lines"><i></i><i></i><i></i><i></i></div></div>`).join("")}</div>`;
}

/** Entrances for the view just rendered. Nothing is pre-hidden: each entrance starts from its first keyframe
 *  just before the element scrolls in (inView with a margin below the viewport), so if a trigger never fires
 *  the content simply stays visible. */
function animateView(name) {
  if (!motionOn()) { if (name === "negotiation" || name === "result") playTimeline(); return; } // static path lights the tape and sets the outcome
  const { animate, inView, scroll, stagger } = M;
  const watch = (el, fn) => el && cleanups.push(inView(el, () => { fn(); }, { margin: "0px 0px 12% 0px", amount: 0 }));

  // Sections below the fold rise in once; anything already on screen is left alone.
  main.querySelectorAll("section.block, .protocol section, .usage").forEach((el) => {
    if (el.getBoundingClientRect().top < innerHeight * 0.92) return;
    watch(el, () => animate(el, { opacity: [0, 1], transform: ["translateY(14px)", "none"] }, { duration: 0.6, ease: EASE }));
  });

  // Figures: axes are drawn first, then marks materialise without overshoot.
  main.querySelectorAll("svg.viz").forEach((svg) => {
    const dots = svg.querySelectorAll('[data-anim="dot"]'), bars = svg.querySelectorAll('[data-anim="bar"]'), cols = svg.querySelectorAll('[data-anim="col"]');
    const means = svg.querySelectorAll('[data-anim="mean"]'), zero = svg.querySelectorAll('[data-anim="zero"]');
    const paired = svg.closest("figure")?.querySelector("figcaption")?.textContent.includes("Paired");
    watch(svg, () => {
      if (zero.length) animate(zero, { opacity: [0, 1], transform: ["scaleY(0)", "scaleY(1)"] }, { duration: 0.6, ease: EASE });
      if (dots.length) animate(dots, { opacity: [0, 1], transform: ["scale(0.4)", "scale(1)"] }, { duration: 0.45, ease: EASE, delay: stagger(paired ? 0.09 : Math.min(0.02, 0.8 / dots.length), { startDelay: 0.15 }) });
      if (bars.length) animate(bars, { transform: ["scaleX(0)", "scaleX(1)"] }, { duration: 0.7, ease: EASE, delay: stagger(0.06, { startDelay: 0.1 }) });
      if (cols.length) animate(cols, { transform: ["scaleY(0)", "scaleY(1)"] }, { duration: 0.6, ease: EASE, delay: stagger(0.04, { startDelay: 0.1 }) });
      if (means.length) animate(means, { opacity: [0, 1] }, { duration: 0.4, delay: 0.55 });
    });
  });

  // Registers: recorded negotiations populate progressively (only cells that hold real data).
  main.querySelectorAll(".register").forEach((reg) => {
    const on = reg.querySelectorAll("i.on");
    if (!on.length) return;
    watch(reg, () => animate(on, { opacity: [0, 1], transform: ["scale(0.4)", "scale(1)"] }, { duration: 0.4, ease: EASE, delay: stagger(Math.min(0.012, 1.2 / on.length)) }));
  });

  if (name === "") {
    const hero = main.querySelector(".hero");
    // Scroll depth: the copy lifts and fades as the hero leaves.
    cleanups.push(scroll(animate(hero.querySelector(".hero-copy"), { transform: ["none", "translateY(-48px)"], opacity: [1, 0.35] }, { ease: "linear" }), { target: hero, offset: ["start start", "end start"] }));
    // The concept reads left to right, and the preview plays its turns in order.
    watch(main.querySelector(".flow"), () => animate(main.querySelectorAll(".flow li"), { opacity: [0, 1], transform: ["translateY(8px)", "none"] }, { duration: 0.5, ease: EASE, delay: stagger(0.09) }));
    watch(main.querySelector(".chat"), () => animate(main.querySelectorAll(".chat .bubble"), { opacity: [0, 1], transform: ["translateY(8px)", "none"] }, { duration: 0.45, ease: EASE, delay: stagger(0.35, { startDelay: 0.1 }) }));
  }
  if (name === "try") animate(main.querySelector(".stepcard"), { opacity: [0, 1], transform: ["translateX(12px)", "none"] }, { duration: 0.35, ease: EASE });
  if (name === "methodology") {
    const slots = main.querySelectorAll(".diagram rect");
    watch(main.querySelector(".diagram"), () => animate(slots, { opacity: [0, 1], transform: ["translateY(6px)", "none"] }, { duration: 0.4, ease: EASE, delay: stagger(0.05) }));
  }
  if (name === "result") animate(main.querySelector(".result-head"), { opacity: [0, 1], transform: ["scale(0.97)", "none"] }, { duration: 0.6, ease: EASE });
  if (name === "negotiation" || name === "result") playTimeline();
}

/** Flight recorder: replay the stored rounds in order; the field follows each recorded turn and the outcome. */
function playTimeline() {
  const tl = main.querySelector("#timeline");
  if (!tl) return;
  const rows = [...tl.querySelectorAll(".turn, .verdict")], tape = [...main.querySelectorAll(".tape a")];
  const outcome = tl.dataset.outcome;
  if (!motionOn()) { tape.forEach((a) => a.classList.add("on")); field.outcome(outcome); return; }
  const { animate } = M, STEP = 0.42;
  field.reset();
  tape.forEach((a) => a.classList.remove("on"));
  rows.forEach((row, i) => {
    const t = 0.15 + i * STEP;
    animate(row, { opacity: [0, 1], transform: ["translateY(8px)", "none"] }, { duration: 0.45, delay: t, ease: EASE });
    row.querySelectorAll(".bar").forEach((bar) => {
      const from = +bar.style.getPropertyValue("--from"), to = +bar.style.getPropertyValue("--to");
      animate(bar.querySelector(".a"), { transform: [`scaleX(${from})`, `scaleX(${to})`] }, { duration: 0.75, delay: t + 0.12, ease: EASE });
      const ghost = bar.querySelector(".ghost");
      if (ghost) animate(ghost, { opacity: [0, 0.7] }, { duration: 0.3, delay: t + 0.1 });
    });
    const round = row.querySelector(".round");
    if (row.classList.contains("accept") || row.classList.contains("verdict")) animate(round, { transform: ["scale(0.85)", "scale(1)"] }, { duration: 0.5, delay: t, ease: EASE });
    if (row.classList.contains("invalid")) animate(row.querySelector(".fault"), { transform: ["translateX(-4px)", "translateX(3px)", "translateX(-2px)", "none"] }, { duration: 0.4, delay: t + 0.1 });
    if (row.dataset.actor) later(() => { field.turn(row.dataset.actor); tape[i]?.classList.add("on"); }, t * 1000);
  });
  later(() => field.outcome(outcome), (0.15 + rows.length * STEP) * 1000);
}

// ------------------------------------------------------------------ wiring
function wire(name) {
  const media = main.querySelector(".hero-media");
  if (media) cleanups.push(heroVideo(media));
  main.querySelectorAll("[data-dataset]").forEach((b) => b.addEventListener("click", () => { st.dataset = b.dataset.dataset; render(); }));
  // From an aggregate to the negotiations behind it: preset the list's filters, then follow the link.
  main.querySelectorAll("[data-show]").forEach((a) => a.addEventListener("click", () => { const [mode, condition] = a.dataset.show.split("|"); st.filters = { mode, condition }; }));
  if (name === "try") wireTry();
  if (name === "lab") wireLab(parse().arg);
  if (name === "live" && main.querySelector("#live-tl")) pollLive(parse().arg); // a finished run renders its result instead
  wireFollowUps();
  if (name === "experiments" && st.snap.negotiations.length) {
    negRows();
    main.querySelectorAll("[data-filter]").forEach((el) => el.addEventListener("input", () => { st.filters[el.dataset.filter] = el.value; negRows(); }));
    main.querySelector("[data-reset]").addEventListener("click", () => { st.filters = {}; render(false); });
    main.querySelector("#rows").addEventListener("click", (e) => { const tr = e.target.closest("tr[data-href]"); if (tr && !e.target.closest("a")) location.hash = tr.dataset.href; });
    const mt = main.querySelector("#measuretoggle");
    mt.addEventListener("change", () => { st.showMeasures = mt.checked; main.querySelector("table.negs").classList.toggle("hide-measures", !mt.checked); });
  }
  main.querySelectorAll("[data-scroll]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); document.getElementById(a.dataset.scroll)?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth" }); }));
  const t = main.querySelector("#evaltoggle");
  if (t) t.addEventListener("change", () => { st.evaluator = t.checked; main.querySelectorAll("[data-eval]").forEach((el) => (el.hidden = !t.checked)); });
  const replay = main.querySelector("#replay");
  if (replay) replay.addEventListener("click", () => { cleanups.forEach((f) => f()); cleanups = []; playTimeline(); });
  main.querySelectorAll(".tape a").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); document.getElementById(`turn-${a.dataset.turn}`)?.scrollIntoView({ behavior: reducedMotion() ? "auto" : "smooth", block: "center" }); }));
  // Explanations fold open with a short fade, so the new text reads as part of the same place.
  main.querySelectorAll("details.glossary, details.more").forEach((d) => d.addEventListener("toggle", () => {
    if (d.open && motionOn()) M.animate([...d.children].filter((c) => c.tagName !== "SUMMARY"), { opacity: [0, 1], transform: ["translateY(-4px)", "none"] }, { duration: 0.25, ease: EASE });
  }));
  const smooth = matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth";
  main.querySelectorAll("[data-jump]").forEach((a) => a.addEventListener("click", (e) => { e.preventDefault(); document.getElementById(`m-${a.dataset.jump}`)?.scrollIntoView({ behavior: smooth }); }));
  if (name === "methodology") {
    const links = new Map([...main.querySelectorAll("[data-jump]")].map((a) => [`m-${a.dataset.jump}`, a]));
    // Short last sections never reach the band below; at the page end the last one is the one being read.
    const atBottom = () => innerHeight + scrollY >= document.documentElement.scrollHeight - 2;
    const mark = (a) => { links.forEach((l) => l.classList.remove("on")); (atBottom() ? [...links.values()].at(-1) : a)?.classList.add("on"); };
    const io = new IntersectionObserver((entries) => entries.forEach((en) => { if (en.isIntersecting) mark(links.get(en.target.id)); }), { rootMargin: "-20% 0px -70% 0px" });
    main.querySelectorAll(".protocol section").forEach((sec) => io.observe(sec));
    const atEnd = () => { if (atBottom()) mark(); };
    addEventListener("scroll", atEnd, { passive: true });
    cleanups.push(() => { io.disconnect(); removeEventListener("scroll", atEnd); });
  }
  const p = main.querySelector("[data-phase]");
  if (p) { if (st.phaseKey && st.phaseKey !== p.dataset.phase) p.classList.add("changed"); st.phaseKey = p.dataset.phase; }
}

/** Try-it: every input updates the draft in place; structural changes re-render the step. */
function wireTry() {
  const d = st.draft, err = main.querySelector("#step-error");
  const say = (m) => { err.textContent = m ?? ""; err.hidden = !m; };
  const num = (v) => (v.trim() === "" ? NaN : Number(v));
  const on = (sel, ev, fn) => main.querySelectorAll(sel).forEach((el) => el.addEventListener(ev, () => fn(el)));
  on('input[name="mode"]', "change", (el) => { d.mode = el.value; render(false); });
  on('input[name="first"]', "change", (el) => { d.first = el.value; render(false); });
  on('input[name="strategy"]', "change", (el) => { d.strategy = el.value; });
  on("[data-agent]", "change", (el) => { d.agents[el.dataset.agent] = el.value; render(false); });
  on("[data-rname]", "input", (el) => { d.pool[+el.dataset.rname][0] = el.value.trim().toLowerCase(); say(null); });
  on("[data-rqty]", "input", (el) => {
    d.pool[+el.dataset.rqty][1] = num(el.value);
    main.querySelector("#pooltotal").textContent = `${sum(d.pool.map(([, q]) => (Number.isInteger(q) ? q : 0)))} units in the pool`;
    say(null);
  });
  on("[data-add]", "click", () => {
    const name = ["coins", "maps", "tools", "seeds", "gems", "lamps"].find((n) => !d.pool.some(([p]) => p === n));
    d.pool.push([name, 5]); d.points.A.push(0); d.points.B.push(0); render(false);
  });
  on("[data-remove]", "click", (el) => { const i = +el.dataset.remove; d.pool.splice(i, 1); d.points.A.splice(i, 1); d.points.B.splice(i, 1); render(false); });
  on("[data-pt]", "input", (el) => {
    const r = el.dataset.pt, i = +el.dataset.i;
    d.points[r][i] = num(el.value);
    main.querySelector(`#total-${r}`).innerHTML = pointsTotal(d, r);
    main.querySelector(`[data-pbar="${r}${i}"]`).style.setProperty("--w", Math.min(1, (d.points[r][i] || 0) / 100));
    say(null);
  });
  on("[data-step]", "click", (el) => { const k = +el.dataset.step, p = stepProblem(d, k); if (p) say(p); else location.hash = `#/try/${k + 1}`; });
  const start = main.querySelector("#start");
  start?.addEventListener("click", async () => {
    start.disabled = true; start.textContent = "Starting…"; say(null);
    const names = d.pool.map(([n]) => n);
    const body = { agents: { A: seatKind(d, "A"), B: seatKind(d, "B") }, first_mover: d.first, strategy: d.strategy, pool: Object.fromEntries(d.pool),
      preferences: Object.fromEntries(["A", "B"].map((r) => [r, Object.fromEntries(names.map((n, i) => [n, d.points[r][i]]))])) };
    try {
      const res = await fetch("/api/sandbox/runs", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const out = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(out.error ?? `The server refused the request (${res.status}).`);
      location.hash = `#/live/${out.id}`;
    } catch (e) {
      say(e.message === "Failed to fetch" ? "The server could not be reached. Check that it is still running, then try again." : e.message);
      start.disabled = false; start.innerHTML = `Start negotiation${icon("chev-r")}`;
    }
  });
}

/** Result and Compare: reuse a finished run's setup, pick runs to compare. */
function wireFollowUps() {
  main.querySelectorAll("[data-flip]").forEach((a) => a.addEventListener("click", () => draftFrom(st.resultRun, a.dataset.flip)));
  main.querySelectorAll("[data-again]").forEach((a) => a.addEventListener("click", () => draftFrom(st.resultRun)));
  main.querySelectorAll("[data-flip-id]").forEach((a) => a.addEventListener("click", () => {
    const run = st.cmpRuns.get(a.dataset.flipId);
    draftFrom(run, run.config.strategy === "structured_v1" ? "baseline_v1" : "structured_v1");
  }));
  main.querySelectorAll("[data-cmp]").forEach((el) => el.addEventListener("change", () => { st.cmp[+el.dataset.cmp] = el.value; render(false); }));
}

// tooltip layer: pointer and keyboard focus
const tip = document.getElementById("tip");
const showTip = (el, x, y) => { tip.textContent = el.dataset.tip; tip.hidden = false; tip.style.left = `${Math.min(x + 14, innerWidth - tip.offsetWidth - 8)}px`; tip.style.top = `${y + 14}px`; };
document.addEventListener("pointerover", (e) => { const el = e.target.closest("[data-tip]"); if (el) showTip(el, e.clientX, e.clientY); });
document.addEventListener("pointermove", (e) => { if (!tip.hidden) { tip.style.left = `${Math.min(e.clientX + 14, innerWidth - tip.offsetWidth - 8)}px`; tip.style.top = `${e.clientY + 14}px`; } });
document.addEventListener("pointerout", (e) => { if (e.target.closest("[data-tip]")) tip.hidden = true; });
document.addEventListener("focusin", (e) => { const el = e.target.closest?.("[data-tip]"); if (el) { const r = el.getBoundingClientRect(); showTip(el, r.right, r.top); } });
document.addEventListener("focusout", (e) => { if (e.target.closest?.("[data-tip]")) tip.hidden = true; });

// On narrow screens the rail sticks to the top; pinned banners sit just below it.
const rail = document.querySelector(".rail");
new ResizeObserver(() => document.documentElement.style.setProperty("--rail-h", `${rail.offsetHeight}px`)).observe(rail);

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
  catch (e) { main.innerHTML = `${head("Cannot reach the lab server", `Start it with <code>PYTHONPATH=. python -m dashboard.server</code>, then reload this page. (${esc(e.message)})`)}`; live.classList.add("stale"); return; }
  setInterval(async () => {
    try {
      const changed = await load();
      live.classList.remove("stale", "tick"); void live.offsetWidth; live.classList.add("tick");
      const { name } = parse();
      if (changed && !["negotiation", "try", "live", "result", "compare"].includes(name)) { const y = scrollY; await render(false); scrollTo(0, y); }
    } catch { live.classList.add("stale"); }
  }, 10000);
}
boot();
