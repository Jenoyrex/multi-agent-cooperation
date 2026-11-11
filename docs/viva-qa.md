# Viva questions and honest answers

Short answers you can defend from the code and docs/spec.md. Where we don't know, we say so.

1. **What did you build?** Two things. Cooperation Lab, an open sandbox you install and run on your own computer, where two
   agents (rule-based, local models, API models with your own key, HTTP services or Python files) negotiate over resources,
   with analysis of how they behave. And a preregistered study design comparing two instruction strategies with one Claude
   and one GPT model.

2. **What are your results?** For the study: none. It has not been run, so H1–H3 are untested. Everything in the repository
   comes from rule-based simulations, test fixtures or a fake model server, and each is labelled as such.

3. **Why wasn't the study run?** It costs real money through paid API keys, and the project ran under a $0 policy. The
   harness, caps and analysis are built and tested offline, so the paid run is a decision, not missing work.

4. **How much would it cost?** We don't know the real figure. The dry run gives worst-case ceilings ($59.30 for the
   24-negotiation pilot, $1,186.10 for the 480-negotiation full run, at the study's approved rates), and real use should be
   far lower. A hard `--max-cost-usd` cap stops it first. See docs/budget-plan.md.

5. **Can a ChatGPT Plus or Claude Pro subscription run it?** No. A chat subscription does not include an API key. API use is
   set up and billed separately in each provider's developer console.

6. **How do you stop an agent from cheating by seeing the other side's values?** Agents only ever receive an `AgentView`
   built by the engine, which has the public pool, their own values and the transcript, never the opponent's values. Tests
   check that the opponent's values appear in no request, HTTP body or Python view.

7. **Why 480 negotiations?** 60 seeds × 8 cells (2 seatings × 2 first movers × 2 conditions), so every comparison is
   paired and balanced for seat and first-mover effects (docs/spec.md §8).

8. **How do you measure "cooperation"?** Against a computed optimum, never self-reported. The tested measures (H1–H3) are
   relative welfare efficiency, egalitarian welfare and agreement rate, with failures scoring 0. Equitability, envy,
   first-mover effects and outcome rates are descriptive only (docs/spec.md §5 and §8.6).

9. **Which statistics, and why?** A two-sided sign-flip permutation test on paired differences, a 95% bootstrap interval,
   a Wilcoxon signed-rank sensitivity check, and Holm correction across the three hypotheses. They were fixed before any
   data existed, and the analysis refuses to run on anything but the complete full run.

10. **Is the sandbox analysis as strong as the study's?** No. It is exploratory and says so on every page: small samples
    are flagged, intervals are shown with n, and the plain-language findings are generated from the numbers and never
    say why an agent acted.

11. **How do you keep users' API keys safe?** Keys stay in memory, or in the user's own `.env` if they choose (refused if
    git could commit it). They are never written to the database, exports, reports or logs, and the browser only sees
    `sk-…abcd`. Key routes answer only 127.0.0.1, same-origin, and do not exist in the public demo. A test searches the
    database bytes, every export, the HTTP responses and the logs for a dummy key.

12. **Can a user overspend by mistake?** A run with a key needs a hard token cap and an explicit "Run (uses your API key,
    you pay)" consent for that exact design. The cap is checked before every call, keeping room for that call's worst
    case, so the run stops before the cap. Prices come only from the user.

13. **Is a Python-file agent safe?** Not fully. It runs in its own process with no API keys and a time limit per turn, but
    with the user's permissions: it could read their files. The app warns about this and asks for consent. The public demo
    never runs code.

14. **Did you test with real models?** No paid model was called. Every provider path was tested against a fake
    OpenAI-compatible server and stub clients. One real local model (Ollama, `llama3.2`) passed "Test connection" on the
    developer's computer.

15. **What are the main limitations?** Two agents per negotiation. Model behaviour is not deterministic, so a sandbox run may
    not repeat exactly. Structured output depends on the model, and invalid replies are recorded, never repaired. The
    install was tested on Windows only. And the study has no results until someone pays for and runs it.
