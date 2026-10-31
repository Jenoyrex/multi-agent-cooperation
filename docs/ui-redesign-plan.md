# UI redesign plan: simple first, nothing lost

Branch `ui-simplify`. Scope: `dashboard/static/*` only. Frozen research code, the server API, demo limits
and the zero-cost guard are not touched. Every route that exists today keeps its hash, so old links keep
working; the redesign changes the navigation, the pages people land on, and what is shown by default.

## a) Feature inventory (before)

| ID | Feature | Route / place today |
|---|---|---|
| F01 | Start page: 5-step journey, sample-experiment button, recent experiments, "what runs here" | `#/` |
| F02 | One-click sample experiment (24 negotiations, built-in agents) | `#/` "Run a sample experiment" |
| F03 | Agent library (built-in, locked providers, uploads, local HTTP) | `#/agents` |
| F04 | Create / edit / duplicate an agent | `#/agents/new`, `#/agents/<id>` |
| F05 | Upload a Python agent: inspected with `ast`, never executed; inspection results | `#/agents/new` (upload) |
| F06 | Local HTTP agents (off in the public demo) | `#/agents/new` |
| F07 | Starter template download (`templates/my_custom_agent.py`) | `#/agents/new` |
| F08 | Resource sets: list, built-in templates, read-only view, duplicate | `#/environments`, `#/environments/<id>` |
| F09 | Resource builder: units, categories, sliders, balance to 100, butterfly, server preview, text/JSON import | `#/environments/new` |
| F10 | Experiment list with progress and status | `#/simulations` |
| F11 | Five-step experiment wizard (agents, resources, preferences, settings, review) | `#/simulations/new/<step>` |
| F12 | Live run: progress, live turns, "thinking", outcome strip with legend | `#/simulation/<id>` Live/Summary tab |
| F13 | Stop a running experiment | `#/simulation/<id>` |
| F14 | Per-experiment analytics: outcomes, agents, behaviour, statistics, findings; filters, hover, click-through | `#/simulation/<id>` Analytics tab |
| F15 | Negotiation list and transcripts | `#/simulation/<id>` Negotiations tab |
| F16 | Engine event log | `#/simulation/<id>` Log tab |
| F17 | Exports CSV / JSON / Markdown | `#/simulation/<id>` Export tab |
| F18 | Cross-experiment analytics | `#/analytics` |
| F19 | Compare two experiments (exploratory) | `#/analytics/compare` |
| F20 | Reports list | `#/reports` |
| F21 | Printable report (save as PDF) | `#/report/<id>` |
| F22 | Settings: theme, motion, cost and provider status | `#/settings` |
| F23 | Theme toggle | rail footer |
| F24 | Try a single negotiation (5-step sandbox) | `#/try/<step>` |
| F25 | Single negotiation live view and result | `#/live/<id>`, `#/result/<id>` |
| F26 | Compare two single negotiations | `#/compare` |
| F27 | Agent Lab: balanced evaluation, probe, report | `#/lab`, `#/lab/<id>` |
| F28 | Research overview (hero, study flow) | `#/study` |
| F29 | Worked example negotiation (illustrative) | `#/example` |
| F30 | Data collection: runs, progress, negotiation table, filters | `#/experiments` |
| F31 | Research negotiation detail / replay | `#/negotiation/<id>` |
| F32 | Research results (pilot / full, locked until 480) | `#/results` |
| F33 | Structured vs baseline comparison | `#/structured` |
| F34 | Methodology and glossary | `#/methodology` |
| F35 | Frozen configuration | `#/configuration` |
| F36 | Public-demo banner, SIMULATED data labels, create-only, rate limits (server-enforced) | every page / server |
| F37 | "Study data live" status and research progress count | rail |
| F38 | Not-found page | any unknown hash |

## b) What makes it hard to use today

- **Navigation:** 15 links in three groups plus Settings. A first-time visitor can't tell which of them
  matters. "Experiments" (Workbench) and "Data collection" (research) sound alike, and so do "Compare
  runs" and Analytics → "Compare two".
- **Start:** it shows a five-step journey, a sample box, recent experiments, a capabilities list and a study
  link all at once. Two primary-looking buttons ("Add an agent" and "Run a sample experiment") compete.
- **Experiment page:** seven equal tiles before the outcome strip, and the three key results (value,
  fairness, deals) aren't singled out. The Analytics tab shows seven charts at once in "Outcomes".
- **Cross-experiment analytics:** every chart is shown at once.
- **Jargon:** "Value captured" and "Worse-off share" are used without saying they are RWE and EW.
- **No Help page:** the glossary is buried on the Methodology page.

