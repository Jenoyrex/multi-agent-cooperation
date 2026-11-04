# Sandbox protocol `sandbox_v1`

`sandbox_v1` is the frozen alternating-offers protocol (docs/spec.md §3) plus **resource events**. It belongs to the
sandbox only. The research study never uses it, and its results are never the study's results.

An experiment without events runs the frozen `NegotiationSession` unchanged. An experiment with events runs
`sandbox/engine.py`, which reuses the study's validation, utility and optimum code. A test checks both engines produce
the same record when there are no events.

## What stays the same

- Turns alternate. On a turn an agent makes an OFFER, an ACCEPT of the other agent's standing offer, or a WALK_AWAY.
- An offer must split every unit of every category. The final turn can only accept or walk away; an offer there times out.
- Malformed output, an infeasible allocation or an illegal accept ends the negotiation as `invalid_action`. Nothing is repaired.
- A connection failure aborts that negotiation. An aborted negotiation is not an outcome.
- Each agent sees the public pool, its own values, the public transcript and the round. It never sees the other agent's values.

## Resource events

An event is `{turn, category, units, max_points, extend_rounds}`. At most 3 per experiment, each on its own turn.
`turn` runs from 2 to one before the round limit of the shortest resource set in the experiment.

At the **start of the chosen turn**, before that turn's agent is asked:

1. **The pool grows.** `units` of `category` are added.
2. **Each agent gets private values.**
   - A new category: each agent draws its own whole number of points from 0 to `max_points`, seeded by
     `sandbox_v1|seed|turn|category|seat`. The per-unit value is points / units.
   - An existing category: each new unit keeps the per-unit value that category already had, for both agents.
3. **Any standing offer becomes void.** It no longer covers the full pool, so accepting it is an `illegal_accept`.
   It stays in the record for analysis, and leaves the transcript that agents see from then on.
4. **Both agents are told.** Their view gains the notice "New resource added at the start of round N: ...". The notice
   gives no values; each agent reads its own value from its own valuation. HTTP agents receive `notices` in the
   observation, and Python agents as `view.notices`.
5. **The deadline stays the same** unless `extend_rounds` moves it back.

## Scoring

- **W\* (the best possible joint deal) is computed on the pool at the end of the negotiation.** With a new category
  that pool has more value in it, so an agent can now earn more than 100 points. Value-created measures use this W\*.
- The analysis also reports the **pre-event W\*** for context.
- A negotiation that ends before its event never sees the event. Its pool and W\* stay as they started, and it is
  excluded from the reaction analysis, with the count shown.
- Each fired event records the turn, the values drawn, the voided offer, the pool after it and W\* after it.

## Determinism

The same seed and design give the same pool, the same event values and the same W\*. Deterministic agents then replay
exactly. Models and HTTP programs may not.

## Sweeps

A sweep runs the same agents on several resource sets, and optionally several personas, with the same seeds for each.
A comparison between resource sets is therefore paired by seed. Note that with the research generator a seed draws a
new instance for each number of categories, so pairing removes less noise there than with fixed resource sets.
