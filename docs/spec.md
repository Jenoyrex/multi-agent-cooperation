# Formal Specification — Resource-Split Negotiation Game

Status: **Phase 0 — approved design, not yet experimentally validated.**
No experiments have been run at the time of writing. Nothing in this document
is a claimed result; it is a specification the implementation must satisfy.

---

## 1. Environment

### 1.1 Resource pool

- The pool consists of `K` resource **categories** (MVP default `K = 3`,
  configurable).
- Each category `c` has an integer quantity `q_c`, drawn uniformly from
  `[MIN_QTY, MAX_QTY]` (MVP default `[10, 50]`), generated from a seeded RNG.
- Units within a category are **indivisible but fungible** (you can't split a
  single unit, but the category as a whole is "divisible" across the two
  agents in integer amounts — e.g. 17 of 30 widgets to Agent A, 13 to Agent
  B). This avoids continuous-allocation edge cases while still giving a rich
  action space.

### 1.2 Allocation

An allocation is a pair `(a_A, a_B)` where each is a mapping
`category -> non-negative integer units`, such that for every category `c`:

```
a_A[c] + a_B[c] == q_c
```

Full allocation of every category is required for an allocation to be valid
— no leftover units. This is enforced by the environment, not trusted from
agent output (see §6, Testing).

### 1.3 Divisibility choice — explicitly flagged

We are using **linear, separable, per-unit valuations** (§2) rather than
diminishing-marginal-value utilities. This is a deliberate MVP simplification
to keep the ground-truth optimum a closed-form computation (§4) instead of
requiring a convex-optimization solver. It has one consequence worth stating
up front rather than discovering after results come in:

> With linear per-unit valuations, the **welfare-maximizing** allocation is
> always "give 100% of each category to whichever agent values it more"
> (ties broken arbitrarily). This means the utilitarian optimum can be
> maximally *unequal* — one agent could get most of the total value even at
> 100% efficiency. Efficiency and fairness are therefore expected to be in
> tension by construction, not by negotiation failure. This is a known
> property of linear fair-division setups and is documented here as a
> limitation/design choice, not treated as a bug if observed later.

If this turns out to make results uninteresting in practice (e.g. every
negotiation trivially converges to winner-take-all), the documented Phase-3
extension is to switch to concave (diminishing-returns) utilities, which
requires a proper optimizer for the ground truth instead of the closed-form
solution. **Not built in Phase 1.**

---

## 2. Agent preferences (private valuations)

- Each agent `i` receives a private **valuation vector** `v_i: category ->
  value_per_unit`.
- Generation: allocate a fixed budget of `TOTAL_POINTS = 100` "importance
  points" across the `K` categories via a Dirichlet draw (concentration
  `alpha = 1.0` by default, i.e. uniform over the simplex), seeded
  deterministically from `(instance_seed, agent_role)` so Agent A and Agent B
  get **different, independent** draws even under the same instance seed.
  Per-unit value for category `c` is then `points_c / q_c`.
- This guarantees `sum_c(v_i[c] * q_c) == 100` for every agent — i.e. every
  agent's *maximum possible utility* (getting 100% of everything) is exactly
  100 points. This fixed ceiling is what makes utilities comparable across
  agents and across negotiation instances (used directly in the fairness
  metric, §5.3).
- Reproducibility: given the same `(instance_seed, agent_role, K, quantities)`
  the valuation vector is bit-identical. Changing the seed changes the draw.
- **Agents never see the opponent's valuation vector.** This is enforced at
  the context-construction layer (§3.4), not by asking the model nicely —
  see the privacy-isolation tests in §6.

---

## 3. Negotiation protocol

### 3.1 Structure

- **Alternating offers**, following the standard bargaining-game convention
  (Rubinstein-style, discrete rounds instead of continuous discounting).
- Who moves first is set by `first_mover_policy` in the experiment config
  (`A`, `B`, or `alternate`), **independently of the instance seed** and of
  resource/valuation generation. The policy and the resulting `first_mover`
  are stored with every record. `alternate` is deterministic by negotiation
  index within a batch (index 0 → A, 1 → B, 2 → A, ...); it restarts at index
  0 for every batch, so odd batch sizes are A-heavy (n=3 → 2 A-first, 1
  B-first). Role-balanced experimental design is handled separately.
- Turns strictly alternate. `MAX_ROUNDS` is fixed per experiment (MVP
  default `10`). One round = one agent's turn. **The final turn (turn
  `MAX_ROUNDS`) is the deadline.**

### 3.2 Offer format

Each turn, the acting agent returns one structured action (see §3.5 schema):

| action_type | meaning |
|---|---|
| `OFFER` | propose an allocation for both agents |
| `ACCEPT` | accept the most recent offer on the table as final |
| `WALK_AWAY` | explicitly end the negotiation with no agreement |

- `ACCEPT` requires a valid standing offer made by the **opponent**. With no
  standing offer (e.g. on the very first turn) it is an invalid action (§6).
- An `OFFER` must be a valid full allocation per §1.2; the environment
  re-validates every offer regardless of what the model claims.
