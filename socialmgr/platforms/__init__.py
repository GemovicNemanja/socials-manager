"""Platform profiles (MODEL.md §1.6): everything platform-specific lives in YAML, not code."""

from functools import cache
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict


class ColdStart(BaseModel):
    model_config = ConfigDict(frozen=True)

    window_h: float
    view_cap: int
    prior: tuple[float, float]
    follower_cap: int
    eligible_types: tuple[str, ...]


class PlatformProfile(BaseModel):
    model_config = ConfigDict(frozen=True)

    platform: str
    primary_horizon_h: float
    secondary_horizon_h: float
    owner_private_metrics_max_age_d: float
    cold_start: ColdStart | None
    type_reach: dict[str, tuple[float, float]]
    oon_factor: float
    author_diversity: tuple[float, float]
    capture_mode: str
    topic_consistency_window: int
    change_tracker_source: str | None
    url_weighted_length: int


@cache
def load_profile(platform: str) -> PlatformProfile:
    path = Path(__file__).with_name(f"{platform}.yaml")
    if not path.exists():
        raise LookupError(f"No platform profile for {platform!r} (expected {path.name})")
    return PlatformProfile.model_validate(yaml.safe_load(path.read_text()))
