"""Leave-future-out rolling-origin backtest on owner data (MODEL.md §5; plan §6).

Each matured owner post is forecast as it would have been at its creation time: the candidate and
the baseline are fitted on the posts whose matured capture was visible by then (``visible_at`` <=
``created_at``), then both are scored exactly as ``score_matured`` scores frozen forecasts: mid-PIT,
randomized PIT seeded by the post id, CRPS on log1p, 50% and 80% hits; plus the 80% interval width
and the Brier score of P(I48 >= G), where G is the owner's goal if given, else the trailing median of
the last 21 originals visible at that time.

Scoring starts once ``min_train`` owner posts are visible (20: the first promotion review, plan §6).
The function is pure: no database, deterministic given its inputs and seed. Fits that share a
training set (posts created between the same two runs) hit the owner model's in-process cache.
"""

import hashlib
import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

import numpy as np

from socialmgr.core.interfaces import FittedModel, ForecastModel, PriorSource
from socialmgr.core.types import ForecastSubject, PredictiveDraws, PriorContext, TrainingExample
from socialmgr.modelling import scoring
from socialmgr.modelling.models.nb2_owner import FitDiagnostics
from socialmgr.modelling.models.trailing_median import WINDOW, TrailingMedianModel

MIN_TRAIN = 20
GOAL_TYPES = ("original", "quote")


@dataclass(frozen=True)
class BacktestPost:
    """A matured owner post as the backtest sees it."""

    example: TrainingExample
    visible_at: datetime  # cutoff of the first run that captured it at age >= 48h
    has_url: bool = False
    pit_seed: int | None = None  # the post's database id, as score_matured seeds the randomized PIT
    late_maturity: bool = False  # first captured after 14 days: kept in training, left out of the gates


@dataclass(frozen=True)
class ForecastScore:
    pit_mid: float
    pit_rand: float
    crps_log: float
    in_50: bool
    in_80: bool
    width_80: float
    median: float
    p_goal: float | None  # P(I48 >= G) under the forecast


@dataclass(frozen=True)
class BacktestRow:
    post_id: str
    created_at: datetime
    outcome: float
    goal: float | None
    hit_goal: bool | None
    n_train: int
    late_maturity: bool
    candidate: ForecastScore
    baseline: ForecastScore


@dataclass(frozen=True)
class BacktestResult:
    candidate_version: str  # the version the candidate reports when fitted on all posts
    baseline_version: str
    rows: tuple[BacktestRow, ...]
    min_train: int
    final_diagnostics: FitDiagnostics | None  # sampler diagnostics of the fit on all posts

    @property
    def gate_rows(self) -> tuple[BacktestRow, ...]:
        """Rows that enter the release gates (late-maturity posts are diagnostics only, MODEL §1.3)."""
        return tuple(r for r in self.rows if not r.late_maturity)


def _seed_from_id(text: str) -> int:
    return int.from_bytes(hashlib.sha256(text.encode()).digest()[:4], "big")


def _score(draws: PredictiveDraws, y: float, seed: int, goal: float | None) -> ForecastScore:
    s = scoring.score(draws.samples, y, seed=seed)
    q10, q50, q90 = np.quantile(draws.samples, [0.1, 0.5, 0.9])
    return ForecastScore(
        pit_mid=s.pit_mid,
        pit_rand=s.pit_rand,
        crps_log=s.crps_log,
        in_50=s.in_50,
        in_80=s.in_80,
        width_80=float(q90 - q10),
        median=float(q50),
        p_goal=None if goal is None else float(np.mean(draws.samples >= goal)),
    )


def trailing_goal(training: Sequence[TrainingExample]) -> float | None:
    """The trailing median I48 of the last 21 owner originals in ``training`` (None if there are none)."""
    originals = sorted(
        (e for e in training if e.account_role == "owner" and e.type in GOAL_TYPES),
        key=lambda e: (e.created_at, e.post_external_id),
    )[-WINDOW:]
    if not originals:
        return None
    return float(statistics.median(e.outcome_i48 for e in originals))


def _fit(
    model: ForecastModel,
    prior_source: PriorSource,
    training: list[TrainingExample],
    followers: int,
    seed: int,
) -> FittedModel:
    owner_n = sum(1 for e in training if e.account_role == "owner")
    prior = prior_source.prior(PriorContext(platform="x", followers=followers, owner_matured_posts=owner_n))
    return model.fit(training, prior, seed)


def run_backtest(
    posts: Sequence[BacktestPost],
    candidate: ForecastModel,
    prior_source: PriorSource,
    *,
    baseline: ForecastModel | None = None,
    goal: float | None = None,
    seed: int = 0,
    min_train: int = MIN_TRAIN,
) -> BacktestResult:
    """Rolling-origin backtest of ``candidate`` against ``baseline`` (default: the trailing median)."""
    base_model = baseline if baseline is not None else TrailingMedianModel()
    ordered = sorted(posts, key=lambda p: (p.example.created_at, p.example.post_external_id))
    by_visible = sorted(ordered, key=lambda p: (p.visible_at, p.example.created_at))
    rows: list[BacktestRow] = []
    for post in ordered:
        ex = post.example
        training = sorted(
            (
                p.example
                for p in by_visible
                if p.visible_at <= ex.created_at and p.example.post_external_id != ex.post_external_id
            ),
            key=lambda e: (e.created_at, e.post_external_id),
        )
        if sum(1 for e in training if e.account_role == "owner") < min_train:
            continue
        pit_seed = post.pit_seed if post.pit_seed is not None else _seed_from_id(ex.post_external_id)
        subject = ForecastSubject(
            subject_id=f"post:{post.pit_seed if post.pit_seed is not None else ex.post_external_id}",
            created_at=ex.created_at,
            type=ex.type,
            followers_at_post=ex.followers_at_post,
            has_url=post.has_url,
        )
        g = goal if goal is not None else trailing_goal(training)
        cand = _fit(candidate, prior_source, training, ex.followers_at_post, seed).predict(subject)
        base = _fit(base_model, prior_source, training, ex.followers_at_post, seed).predict(subject)
        rows.append(
            BacktestRow(
                post_id=ex.post_external_id,
                created_at=ex.created_at,
                outcome=ex.outcome_i48,
                goal=g,
                hit_goal=None if g is None else bool(ex.outcome_i48 >= g),
                n_train=len(training),
                late_maturity=post.late_maturity,
                candidate=_score(cand, ex.outcome_i48, pit_seed, g),
                baseline=_score(base, ex.outcome_i48, pit_seed, g),
            )
        )
    everything = [p.example for p in ordered]
    followers = everything[-1].followers_at_post if everything else 0
    final = _fit(candidate, prior_source, everything, followers, seed)
    final_base = _fit(base_model, prior_source, everything, followers, seed)
    diagnostics = getattr(final, "diagnostics", None)
    return BacktestResult(
        candidate_version=final.version,
        baseline_version=final_base.version,
        rows=tuple(rows),
        min_train=min_train,
        final_diagnostics=diagnostics if isinstance(diagnostics, FitDiagnostics) else None,
    )
