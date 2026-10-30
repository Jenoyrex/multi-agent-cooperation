"""Preregistered inferential analysis (docs/spec.md §8.9, §8.11, §8.14).

Pure Python (stdlib only), so the analysis adds no dependency.

Design rules implemented exactly as preregistered:
- Unit of analysis: the instance (instance_seed). Negotiations within an instance
  are never treated as independent.
- Per-instance value m_{i,k}: the mean of the metric over that instance's
  negotiations in condition k (4 in the full design: 2 seats x 2 first movers).
  For agreement rate, the fraction agreed.
- Paired difference d_i = m_{i,structured_v1} - m_{i,baseline_v1}, only for
  instances observed in BOTH conditions. Pairs are never formed across instances;
  an instance missing a condition is reported, not imputed.
- T = mean(d_i). Two-sided sign-flip permutation test, 10,000 random sign flips:
  p = (1 + #{|T*| >= |T|}) / (1 + 10000).
- 95% percentile bootstrap CI for mean(d_i), resampling instances with replacement,
  10,000 resamples.
- Sensitivity: two-sided Wilcoxon signed-rank on the same d_i, zero differences
  dropped; not part of the corrected family.
- Holm correction across the three permutation p-values (H1 RWE, H2 egalitarian
  welfare, H3 agreement rate), family-wise alpha = 0.05.
- Metric scoring follows §8.6: RWE and EW score every failure as 0, and agreement
  uses every negotiation, so all three confirmatory metrics are defined for every
  negotiation that has an outcome. Aborted negotiations are not outcomes and never
  appear here.
- §8.11 invalid-action sensitivity: H1-H3 recomputed with invalid_action
  negotiations removed. Per-instance means use each condition's remaining
  negotiations; an instance with none left in a condition is left out of this
  analysis only.

Details the spec leaves open, fixed here before any full-run data exists:
- ANALYSIS_SEED = 20260921. Each hypothesis and procedure draws from its own stream
  seeded with f"{seed}:{metric}:{procedure}", so results do not depend on the order
  in which hypotheses are analysed.
- Percentile CI: linear interpolation between order statistics (the common "type 7"
  definition) at 2.5% and 97.5% of the 10,000 bootstrap means.
- Permutation comparison |T*| >= |T| uses a relative tolerance of 1e-12, so that
  sign patterns giving the same |T| up to floating-point rounding count as ties.
- Wilcoxon p-value: the exact null distribution when there are no ties among |d_i|
  and n <= 50; otherwise the normal approximation with tie correction and no
  continuity correction.
"""
from __future__ import annotations

import copy
import math
import random
from collections import defaultdict
from functools import lru_cache
from statistics import mean
from typing import Iterable, Optional

ANALYSIS_SEED = 20260921
PERMUTATIONS = 10_000
BOOTSTRAP_RESAMPLES = 10_000
ALPHA = 0.05
CONFIDENCE = 0.95
BASELINE, STRUCTURED = "baseline_v1", "structured_v1"
HYPOTHESES = (("H1", "rwe", "Relative welfare efficiency"),
              ("H2", "ew", "Egalitarian welfare"),
              ("H3", "agreement_rate", "Agreement rate"))
OUTCOMES = ("agreed", "walked_away", "timeout", "invalid_action")
EXACT_WILCOXON_MAX_N = 50
_TOL = 1e-12


# ------------------------------------------------------------ per-instance values
def metric_value(row: dict, metric: str) -> float:
    """One negotiation's value of a confirmatory metric (§8.6)."""
    if row["outcome"] not in OUTCOMES:
        raise ValueError(f"not a strategic outcome: {row['outcome']!r}")
    if metric == "agreement_rate":
        return 1.0 if row["outcome"] == "agreed" else 0.0
    value = row[metric]
    if row["outcome"] != "agreed":
        if value not in (0, 0.0):  # the frozen scoring: every failure scores 0
            raise ValueError(f"{metric} of a non-agreed negotiation must be 0, got {value!r}")
        return 0.0
    if value is None or not math.isfinite(value):
        raise ValueError(f"{metric} of an agreed negotiation is missing")
    return float(value)


