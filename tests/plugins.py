"""Second implementations of the extension interfaces, loaded by module:attribute path in tests."""

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from socialmgr.core.config import Settings
from socialmgr.core.types import (
    Distribution,
    ForecastSubject,
    PredictiveDraws,
    PriorContext,
    PriorSpec,
    TrainingExample,
)


class FlatPrior:
    name = "flat"
    version = "test"

    def prior(self, context: PriorContext) -> PriorSpec:
        return PriorSpec(
            source=self.name,
            version=self.version,
            params={"level": Distribution(family="normal", params={"mean": 500.0, "sd": 1.0})},
        )


@dataclass(frozen=True)
class FittedConstant:
    version: str
    level: float

    def predict(self, subject: ForecastSubject) -> PredictiveDraws:
        return PredictiveDraws(metric="impressions", horizon_h=48.0, samples=np.full(1000, self.level))


class ConstantModel:
    family = "constant"
    version = "test"

    def fit(self, examples: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> FittedConstant:
        return FittedConstant(
            version=f"constant@test+{prior.source}", level=prior.params["level"].params["mean"]
        )


def flat_prior(settings: Settings) -> FlatPrior:
    return FlatPrior()


def constant_model(settings: Settings) -> ConstantModel:
    return ConstantModel()
