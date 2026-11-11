# Run checklist (research study)

For whoever runs the paid pilot or full run. It uses **your own API keys and you pay**. Nothing paid happens until
step 7.

## Before

1. **Get API keys** for Anthropic and OpenAI from their developer consoles (docs/references.md). A chat subscription is not
   an API key. Set up billing, and a spending limit in each console if you want one.
2. **Clean tree.** `git status` shows nothing to commit, so the run records a committed `code_version`.
3. **Create `.env`** from `.env.example`. Fill in `ANTHROPIC_API_KEY` and `OPENAI_API_KEY`. Do not change the generation
   settings: the run refuses settings that differ from the approved ones. `.env` is gitignored; check with
   `git check-ignore .env`.
4. **Tests pass.** `py -m pytest -q` (no keys needed, no network).

Commands below are for Git Bash, macOS or Linux. In PowerShell, set variables first instead: `$env:PYTHONPATH="."`
(and in step 6 `$env:ALLOW_PAID_PROVIDER_CALLS="1"`), then run `py scripts/run_pilot.py ...`.

## Dry run (free)

5. `PYTHONPATH=. py scripts/run_pilot.py --max-input-tokens-per-call 20000 --max-cost-usd 20`
   It prints the 24-negotiation plan, the budget and the worst case, then "Validation only". **No API call is made.**
   Check that the models, seeds and caps are what you expect.

## Paid run (needs the team's and guide's go-ahead)

6. Allow paid calls for this shell only: `set ALLOW_PAID_PROVIDER_CALLS=1` (Windows) or
   `export ALLOW_PAID_PROVIDER_CALLS=1` (macOS/Linux).
7. `PYTHONPATH=. py scripts/run_pilot.py --max-input-tokens-per-call 20000 --max-cost-usd 20 --confirm`
8. If it stops (network, cap, Ctrl-C): rerun the same command with `--resume`. The spend already charged carries over.

## After

9. Open the dashboard (`py -m dashboard.server`) → Research study → Runs: status, spend, aborted attempts.
10. Pilot values are an operational check only, never findings (docs/spec.md).
11. Full run: `--full`, same steps, with a cap chosen from the pilot's real cost (docs/budget-plan.md). The preregistered
    analysis runs only on the complete 480: `PYTHONPATH=. py scripts/run_analysis.py --db results/full.db`.
12. Close the shell, or `unset ALLOW_PAID_PROVIDER_CALLS`.
