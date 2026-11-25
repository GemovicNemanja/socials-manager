"""Resolve the configured implementations of each extension interface."""

from dataclasses import dataclass

from socialmgr import plugins
from socialmgr.core.config import Settings
from socialmgr.core.interfaces import DataProvider, ForecastModel, PriorSource


@dataclass(frozen=True)
class ActivePlugins:
    providers: tuple[DataProvider, ...]
    prior_source: PriorSource
    champion: ForecastModel
    challengers: tuple[ForecastModel, ...]


def load_plugins(settings: Settings) -> ActivePlugins:
    cfg = settings.plugins
    return ActivePlugins(
        providers=tuple(plugins.providers.resolve(name, settings) for name in cfg.providers),
        prior_source=plugins.prior_sources.resolve(cfg.prior_source, settings),
        champion=plugins.models.resolve(cfg.champion_model, settings),
        challengers=tuple(plugins.models.resolve(name, settings) for name in cfg.challenger_models),
    )
