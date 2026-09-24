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
- Fine-tuning or RL training of either agent — the experiment is
  prompting-only. The only intervention is the `structured_v1` negotiation
  instruction strategy (§8), which changes instructions, not the protocol.
- Precise billing: token/cost accounting (§3.7) records what providers
  report per call and cannot see tokens from discarded retry attempts or
  failed calls; it is not a billing system.

---

## 8. Preregistration of the experimental design

This section preregisters the approved experimental design. It is written
before any full-run negotiation has been run, and it contains no results
and no expected results. The frozen negotiation protocol (§1–§4) and the
metric definitions below are authoritative. Nothing in this section changes
the protocol.

### 8.1 Research question

When two LLM agents (one Claude model, one GPT model) with private linear
valuations negotiate a split of a shared resource pool under the frozen
alternating-offers protocol, does the `structured_v1` negotiation
instruction strategy change relative welfare efficiency, egalitarian
welfare, or agreement rate compared with the `baseline_v1` instructions?

### 8.2 Experimental conditions

There are exactly two conditions. These are the exact condition labels used
in the implementation (`INSTRUCTION_VARIANTS` in `src/agents/prompting.py`,
stored as the run's `method`, and checked by `check_condition_label` in
`src/experiments/runner.py`):

| Condition label | System instructions |
|---|---|
| `baseline_v1` | exactly `SYSTEM_INSTRUCTIONS`, with nothing appended |
| `structured_v1` | `SYSTEM_INSTRUCTIONS` followed by the fixed `STRUCTURED_V1_BLOCK` |

In any negotiation both agents receive the same condition.

**`structured_v1` is a multi-component negotiation instruction strategy,
not a new negotiation protocol.** Both conditions use the same engine,
turn structure, action set, termination rules, action schema, user-turn
prompt, information given to each agent, and deadline. The only difference
is the fixed instruction block appended to the system instructions.

`structured_v1` has exactly three components, delivered together as one
package:

1. **Preference ranking/revelation**: in its `message`, the agent states
   which categories matter most and least to it (a ranking is enough), and
   asks the other agent for its ranking.
2. **Integrative trade guidance**: the agent builds offers that give the
   other agent more of the categories it values less, in exchange for more
   of the categories it values more.
3. **Disagreement-point/deadline reasoning**: before walking away, or before
   the final round passes without an accepted offer, the agent compares the
   offer on the table with the zero value both agents get if there is no
   agreement.

`structured_v1` has **no fairness instruction**. It never asks agents to
split value equally, fairly, or equitably.

### 8.3 Experimental design (full run)

- **Instances:** 60 generated negotiation instances, with instance seeds
  **40000–40059** (inclusive). An instance fixes the resource pool and both
  private valuations.
- **Resource categories:** K = 3.
- **Valuations:** linear, separable private per-unit valuations, generated
  as described in §2 (100-point ceiling per agent).
- **Within-instance design:** a 2×2 role-balancing design, crossed with
  condition. Every instance is run under every combination of:
  - **Model seat swap:** Claude as Agent A with GPT as Agent B, and GPT as
    Agent A with Claude as Agent B.
  - **First-mover swap:** Agent A moves first, and Agent B moves first.
  - **Condition:** `baseline_v1` and `structured_v1`.
- **Negotiations per instance:** 2 × 2 × 2 = **8**.
- **Total full-run negotiations:** 60 × 8 = **480**.

The design uses one fixed Claude model and one fixed GPT model. Their exact
model ids and generation settings are fixed in §8.15, are recorded in each
run's provenance (§3.7), and are identical in every cell and in both
conditions.

### 8.4 Pilot design

- **Pilot seeds:** 30000, 30001, 30002. These are different from the
  full-run seeds.
- **Cells:** the same 8 cells as the full run (seat × first mover ×
  condition).
