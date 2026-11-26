# ruff: noqa: E501  (report strings)
"""The external backtest: the app's models on other accounts' real public posts, by size band.

For each account, posts older than 3 days stand in for matured outcomes (their current impression
count is close to the 48h value for most posts). Two questions per account:

1. **Starting assumptions** (the prior-only model every new account starts with): does each post's
   outcome land inside its 50% and 80% range at the expected rate, and is the typical post over- or
   under-forecast?
2. **The owner-level model**: a rolling-origin backtest (each post forecast from the posts visible
   before it) against the trailing-median baseline, with the same release gates as the owner's own
   account.

Limits, reported with the results: the follower count at each post is today's count (history isn't
public), current totals stand in for 48h values, and one account per band is an anecdote, not a
sample.
"""

import math
import statistics
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np

from socialmgr.core.types import ForecastSubject, PriorContext, TrainingExample
from socialmgr.modelling import scoring
from socialmgr.modelling.backtest import BacktestPost, run_backtest
from socialmgr.modelling.models.nb2 import NB2PriorModel
from socialmgr.modelling.models.nb2_owner import NB2OwnerModel
from socialmgr.modelling.priors.hand_set import HandSetPrior
from socialmgr.modelling.release import GateThresholds, evaluate_release
from socialmgr.providers.x.provider import classify, has_external_url

MATURE_AFTER = timedelta(days=3)
BANDS = (
    ("under 1k", 0, 1_000),
    ("1k-10k", 1_000, 10_000),
    ("10k-100k", 10_000, 100_000),
    ("100k+", 100_000, None),
)


def band_of(followers: int) -> str:
    for name, lo, hi in BANDS:
        if followers >= lo and (hi is None or followers < hi):
            return name
    return BANDS[-1][0]


@dataclass
class AccountResult:
    handle: str
    followers: int
    band: str
    posts_read: int
    posts_used: int
    types: dict[str, int]
    prior_cov50: float | None = None
    prior_cov80: float | None = None
    prior_median_ratio: float | None = None  # median of outcome / forecast median (1 = unbiased)
    prior_pit_low: float | None = None  # share of posts with PIT < 0.1 (forecast far too high)
    prior_pit_high: float | None = None  # share with PIT > 0.9 (forecast far too low)
    owner_scored: int = 0
    owner_cov50: float | None = None
    owner_cov80: float | None = None
    crps_skill: float | None = None
    skill_ci90: tuple[float, float] | None = None
    gates: list[dict[str, Any]] = field(default_factory=list)
    promote: bool | None = None
    reason: str = ""
    notes: list[str] = field(default_factory=list)


def examples_from_timeline(
    tweets: list[dict[str, Any]], user: dict[str, Any], now: datetime
) -> list[BacktestPost]:
    followers = int((user.get("public_metrics") or {}).get("followers_count") or 0)
    out = []
    for t in tweets:
        created = datetime.fromisoformat(t["created_at"].replace("Z", "+00:00")).astimezone(UTC)
        if now - created < MATURE_AFTER:
            continue
        impressions = (t.get("public_metrics") or {}).get("impression_count")
        if impressions is None:
            continue
        ptype = classify(t, str(user["id"]))
        if ptype == "repost":
            continue
        ex = TrainingExample(
            post_external_id=str(t["id"]),
            account_role="owner",  # the model treats the account under test as its own
            created_at=created,
            type=ptype,
            followers_at_post=followers,
            outcome_i48=float(impressions),
            has_url=has_external_url(t),
        )
        out.append(
            BacktestPost(
                example=ex,
                visible_at=created + timedelta(hours=48),
                has_url=ex.has_url,
                pit_seed=int(t["id"]) % 2**31,
            )
        )
    return sorted(out, key=lambda p: p.example.created_at)


def prior_only_check(posts: list[BacktestPost]) -> dict[str, float | None]:
    """How the starting assumptions (no account history) fit each post."""
    prior_source = HandSetPrior()
    model = NB2PriorModel()
    pits, in50, in80, ratios = [], [], [], []
    for p in posts:
        e = p.example
        prior = prior_source.prior(
            PriorContext(platform="x", followers=e.followers_at_post, owner_matured_posts=0)
        )
        fitted = model.fit([], prior, seed=1)
        draws = fitted.predict(
            ForecastSubject(
                subject_id=f"bt:{e.post_external_id}",
                created_at=e.created_at,
                type=e.type,
                followers_at_post=e.followers_at_post,
                has_url=e.has_url,
            )
        ).samples
        s = scoring.score(draws, e.outcome_i48, seed=p.pit_seed or 0)
        pits.append(s.pit_mid)
        in50.append(s.in_50)
        in80.append(s.in_80)
        med = float(np.median(draws))
        ratios.append((e.outcome_i48 + 1) / (med + 1))
    if not posts:
        return {}
    return {
        "cov50": float(np.mean(in50)),
        "cov80": float(np.mean(in80)),
        "median_ratio": float(statistics.median(ratios)),
        "pit_low": float(np.mean([x < 0.1 for x in pits])),
        "pit_high": float(np.mean([x > 0.9 for x in pits])),
    }


