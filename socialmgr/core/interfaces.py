"""Extension interfaces (IMPLEMENTATION_PLAN.md §13.1).

Core code depends only on these protocols. Implementations live in ``providers/``,
``modelling/priors/`` and ``modelling/models/`` and are chosen in config.
"""

from collections.abc import Sequence
from typing import Protocol, runtime_checkable

from socialmgr.core.types import (
    Capability,
    CollectionBatch,
    CollectionRequest,
    ForecastSubject,
    LabelInput,
    LabelResult,
    PredictiveDraws,
    PriorContext,
    PriorSpec,
    TrainingExample,
)


@runtime_checkable
class DataProvider(Protocol):
    """A source of account, post and metric data for one or more account roles."""

    name: str
    capabilities: frozenset[Capability]
    roles: frozenset[str]

    def collect(self, request: CollectionRequest) -> CollectionBatch: ...


@runtime_checkable
class PriorSource(Protocol):
    """Supplies model priors. v1: hand-set (MODEL §2a). Later: the peer-panel prior (MODEL §3)."""

    name: str
    version: str

    def prior(self, context: PriorContext) -> PriorSpec: ...


@runtime_checkable
class FittedModel(Protocol):
    @property
    def version(self) -> str: ...

    def predict(self, subject: ForecastSubject) -> PredictiveDraws: ...


@runtime_checkable
class ForecastModel(Protocol):
    """Any model that can be fitted at a cutoff and return joint predictive draws."""

    family: str
    version: str

    def fit(self, examples: Sequence[TrainingExample], prior: PriorSpec, seed: int) -> FittedModel: ...


@runtime_checkable
class Labeler(Protocol):
    """Assigns LLM labels (topic, content kind, hook) from a versioned label set.

    v1: headless Claude Code on the member's subscription (default) or the Anthropic API.
    A failure returns no labels for the affected posts; the run continues and says so.
    """

    name: str
    version: str

    def label(self, items: Sequence[LabelInput]) -> list[LabelResult]: ...