- **Negotiations per cell:** 3 (one per pilot seed).
- **Total pilot negotiations:** 8 × 3 = **24**.
- **Pilot batches:** 8, one batch per cell. Each batch runs 3 negotiations
  with a fixed first-mover policy (`A` or `B`). The pilot is split into 8
  batches because pilot mode limits a batch to 20 negotiations.

The pilot is an operational check. Pilot negotiations are not part of the
confirmatory dataset.

### 8.5 Frozen outcome semantics

Each negotiation ends in exactly one of four outcomes (§3.3):

- `agreed`: an agent returns `ACCEPT` while a valid standing offer from the
  **opponent** is on the table. The final allocation is the accepted offer.
- `walked_away`: an agent returns `WALK_AWAY`. `WALK_AWAY` is allowed on
  any turn, including the first and the final turn.
- `timeout`: an agent returns a well-formed, valid `OFFER` on the final
  turn (turn `MAX_ROUNDS`). A final valid OFFER becomes `timeout`: it is
  recorded in the transcript, it is never a standing offer, and it produces
  no allocation.
- `invalid_action`: a protocol or output fault. `invalid_reason` holds
  exactly one of these values:
  - `malformed_output`: the output could not be parsed into an action
    (includes truncated, refused, empty, or non-JSON output).
  - `invalid_allocation`: an `OFFER` whose allocation is missing or not
    feasible. A broken OFFER on the final turn is `invalid_action` /
    `invalid_allocation`, **not** `timeout`.
  - `illegal_accept`: an `ACCEPT` with no valid standing opponent offer.
    This includes an `ACCEPT` on the first turn and an `ACCEPT` of the
    agent's own standing offer.

`ACCEPT` requires a valid standing offer from the opponent. There are no
automatic retries of strategic actions. Allocations are never repaired
(§3.5).

### 8.6 Metric definitions

Notation, for one negotiation:

- `q_c`: quantity of category `c`.
- `v_i[c]`: agent `i`'s private per-unit value. By construction
  `sum_c v_i[c] * q_c = 100` (§2).
- `x_i[c]`: units of `c` given to agent `i` in the final allocation.
- `u_i = sum_c v_i[c] * x_i[c]`: agent `i`'s utility. The 100-point ceiling
  makes `u_i / 100` the normalized utility.
- `agreed`: 1 if the outcome is `agreed`, otherwise 0.

**Social welfare**
```
SW = u_A + u_B                                  (agreed negotiations only)
```

**Theoretical maximum welfare** (§4, computed from both hidden valuations)
```
W* = sum_c q_c * max(v_A[c], v_B[c])
```
`W* >= 100` for every instance, so every ratio below is well-defined.

**Relative welfare efficiency (RWE), unconditional**
```
RWE = SW / W*    if agreed
RWE = 0          otherwise (walked_away, timeout, invalid_action)
```

**Egalitarian welfare, unconditional**
```
EW = min(u_A, u_B) / 100    if agreed
EW = 0                      otherwise
```

**Agreement rate**
```
agreement_rate = (number of agreed negotiations) / (number of negotiations)
```

**Conditional social welfare efficiency** (agreed negotiations only)
```
SWE = SW / W*
```
This is RWE restricted to agreed negotiations (the SWE of §5.2).

**Equitability** (agreed negotiations only, §5.3)
```
equitability = 1 - | u_A/100 - u_B/100 |
```

**Envy** (agreed negotiations only, §5.4). Let `x_A` and `x_B` be the two
final bundles:
```
envious_A = (sum_c v_A[c] * x_B[c]) > (sum_c v_A[c] * x_A[c])
envious_B = (sum_c v_B[c] * x_A[c]) > (sum_c v_B[c] * x_B[c])
envy_free = not envious_A and not envious_B
```

**Model imbalance** (agreed negotiations only, §5.6, reported by model
rather than by seat)
```
imbalance = u_Claude/100 - u_GPT/100
```

