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
| Frozen code | `src/experiments/simulation.py` has its own CSV writer with `\n` | Only numbers and fixed labels, never user text: not affected |

## Blocked

None so far.
