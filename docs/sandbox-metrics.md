# Sandbox behaviour metrics

Every metric is computed from saved transcripts and each agent's private values, after the negotiation. Nothing is
inferred about motives. All of it is exploratory: it describes these agents on these resources in these runs, and is
never the research study's result. The code is `sandbox/analysis.py`.

## How numbers are summarised

- **n counts repetitions, not negotiations.** Negotiations that share a seed (the seat and opener swaps of one repetition)
  are not independent, so each repetition is averaged first. The negotiation count is shown too.
- **95% confidence intervals** are percentile bootstrap intervals over those repetition averages, using the function in
  `src/evaluation/inference.py` read-only, with 2,000 resamples.
- With one repetition there is **no interval**. One is never faked. Intervals appear when the experiment has finished.
- **Fewer than 10 repetitions** in a group gets a small-sample warning.
- **Not observed stays not observed.** A metric with no data is empty, never 0, and its n excludes those negotiations.
- **Fixed rules on a fixed resource set replay identically.** More repetitions then add no information and the interval
  collapses. The analysis says so; use the research generator to vary the situation.
- **Aborted, cancelled or capped negotiations are not outcomes** and are left out of every number, with their count shown.

## Per agent

| Metric | Plain-language definition |
|---|---|
| Opening demand | Value of the agent's first offer to itself, as a share of the most it could get from the pool at that moment. |
| Concession speed | How much its demand falls per offer: (first offer's share of its maximum minus last offer's) / (offers minus 1). Negative means it asked for more. Needs two or more offers. |
| Share of value | In agreed deals, the agent's points as a share of both agents' points together. 50% is an equal split of points. |
| Fairness of its offers | For each offer it made, 1 minus the gap between what each agent would get (each as a share of its own maximum), over their sum. 100% means both would get the same share. |
| Walks away | Share of negotiations the agent ended by walking away. |
| Breaks the rules | Share of negotiations the agent ended with an invalid action: malformed output, an infeasible offer or an illegal accept. |
| Turns to agreement | Turns taken in negotiations that reached a deal, counted across both agents. |
| Reveals preferences | Share of its messages that name a resource alongside a priority word or its own exact points. A text pattern only: it can miss paraphrases and cannot tell truth from bluff. |
| Demand change after an event | First offer after a resource event minus last offer before it, each as a share of the agent's maximum on the pool at that moment. Only negotiations where the agent made both offers. |

## Per experiment or group

| Metric | Plain-language definition |
|---|---|
| Deals reached | Share of negotiations ending in a deal. |
| Value created (RWE) | Both agents' points over the best possible joint total (W\*), with failed negotiations scored 0. |
| Fairness (EW) | The worse-off agent's points as a share of its maximum, with failed negotiations scored 0. |

## Comparisons

A sweep compares each group with the first one, **paired by repetition**: the same seed in each group. The difference is
averaged per repetition and given with a 95% bootstrap interval and n. If the interval includes 0, the findings say these
runs cannot tell the two apart. If it excludes 0, they say the rates differ in these runs, not that the difference is general.

## Reaction to a resource event

For negotiations that reached the event, and for each agent: demand before, demand after, the change, how much of the new
resource it claimed, and what it did first. The best possible joint total (W\*) is shown before and after. Negotiations
that ended before the event are counted and left out. See docs/sandbox-protocol.md.

## Usage and cost

Calls, attempts and tokens are what the provider reported. If any completed call reports no usage, the total is unknown
rather than partial. Tokens from discarded retries cannot be seen, so real usage may be higher. Cost uses only the prices
you entered, and without prices it stays unknown. Your provider's bill is the real figure.

**Per agent.** Each negotiation records calls and tokens per agent (`usage_by_agent`, from the per-call log), and the
analysis sums them per agent. Rule-based, HTTP and Python agents make no model calls, so they show 0 calls. Experiments
saved before this field existed show no per-agent table.

**Live, against the cap.** While a capped run is going, the page shows the tokens the capped models have used so far
(`spend.tokens`) against the cap, updated after every call. When the run has an API model, only API models count against
the cap; local models are free and count only in a run without any API model.

## Checking the numbers

`metrics.csv` holds every metric per negotiation and agent. Average per repetition, then across repetitions, and you get
the charted value. `tests/test_sandbox_analysis.py` does exactly that.