**First-mover effect** (agreed negotiations only)
```
first_mover_gap = u_first/100 - u_second/100
```
Outcome rates are also reported separately for A-first and B-first cells.

**Failures score 0 on the unconditional confirmatory metrics.**
`walked_away`, `timeout`, and `invalid_action` all score 0 for RWE and
egalitarian welfare.

**Allocation-dependent metrics** (SW, conditional SWE, equitability, envy,
model imbalance, first-mover gap) **are defined only for `agreed`
negotiations with a valid final allocation.** For every other outcome they
are undefined, not 0.

### 8.7 Metric hierarchy

**Confirmatory** (tested, H1–H3):
1. Relative welfare efficiency (RWE), unconditional.
2. Egalitarian welfare, unconditional.
3. Agreement rate.

**Secondary descriptive** (reported, not tested):
- Equitability, agreement-only.
- Conditional social welfare efficiency, agreement-only.

**Diagnostics** (reported, not tested):
- Envy (per-agent envy and envy-free rate).
- Model imbalance (Claude vs. GPT).
- First-mover effects.
- Outcome rates (walked_away, timeout, invalid_action, with invalid
  actions broken down by `invalid_reason`) and rounds to agreement.

Secondary and diagnostic metrics are reported with the number of agreed
negotiations each one is based on.

### 8.8 Hypotheses

The hypotheses are **non-directional**:

- **H1:** Relative welfare efficiency differs between `baseline_v1` and
  `structured_v1`.
- **H2:** Egalitarian welfare differs between `baseline_v1` and
  `structured_v1`.
- **H3:** Agreement rate differs between `baseline_v1` and
  `structured_v1`.

The null hypothesis for each is that the metric does not differ between
conditions.

### 8.9 Statistical analysis

- **Unit of analysis:** the instance (n = 60). Negotiations within an
  instance are not treated as independent.
- **Per-instance value:** for each instance `i`, condition `k`, and
  confirmatory metric, `m_{i,k}` is the mean of the metric over that
  condition's 4 negotiations (2 seats × 2 first movers). For agreement
  rate, this is the fraction of those 4 negotiations that were `agreed`.
- **Paired difference:** `d_i = m_{i,structured_v1} - m_{i,baseline_v1}`.
- **Test statistic:** `T = mean_i(d_i)`.
- **Primary test:** a two-sided sign-flip permutation test on the `d_i`
  with 10,000 random sign-flip permutations. Each permutation multiplies
  every `d_i` by an independent random ±1 and recomputes `T*`.
  `p = (1 + #{ |T*| >= |T| }) / (1 + 10000)`.
- **Interval estimate:** a 95% percentile bootstrap CI for `mean(d_i)`,
  resampling instances with replacement, 10,000 resamples.
- **Sensitivity analysis:** a two-sided Wilcoxon signed-rank test on the
  same `d_i` (zero differences dropped).
- **Multiplicity:** Holm correction across the three permutation p-values
  for H1–H3, with family-wise α = 0.05. The Wilcoxon results are reported
  as sensitivity checks and are not part of the corrected family.
- **Secondary and diagnostic metrics:** descriptive only (condition-level
  summaries and agreed-negotiation counts). No hypothesis tests.
- **Randomness in the analysis:** the random seed for permutation and
  bootstrap resampling is fixed and recorded with the analysis output.
- **No interim stopping:** all 480 full-run negotiations are run. Outcomes
  are not compared by condition before the full run is complete, and the
  sample size does not depend on any observed result.

### 8.10 Experimental controls

- **Deterministic instance generation:** the resource pool and both
  valuations are fully determined by the instance seed (§1.1, §2). In a
  batch, the instance seed of negotiation `i` is `base_seed + i`.
- **Stable SHA-256 seed derivation:** per-agent valuation seeds come from
  `derive_seed(instance_seed, role)`, a SHA-256 hash. They do not depend on
  Python's per-process salted `hash()`.
