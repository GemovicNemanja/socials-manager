"""Resolve the configured implementations of each extension interface."""

from dataclasses import dataclass, replace

from socialmgr import plugins
from socialmgr.core.config import Settings
from socialmgr.core.interfaces import DataProvider, ForecastModel, Labeler, PriorSource


@dataclass(frozen=True)
class ActivePlugins:
    providers: tuple[DataProvider, ...]
    prior_source: PriorSource
    champion: ForecastModel
    challengers: tuple[ForecastModel, ...]
    labeler: Labeler
    # Registry name of ``champion``. Frozen into each forecast manifest so a forecast recomputes
    # with the model that made it, whichever model is champion later (MODEL §5 promotion).
    champion_name: str = "nb2"
    settings: Settings | None = None  # passed to model factories resolved later
    # True when the champion is fixed by the caller (an experiment's frozen model) rather than
    # the latest promotion review.
    champion_pinned: bool = False

    def model(self, name: str) -> ForecastModel:
        """The model registered as ``name`` (the champion's instance when it is the champion)."""
        if name == self.champion_name:
            return self.champion
        return plugins.models.resolve(name, self.settings or Settings())


def load_plugins(settings: Settings) -> ActivePlugins:
    cfg = settings.plugins
    return ActivePlugins(
        providers=tuple(plugins.providers.resolve(name, settings) for name in cfg.providers),
        prior_source=plugins.prior_sources.resolve(cfg.prior_source, settings),
        champion=plugins.models.resolve(cfg.champion_model, settings),
        challengers=tuple(plugins.models.resolve(name, settings) for name in cfg.challenger_models),
        labeler=plugins.labelers.resolve(cfg.labeler, settings),
        champion_name=cfg.champion_model,
        settings=settings,
    )


def with_champion(
    plugins_: ActivePlugins, settings: Settings, name: str, *, pinned: bool = False
) -> ActivePlugins:
    """The same plugins with ``name`` as champion (after a promotion, or a protocol's frozen model)."""
    model = plugins_.champion if name == plugins_.champion_name else plugins.models.resolve(name, settings)
    return replace(plugins_, champion=model, champion_name=name, champion_pinned=pinned)