- On the final turn only `ACCEPT` and `WALK_AWAY` can end in a meaningful
  result: an `OFFER` there could never be answered, so it is treated as
  failure to reach agreement before the deadline (§3.3, `timeout`).

### 3.3 Termination

Negotiation ends when any of the following happens first. There are four
outcomes: `agreed`, `walked_away`, `timeout`, `invalid_action`.
1. An agent returns `ACCEPT` with a valid standing opponent offer →
   `agreed`, final allocation = the offer being accepted.
2. An agent returns `WALK_AWAY` → `walked_away`.
3. An agent returns a well-formed, feasible `OFFER` on the final turn →
   `timeout` (the deadline passed without agreement). `num_rounds =
   MAX_ROUNDS`; the offer is recorded in the transcript but is not standing
   and yields no allocation.
4. An agent's output is malformed, or it returns `ACCEPT` with no valid
   standing opponent offer, or its `OFFER` is not a valid allocation →
   `invalid_action`, with a machine-readable `invalid_reason`:
   `malformed_output`, `illegal_accept`, or `invalid_allocation`. There are
   no automatic retries. `invalid_action` is reserved for protocol/output
   faults; failing to agree in time is `timeout`, never `invalid_action`.

Every final turn therefore terminates the negotiation, and no valid offer is
ever left unanswerable.

On any non-agreed outcome, no allocation exists. The stored per-agent
utilities are `0` (disagreement point) but are **not** used by any
allocation-dependent metric (§5).

### 3.4 Public vs. private information

What an agent's context **includes**:
- The resource pool (categories + quantities) — public.
- Its own private valuation vector.
- The full negotiation transcript so far (all offers, in both directions,
  and any free-text message attached to them).
- The round number and rounds remaining.

What an agent's context **must never include**:
- The opponent's valuation vector.
- The evaluator's precomputed optimal allocation/welfare.
- Any other negotiation instance's data.
- Hidden metric values (fairness score, exploitability, etc.) mid-negotiation.

