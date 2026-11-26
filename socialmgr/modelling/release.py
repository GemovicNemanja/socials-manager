"""Release gates for promoting a candidate forecast model (MODEL.md §5; plan §6).

All gates must pass. A failed gate keeps the previous version: ``evaluate_release`` returns that
decision and the evidence; recording it (model_versions, the Forecasts screen) is the caller's job.

1. Log-scale CRPS skill vs the trailing-median baseline > 0, and the 5th percentile of a week-block
   bootstrap (90% interval lower bound) > 0.
2. 50% and 80% coverage inside exact binomial 95% bands (for n = 60 the 80% band is [0.70, 0.90]).
3. Berkowitz LR (3 df) and PIT chi-square p-values > 0.01, both on the randomized PIT.
4. No alarm of a two-sided location CUSUM on z = Phi^-1(randomized PIT), k = 0.25, h = 7.62, run
   over the backtest in post order.
5. Mean 80% interval width <= 1.1 x the baseline's.
6. R-hat < 1.01 and bulk ESS > 400 in the candidate's fit, and SBC recorded as passed for its version.
7. Brier score of P(I48 >= G) no worse than the baseline's.

Before the gates, the backtest must hold at least ``min_scored`` scored posts; with fewer the
evidence is too thin and the decision is to keep the previous version.
"""

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
from scipy import special, stats

from socialmgr.modelling.backtest import BacktestResult, BacktestRow
from socialmgr.modelling.models.nb2_owner import FitDiagnostics
from socialmgr.modelling.sbc import SBC_RESULTS, SBCRecord

CUSUM_K = 0.25
CUSUM_H = 7.62  # MODEL §4.3: two-sided, K = 3 charts, ARL0 = 300 (each side 600)
PIT_EPS = 0.5 / 1000  # randomized PITs are clipped to [eps, 1 - eps] before Phi^-1 (1,000 draws)


@dataclass(frozen=True)
class GateThresholds:
    min_scored: int = 20
    bootstrap_reps: int = 2000
    bootstrap_level: float = 0.90
    coverage_band: float = 0.95  # central probability of the exact binomial band
    p_min: float = 0.01
    cusum_k: float = CUSUM_K
    cusum_h: float = CUSUM_H
    # None: the chart runs over the whole backtest (MODEL §5 as written). Its false-alarm chance then
    # grows with the backtest's length (about 13% at 57 posts, 25% at 100, 63% at 300 for calibrated
    # iid z), so a caller may restrict it to the most recent posts.
    cusum_window: int | None = None
    width_ratio_max: float = 1.1
    rhat_max: float = 1.01
    ess_min: float = 400.0
    brier_tolerance: float = 0.0  # candidate Brier may exceed the baseline's by at most this


@dataclass(frozen=True)
class GateResult:
    name: str
    passed: bool
    value: float | None
    threshold: str
    detail: str


