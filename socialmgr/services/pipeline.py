"""The data path of a run: collect, freeze as-of forecasts, score matured posts.

M1 wraps these functions as resumable run steps. They are written so that a forecast only ever
reads information available at the end of the last run before its post (MODEL.md §1.3).
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.core.types import (
    AccountRef,
    CollectionRequest,
    ForecastSubject,
    PredictiveDraws,
    PriorContext,
    TrainingExample,
)
from socialmgr.db.models import (
    Account,
    Cost,
    FollowerSnapshot,
    Forecast,
    Label,
    MetricSnapshot,
    ModelVersion,
    Post,
    Run,
    ScoreRow,
)
from socialmgr.modelling import maturity, scoring
from socialmgr.services import draws
from socialmgr.services.hashing import sha256_json, sha256_text
from socialmgr.services.plugins import ActivePlugins

FEATURE_SET_VERSION = "fs-0"
LABEL_SET_VERSION = "provider-hints-0"
CODE_VERSION = "m0"
OWNER_TERMS_MIN_POSTS = 20  # MODEL §2a minimum owner posts before owner-level terms (A22)
LATE_MATURITY = timedelta(days=14)
QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)


# ---------------------------------------------------------------- accounts and runs


def ensure_accounts(session: Session, settings: Settings, now: datetime) -> list[Account]:
    """Create the owner (and any configured peers) on first use."""
    wanted = [(settings.account.platform, settings.account.handle, "owner")]
    wanted += [(p.platform, p.handle, p.role) for p in settings.peers]
    accounts = []
    for platform, handle, role in wanted:
        acct = session.scalar(
            select(Account).where(
                Account.workspace_id == settings.workspace_id,
                Account.platform == platform,
                Account.handle == handle,
            )
        )
        if acct is None:
            acct = Account(
                workspace_id=settings.workspace_id,
                platform=platform,
                handle=handle,
                role=role,
                tz=settings.account.tz if role == "owner" else "UTC",
                premium=settings.account.premium if role == "owner" else False,
                tracked_since=now,
            )
            session.add(acct)
            session.flush()
        accounts.append(acct)
    return accounts


def owner_account(session: Session, workspace_id: int) -> Account | None:
    return session.scalar(
        select(Account).where(Account.workspace_id == workspace_id, Account.role == "owner").limit(1)
    )


def last_finished_run(session: Session, workspace_id: int, before: datetime | None = None) -> Run | None:
    q = select(Run).where(Run.workspace_id == workspace_id, Run.status == "finished")
    if before is not None:
        q = q.where(Run.cutoff_at < before)
    return session.scalar(q.order_by(Run.cutoff_at.desc()).limit(1))


def start_run(session: Session, settings: Settings, trigger: str, cutoff: datetime, now: datetime) -> Run:
    prev = last_finished_run(session, settings.workspace_id)
    run = Run(
        workspace_id=settings.workspace_id,
        trigger=trigger,
        started_at=now,
        cutoff_at=cutoff,
        status="running",
        gap_since_prev_h=(cutoff - prev.cutoff_at).total_seconds() / 3600 if prev else None,
        steps={},
    )
    session.add(run)
    session.flush()
    return run


def finish_run(session: Session, run: Run, now: datetime) -> None:
    run.finished_at = now
    run.status = "finished"
    run.spend_usd = float(
        session.scalar(select(func.coalesce(func.sum(Cost.amount_usd), 0.0)).where(Cost.run_id == run.id))
        or 0.0
    )
    session.flush()


# ---------------------------------------------------------------- collection


@dataclass(frozen=True)
class CollectSummary:
    new_posts: int
    snapshots: int
    cost_usd: float
    caveats: tuple[str, ...] = ()


def _followers_before(session: Session, account_id: int, t: datetime) -> int | None:
    """Follower count from the last run before ``t``: carried forward, never interpolated (MODEL A21)."""
    return session.scalar(
        select(FollowerSnapshot.followers)
        .where(FollowerSnapshot.account_id == account_id, FollowerSnapshot.fetched_at < t)
        .order_by(FollowerSnapshot.fetched_at.desc())
        .limit(1)
    )


def collect(session: Session, settings: Settings, plugins: ActivePlugins, run: Run) -> CollectSummary:
    accounts = list(
        session.scalars(select(Account).where(Account.workspace_id == settings.workspace_id, Account.active))
    )
    prev = last_finished_run(session, settings.workspace_id, before=run.cutoff_at)
    new_posts = snapshots = 0
    cost = 0.0
    follower_counts: dict[int, int] = {}
    new_follower_counts: dict[int, int] = {}
    caveats: list[str] = []
    for provider in plugins.providers:
        refs = tuple(
            AccountRef(
                account_id=a.id, platform=a.platform, handle=a.handle, role=a.role, external_id=a.external_id
            )
            for a in accounts
            if a.role in provider.roles
        )
        if not refs:
            continue
        batch = provider.collect(
            CollectionRequest(accounts=refs, since=prev.cutoff_at if prev else None, until=run.cutoff_at)
        )
        for fa in batch.accounts:
            acct = session.get(Account, fa.account_id)
            if acct is not None and acct.external_id is None:
                acct.external_id = fa.external_id
            follower_counts.setdefault(fa.account_id, fa.followers)
            known = next((r.known_follower_ids for r in refs if r.account_id == fa.account_id), ())
            if fa.new_follower_ids is not None and known:  # without known ids it is only a baseline
                new_follower_counts.setdefault(fa.account_id, len(fa.new_follower_ids))
        caveats += [f"{batch.provider}: {c}" for c in batch.caveats]

        post_ids: dict[str, int] = {}
        for fp in batch.posts:
            existing = session.scalar(
                select(Post.id).where(Post.account_id == fp.account_id, Post.external_id == fp.external_id)
            )
            if existing is not None:
                post_ids[fp.external_id] = existing
                continue
            post = Post(
                workspace_id=settings.workspace_id,
                account_id=fp.account_id,
                external_id=fp.external_id,
                created_at=fp.created_at,
                type=fp.type,
                conversation_id=fp.conversation_id,
                in_reply_to_user_id=fp.in_reply_to_user_id,
                text=fp.text,
                text_hash=sha256_text(fp.text),
                has_url=fp.has_url,
                media={"kind": fp.media_kind} if fp.media_kind else None,
                followers_at_post=_followers_before(session, fp.account_id, fp.created_at),
                first_seen_run_id=run.id,
            )
            session.add(post)
            session.flush()
            post_ids[fp.external_id] = post.id
            new_posts += 1
            for fld, value in (("format", fp.format_hint), ("topic", fp.topic_hint)):
                if value:
                    session.add(
                        Label(
                            workspace_id=settings.workspace_id,
                            post_id=post.id,
                            label_set_version=LABEL_SET_VERSION,
                            field=fld,
                            value=value,
                            source="provider",
                            confidence=None,
                            created_at=run.cutoff_at,
                        )
                    )

        for fm in batch.metrics:
            pid = post_ids.get(fm.post_external_id) or session.scalar(
                select(Post.id).where(Post.external_id == fm.post_external_id)
            )
            if pid is None:
                continue
            created = session.scalar(select(Post.created_at).where(Post.id == pid))
            assert created is not None
            session.add(
                MetricSnapshot(
                    workspace_id=settings.workspace_id,
                    post_id=pid,
                    run_id=run.id,
                    provider=batch.provider,
                    impressions_source=fm.impressions_source,
                    fetched_at=fm.fetched_at,
                    age_s=int((fm.fetched_at - created).total_seconds()),
                    impressions=fm.impressions,
                    likes=fm.likes,
                    reposts=fm.reposts,
                    quotes=fm.quotes,
                    replies=fm.replies,
                    bookmarks=fm.bookmarks,
                    profile_clicks=fm.profile_clicks,
                    link_clicks=fm.link_clicks,
                )
            )
            snapshots += 1

        if batch.cost_usd:
            session.add(
                Cost(
                    workspace_id=settings.workspace_id,
                    run_id=run.id,
                    provider=batch.provider,
                    item="collection",
                    amount_usd=batch.cost_usd,
                    simulated=batch.provider.startswith("fake"),
                    incurred_at=run.cutoff_at,
                )
            )
            cost += batch.cost_usd

    # Follower counts are written after new posts took their as-of count from earlier runs.
    for account_id, followers in follower_counts.items():
        session.add(
            FollowerSnapshot(
                workspace_id=settings.workspace_id,
                account_id=account_id,
                run_id=run.id,
                fetched_at=run.cutoff_at,
                followers=followers,
                new_followers=new_follower_counts.get(account_id),
            )
        )
    session.flush()
    return CollectSummary(
        new_posts=new_posts, snapshots=snapshots, cost_usd=round(cost, 6), caveats=tuple(caveats)
    )


# ---------------------------------------------------------------- forecasts


def _post_labels(session: Session, post_id: int) -> dict[str, str]:
    rows = session.execute(
        select(Label.field, Label.value).where(Label.post_id == post_id).order_by(Label.created_at)
    ).all()
    return {f: v for f, v in rows}


def matured_examples(session: Session, workspace_id: int, cutoff: datetime) -> list[TrainingExample]:
    """Outcomes visible at ``cutoff``: each post's first capture at age >= 48h fetched by then."""
    first_mature = (
        select(MetricSnapshot.post_id, func.min(MetricSnapshot.age_s).label("age_s"))
        .where(
            MetricSnapshot.workspace_id == workspace_id,
            MetricSnapshot.fetched_at <= cutoff,
            MetricSnapshot.age_s >= int(maturity.PRIMARY_AGE_H * 3600),
        )
        .group_by(MetricSnapshot.post_id)
        .subquery()
    )
    rows = session.execute(
        select(Post, Account.role, MetricSnapshot.impressions, MetricSnapshot.age_s)
        .join(first_mature, first_mature.c.post_id == Post.id)
        .join(
            MetricSnapshot,
            (MetricSnapshot.post_id == Post.id) & (MetricSnapshot.age_s == first_mature.c.age_s),
        )
        .join(Account, Account.id == Post.account_id)
        .where(Post.deleted_at.is_(None))
    ).all()
    examples = []
    for post, role, impressions, age_s in rows:
        if impressions is None:
            continue
        estimate = maturity.estimate_i48(impressions, age_s / 3600)
        if estimate is None:
            continue
        examples.append(
            TrainingExample(
                post_external_id=post.external_id,
                account_role=role,
                created_at=post.created_at,
                type=post.type,
                followers_at_post=post.followers_at_post or 0,
                outcome_i48=round(estimate, 3),
            )
        )
    return sorted(examples, key=lambda e: (e.created_at, e.post_external_id))