This separation is structural: the environment builds two distinct context
objects (`AgentView_A`, `AgentView_B`) from one `EvaluatorState` object that
holds everything; the agent-facing code path has no reference to the
evaluator-only fields at all (not just "doesn't send them" — the objects
don't carry the fields into that code path). See §6 for the isolation test.

### 3.5 Action schema (structured output)

```
NegotiationAction:
  action_type: "OFFER" | "ACCEPT" | "WALK_AWAY"
  allocation:  { category: int, ... } | null   # required iff action_type == OFFER
  message:     string | null                    # short externally-visible rationale, optional
```

We do **not** store or request hidden chain-of-thought — only this
structured action plus the visible `message` field (which is the agent's
externally-shown negotiation statement, not an internal scratchpad).

**Allocation parsing (no repair).** Real-model agents report an offer as
"my units per category"; the other side is computed as `pool - mine`, which
is arithmetic only. Nothing is clamped, truncated, defaulted or dropped: a
missing category, extra category, negative or over-large quantity, or a null
allocation is rejected by the environment's allocation validator
(`invalid_action` / `invalid_allocation`), and a non-integer quantity is
rejected at parse time with the same reason. A malformed output is never
turned into a different valid action. Truncated, refused, empty, or
non-JSON output is `invalid_action` / `malformed_output`.

### 3.6 Infrastructure failures (not outcomes)

API/transport failures (connection error, timeout, rate limit or 5xx after
retries, auth or bad-request errors) and run-budget stops are **not
strategic actions and not outcomes**. The session is aborted, no outcome is
assigned, and the failure is stored separately (`aborted_negotiations`) with
the partial transcript and call log. A negotiation is never resumed
mid-session; if `max_transport_reruns > 0` the whole negotiation is re-run
from turn 1. A negotiation whose reruns are exhausted has no row in
`negotiations`, and its run is marked `incomplete`. Retries happen inside a
single turn and never add a strategic turn; each retry is a fresh model
call, and tokens of discarded attempts are not observable.

### 3.7 Run provenance and usage

Every run has a unique `run_id` (table `runs`) storing the complete
experiment configuration (including `base_seed`, number of negotiations,
`max_rounds`, `num_categories`, environment parameters, first-mover policy,
agent/generation settings, prices and budget), the protocol id, spec
fingerprint, prompt hash, code version (git commit) and dependency
versions. Each negotiation references `(run_id, negotiation_index)`, which
is unique. Per real-model turn the raw model output, tokens, latency and
attempt count are stored; they are aggregated per negotiation and per run.
Generation settings (model id, temperature, max output tokens, API
timeout, max retries) are explicit configuration with no provider
defaults. Pilot/full runs require a hard token and/or dollar budget,
checked before every call.

---

## 4. Ground-truth optimum

Because valuations are linear and separable across categories (§1.3), the
social-welfare-maximizing allocation has a closed form and is computed
**independently of any agent**, directly from the two hidden valuation
vectors:

```
for each category c:
    if v_A[c] >= v_B[c]: give all q_c units to A
    else:                give all q_c units to B
optimal_welfare = sum_c( q_c * max(v_A[c], v_B[c]) )
```

This is computed once per negotiation instance at setup time, stored by the
evaluator, and never shown to either agent.

---

## 5. Metrics

**Allocation-dependent metrics** (SW, SWE, equitability, envy-freeness,
imbalance) are defined **only for `agreed` negotiations** with a valid final
allocation. For `walked_away`, `timeout`, and `invalid_action` they are
undefined (`None`), not `0`, and batch means are taken over agreed
negotiations only. Outcome rates (agreement, walk-away, timeout,
invalid-action) are reported separately over **all** negotiations.

All metrics are computed by the evaluator from data agents already exposed
during the game (their own accepted allocation) plus the hidden valuations
(which agents never see) — never by asking an agent to self-report its score.

### 5.1 Social Welfare (SW)
```
SW = utility_A(final_allocation) + utility_B(final_allocation)
```
Undefined if the negotiation did not end in agreement.

### 5.2 Social Welfare Efficiency (SWE)
```
SWE = SW / optimal_welfare
```
Bounded in `[0, 1]` for any valid allocation (optimal_welfare is the max
achievable by construction). Undefined if the negotiation did not end in
agreement.

### 5.3 Fairness — Equitability
Using the fixed 100-point ceiling from §2:
```
norm_utility_i = utility_i / 100
equitability = 1 - | norm_utility_A - norm_utility_B |
```
`1.0` = both agents realized the same fraction of their personal maximum
possible value; `0.0` = maximally unequal. This is a standard fair-division
concept ("equitability") and is well-defined even though the two agents'
raw utility scales differ, because both were normalized against the same
100-point ceiling at generation time (§2).

### 5.4 Fairness — Envy-freeness (diagnostic, secondary)
For the final allocation, agent `i` is **envious** if:
```
v_i(other's bundle) > v_i(own bundle)
```
Reported as a boolean per agent, and as an aggregate "% of negotiations that
were envy-free for both agents" across a batch. This is a classic
fair-division check computable only by the evaluator (needs both valuations
against one allocation).

### 5.5 Process efficiency
- **Rounds to agreement**: count of turns until termination.
- **Agreement rate**: fraction of a batch that ends in `ACCEPT`.
- **Walk-away rate**, **timeout rate**, **invalid-action rate**: fraction of
  all negotiations with each outcome. **Failure-to-converge rate** =
  walk-away + timeout (invalid actions are protocol faults, reported
  separately).

### 5.6 Exploitability / Imbalance
Across a batch of negotiations with the same method but varying who-moves-first
and model pairing:
```
imbalance = mean( norm_utility_modelX - norm_utility_modelY )
```
Sign indicates which model tends to extract more value under that method.
Requires role-swapped trials (X first vs. Y first) before this is
interpretable — a single-direction batch cannot separate "model skill" from
"first-mover advantage." **This is a pilot-batch design requirement, not
optional.**

### 5.7 Recorded per negotiation (full record)
`final_allocation, utility_A, utility_B, total_welfare, optimal_welfare,
swe, equitability, envy_free_A, envy_free_B, num_rounds, outcome
(agreed/walked_away/timeout/invalid_action), invalid_reason,
first_mover_policy, method, model_A, model_B, first_mover,
random_seed, timestamp, full_public_transcript, structured_actions`.

---

## 6. Testing requirements (implemented in `tests/`)

- Resource/valuation generation is deterministic given a seed.
- No valid allocation can exceed available quantity per category (over-alloc
  is rejected before it ever reaches storage).
- Utility calculation matches a hand-computed example.
- Optimal welfare calculation matches a hand-computed example, including a
  tie case.
- Equitability and envy-freeness match hand-computed examples.
- Negotiation always terminates within `MAX_ROUNDS`, in all four outcome
  modes; a non-final OFFER can be answered; final-turn OFFER → `timeout`,
  final-turn ACCEPT → `agreed`, final-turn WALK_AWAY → `walked_away`.
- Malformed / schema-invalid agent output, an illegal ACCEPT, and an invalid
  allocation are each rejected safely (do not crash the session): the
  negotiation ends as `invalid_action` with the matching `invalid_reason`.
- `alternate` first-mover assignment maps index 0,1,2,3 → A,B,A,B, and
  changing `first_mover_policy` never changes the resource pool or either
  valuation.
- Stub-client tests for both real agents cover valid, malformed, refused/
  empty, truncated, missing/invalid allocation, retry and transport-failure
  behavior without any API access; bad allocations are never repaired.
- **Privacy isolation**: a direct test that constructs `AgentView_A` and
  asserts the opponent's valuation is not reachable from it by any
  attribute/key path — not just "not printed."

---

## 7. Explicitly out of scope for Phase 0/1

- Concave/diminishing-returns utilities (§1.3 note).
- More than 2 agents (protocol and metrics above are 2-agent only for now).
- Any dashboard/UI.
- Fine-tuning or RL training of either agent — Phase 1 is prompting-only
  (baseline method). Structured protocol interventions come in Phase 2, per
  the approved plan, and are not built yet.
- Precise billing: token/cost accounting (§3.7) records what providers
  report per call and cannot see tokens from discarded retry attempts or
  failed calls; it is not a billing system.