@dataclass(frozen=True)
class ReleaseDecision:
    promote: bool
    reason: str
    candidate_version: str
    previous_version: str
    chosen_version: str  # the candidate when promoted, else the previous version
    n_scored: int
    gates: tuple[GateResult, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------- statistics used by the gates


def pit_to_z(pits: Sequence[float], eps: float = PIT_EPS) -> np.ndarray:
    p = np.clip(np.asarray(pits, dtype=np.float64), eps, 1.0 - eps)
    return np.asarray(special.ndtri(p), dtype=np.float64)


def cusum_first_alarm(z: Sequence[float], k: float = CUSUM_K, h: float = CUSUM_H) -> int | None:
    """Index of the first alarm of a two-sided location CUSUM on z, or None.

    S+ = max(0, S+ + z - k), S- = max(0, S- - z - k); alarm when either exceeds h. Kept inline until
    ``socialmgr/modelling/monitors.py`` provides the shared implementation.
    """
    hi = lo = 0.0
    for i, v in enumerate(z):
        hi = max(0.0, hi + v - k)
        lo = max(0.0, lo - v - k)
        if hi > h or lo > h:
            return i
    return None


def berkowitz_p(z: Sequence[float]) -> float:
    """Berkowitz (2001) LR test of z ~ iid N(0, 1) against a Gaussian AR(1) with free mean and
    variance (3 df), using the likelihood conditional on the first observation."""
    x = np.asarray(z, dtype=np.float64)
    if x.size < 4:
        return math.nan
    prev, cur = x[:-1], x[1:]
    design = np.column_stack([np.ones_like(prev), prev])
    coef, *_ = np.linalg.lstsq(design, cur, rcond=None)
    resid = cur - design @ coef
    sigma2 = max(float(np.mean(resid**2)), 1e-12)
    n = cur.size
    ll_alt = -0.5 * n * (math.log(2 * math.pi * sigma2) + 1.0)
    ll_null = -0.5 * n * math.log(2 * math.pi) - 0.5 * float(np.sum(cur**2))
    lr = max(2.0 * (ll_alt - ll_null), 0.0)
    return float(stats.chi2.sf(lr, 3))


def pit_chi2_p(pits: Sequence[float], bins: int | None = None) -> float:
    """Chi-square test of uniform randomized PITs; 10 bins, fewer so each expects at least 5."""
    p = np.asarray(pits, dtype=np.float64)
    n = p.size
    if n < 10:
        return math.nan
    k = bins if bins is not None else max(2, min(10, n // 5))
    counts = np.bincount(np.minimum((p * k).astype(int), k - 1), minlength=k)
    expected = n / k
    stat = float(np.sum((counts - expected) ** 2) / expected)
    return float(stats.chi2.sf(stat, k - 1))


def binomial_band(n: int, p: float, prob: float = 0.95) -> tuple[float, float]:
    """Central exact binomial band for the hit rate of a p-interval over n forecasts."""
    tail = (1.0 - prob) / 2.0
    lo = float(stats.binom.ppf(tail, n, p))
    hi = float(stats.binom.isf(tail, n, p))
    return lo / n, hi / n


def crps_skill(rows: Sequence[BacktestRow]) -> float:
    cand = sum(r.candidate.crps_log for r in rows)
    base = sum(r.baseline.crps_log for r in rows)
    return 1.0 - cand / base if base > 0 else math.nan


def week_block_bootstrap_skill(rows: Sequence[BacktestRow], reps: int, seed: int) -> np.ndarray:
    """CRPS skill over ``reps`` resamples of whole ISO weeks (posts keep their week's dependence)."""
    weeks: dict[tuple[int, int], list[BacktestRow]] = {}
    for r in rows:
        iso = r.created_at.isocalendar()
        weeks.setdefault((iso.year, iso.week), []).append(r)
    keys = sorted(weeks)
    cand = np.array([sum(r.candidate.crps_log for r in weeks[k]) for k in keys])
    base = np.array([sum(r.baseline.crps_log for r in weeks[k]) for k in keys])
    rng = np.random.default_rng([seed, 0xB007])
    idx = rng.integers(0, len(keys), size=(reps, len(keys)))
    with np.errstate(divide="ignore", invalid="ignore"):
        return np.asarray(1.0 - cand[idx].sum(axis=1) / base[idx].sum(axis=1), dtype=np.float64)


def brier(rows: Sequence[BacktestRow], which: str) -> float:
    pairs = [
        (getattr(r, which).p_goal, r.hit_goal) for r in rows if r.hit_goal is not None and r.goal is not None
    ]
    if not pairs:
        return math.nan
    return float(np.mean([(p - float(h)) ** 2 for p, h in pairs]))


# ---------------------------------------------------------------- gates


def _fmt(v: float) -> str:
    return "n/a" if v is None or not math.isfinite(v) else f"{v:.4g}"


def gate_crps_skill(rows: Sequence[BacktestRow], t: GateThresholds, seed: int) -> GateResult:
    skill = crps_skill(rows)
    boot = week_block_bootstrap_skill(rows, t.bootstrap_reps, seed)
    boot = boot[np.isfinite(boot)]
    lower = float(np.quantile(boot, (1.0 - t.bootstrap_level) / 2.0)) if boot.size else math.nan
    passed = math.isfinite(skill) and skill > 0 and math.isfinite(lower) and lower > 0
    return GateResult(
        "crps_skill",
        passed,
        skill,
        f"> 0 and week-block bootstrap {t.bootstrap_level:.0%} lower bound > 0",
        f"log-CRPS skill {_fmt(skill)}, bootstrap lower bound {_fmt(lower)}",
    )


def gate_coverage(rows: Sequence[BacktestRow], t: GateThresholds) -> GateResult:
    n = len(rows)
    parts, ok = [], True
    cov80 = math.nan
    for level, attr in ((0.5, "in_50"), (0.8, "in_80")):
        cov = sum(getattr(r.candidate, attr) for r in rows) / n if n else math.nan
        lo, hi = binomial_band(n, level, t.coverage_band) if n else (math.nan, math.nan)
        inside = n > 0 and lo <= cov <= hi
        ok = ok and inside
        parts.append(f"{level:.0%}: {_fmt(cov)} in [{_fmt(lo)}, {_fmt(hi)}]")
        if level == 0.8:
            cov80 = cov
    return GateResult(
        "coverage", ok, cov80, f"inside exact binomial {t.coverage_band:.0%} bands", "; ".join(parts)
    )


def gate_pit_tests(rows: Sequence[BacktestRow], t: GateThresholds) -> GateResult:
    pits = [r.candidate.pit_rand for r in rows]
    p_berk = berkowitz_p(pit_to_z(pits).tolist())
    p_chi = pit_chi2_p(pits)
    value = min(p_berk, p_chi)
    passed = math.isfinite(value) and value > t.p_min
    return GateResult(
        "pit_calibration",
        passed,
        value,
        f"Berkowitz and PIT chi-square p > {t.p_min}",
        f"Berkowitz p {_fmt(p_berk)}, PIT chi-square p {_fmt(p_chi)}",
    )


def gate_cusum(
    rows: Sequence[BacktestRow], t: GateThresholds, cusum: Callable[[Sequence[float]], int | None] | None
) -> GateResult:
    if t.cusum_window is not None:
        rows = rows[-t.cusum_window :]
    z = pit_to_z([r.candidate.pit_rand for r in rows]).tolist()
    alarm = cusum(z) if cusum is not None else cusum_first_alarm(z, t.cusum_k, t.cusum_h)
    detail = "no alarm" if alarm is None else f"alarm at backtest post {alarm + 1} ({rows[alarm].post_id})"
    return GateResult(
        "cusum",
        alarm is None,
        None if alarm is None else float(alarm + 1),
        f"no two-sided location CUSUM alarm (k = {t.cusum_k}, h = {t.cusum_h})",
        detail,
    )


def calibrated_baseline_width(rows: Sequence[BacktestRow], level: float = 0.8) -> tuple[float, float]:
    """The baseline's mean 80% width after recalibrating its ranges to cover 80% on this backtest.

    Amendment to MODEL §5 gate 5 (owner, 2026-11-26): on real accounts the trailing median is
    overconfident (40-52% of outcomes inside its 80% range), so a raw width comparison penalised an
    honest model. The baseline is lognormal (median m, log-sd s), so its central interval at level L
    is 2·m·sinh(z_L·s). L* is the level at which its own randomized PITs cover 80%, and each row's
    width is rescaled from z_0.8 to z_L*. Returns (calibrated mean width, L*).
    """
    pits = np.array([r.baseline.pit_rand for r in rows])
    l_star = float(np.clip(np.quantile(np.abs(2 * pits - 1), level), 0.5, 0.999))
    z80, z_star = stats.norm.ppf(0.9), stats.norm.ppf((1 + l_star) / 2)
    widths = []
    for r in rows:
        m = max(r.baseline.median, 1e-9)
        s = math.asinh(r.baseline.width_80 / (2 * m)) / z80
        widths.append(2 * m * math.sinh(z_star * s))
    return float(np.mean(widths)), l_star


def gate_width(rows: Sequence[BacktestRow], t: GateThresholds) -> GateResult:
    cand = float(np.mean([r.candidate.width_80 for r in rows]))
    raw = float(np.mean([r.baseline.width_80 for r in rows]))
    base, l_star = calibrated_baseline_width(rows)
    ratio = cand / base if base > 0 else math.inf
    return GateResult(
        "width_80",
        ratio <= t.width_ratio_max,
        ratio,
        f"mean 80% width <= {t.width_ratio_max} x the calibrated baseline's",
        f"candidate {_fmt(cand)}, baseline {_fmt(base)} once recalibrated "
        f"(raw {_fmt(raw)}; its {l_star:.0%} range covers 80%)",
    )


def gate_sampler(
    diagnostics: FitDiagnostics | None,
    version: str,
    sbc_results: Mapping[str, SBCRecord],
    t: GateThresholds,
) -> GateResult:
    sbc = sbc_results.get(version)
    sbc_ok = sbc is not None and sbc.passed
    sbc_txt = "SBC not recorded for this version" if sbc is None else sbc.summary()
    if diagnostics is None:
        return GateResult(
            "sampler",
            False,
            None,
            f"R-hat < {t.rhat_max}, ESS > {t.ess_min:.0f}, SBC passed",
            f"no sampler diagnostics from the fit; {sbc_txt}",
        )
    rh, ess = diagnostics.rhat_max, diagnostics.ess_bulk_min
    passed = rh < t.rhat_max and ess > t.ess_min and sbc_ok
    return GateResult(
        "sampler",
        passed,
        rh,
        f"R-hat < {t.rhat_max}, ESS > {t.ess_min:.0f}, SBC passed",
        f"max R-hat {rh:.4f}, min bulk ESS {ess:.0f}; {sbc_txt}",
    )


def gate_brier(rows: Sequence[BacktestRow], t: GateThresholds) -> GateResult:
    cand, base = brier(rows, "candidate"), brier(rows, "baseline")
    passed = math.isfinite(cand) and math.isfinite(base) and cand <= base + t.brier_tolerance
    return GateResult(
        "brier",
        passed,
        cand,
        "<= baseline Brier" + (f" + {t.brier_tolerance}" if t.brier_tolerance else ""),
        f"candidate {_fmt(cand)}, baseline {_fmt(base)}",
    )


def evaluate_release(
    backtest: BacktestResult,
    previous_version: str,
    *,
    diagnostics: FitDiagnostics | None = None,
    candidate_version: str | None = None,
    sbc_results: Mapping[str, SBCRecord] = SBC_RESULTS,
    thresholds: GateThresholds = GateThresholds(),
    seed: int = 0,
    cusum: Callable[[Sequence[float]], int | None] | None = None,
) -> ReleaseDecision:
    """Run every gate on the backtest and decide: promote the candidate or keep ``previous_version``.

    ``diagnostics`` and ``candidate_version`` default to the backtest's final fit. ``cusum`` lets the
    shared monitor replace the inline chart (it returns the first alarm index or None).
    """
    version = candidate_version or backtest.candidate_version
    diag = diagnostics if diagnostics is not None else backtest.final_diagnostics
    rows = backtest.gate_rows
    n = len(rows)
    t = thresholds
    if n < t.min_scored:
        size = GateResult(
            "backtest_size",
            False,
            float(n),
            f">= {t.min_scored} scored posts",
            f"{n} posts scored in the backtest; too few to judge, so the previous version stays",
        )
        return ReleaseDecision(
            promote=False,
            reason=f"kept {previous_version}: only {n} backtest posts (need {t.min_scored})",
            candidate_version=version,
            previous_version=previous_version,
            chosen_version=previous_version,
            n_scored=n,
            gates=(size,),
        )
    gates = (
        gate_crps_skill(rows, t, seed),
        gate_coverage(rows, t),
        gate_pit_tests(rows, t),
        gate_cusum(rows, t, cusum),
        gate_width(rows, t),
        gate_sampler(diag, version, sbc_results, t),
        gate_brier(rows, t),
    )
    failed = [g.name for g in gates if not g.passed]
    promote = not failed
    reason = (
        f"promote {version}: all {len(gates)} gates passed on {n} backtest posts"
        if promote
        else f"kept {previous_version}: failed {', '.join(failed)}"
    )
    return ReleaseDecision(
        promote=promote,
        reason=reason,
        candidate_version=version,
        previous_version=previous_version,
        chosen_version=version if promote else previous_version,
        n_scored=n,
        gates=gates,
    )
