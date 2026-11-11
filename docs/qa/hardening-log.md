# Hardening log (v1.0.0 → fix/hardening)

Baseline on main `b750239` (2026-11-11): 757 pytest + 24 node = **781 passing** (Python 3.12.4, Windows). Live demo on
`b750239`, `/healthz` ok. Frozen files (docs/spec.md, src/) are not changed by any fix.

## Bugs

| ID | Area | How to reproduce | Severity | Status | Test |
|---|---|---|---|---|---|
| H-01 | Exports | Name an agent or experiment `<img src=x onerror=…>` or put `\|` in it, export report.md: raw HTML in the Markdown, and the table breaks | Low (Markdown viewers usually sanitise) | Fixed | `test_h01_report_md_never_carries_raw_html_or_breaks_a_table` |
| H-02 | CLI | `COOPERATION_LAB_HOME` points at a file (or a folder you can't write): a Python traceback | Medium | Fixed | `test_h02_unusable_data_folder_is_a_plain_message` |
| H-03 | CLI / Windows | Start the app twice with the same `--port`: the second says "running" too, and both share the port (Windows `SO_REUSEADDR`) | Medium | Fixed | `test_h03_a_taken_port_cannot_be_shared`, `test_h03_an_explicit_taken_port_is_a_plain_message` |
| H-04 | Wizard | Two valid 60-character agent names, no experiment name: "Keep the name under 80 characters" for a name you never typed | Medium | Fixed | `test_h04_automatic_name_is_shortened_not_refused` |
| H-06 | Resources | Text import `resources: food=3, food=4, water=2` silently kept `food=4` | Low | Fixed | `test_h06_a_resource_listed_twice_is_an_error_not_a_silent_overwrite` |
| H-07 | Live view | Fresh-user test: during a run the header said "Started 1 s ago" while the progress line said 30 s | Low | Fixed (refresh every 5 s instead of 30 s) | Browser check (fresh-user test) |
| H-08 | Model errors | Test connection with an unknown model: the hint said "(Refresh models)", but the button is "Find models" | Low | Fixed | `test_h08_error_hints_name_buttons_that_exist` |
| H-09 | Agents | Fresh-user test: after saving an AI-model agent, its page had no way to use it; you had to find New experiment and pick it | Low | Fixed ("Use in an experiment" opens the wizard with it as Agent 1) | Browser check: the wizard opened with the saved agent selected |
| H-10 | Wizard / a11y | Fresh-user test: with a key, Run stays disabled until consent, but nothing near the button said why (and screen readers got no reason) | Low | Fixed (a note tied to the button by `aria-describedby`) | Browser check: note shown while disabled, gone after ticking |
| H-11 | Server / macOS | CI matrix: every macOS job sat in the tests for 20+ minutes. `http.server` binds with a reverse DNS lookup (`socket.getfqdn`) that is slow on macOS, paid by every app start and test server | Medium (slow start on a Mac) | Fixed (bind without the lookup; tests' stdlib servers too) | `test_h11_starting_the_server_does_no_reverse_dns_lookup` + macOS CI jobs |
| H-05 | Exports / Python 3.10 | A text cell holding `\r` was not quoted on Python 3.10, so it could split a CSV row (found by the CI matrix) | Medium | Fixed | `test_h05_a_line_break_inside_a_cell_stays_inside_the_cell`, `test_csv_neutralising_leaves_empty_strings_alone` |

## Checked and fine (no bug)

| Area | What was tried | Result |
|---|---|---|
| XSS | Payload in agent, resource set, experiment, connection names and notes; every route, tab, analysis view, tooltip and the report page | No script ran, no `img[onerror]` in the DOM |
| CSV injection | Agent named `=HYPERLINK(...)` | Neutralised with a leading `'` in every CSV |
| CSRF / DNS rebinding | Every POST route with a cross-origin Origin and a foreign Host; key routes also with no Origin; GETs with a foreign Host | All 403 |
| Path traversal | `..`, `%2e%2e`, `%2f` in static and export paths | Nothing outside `dashboard/static` is served |
| Body limits | 2 MB body; non-JSON body | 413; 400 |
| Validation | Empty/space/5,000-char names; 0, negative, 1e9, 2.5 and "3" repetitions; negative/huge seeds; bad parameter values | Clear field errors |
| Delete in use | Delete an agent a finished experiment used | Allowed; the experiment keeps its snapshot and still loads |
| Public demo | Every agent kind, every model/key route, Agent Lab probe, Try-it with paid agents, upload inspect, with a network tripwire | Nothing runs, nothing reaches the network |
| Two runs at once | Start a second experiment while one runs | 409 "Another simulation is still running…" |
| Server restart | Restart with an experiment running | It loads as "interrupted" (existing store migration) |
| Accuracy | 3 experiments (49 checks): behaviour means and n from metrics.csv, interval contains the mean, agreement rate | No mismatch |
| UI | All routes after a reload: console errors; keyboard Tab through the wizard | None; every control named, focus visible, skip link |
| Robustness | Empty model reply; the model server dropping connections mid-run (new tests); slow, timeout, malformed, 401/403/429/quota/5xx (existing and Loop 2 tests) | Empty reply is `invalid_action`, never repaired; a dropped network stops the run with a plain reason and no fake outcome |
| Resource import | Emoji and Arabic names, CRLF, negative, decimal, zero, huge, NUL, Arabic digits, 26 resources, 200 KB name, rounds 0/100000, first C | Accepted where valid, otherwise a line-numbered message |
| Python agent | `COOPERATION_LAB_KEY_*` and `*_API_KEY` in the environment | Not passed to the child (new test) |
| Frozen code | `src/experiments/simulation.py` has its own CSV writer with `\n` | Only numbers and fixed labels, never user text: not affected |

## Real local model (free): Ollama `llama3.2` (3.2B, Q4_K_M), labelled "Local model: llama3.2:3b"

Exploratory, on this computer only, never study results. Prompt agent vs the rule-based Priority trader, `baseline_v1`.

| Design | Negotiations | Outcomes | Invalid actions | Avg turns | Calls | Tokens |
|---|---|---|---|---|---|---|
| 3 repetitions | 3 | 3 agreed | 0 (0%) | 2.7 | 5 | 3,117 |
| Resource event (turn 2, +6 bonus) | 2 | 2 agreed | 0 (0%) | 3.0 | 4 | 2,589 |
| Sweep (shared lab + field hospital) | 4 | 4 agreed | 0 (0%) | 2.8 | 6 | 3,824 |
| Fresh-user run (wizard, 1 repetition, seats and opener swapped) | 8 | 8 agreed | 0 (0%) | — | — | — |

The model never broke the reply format, so the request was not changed (no before/after to show). Small samples: a first look.

## Fresh-user test

New venv, `pip install git+https://github.com/Jenoyrex/multi-agent-cooperation@fix/hardening`, empty data folder, only in-app
instructions: (a) Run a sample → live view → report; (b) Add an agent → Use an AI model → Ollama found by itself, Test
connection, Save, run 8 negotiations; (c) Model connections → add an OpenAI-compatible provider (fake server) → wrong key
"Your API key was rejected", right key "Connected"; no credit, unknown model, rate limit and 500 each explained; wizard with
estimate, required cap and "Run (uses your API key, you pay)"; live tokens. Confusing points found: H-07, H-08, H-09, H-10.

## Blocked

None.