def _model_version_row(session: Session, workspace_id: int, version: str, family: str, prior: str) -> None:
    if session.scalar(select(ModelVersion.id).where(ModelVersion.version == version)) is None:
        session.add(
            ModelVersion(
                workspace_id=workspace_id,
                version=version,
                family=family,
                prior_source=prior,
                feature_set_version=FEATURE_SET_VERSION,
                code_version=CODE_VERSION,
                role="champion",
                created_at=datetime.now(UTC),
            )
        )


def build_manifest(session: Session, workspace_id: int, post: Post) -> dict[str, Any]:
    """The as-of input manifest: everything the forecast may use, named by snapshot run."""
    snapshot = last_finished_run(session, workspace_id, before=post.created_at)
    cutoff = snapshot.cutoff_at if snapshot else None
    examples = matured_examples(session, workspace_id, cutoff) if cutoff else []
    followers_at_snapshot = (
        session.scalar(
            select(FollowerSnapshot.followers).where(
                FollowerSnapshot.run_id == snapshot.id, FollowerSnapshot.account_id == post.account_id
            )
        )
        if snapshot
        else None
    )
    labels = _post_labels(session, post.id)
    return {
        "snapshot_run_id": snapshot.id if snapshot else None,
        "cutoff": cutoff.isoformat() if cutoff else None,
        "feature_set_version": FEATURE_SET_VERSION,
        "curve_version": maturity.PRIOR_CURVE_VERSION,
        "subject": {
            "subject_id": f"post:{post.id}",
            "created_at": post.created_at.isoformat(),
            "type": post.type,
            "followers_at_post": followers_at_snapshot or 0,
            "has_url": post.has_url,
            "format": labels.get("format"),
            "topic": labels.get("topic"),
        },
        "training": [e.model_dump(mode="json") for e in examples],
        "seed": snapshot.id if snapshot else 0,
    }