- **First mover independent of generation:** the first mover comes only
  from the configured first-mover policy and the negotiation index. It
  never affects, and is never affected by, resource or valuation
  generation (§3.1).
- **Role/seat swapping:** each model plays both seats, and each seat moves
  first and second, on every instance in both conditions (§8.3).
- **Frozen protocol:** the negotiation engine, action schema, prompts,
  outcome semantics, and metrics are frozen for the experiment. The
  condition label is checked against the instructions each agent actually
  sends, so a run cannot carry the wrong label.
- **No strategic retries:** strategic outcomes (including invalid actions)
  are never retried. A negotiation is never re-run because of its outcome.
- **Provenance:** every run stores its full configuration, protocol id,
  spec fingerprint, prompt hash, code version, dependency versions, and raw
  model outputs (§3.7).

### 8.11 Failure handling

- **Strategic outcomes vs. infrastructure failures.** `agreed`,
  `walked_away`, `timeout`, and `invalid_action` are strategic outcomes and
  are analysed as specified. Transport failures (connection errors,
  timeouts, rate limits, or 5xx errors after in-turn retries, auth or
  bad-request errors) and budget stops are infrastructure failures, not
  outcomes (§3.6).
- **Transport aborts need a clean rerun.** An aborted negotiation gets no
  outcome and is stored separately in `aborted_negotiations`. It is never
  resumed mid-session. It must be re-run cleanly from turn 1 with the same
  instance seed, cell, and condition, so that the dataset contains all 480
  full-run negotiations.
- **Invalid-action sensitivity analysis.** In the primary analysis,
  `invalid_action` is a failure (0 for RWE and egalitarian welfare, not
  agreed for agreement rate). As a pre-specified sensitivity analysis,
  H1–H3 are recomputed with `invalid_action` negotiations removed.
  Per-instance means use each condition's remaining negotiations, and an
  instance with no remaining negotiations in a condition is left out of
  this sensitivity analysis only. Invalid-action rates by condition and by
  `invalid_reason` are always reported. The primary analysis stays the
  confirmatory one.

### 8.12 Methodological limitations

- **Bundled intervention.** `structured_v1` is one multi-component package
  (preference ranking/revelation, integrative trade guidance,
  disagreement-point/deadline reasoning). Any difference between conditions
  can only be attributed to the package as a whole. The design cannot
  causally isolate the effect of any single component.
- **Provider/output-schema asymmetry.** Claude returns actions through a
  forced tool call. GPT returns actions through strict JSON-schema
  structured output. The prompts carry the same information, but the
  output mechanisms differ, which may affect malformed-output rates and
  behavior.
- **One model pairing.** Only one Claude model and one GPT model are
  tested. Results may not generalize to other models or pairings.
- **LLM stochasticity.** Model outputs are stochastic. Repeating a
  negotiation with the same seed and settings may give a different outcome.
- **Model/version drift.** Provider models can change behind the same
  identifier over time. Model ids and run timestamps are recorded, but
  drift cannot be fully ruled out.
- **Welfare/fairness tension from linear utility.** With linear
  valuations, the welfare-maximizing allocation gives each category
  entirely to the agent that values it more (§1.3, §4). It can therefore be
  highly unequal. Efficiency and fairness metrics may diverge by
  construction.
- **Post-treatment selection in agreement-only metrics.** Conditional SWE
  and equitability are computed only on agreed negotiations. Condition can
  affect which negotiations reach agreement, so differences in these
  metrics are not clean treatment effects. This is why they are secondary
  and descriptive.
- **Correlated outcomes.** RWE, egalitarian welfare, and agreement rate
  are all driven by agreement (failures score 0), so they may be strongly
  correlated. Holm correction controls the family-wise error rate but does
  not make the three tests independent.

### 8.13 Utility model scope

The primary experiment uses **linear** utilities (§1.3, §2). Concave
(diminishing-returns) utilities are a possible future or secondary
experiment. They are **not** part of this preregistration.

