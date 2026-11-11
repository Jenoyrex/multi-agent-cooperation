# Budget plan (research study)

The study is paid for with **your own** Anthropic and OpenAI API keys, from `.env`. A chat subscription is not an API key.
Nothing here is a bill: these are worst-case ceilings from the code, and the real cost is what your providers charge.

## Where the numbers come from

`scripts/run_pilot.py` without `--confirm` validates the setup and prints the plan and its worst case. It makes **no API
call** and creates no database. Run on 2026-11-11 with the generation settings from `.env.example` and
`--max-input-tokens-per-call 20000 --max-cost-usd 20`:

| Run | Negotiations | Calls per model (worst case) | Worst-case tokens | Worst-case cost at the approved rates |
|---|---|---|---|---|
| Pilot | 24 (seeds 30000–30002, 8 cells) | 120 | 20,183,040 | $59.30 |
| Full | 480 | 2,400 | 403,660,800 | $1,186.10 |

"Worst case" assumes every negotiation runs to the last round and every call uses the full input ceiling (20,000 tokens)
plus the full output limit, before transport reruns. Real runs use far less: most negotiations end early, and prompts
are much shorter than the ceiling. The worst case is the reason for the caps below.

## Caps that protect you

- `--max-cost-usd` (required): a hard cap, checked before every call. With `--max-cost-usd 20`, the pilot stops before
  $20 of recorded spend even though its worst case is higher.
- `--max-input-tokens-per-call`: no single call may exceed this.
- `--max-total-tokens` (optional).
- `ALLOW_PAID_PROVIDER_CALLS=1` must be set as well, or the zero-cost guard refuses every paid call.
- `--resume` continues the same database with the spend already charged, never a fresh budget.

## Prices

The dollar figures use the study's approved rates in `src/experiments/config.py`: `claude-sonnet-4-6` $3 / $15 and
`gpt-4.1-2025-04-14` $2 / $8 per million input / output tokens. **TO CONFIRM** against the official pricing pages
(docs/references.md) on the day you run: if they differ, the study's spend records will be wrong by that difference.

## What a sensible order looks like

1. Pilot dry run, then the pilot with `--max-cost-usd 20 --confirm` (decision for the team and guide).
2. Look at the pilot's real spend per negotiation in the dashboard (Research study → Runs).
3. Only then decide the full run's cap. A first guess is the pilot's real cost per negotiation × 480, plus a margin.