def forecast_from_manifest(
    plugins: ActivePlugins, manifest: dict[str, Any]
) -> tuple[str, str, PredictiveDraws]:
    """Recompute a forecast from its manifest alone. Returns (model_version, kind, draws)."""
    training = [TrainingExample.model_validate(e) for e in manifest["training"]]
    subject_raw = manifest["subject"]
    subject = ForecastSubject(
        subject_id=subject_raw["subject_id"],
        created_at=datetime.fromisoformat(subject_raw["created_at"]),
        type=subject_raw["type"],
        followers_at_post=subject_raw["followers_at_post"],
        has_url=subject_raw["has_url"],
        format=subject_raw["format"],
        topic=subject_raw["topic"],
    )
    owner_n = sum(1 for e in training if e.account_role == "owner")
    prior = plugins.prior_source.prior(
        PriorContext(platform="x", followers=subject.followers_at_post, owner_matured_posts=owner_n)
    )
    fitted = plugins.champion.fit(training, prior, seed=int(manifest["seed"]))
    predicted = fitted.predict(subject)
    kind = "as_of" if owner_n >= OWNER_TERMS_MIN_POSTS else "prior_only_uncalibrated"
    return fitted.version, kind, predicted


def freeze_as_of_forecasts(
    session: Session, settings: Settings, plugins: ActivePlugins, now: datetime
) -> int:
    """Give every owner post without a forecast its as-of forecast. Forecasts are never edited."""
    owner = owner_account(session, settings.workspace_id)
    if owner is None:
        return 0
    frozen = set(
        session.scalars(select(Forecast.subject).where(Forecast.workspace_id == settings.workspace_id))
    )
    pending = [
        post
        for post in session.scalars(
            select(Post)
            .where(Post.account_id == owner.id, Post.deleted_at.is_(None))
            .order_by(Post.created_at)
        )
        if f"post:{post.id}" not in frozen
    ]
    for post in pending:
        manifest = build_manifest(session, settings.workspace_id, post)
        version, kind, predicted = forecast_from_manifest(plugins, manifest)
        manifest = {**manifest, "model_version": version}
        digest = draws.store(session, predicted.samples, settings.workspace_id)
        _model_version_row(
            session, settings.workspace_id, version, plugins.champion.family, plugins.prior_source.name
        )
        session.add(
            Forecast(
                workspace_id=settings.workspace_id,
                subject=f"post:{post.id}",
                kind=kind,
                model_version=version,
                curve_version=maturity.PRIOR_CURVE_VERSION,
                snapshot_run_id=manifest["snapshot_run_id"],
                metric="impressions",
                horizon_h=48.0,
                quantiles={f"q{round(q * 100):02d}": predicted.quantile(q) for q in QUANTILES},
                p_ge_goal=None,
                input_manifest=manifest,
                manifest_hash=sha256_json(manifest),
                draws_hash=digest,
                frozen_at=now,
            )
        )
    session.flush()
    return len(pending)