def paired_differences(rows: Iterable[dict], metric: str, drop_invalid: bool = False) -> dict:
    """d_i per instance. Rows need 'seed', 'condition', 'outcome' and the metric.
    Returns the pairs plus every instance that could not be paired, and why."""
    groups: dict = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if drop_invalid and r["outcome"] == "invalid_action":
            continue
        if r["condition"] not in (BASELINE, STRUCTURED):
            raise ValueError(f"unknown condition {r['condition']!r}")
        groups[r["seed"]][r["condition"]].append(metric_value(r, metric))
    pairs, unpaired = [], []
    for seed in sorted(groups):
        g = groups[seed]
        if g[BASELINE] and g[STRUCTURED]:
            b, s = mean(g[BASELINE]), mean(g[STRUCTURED])
            pairs.append({"seed": seed, "baseline": b, "structured": s, "d": s - b,
                          "n_baseline": len(g[BASELINE]), "n_structured": len(g[STRUCTURED])})
        else:
            unpaired.append({"seed": seed, "missing": BASELINE if not g[BASELINE] else STRUCTURED,
                             "n_baseline": len(g[BASELINE]), "n_structured": len(g[STRUCTURED])})
    return {"pairs": pairs, "unpaired": unpaired}


# ------------------------------------------------------------ the three procedures
def _rng(seed: int, metric: str, procedure: str) -> random.Random:
    return random.Random(f"{seed}:{metric}:{procedure}")


def sign_flip_test(d: list[float], rng: random.Random, permutations: int = PERMUTATIONS) -> dict:
    """Two-sided sign-flip permutation test of mean(d) = 0, as preregistered."""
    n = len(d)
    if n == 0:
        return {"defined": False, "reason": "no paired instances"}
    t = mean(d)
    bound = abs(t) * (1 - _TOL)
    hits = 0
    for _ in range(permutations):
        bits = rng.getrandbits(n)
        s = sum(-x if (bits >> i) & 1 else x for i, x in enumerate(d))
        hits += abs(s / n) >= bound
    return {"defined": True, "statistic": t, "p": (1 + hits) / (1 + permutations), "permutations": permutations,
            "distinct_sign_patterns": 2 ** n if n <= 60 else None,
            "note": None if n > 13 else f"Only 2^{n} = {2 ** n} distinct sign patterns exist; random flips repeat them."}


def _percentile(sorted_xs: list[float], q: float) -> float:
    """Linear interpolation between order statistics ("type 7")."""
    pos = (len(sorted_xs) - 1) * q
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_xs) - 1)
    return sorted_xs[lo] + (sorted_xs[hi] - sorted_xs[lo]) * (pos - lo)


def bootstrap_ci(d: list[float], rng: random.Random, resamples: int = BOOTSTRAP_RESAMPLES,
                 confidence: float = CONFIDENCE) -> dict:
    """Percentile bootstrap CI for mean(d), resampling instances with replacement."""
    n = len(d)
    if n == 0:
        return {"defined": False, "reason": "no paired instances"}
    means = sorted(sum(d[rng.randrange(n)] for _ in range(n)) / n for _ in range(resamples))
    a = (1 - confidence) / 2
    return {"defined": True, "low": _percentile(means, a), "high": _percentile(means, 1 - a),
            "confidence": confidence, "resamples": resamples,
            "note": "Degenerate: one instance, so every resample is identical." if n == 1 else None}


