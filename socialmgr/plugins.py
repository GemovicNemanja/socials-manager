"""Built-in implementations of the extension interfaces, registered by name."""

from socialmgr.core.config import Settings
from socialmgr.core.interfaces import DataProvider, ForecastModel, Labeler, PriorSource
from socialmgr.core.registry import Registry
from socialmgr.labelling.llm import AnthropicApiLabeler, ClaudeCodeLabeler, NoLabeler
from socialmgr.modelling.models.nb2 import NB2PriorModel
from socialmgr.modelling.models.trailing_median import TrailingMedianModel
from socialmgr.modelling.priors.hand_set import HandSetPrior
from socialmgr.providers.fake import FakeOwnerProvider, FakePublicProvider

providers: Registry[DataProvider] = Registry("provider")
prior_sources: Registry[PriorSource] = Registry("prior source")
models: Registry[ForecastModel] = Registry("model")
labelers: Registry[Labeler] = Registry("labeler")


@providers.register("fake_owner")
def _fake_owner(settings: Settings) -> DataProvider:
    return FakeOwnerProvider()


@providers.register("fake_public")
def _fake_public(settings: Settings) -> DataProvider:
    return FakePublicProvider()


@prior_sources.register("hand_set")
def _hand_set(settings: Settings) -> PriorSource:
    return HandSetPrior()


@models.register("trailing_median")
def _trailing_median(settings: Settings) -> ForecastModel:
    return TrailingMedianModel()


@models.register("nb2")
def _nb2(settings: Settings) -> ForecastModel:
    return NB2PriorModel()


@labelers.register("claude_code")
def _claude_code(settings: Settings) -> Labeler:
    return ClaudeCodeLabeler()


@labelers.register("anthropic_api")
def _anthropic_api(settings: Settings) -> Labeler:
    key = settings.anthropic_api_key.get_secret_value() if settings.anthropic_api_key else None
    return AnthropicApiLabeler(api_key=key)


@labelers.register("none")
def _no_labeler(settings: Settings) -> Labeler:
    return NoLabeler()
