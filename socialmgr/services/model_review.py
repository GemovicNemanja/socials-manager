"""The run's model review (MODEL §5; plan §6 "At each run": refit, evaluate, promote or hold).

From 20 matured owner posts, and then every 21 more, the owner-level NB2 is backtested (rolling
origin over the owner's matured posts, each fitted on what was visible at its creation) against
the trailing-median baseline, and the release gates decide. A failed gate keeps the previous
champion. Every review is stored with its gates; the champion is the latest review's choice.
"""

import math
from datetime import datetime
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.core.types import PriorContext
from socialmgr.db.models import MetricSnapshot, ModelReview, Post, Run
from socialmgr.modelling import maturity
from socialmgr.modelling.backtest import BacktestPost, run_backtest
from socialmgr.modelling.models.nb2_owner import NB2OwnerModel
from socialmgr.modelling.monitors import location_chart, pit_to_z
from socialmgr.modelling.release import GateThresholds, evaluate_release, week_block_bootstrap_skill
from socialmgr.services import pipeline
from socialmgr.services.plugins import ActivePlugins

CANDIDATE = "nb2_owner"
REVIEW_EVERY = 21  # MODEL §4.3's diagnostic cadence
# Owner terms start at 20 matured posts, and the backtest scores each later post, so the gates (which
# need 20 scored posts) can first pass at 40: the first review waits until then.
FIRST_REVIEW_AT = 2 * pipeline.OWNER_TERMS_MIN_POSTS
LATE_S = 14 * 86400
# The gate-4 CUSUM runs over the diagnostic window: over a whole long backtest its false-hold chance
# grows with length (13% at 57 posts, 63% at 300 for calibrated forecasts).
THRESHOLDS = GateThresholds(cusum_window=63)
GATE_LABELS = {
    "backtest_size": "Enough scored posts",
    "crps_skill": "CRPS skill vs trailing median",
    "coverage": "50% and 80% coverage in band",
    "pit_calibration": "Berkowitz and PIT chi-square",
    "cusum": "No CUSUM alarm in the backtest",
    "width": "80% width within 1.1x baseline",
    "sampler": "R-hat, ESS and SBC",
    "brier": "Brier no worse than baseline",
}


def _backtest_model() -> NB2OwnerModel:
    """A lighter sampler for the rolling refits; gate 6 reads a full-settings fit."""
    return NB2OwnerModel(chains=4, warmup=300, draws=500)


def cusum_first_alarm(pits: Any) -> int | None:
    chart = location_chart()
    for i, pit in enumerate(pits):
        chart, alarm = chart.update(pit_to_z(float(pit), 0.5 / 1001), armed=True)
        if alarm is not None:
            return i
    return None


def backtest_posts(session: Session, settings: Settings, run: Run) -> list[BacktestPost]:
    ws = settings.workspace_id
    curve = pipeline.curve_as_of(session, ws, run.cutoff_at)
    examples = [
        e for e in pipeline.matured_examples(session, ws, run.cutoff_at, curve) if e.account_role == "owner"
    ]
    owner = pipeline.owner_account(session, ws)
    if owner is None:
        return []
    first = session.execute(
        select(
            Post.external_id, Post.id, Post.has_url, func.min(Run.cutoff_at), func.min(MetricSnapshot.age_s)
        )
        .join(MetricSnapshot, MetricSnapshot.post_id == Post.id)
        .join(Run, Run.id == MetricSnapshot.run_id)
        .where(
            Post.account_id == owner.id,
            Run.cutoff_at <= run.cutoff_at,
            MetricSnapshot.age_s >= int(maturity.PRIMARY_AGE_H * 3600),
        )
        .group_by(Post.id)
    ).all()
    seen = {ext: (pid, url, visible, age) for ext, pid, url, visible, age in first}
    out = []
    for e in examples:
        if e.post_external_id not in seen:
            continue
        pid, url, visible, age = seen[e.post_external_id]
        out.append(
            BacktestPost(
                example=e, visible_at=visible, has_url=bool(url), pit_seed=pid, late_maturity=age > LATE_S
            )
        )
    return out


