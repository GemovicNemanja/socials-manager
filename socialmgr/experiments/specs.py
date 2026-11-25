"""Experiment protocols as data (MODEL.md §6.1, §6.9): designs, factors, levers and slot plans.

Pure: no database. The engine (``experiments/engine.py``) validates every proposal against these.
"""

from dataclasses import dataclass
from datetime import date, time
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from socialmgr.experiments.stats import DELTA

Design = Literal["per_post", "switchback"]
Actor = Literal["agent", "owner"]

MAX_FACTORS = 3  # MODEL §6.1: up to 3 per-post factors run factorially on the same posts
MAX_CONCURRENT = 3  # MODEL §6.9: a 4th concurrent experiment needs the owner
AUTO_START_MAX_WEEKS = 8.0  # MODEL §6.9 / plan §7: horizons over 8 weeks need the owner
MAX_DAYS_AHEAD = 7  # MODEL §6.1: slots are registered for whole local days, up to 7 days ahead
SWITCHBACK_BLOCK_DAYS = 4  # MODEL §6.1: 2 washout days, then 2 measured days
SWITCHBACK_WASHOUT_DAYS = 2


@dataclass(frozen=True)
class Lever:
    """A lever the agent may randomize. ``blinds`` are the slot fields a treatment-blind forecast
    must not see (they would reveal or depend on the arm)."""

    name: str
    level: Literal["per_post", "time"]
    blinds: tuple[str, ...]
    description: str


LEVERS: dict[str, Lever] = {
    lv.name: lv
    for lv in (
        Lever("format", "per_post", ("format", "has_url"), "post format (video, image, text, thread)"),
        Lever("media", "per_post", ("format",), "media attached to the post"),
        Lever("link_placement", "per_post", ("format", "has_url"), "where the link goes"),
        Lever("hook", "per_post", ("hook_type",), "the opening line"),
        Lever("cta", "per_post", ("cta",), "call to action"),
        Lever("content_kind", "per_post", ("content_kind", "topic"), "kind of content"),
        Lever("topic_ordering", "per_post", ("topic",), "which topic goes in which slot"),
        Lever("quote", "per_post", ("format",), "quote an in-niche account vs a standalone original"),
        Lever("thread_vs_long", "per_post", ("format",), "single long post vs a thread"),
        Lever("posting_window", "time", (), "when in the day posts go out"),
        Lever("frequency", "time", (), "originals per day"),
    )
}
# MODEL §6.9: account-level changes, money and other accounts always need the owner; v1 defers
# account-level designs altogether (plan §7), so these are refused outright.
ACCOUNT_LEVEL_LEVERS = frozenset(
    {"premium", "bio", "pinned_post", "reply_strategy", "follow_strategy", "reply_target", "mutual_follow"}
)
METRICS: dict[Design, tuple[str, ...]] = {
    "per_post": ("log_i48",),
    "switchback": ("daily_mean_log_i48", "daily_sum_i48"),
}


class AppliesTo(BaseModel):
    """Which slots a factor randomizes: post types, plus label filters on the slot's labels."""

    model_config = ConfigDict(frozen=True)

    post_types: list[str] = Field(default_factory=lambda: ["original", "quote"])
    labels: dict[str, list[str]] = Field(default_factory=dict)  # every field must match one value
    labels_not: dict[str, list[str]] = Field(default_factory=dict)  # no field may match

    def matches(self, post_type: str, labels: dict[str, str]) -> bool:
        if post_type not in self.post_types:
            return False
        for fld, values in self.labels.items():
            if labels.get(fld) not in values:
                return False
        return not any(labels.get(fld) in values for fld, values in self.labels_not.items())


class FactorSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(pattern=r"^[a-z][a-z0-9_]{0,31}$")
    lever: str
    control: str
    treatment: str
    applies_to: AppliesTo = Field(default_factory=AppliesTo)


class ExperimentSpec(BaseModel):
    """What ``propose_experiment`` takes for a custom experiment (suggestions build one too)."""

    model_config = ConfigDict(frozen=True)

    key: str | None = Field(default=None, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,31}$")
    name: str
    hypothesis: str
    design: Design = "per_post"
    factors: list[FactorSpec]
    primary_metric: str = "log_i48"
    secondary_metrics: list[str] = Field(default_factory=list)
    delta: float = DELTA
    horizon_weeks: float = Field(default=4.0, gt=0, le=52)
    seed: int | None = None

    @model_validator(mode="after")
    def _check(self) -> "ExperimentSpec":
        if not self.factors:
            raise ValueError("an experiment needs at least one factor")
        if len({f.name for f in self.factors}) != len(self.factors):
            raise ValueError("factor names must be unique")
        if self.design == "per_post" and len(self.factors) > MAX_FACTORS:
            raise ValueError(f"at most {MAX_FACTORS} factors run factorially (MODEL §6.1)")
        if self.design == "switchback" and len(self.factors) != 1:
            raise ValueError("a switchback randomizes exactly one time-level factor")
        if self.primary_metric not in METRICS[self.design]:
            raise ValueError(
                f"primary metric {self.primary_metric!r} isn't available for a {self.design} design "
                f"(choose from {', '.join(METRICS[self.design])})"
            )
        for f in self.factors:
            if f.lever in ACCOUNT_LEVEL_LEVERS:
                raise ValueError(
                    f"{f.lever} is an account-level lever: account-level designs are deferred in v1 "
                    "(plan §7) and always need the owner (MODEL §6.9)"
                )
            lever = LEVERS.get(f.lever)
            if self.design == "switchback" and (lever is None or lever.level != "time"):
                raise ValueError(f"a switchback randomizes a time-level lever, not {f.lever!r}")
            if self.design == "per_post" and lever is not None and lever.level != "per_post":
                raise ValueError(f"{f.lever} is a time-level lever: use a switchback design")
            if f.lever == "frequency":
                for v in (f.control, f.treatment):
                    if not v.isdigit() or int(v) < 1:
                        raise ValueError("frequency arms are posts per day, e.g. control '2', treatment '4'")
        return self


class SlotSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    time: time  # local time in the account's zone
    post_type: str = "original"
    labels: dict[str, str] = Field(default_factory=dict)  # e.g. {"content_kind": "demo"}


class DayPlan(BaseModel):
    """One whole local day of slots. ``treatment_slots`` is for switchbacks whose lever changes the
    slots themselves (posting window, frequency): the day's drawn condition picks the list."""

    model_config = ConfigDict(frozen=True)

    day: date
    slots: list[SlotSpec]
    treatment_slots: list[SlotSpec] | None = None

    @field_validator("slots")
    @classmethod
    def _non_empty(cls, v: list[SlotSpec]) -> list[SlotSpec]:
        if not v:
            raise ValueError("a day needs at least one slot")
        return v
