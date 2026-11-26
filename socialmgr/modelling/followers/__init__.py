"""Follower model, forecast and scoring (MODEL.md §2c, §4.1; plan §6 "Followers and goals (M3)").

Per run, the caller builds ``RunInterval``s (``intervals_from_runs``), fits ``fit_follower_model``,
forecasts with ``forecast_followers`` (freezing ``FollowerForecast.to_dict()`` and its manifest),
scores matured frozen forecasts with ``score_follower_forecast``, and feeds the next interval's
gains to the drift detector through ``FollowerFit.one_step_predictive(...).z(y, seed_id)``.
"""

from socialmgr.modelling.followers.baselines import BaselineForecast, local_linear_trend, persistence_drift
from socialmgr.modelling.followers.data import (
    RunInterval,
    RunObservation,
    intervals_from_runs,
    sum_increments,
)
from socialmgr.modelling.followers.forecast import (
    DEFAULT_CHECKPOINTS,
    DEFAULT_PATHS,
    DEFAULT_SCENARIOS,
    FORECAST_VERSION,
    QUANTILE_KEYS,
    ArrayImpressionSampler,
    FollowerForecast,
    FollowerGoal,
    ImpressionSampler,
    Scenario,
    ScenarioResult,
    forecast_followers,
    forecast_from_manifest,
    manifest_hash,
)
from socialmgr.modelling.followers.model import (
    MODEL_VERSION,
    FollowerConfig,
    FollowerFit,
    FollowerPriors,
    GainPredictive,
    IntervalDiagnostic,
    NormalPrior,
    fit_follower_model,
)
from socialmgr.modelling.followers.scoring import FollowerScore, score_follower_forecast

__all__ = [
    "DEFAULT_CHECKPOINTS",
    "DEFAULT_PATHS",
    "DEFAULT_SCENARIOS",
    "FORECAST_VERSION",
    "MODEL_VERSION",
    "QUANTILE_KEYS",
    "ArrayImpressionSampler",
    "BaselineForecast",
    "FollowerConfig",
    "FollowerFit",
    "FollowerForecast",
    "FollowerGoal",
    "FollowerPriors",
    "FollowerScore",
    "GainPredictive",
    "ImpressionSampler",
    "IntervalDiagnostic",
    "NormalPrior",
    "RunInterval",
    "RunObservation",
    "Scenario",
    "ScenarioResult",
    "fit_follower_model",
    "forecast_followers",
    "forecast_from_manifest",
    "intervals_from_runs",
    "local_linear_trend",
    "manifest_hash",
    "persistence_drift",
    "score_follower_forecast",
    "sum_increments",
]