## c) New information architecture

Top-level navigation (6 items); Settings and the theme toggle sit in the rail footer.

| Nav item | Route | One primary action | Holds |
|---|---|---|---|
| Home | `#/` | Run a sample experiment | 1-sentence intro, 3-step "How it works", recent experiments (F01, F02) |
| New experiment | `#/simulations/new` | Run (with defaults) | wizard F11; secondary links to F24 Try a single negotiation and F27 Agent Lab |
| My experiments | `#/simulations` | New experiment | sub-tabs: Experiments F10 · Analytics F18 · Compare F19 · Reports F20; experiment page F12–F17; report F21; single-negotiation pages F25, F26 |
| Agents & resources | `#/agents` | Add an agent / Build a resource set | sub-tabs: Agents F03–F07 · Resource sets F08–F09 |
| Research study | `#/study` | Read the method | sub-tabs: Overview F28 · Data collection F30 · Results F32 · Structured vs baseline F33 · Methodology F34 · Configuration F35; F29, F31 inside |
| Help | `#/help` (new) | Run a sample experiment | what the app does, how it works, glossary, what runs here, links to Settings F22 and the playground tools |

The experiment page changes too: the top shows the status and three key numbers in plain words with the
technical term and a tooltip ("Deals reached (agreement rate)", "Value created (RWE)", "Fairness for the
worse-off side (EW)"). The middle has the outcome strip with its legend, plus the live transcript while the
run is going. Analytics shows 4 charts by default, and the rest sit under "More charts".

## d) Route mapping

No hash is renamed, so every old URL opens the same page as before. Only the highlighted nav item changes.

| Old route | Opens | Nav item highlighted |
|---|---|---|
| `#/` | Home (simplified) | Home |
| `#/simulations/new/*` | wizard | New experiment |
| `#/simulations`, `#/simulation/*`, `#/analytics`, `#/analytics/compare`, `#/reports`, `#/report/*` | unchanged | My experiments |
| `#/try/*`, `#/live/*`, `#/result/*`, `#/compare`, `#/lab`, `#/lab/*` | unchanged | New experiment (try, lab) / My experiments (live, result, compare) |
| `#/agents/*`, `#/environments/*` | unchanged | Agents & resources |
| `#/study`, `#/example`, `#/experiments`, `#/negotiation/*`, `#/results`, `#/structured`, `#/methodology`, `#/configuration` | unchanged | Research study |
| `#/settings` | unchanged | (footer gear) |
| `#/workbench` | redirects to `#/` (already) | Home |
| `#/help` | new | Help |

## e) Acceptance checklist

- [ ] Every feature F01–F38 reachable (table below, ticked with where it lives)
- [ ] Home → finished sample experiment → report in ≤3 clicks plus waiting
- [ ] Top-level nav ≤6 items; one obvious primary action per page
- [ ] Key numbers in plain words, with the technical term and a tooltip
- [ ] pytest count ≥ 619, node tests ≥ 20, `node --check` clean, no console errors on any route
- [ ] No horizontal overflow at 390 px; nav and Settings visible at 1280×666
- [ ] Old URLs open the right page
- [ ] Demo mode: SIMULATED labels, create-only, rate limits, zero cost unchanged
- [ ] `git diff --stat main...ui-simplify` touches no frozen research file

## Proposed removals — needs approval

- **Research overview hero video** (`assets/live wally.mp4`, `#/study`). It is decorative, and the file is
  excluded in `.git/info/exclude`, so it never ships. Every clean clone and the Render demo log a 404 on
  `#/study` (the page already falls back to a still background). Kept for now; removing the `<video>` would
  clear the last console error.

## Blockers

None yet.

## Loop notes

**Loop 1: navigation shell.** Rail cut from 15 grouped links to 6 (Home, New experiment, My experiments,
Agents & resources, Research study, Help), with Settings as a footer link. Section sub-tabs appear on list
pages. Home is simplified to one primary button. New Help page. `navOf`/`SECTION` moved to `lib.js` with a
test that every old route maps to a section (it replaces the test of the now-unused `journey`).
Found and fixed a real bug: the CSP hash of the inline theme script was computed over raw bytes, so on a CRLF
checkout (Windows `core.autocrlf`) the browser blocked the script and dark mode never applied. `server.py`
now normalises newlines the way the HTML parser does, and there is a new test. Verified: 22 old routes open
the right page with the right nav item; 6 pages × 3 viewports × 2 themes show no overflow and nav/Settings
are visible at 1280×666. The only console error is the known video 404. pytest 620, node 20.