def _ranks(values: list[float]) -> list[float]:
    """Average ranks (1-based) with ties sharing the mean rank."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        for k in range(i, j + 1):
            ranks[order[k]] = (i + j) / 2 + 1
        i = j + 1
    return ranks


def _normal_two_sided(z: float) -> float:
    return math.erfc(abs(z) / math.sqrt(2))


def wilcoxon_signed_rank(d: list[float]) -> dict:
    """Two-sided Wilcoxon signed-rank test, zero differences dropped (§8.9)."""
    nz = [x for x in d if x != 0]
    n, zeros = len(nz), len(d) - len(nz)
    if n == 0:
        return {"defined": False, "reason": "every paired difference is zero", "n_used": 0, "zeros_dropped": zeros}
    absd = [abs(x) for x in nz]
    ranks = _ranks(absd)
    w_plus = sum(r for r, x in zip(ranks, nz) if x > 0)
    ties = len(set(absd)) != len(absd)
    out = {"defined": True, "statistic_w_plus": w_plus, "n_used": n, "zeros_dropped": zeros}
    if not ties and n <= EXACT_WILCOXON_MAX_N:
        # Exact null: each rank 1..n enters W+ with probability 1/2.
        counts = [1] + [0] * (n * (n + 1) // 2)
        for r in range(1, n + 1):
            for s in range(len(counts) - 1, r - 1, -1):
                counts[s] += counts[s - r]
        total = 2 ** n
        w = int(round(w_plus))
        lower = sum(counts[: w + 1]) / total
        upper = sum(counts[w:]) / total
        out.update(method="exact", p=min(1.0, 2 * min(lower, upper)))
    else:
        mu = n * (n + 1) / 4
        tie_term = sum(t ** 3 - t for t in _tie_sizes(absd)) / 48
        var = n * (n + 1) * (2 * n + 1) / 24 - tie_term
        if var <= 0:
            return {**out, "defined": False, "reason": "zero variance under the null"}
        out.update(method="normal approximation, tie-corrected, no continuity correction",
                   p=_normal_two_sided((w_plus - mu) / math.sqrt(var)))
    if n < 6:
        out["note"] = f"With n = {n} non-zero differences, no two-sided p below 0.05 is attainable."
    return out


def _tie_sizes(values: list[float]) -> list[int]:
    counts: dict = defaultdict(int)
    for v in values:
        counts[v] += 1
    return [c for c in counts.values() if c > 1]


def holm(pvalues: dict, alpha: float = ALPHA) -> dict:
    """Holm step-down adjusted p-values and rejections for {name: p} (None = undefined, skipped)."""
    defined = sorted(((p, k) for k, p in pvalues.items() if p is not None))
    m, running, out = len(defined), 0.0, {}
    for i, (p, k) in enumerate(defined):
        running = max(running, min(1.0, (m - i) * p))  # monotone step-down adjustment
        out[k] = {"p_holm": running, "reject": running <= alpha}  # same decisions as the step-down rule
    for k, p in pvalues.items():
        if p is None:
            out[k] = {"p_holm": None, "reject": False}
    return out


# ------------------------------------------------------------ the analysis
def _hypothesis(rows: list[dict], metric: str, seed: int, drop_invalid: bool) -> dict:
    pd = paired_differences(rows, metric, drop_invalid)
    d = [p["d"] for p in pd["pairs"]]
    stream = f"{metric}{':no-invalid' if drop_invalid else ''}"
    return {"n_pairs": len(d), "pairs": pd["pairs"], "unpaired": pd["unpaired"],
            "mean_difference": mean(d) if d else None,
            "permutation": sign_flip_test(d, _rng(seed, stream, "permutation")),
            "bootstrap": bootstrap_ci(d, _rng(seed, stream, "bootstrap")),
            "wilcoxon": wilcoxon_signed_rank(d)}


def analyse(rows: Iterable[dict], seed: int = ANALYSIS_SEED) -> dict:
    """The full §8.9 analysis plus the §8.11 invalid-action sensitivity analysis.
    Rows: one per negotiation with an outcome ('seed', 'condition', 'outcome',
    'rwe', 'ew'). Aborted negotiations must not be passed in."""
    rows = list(rows)
    out = {"seed": seed, "permutations": PERMUTATIONS, "bootstrap_resamples": BOOTSTRAP_RESAMPLES,
           "alpha": ALPHA, "n_negotiations": len(rows), "n_instances": len({r["seed"] for r in rows}),
           "outcomes_by_condition": {c: {o: sum(r["outcome"] == o and r["condition"] == c for r in rows) for o in OUTCOMES}
                                     for c in (BASELINE, STRUCTURED)},
           "primary": {}, "invalid_action_sensitivity": {}}
    for drop, key in ((False, "primary"), (True, "invalid_action_sensitivity")):
        res = {h: {"metric": m, "label": label, **_hypothesis(rows, m, seed, drop)} for h, m, label in HYPOTHESES}
        adj = holm({h: r["permutation"].get("p") for h, r in res.items()})
        for h, r in res.items():
            r["permutation"].update(adj[h])
        out[key] = res
    return out


_FIELDS = ("seed", "condition", "outcome", "rwe", "ew")  # everything analyse() reads from a row


def analyse_cached(rows: Iterable[dict], seed: int = ANALYSIS_SEED) -> dict:
    """analyse(), memoised on exactly the fields it reads, so a page polling unchanged data does not
    redo 60,000 resamples each time. Returns a private copy, so callers may not corrupt the cache."""
    return copy.deepcopy(_analyse_key(tuple(tuple(r[k] for k in _FIELDS) for r in rows), seed))


@lru_cache(maxsize=16)
def _analyse_key(key: tuple, seed: int) -> dict:
    return analyse([dict(zip(_FIELDS, k)) for k in key], seed)


def interpret(h: dict, alpha: float = ALPHA) -> str:
    """One sentence that says no more than the test supports."""
    perm, ci = h["permutation"], h["bootstrap"]
    if not perm.get("defined"):
        return f"Not testable: {perm['reason']}."
    d = h["mean_difference"]
    direction = "higher under structured" if d > 0 else "lower under structured" if d < 0 else "no difference between conditions"
    verdict = ("statistically significant after Holm correction" if perm["reject"]
               else "not statistically significant after Holm correction")
    return (f"Mean paired difference {h['mean_difference']:+.4f} (structured − baseline; {direction}) over "
            f"{h['n_pairs']} instances, 95% CI [{ci['low']:+.4f}, {ci['high']:+.4f}]; permutation p = {perm['p']:.4f}, "
            f"Holm-adjusted p = {perm['p_holm']:.4f}: {verdict} at α = {alpha}.")