### 8.14 Analysis decisions made before observing full experimental results

All of the following were fixed before any full-run negotiation was run:

1. Conditions and labels: `baseline_v1` vs. `structured_v1`, with the
   instruction texts frozen in `src/agents/prompting.py`.
2. Design: 60 instances (seeds 40000–40059), K = 3, linear valuations,
   seat swap × first-mover swap × condition, 8 negotiations per instance,
   480 in total.
3. Pilot: seeds 30000–30002, 8 cells × 3 negotiations, 24 negotiations in
   8 batches, not part of the confirmatory dataset.
4. Outcome semantics and `invalid_reason` values as in §8.5.
5. Metric formulas as in §8.6, including 0 for failures in unconditional
   RWE and egalitarian welfare, and undefined allocation-dependent metrics
   for non-agreed negotiations.
6. Metric hierarchy: confirmatory (RWE, egalitarian welfare, agreement
   rate), secondary descriptive (agreement-only equitability, conditional
   SWE), diagnostics (envy, model imbalance, first-mover effects).
7. Non-directional hypotheses H1–H3.
8. Instance as the unit of analysis, with per-instance means over the 4
   negotiations of each condition and paired differences.
9. Two-sided sign-flip permutation test (10,000 permutations), 95%
   percentile bootstrap CI (10,000 resamples), Wilcoxon signed-rank
   sensitivity analysis, Holm correction across H1–H3 at α = 0.05.
10. No interim stopping.
11. Transport aborts are infrastructure failures that need a clean rerun
    and are never outcomes. There are no strategic retries.
12. The invalid-action sensitivity analysis as specified in §8.11.
13. No exclusion rules beyond those stated in this section.
14. The runtime configuration in §8.15, fixed before the first pilot API
    call and identical for the pilot and the full run.

### 8.15 Runtime configuration

Fixed before the first pilot API call. The pilot and the full run use exactly
these values. Every pilot and full run is checked against them before any
API call (`check_approved_runtime` in `src/experiments/config.py`) and
refuses to start if any value differs.

**Scientific configuration.** These settings shape model behavior and are
part of the preregistration.

| Setting | Claude | OpenAI |
|---|---|---|
| Model | `claude-sonnet-4-6` (pinned snapshot) | `gpt-4.1-2025-04-14` (pinned snapshot, non-reasoning) |
| Temperature | 1.0 | 1.0 |
| Max output tokens | 1024 (`max_tokens`) | 1024 (`max_completion_tokens`) |
| Thinking / reasoning | Thinking off, sent explicitly as `thinking: {type: "disabled"}` | None: non-reasoning model, no `reasoning_effort` sent |
| Effort | `medium` (`output_config.effort`) | Not applicable |
| API seed | None: the Messages API has no seed parameter | None: no `seed` is sent |

Both conditions and both seats use the same values. `MAX_ROUNDS` = 10 for
every pilot and full-run negotiation.

No API-level seed is sent to either provider, so model outputs are not
deterministic (§8.12, LLM stochasticity). Instance generation stays fully
deterministic (§8.10).

**Operational configuration.** These settings affect only infrastructure
failures (§3.6, §8.11) and never a strategic outcome.

| Setting | Claude | OpenAI |
|---|---|---|
| Request timeout | 120 s | 120 s |
| Max in-turn retries | 3 | 3 |
| Retry backoff | 2.0 s (waits of 2, 4, 8 s) | 2.0 s (waits of 2, 4, 8 s) |

`max_transport_reruns` = 2: a negotiation aborted by a transport failure is
re-run cleanly from turn 1 up to twice (§8.11).

**Software.** The provider SDKs are pinned to `anthropic==1.8.0` and
`openai==3.19.2` in `pyproject.toml`. Dependency versions, including the
SDKs and their HTTP transport, are recorded with every run (§3.7).