def run_account(
    handle: str, user: dict[str, Any], tweets: list[dict[str, Any]], now: datetime
) -> AccountResult:
    posts = examples_from_timeline(tweets, user, now)
    followers = int((user.get("public_metrics") or {}).get("followers_count") or 0)
    types: dict[str, int] = {}
    for p in posts:
        types[p.example.type] = types.get(p.example.type, 0) + 1
    r = AccountResult(
        handle=handle,
        followers=followers,
        band=band_of(followers),
        posts_read=len(tweets),
        posts_used=len(posts),
        types=types,
    )
    prior = prior_only_check(posts)
    if prior:
        r.prior_cov50, r.prior_cov80 = prior["cov50"], prior["cov80"]
        r.prior_median_ratio, r.prior_pit_low, r.prior_pit_high = (
            prior["median_ratio"],
            prior["pit_low"],
            prior["pit_high"],
        )
    if len(posts) < 40:
        r.notes.append(f"only {len(posts)} usable posts: too few for the owner-level backtest (needs 40)")
        return r
    bt = run_backtest(posts, NB2OwnerModel(chains=4, warmup=300, draws=500), HandSetPrior(), seed=7)
    rows = bt.gate_rows
    r.owner_scored = len(rows)
    if rows:
        r.owner_cov50 = float(np.mean([x.candidate.in_50 for x in rows]))
        r.owner_cov80 = float(np.mean([x.candidate.in_80 for x in rows]))
    decision = evaluate_release(
        bt, previous_version="nb2@prior", thresholds=GateThresholds(cusum_window=63), seed=7
    )
    r.promote, r.reason = decision.promote, decision.reason
    r.gates = [
        {"name": g.name, "passed": g.passed, "value": g.value, "detail": g.detail} for g in decision.gates
    ]
    skill = next((g for g in decision.gates if g.name == "crps_skill"), None)
    if skill is not None and skill.value is not None and math.isfinite(skill.value):
        r.crps_skill = skill.value
    return r


def _pct(x: float | None) -> str:
    return f"{x:.0%}" if x is not None else "—"


def report(results: list[AccountResult], now: datetime) -> str:
    lines = [
        f"# External backtest ({now:%Y-%m-%d})",
        "",
        "The app's models on other accounts' real public posts. One account per size band, so read each row "
        "as an anecdote, not a sample.",
        "",
        "## 1. Starting assumptions (the forecasts every new account gets)",
        "",
        "Targets: 50% of outcomes inside the 50% range and 80% inside the 80% range; a typical outcome/forecast "
        "ratio near 1x.",
        "",
        "| Account | Band | Followers | Posts used | Inside 50% | Inside 80% | Typical outcome / forecast | Far above (PIT > 0.9) | Far below (PIT < 0.1) |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        ratio = f"{r.prior_median_ratio:.2f}x" if r.prior_median_ratio is not None else "—"
        lines.append(
            f"| @{r.handle} | {r.band} | {r.followers:,} | {r.posts_used} | {_pct(r.prior_cov50)} | {_pct(r.prior_cov80)} "
            f"| {ratio} | {_pct(r.prior_pit_high)} | {_pct(r.prior_pit_low)} |"
        )
    lines += [
        "",
        "## 2. The owner-level model, as if each account were yours",
        "",
        "Each post forecast only from the account's earlier posts; the same release gates as your account.",
        "",
        "| Account | Scored posts | Inside 50% | Inside 80% | CRPS skill vs trailing median | Would be promoted | Failed gates |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        failed = ", ".join(g["name"] for g in r.gates if g["passed"] is False) or (
            "—" if r.gates else "not run"
        )
        skill = f"{r.crps_skill:+.2f}" if r.crps_skill is not None else "—"
        promoted = "yes" if r.promote else ("no" if r.promote is False else "—")
        lines.append(
            f"| @{r.handle} | {r.owner_scored} | {_pct(r.owner_cov50)} | {_pct(r.owner_cov80)} | {skill} | {promoted} | {failed} |"
        )
    notes = [f"- @{r.handle}: {n}" for r in results for n in r.notes]
    lines += [
        "",
        "## Limits",
        "",
        "- The follower count at each post is today's count; follower history isn't public.",
        "- Current impression totals stand in for 48-hour values (posts younger than 3 days are left out).",
        "- Post types: "
        + "; ".join(
            f"@{r.handle} " + ", ".join(f"{k} {v}" for k, v in sorted(r.types.items())) for r in results
        ),
        *notes,
        "",
    ]
    return "\n".join(lines)