# ---------------------------------------------------------------- scoring


def score_matured(session: Session, settings: Settings, run: Run, now: datetime) -> int:
    """Score each unscored forecast once its post has a capture at age >= 48h."""
    scored_ids = select(ScoreRow.forecast_id)
    forecasts = session.scalars(
        select(Forecast).where(Forecast.workspace_id == settings.workspace_id, Forecast.id.not_in(scored_ids))
    ).all()
    count = 0
    for fc in forecasts:
        post_id = int(fc.subject.split(":", 1)[1])
        capture = session.scalar(
            select(MetricSnapshot)
            .where(
                MetricSnapshot.post_id == post_id,
                MetricSnapshot.age_s >= int(maturity.PRIMARY_AGE_H * 3600),
                MetricSnapshot.impressions.is_not(None),
            )
            .order_by(MetricSnapshot.age_s)
            .limit(1)
        )
        if capture is None or capture.impressions is None:
            continue
        age_h = capture.age_s / 3600
        outcome = maturity.estimate_i48(capture.impressions, age_h)
        if outcome is None:
            continue
        samples = draws.load(session, fc.draws_hash)
        s = scoring.score(samples, outcome, seed=post_id)
        session.add(
            ScoreRow(
                workspace_id=settings.workspace_id,
                forecast_id=fc.id,
                run_id=run.id,
                outcome=round(outcome, 3),
                capture_age_h=round(age_h, 3),
                curve_version=fc.curve_version,
                exact=False,
                late_maturity=age_h > LATE_MATURITY.total_seconds() / 3600,
                pit_mid=s.pit_mid,
                pit_rand=s.pit_rand,
                crps_log=s.crps_log,
                in_80=s.in_80,
                in_50=s.in_50,
                scored_at=now,
            )
        )
        count += 1
    session.flush()
    return count