def _due(session: Session, ws: int, owner_n: int, champion: str) -> bool:
    last = session.scalar(
        select(ModelReview).where(ModelReview.workspace_id == ws).order_by(ModelReview.id.desc()).limit(1)
    )
    if owner_n < FIRST_REVIEW_AT:
        return False
    return last is None or owner_n - last.owner_n >= REVIEW_EVERY


def _summary(bt: Any, seed: int, diag: Any) -> dict[str, Any]:
    rows = bt.gate_rows
    n = len(rows)
    out: dict[str, Any] = {"n": n, "candidate_version": bt.candidate_version}
    if n:
        cand = np.array([r.candidate.crps_log for r in rows])
        base = np.array([r.baseline.crps_log for r in rows])
        out["crps_skill"] = float(1 - cand.mean() / base.mean()) if base.mean() > 0 else None
        boot = week_block_bootstrap_skill(rows, 2000, seed)
        boot = boot[np.isfinite(boot)]
        if boot.size:
            out["crps_skill_ci90"] = [float(np.quantile(boot, 0.05)), float(np.quantile(boot, 0.95))]
        out["cov50"] = float(np.mean([r.candidate.in_50 for r in rows]))
        out["cov80"] = float(np.mean([r.candidate.in_80 for r in rows]))
        bw = float(np.mean([r.baseline.width_80 for r in rows]))
        out["width_ratio"] = float(np.mean([r.candidate.width_80 for r in rows]) / bw) if bw > 0 else None
    if diag is not None and "beta" in diag.posterior_mean:
        m, sd = diag.posterior_mean["beta"], diag.posterior_sd.get("beta", 0.0)
        out["elasticity"] = {"q50": m, "q10": m - 1.2816 * sd, "q90": m + 1.2816 * sd}
    return out


def run_step(
    session: Session, settings: Settings, plugins: ActivePlugins, run: Run, now: datetime
) -> dict[str, Any]:
    ws = settings.workspace_id
    champion = pipeline.champion_at(session, ws, run.cutoff_at) or plugins.champion_name
    posts = backtest_posts(session, settings, run)
    owner_n = len(posts)
    if not _due(session, ws, owner_n, champion):
        return {"decision": "not_due", "owner_n": owner_n, "champion": champion}
    model = plugins.model(CANDIDATE)
    bt = run_backtest(posts, _backtest_model(), plugins.prior_source, seed=run.id)
    prior = plugins.prior_source.prior(
        PriorContext(
            platform=settings.account.platform,
            followers=posts[-1].example.followers_at_post if posts else 0,
            owner_matured_posts=owner_n,
        )
    )
    full = model.fit([p.example for p in posts], prior, seed=run.id)
    diag = getattr(full, "diagnostics", None)
    decision = evaluate_release(
        bt,
        previous_version=champion,
        diagnostics=diag,
        candidate_version=full.version,
        thresholds=THRESHOLDS,
        seed=run.id,
        cusum=cusum_first_alarm,
    )
    already = champion == CANDIDATE
    if already:
        verdict, after = "kept", champion
        reason = ("gates pass" if decision.promote else decision.reason) + "; champion unchanged"
    elif decision.promote:
        verdict, after, reason = "promoted", CANDIDATE, decision.reason
    else:
        verdict, after, reason = "held", champion, decision.reason
    gates = [
        {
            "key": g.name,
            "label": GATE_LABELS.get(g.name, g.name),
            "passed": g.passed,
            "value": g.value if g.value is None or math.isfinite(g.value) else None,
            "threshold": g.threshold,
            "detail": g.detail,
        }
        for g in decision.gates
    ]
    session.add(
        ModelReview(
            workspace_id=ws,
            run_id=run.id,
            owner_n=owner_n,
            candidate_plugin=CANDIDATE,
            candidate_version=full.version,
            champion_before=champion,
            champion_after=after,
            decision=verdict,
            reason=reason,
            gates=gates,
            backtest=_summary(bt, run.id, diag),
            created_at=now,
        )
    )
    session.flush()
    return {"decision": verdict, "reason": reason, "owner_n": owner_n, "champion": after}
