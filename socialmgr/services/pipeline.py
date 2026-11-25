"""The data path of a run: collect, freeze forecasts, score matured posts, refit the maturity curve.

The run orchestrator (``services/runs.py``) calls these as resumable steps. They are written so that
a forecast only ever reads information available at the end of the last run before its post
(MODEL.md §1.3), and every forecast can be recomputed bit-for-bit from its stored manifest.
"""

from dataclasses import dataclass, field
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
    FollowerId,
    FollowerSnapshot,
    Forecast,
    Label,
    MaturityCurve,
    MetricSnapshot,
    ModelVersion,
    PlannedSlot,
    Post,
    Regime,
    ResourceRead,
    Run,
    ScoreRow,
)
from socialmgr.labelling.taxonomy import SOURCE_PRECEDENCE
from socialmgr.modelling import maturity, scoring
from socialmgr.services import draws
from socialmgr.services.hashing import sha256_json, sha256_text
from socialmgr.services.plugins import ActivePlugins

FEATURE_SET_VERSION = "fs-1"
LABEL_SET_VERSION_PROVIDER = "provider-hints-0"
CODE_VERSION = "m1"
OWNER_TERMS_MIN_POSTS = 20  # MODEL §2a minimum owner posts before owner-level terms (A22)
LATE_MATURITY = timedelta(days=14)
QUANTILES = (0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95)
# Reposts are someone else's content (X reports no impressions for them): stored, never forecast.
FORECAST_TYPES = ("original", "quote", "reply", "self_reply")


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


def run_spend(session: Session, run_id: int) -> float:
    return float(
        session.scalar(select(func.coalesce(func.sum(Cost.amount_usd), 0.0)).where(Cost.run_id == run_id))
        or 0.0
    )


def finish_run(session: Session, run: Run, now: datetime) -> None:
    run.finished_at = now
    run.status = "finished"
    run.spend_usd = run_spend(session, run.id)
    session.flush()


def month_spend(session: Session, workspace_id: int, at: datetime) -> float:
    """Spend in the UTC calendar month containing ``at``."""
    start = at.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return float(
        session.scalar(
            select(func.coalesce(func.sum(Cost.amount_usd), 0.0)).where(
                Cost.workspace_id == workspace_id, Cost.incurred_at >= start, Cost.incurred_at <= at
            )
        )
        or 0.0
    )


# ---------------------------------------------------------------- collection


@dataclass(frozen=True)
class CollectSummary:
    new_posts: int
    snapshots: int
    cost_usd: float
    caveats: tuple[str, ...] = ()
    skipped: bool = False


def earliest_followers(session: Session, account_id: int) -> int | None:
    return session.scalar(
        select(FollowerSnapshot.followers)
        .where(FollowerSnapshot.account_id == account_id)
        .order_by(FollowerSnapshot.fetched_at, FollowerSnapshot.id)
        .limit(1)
    )


def _followers_before(session: Session, account_id: int, t: datetime) -> int | None:
    """Follower count from the last run before ``t``: carried forward, never interpolated (MODEL A21)."""
    return session.scalar(
        select(FollowerSnapshot.followers)
        .where(FollowerSnapshot.account_id == account_id, FollowerSnapshot.fetched_at < t)
        .order_by(FollowerSnapshot.fetched_at.desc())
        .limit(1)
    )


def _bill(
    session: Session, settings: Settings, run: Run, provider: str, batch_cost: float, billable: Any
) -> float:
    """Charge each billable read once per provider per UTC day (X deduplicates Owned Reads daily)."""
    if not billable:
        return batch_cost
    day = run.cutoff_at.astimezone(UTC).date().isoformat()
    seen = set(
        session.scalars(
            select(ResourceRead.resource).where(
                ResourceRead.provider == provider, ResourceRead.utc_day == day
            )
        )
    )
    cost = 0.0
    for read in billable:
        if read.resource in seen:
            continue
        seen.add(read.resource)
        session.add(
            ResourceRead(
                workspace_id=settings.workspace_id,
                provider=provider,
                resource=read.resource,
                utc_day=day,
                run_id=run.id,
            )
        )
        cost += read.unit_cost_usd
    return cost


KNOWN_FOLLOWERS_SENT = 200


def _recent_follower_ids(session: Session, account_id: int) -> tuple[str, ...]:
    return tuple(
        session.scalars(
            select(FollowerId.external_id)
            .where(FollowerId.account_id == account_id)
            .order_by(FollowerId.id.desc())
            .limit(KNOWN_FOLLOWERS_SENT)
        )
    )


def _record_followers(
    session: Session, settings: Settings, run: Run, account_id: int, ids: tuple[str, ...]
) -> None:
    known = set(
        session.scalars(
            select(FollowerId.external_id).where(
                FollowerId.account_id == account_id, FollowerId.external_id.in_(ids)
            )
        )
    )
    # The list is newest first; insert oldest first so ``id`` order follows follow order.
    for ext in reversed(ids):
        if ext not in known:
            known.add(ext)
            session.add(
                FollowerId(
                    workspace_id=settings.workspace_id,
                    account_id=account_id,
                    external_id=ext,
                    first_seen_run_id=run.id,
                    first_seen_at=run.cutoff_at,
                )
            )


def collect(session: Session, settings: Settings, plugins: ActivePlugins, run: Run) -> CollectSummary:
    spent = month_spend(session, settings.workspace_id, run.cutoff_at)
    if spent >= settings.monthly_cap_usd:
        return CollectSummary(
            0,
            0,
            0.0,
            caveats=(
                f"Collection skipped: this month's spend ${spent:.2f} reached the "
                f"${settings.monthly_cap_usd:.2f} cap (SOCIALMGR_MONTHLY_CAP_USD).",
            ),
            skipped=True,
        )
    accounts = list(
        session.scalars(select(Account).where(Account.workspace_id == settings.workspace_id, Account.active))
    )
    prev = last_finished_run(session, settings.workspace_id, before=run.cutoff_at)
    new_posts = snapshots = 0
    cost = 0.0
    caveats: list[str] = []
    follower_counts: dict[int, int] = {}
    new_follower_counts: dict[int, int] = {}
    # Providers that refresh credentials do it now, before this session writes anything
    # (SQLite allows one writer at a time; see providers/x/tokens.py).
    for provider in plugins.providers:
        prepare = getattr(provider, "prepare", None)
        if callable(prepare):
            for a in accounts:
                if a.role in provider.roles:
                    prepare(a.id)
    for provider in plugins.providers:
        refs = tuple(
            AccountRef(
                account_id=a.id,
                platform=a.platform,
                handle=a.handle,
                role=a.role,
                external_id=a.external_id,
                known_follower_ids=_recent_follower_ids(session, a.id),
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
            if fa.new_follower_ids is not None and known and fa.new_followers_complete:
                # without known ids (first run) or after giving up, it is only a baseline
                new_follower_counts.setdefault(fa.account_id, len(fa.new_follower_ids))
            if fa.new_follower_ids:
                _record_followers(session, settings, run, fa.account_id, fa.new_follower_ids)
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
                            label_set_version=LABEL_SET_VERSION_PROVIDER,
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

        charged = _bill(session, settings, run, batch.provider, batch.cost_usd, batch.billable)
        if charged:
            session.add(
                Cost(
                    workspace_id=settings.workspace_id,
                    run_id=run.id,
                    provider=batch.provider,
                    item="collection",
                    amount_usd=round(charged, 6),
                    simulated=batch.provider.startswith("fake"),
                    incurred_at=run.cutoff_at,
                )
            )
            cost += charged

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


# ---------------------------------------------------------------- labels


def pick_labels(labels: list[Label]) -> dict[str, Label]:
    """One label per field: owner over provider over LLM over rule; newest wins within a source."""
    best: dict[str, Label] = {}
    for lb in labels:
        cur = best.get(lb.field)
        key = (SOURCE_PRECEDENCE.get(lb.source, 0), lb.created_at, lb.id)
        if cur is None or key > (SOURCE_PRECEDENCE.get(cur.source, 0), cur.created_at, cur.id):
            best[lb.field] = lb
    return best


def effective_labels(session: Session, post_id: int) -> dict[str, str]:
    """One value per field: owner over provider over LLM over rule; newest wins within a source."""
    rows = session.execute(
        select(Label.field, Label.value, Label.source, Label.created_at, Label.id).where(
            Label.post_id == post_id
        )
    ).all()
    best: dict[str, tuple[int, datetime, int, str]] = {}
    for fld, value, source, created, label_id in rows:
        key = (SOURCE_PRECEDENCE.get(source, 0), created, label_id, value)
        if fld not in best or key > best[fld]:
            best[fld] = key
    return {fld: v[3] for fld, v in best.items()}


# ---------------------------------------------------------------- maturity curves


def curve_as_of(session: Session, workspace_id: int, cutoff: datetime | None) -> maturity.Curve:
    """The curve recorded by the newest run at or before ``cutoff`` (its fit_curve step result)."""
    if cutoff is None:
        return maturity.PRIOR
    for run in session.scalars(
        select(Run)
        .where(Run.workspace_id == workspace_id, Run.cutoff_at <= cutoff)
        .order_by(Run.cutoff_at.desc(), Run.id.desc())
    ):
        step = (run.steps or {}).get("fit_curve", {})
        version = step.get("result", {}).get("curve_version") if step.get("status") == "done" else None
        if version:
            return load_curve(session, version)
    return maturity.PRIOR


def load_curve(session: Session, version: str) -> maturity.Curve:
    if version == maturity.PRIOR_CURVE_VERSION:
        return maturity.PRIOR
    row = session.scalar(select(MaturityCurve).where(MaturityCurve.version == version))
    if row is None:
        raise LookupError(f"unknown maturity curve version {version}")
    return _curve_from_row(row)


def _curve_from_row(row: MaturityCurve) -> maturity.Curve:
    return maturity.Curve(
        version=row.version,
        scale_h=float(row.params["scale_h"]),
        shape=float(row.params["shape"]),
        kappa=row.params.get("kappa"),
        n_pairs=row.n_pairs,
    )


def capture_pairs(session: Session, workspace_id: int, cutoff: datetime) -> list[maturity.CapturePair]:
    """Consecutive capture pairs of owner posts fetched by ``cutoff``."""
    rows = session.execute(
        select(MetricSnapshot.post_id, MetricSnapshot.age_s, MetricSnapshot.impressions)
        .join(Post, Post.id == MetricSnapshot.post_id)
        .join(Account, Account.id == Post.account_id)
        .join(Run, Run.id == MetricSnapshot.run_id)
        .where(
            MetricSnapshot.workspace_id == workspace_id,
            Run.cutoff_at <= cutoff,
            MetricSnapshot.impressions.is_not(None),
            Account.role == "owner",
        )
        .order_by(MetricSnapshot.post_id, MetricSnapshot.age_s)
    ).all()
    by_post: dict[int, list[tuple[float, int]]] = {}
    for post_id, age_s, impressions in rows:
        if impressions is not None:
            by_post.setdefault(post_id, []).append((age_s / 3600, int(impressions)))
    pairs: list[maturity.CapturePair] = []
    for captures in by_post.values():
        pairs += maturity.pairs_from_captures(captures)
    return pairs


def fit_curve(session: Session, settings: Settings, run: Run, now: datetime) -> maturity.Curve:
    """Refit the curve on every capture pair seen so far; record it for this run."""
    curve = maturity.fit(capture_pairs(session, settings.workspace_id, run.cutoff_at))
    if curve.version == maturity.PRIOR_CURVE_VERSION:
        return curve
    if session.scalar(select(MaturityCurve.id).where(MaturityCurve.version == curve.version)) is None:
        session.add(
            MaturityCurve(
                workspace_id=settings.workspace_id,
                version=curve.version,
                family=maturity.FAMILY,
                params=curve.params,
                n_pairs=curve.n_pairs,
                run_id=run.id,
                fitted_at=now,
            )
        )
        session.flush()
    return curve


# ---------------------------------------------------------------- regimes


def regime_at(
    session: Session, workspace_id: int, platform: str, t: datetime, known_by: datetime | None
) -> int | None:
    """The regime in force at ``t`` among those recorded by ``known_by``."""
    q = select(Regime.id).where(
        Regime.workspace_id == workspace_id, Regime.platform == platform, Regime.started_at <= t
    )
    if known_by is not None:
        q = q.where(Regime.created_at <= known_by)
    return session.scalar(q.order_by(Regime.started_at.desc(), Regime.id.desc()).limit(1))


# ---------------------------------------------------------------- forecasts


def matured_examples(
    session: Session, workspace_id: int, cutoff: datetime, curve: maturity.Curve = maturity.PRIOR
) -> list[TrainingExample]:
    """Outcomes visible at ``cutoff``: each post's first capture at age >= 48h fetched by then."""
    first_mature = (
        select(MetricSnapshot.post_id, func.min(MetricSnapshot.age_s).label("age_s"))
        .join(Run, Run.id == MetricSnapshot.run_id)
        .where(
            MetricSnapshot.workspace_id == workspace_id,
            Run.cutoff_at <= cutoff,
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
        .where(Post.deleted_at.is_(None), Post.type.in_(FORECAST_TYPES))
    ).all()
    examples = []
    for post, role, impressions, age_s in rows:
        if impressions is None:
            continue
        estimate = curve.estimate_i48(impressions, age_s / 3600)
        if estimate is None:
            continue
        examples.append(
            TrainingExample(
                post_external_id=post.external_id,
                account_role=role,
                created_at=post.created_at,
                type=post.type,
                followers_at_post=(
                    post.followers_at_post
                    if post.followers_at_post is not None
                    else earliest_followers(session, post.account_id) or 0
                ),
                outcome_i48=round(estimate, 3),
            )
        )
    return sorted(examples, key=lambda e: (e.created_at, e.post_external_id))


def _model_version_row(
    session: Session, workspace_id: int, version: str, family: str, prior: str, role: str
) -> None:
    if session.scalar(select(ModelVersion.id).where(ModelVersion.version == version)) is None:
        session.add(
            ModelVersion(
                workspace_id=workspace_id,
                version=version,
                family=family,
                prior_source=prior,
                feature_set_version=FEATURE_SET_VERSION,
                code_version=CODE_VERSION,
                role=role,
                created_at=datetime.now(UTC),
            )
        )


def _manifest(
    session: Session,
    workspace_id: int,
    account_id: int,
    platform: str,
    snapshot: Run | None,
    subject: dict[str, Any],
) -> dict[str, Any]:
    """Everything a forecast may use, named by snapshot run (MODEL §1.3)."""
    cutoff = snapshot.cutoff_at if snapshot else None
    curve = curve_as_of(session, workspace_id, cutoff)
    examples = matured_examples(session, workspace_id, cutoff, curve) if cutoff else []
    # Carried forward from the last run at or before the snapshot that measured it (MODEL A21):
    # a run whose collection was skipped (cap, rate limit) wrote no count of its own.
    followers = (
        session.scalar(
            select(FollowerSnapshot.followers)
            .join(Run, Run.id == FollowerSnapshot.run_id)
            .where(FollowerSnapshot.account_id == account_id, Run.cutoff_at <= snapshot.cutoff_at)
            .order_by(Run.cutoff_at.desc(), FollowerSnapshot.id.desc())
            .limit(1)
        )
        if snapshot
        else None
    )
    followers_source = "snapshot"
    if followers is None:
        # Posted before any count existed (the first run back-fills 30 days). Use the earliest
        # count instead of 0: a small look-ahead, flagged in the manifest.
        followers = earliest_followers(session, account_id)
        followers_source = "first_run_backfill"
    created_at = datetime.fromisoformat(subject["created_at"])
    return {
        "snapshot_run_id": snapshot.id if snapshot else None,
        "cutoff": cutoff.isoformat() if cutoff else None,
        "feature_set_version": FEATURE_SET_VERSION,
        "curve_version": curve.version,
        "regime_id": regime_at(session, workspace_id, platform, created_at, cutoff),
        "subject": {**subject, "followers_at_post": followers or 0},
        "followers_source": followers_source,
        "training": [e.model_dump(mode="json") for e in examples],
        "seed": snapshot.id if snapshot else 0,
    }


def build_manifest(session: Session, workspace_id: int, post: Post) -> dict[str, Any]:
    """The as-of manifest of a published post: the last finished run before it was posted."""
    snapshot = last_finished_run(session, workspace_id, before=post.created_at)
    labels = effective_labels(session, post.id)
    account = session.get(Account, post.account_id)
    assert account is not None
    return _manifest(
        session,
        workspace_id,
        post.account_id,
        account.platform,
        snapshot,
        {
            "subject_id": f"post:{post.id}",
            "created_at": post.created_at.isoformat(),
            "type": post.type,
            "has_url": post.has_url,
            "format": labels.get("format"),
            "topic": labels.get("topic"),
            "own_posts_48h": post.own_posts_48h,
            "gap_since_prev_min": post.gap_since_prev_min,
        },
    )


def build_live_manifest(
    session: Session, workspace_id: int, slot: PlannedSlot, now: datetime
) -> dict[str, Any]:
    """The live manifest of a planned post: the last finished run before registration."""
    snapshot = last_finished_run(session, workspace_id, before=now)
    account = session.get(Account, slot.account_id)
    assert account is not None
    labels = slot.labels or {}
    return _manifest(
        session,
        workspace_id,
        slot.account_id,
        account.platform,
        snapshot,
        {
            "subject_id": f"slot:{slot.id}",
            "created_at": slot.planned_at.isoformat(),
            "type": slot.post_type,
            "has_url": bool(labels.get("has_url") == "true"),
            "format": labels.get("format"),
            "topic": labels.get("topic"),
            "own_posts_48h": None,
            "gap_since_prev_min": None,
        },
    )


def forecast_from_manifest(
    plugins: ActivePlugins, manifest: dict[str, Any]
) -> tuple[str, bool, PredictiveDraws]:
    """Recompute a forecast from its manifest alone. Returns (model_version, calibrated, draws).

    ``calibrated`` is false while the model ran on priors only (MODEL A22: ``prior_only_uncalibrated``).
    """
    training = [TrainingExample.model_validate(e) for e in manifest["training"]]
    raw = manifest["subject"]
    subject = ForecastSubject(
        subject_id=raw["subject_id"],
        created_at=datetime.fromisoformat(raw["created_at"]),
        type=raw["type"],
        followers_at_post=raw["followers_at_post"],
        has_url=raw["has_url"],
        format=raw["format"],
        topic=raw["topic"],
    )
    owner_n = sum(1 for e in training if e.account_role == "owner")
    prior = plugins.prior_source.prior(
        PriorContext(platform="x", followers=subject.followers_at_post, owner_matured_posts=owner_n)
    )
    fitted = plugins.champion.fit(training, prior, seed=int(manifest["seed"]))
    predicted = fitted.predict(subject)
    uses_owner = predicted.meta.get("uses_owner_data", "true") == "true"
    calibrated = uses_owner and owner_n >= OWNER_TERMS_MIN_POSTS
    return fitted.version, calibrated, predicted


def freeze_forecast(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    subject: str,
    kind: str,
    manifest: dict[str, Any],
    now: datetime,
) -> Forecast:
    """Compute and freeze one forecast. Frozen rows are never edited (append-only triggers)."""
    version, calibrated, predicted = forecast_from_manifest(plugins, manifest)
    if not calibrated and kind == "as_of":
        kind = "prior_only_uncalibrated"
    manifest = {**manifest, "model_version": version, "calibrated": calibrated}
    digest = draws.store(session, predicted.samples, settings.workspace_id)
    _model_version_row(
        session,
        settings.workspace_id,
        version,
        plugins.champion.family,
        plugins.prior_source.name,
        "champion",
    )
    forecast = Forecast(
        workspace_id=settings.workspace_id,
        subject=subject,
        kind=kind,
        model_version=version,
        curve_version=manifest["curve_version"],
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
    session.add(forecast)
    session.flush()
    return forecast


def freeze_as_of_forecasts(
    session: Session, settings: Settings, plugins: ActivePlugins, now: datetime
) -> int:
    """Give every owner post without a forecast its as-of forecast."""
    owner = owner_account(session, settings.workspace_id)
    if owner is None:
        return 0
    frozen = set(
        session.scalars(
            select(Forecast.subject).where(
                Forecast.workspace_id == settings.workspace_id, Forecast.subject.like("post:%")
            )
        )
    )
    pending = [
        post
        for post in session.scalars(
            select(Post)
            .where(Post.account_id == owner.id, Post.deleted_at.is_(None), Post.type.in_(FORECAST_TYPES))
            .order_by(Post.created_at)
        )
        if f"post:{post.id}" not in frozen
    ]
    for post in pending:
        manifest = build_manifest(session, settings.workspace_id, post)
        freeze_forecast(session, settings, plugins, f"post:{post.id}", "as_of", manifest, now)
    return len(pending)


# ---------------------------------------------------------------- scoring


@dataclass
class ScoreSummary:
    scored: int = 0
    late_maturity: int = 0
    labels: dict[str, int] = field(default_factory=dict)


def subject_post_id(session: Session, subject: str) -> int | None:
    """The published post a forecast subject refers to (a planned slot once it's matched)."""
    kind, _, raw = subject.partition(":")
    if kind == "post":
        return int(raw)
    if kind == "slot":
        return session.scalar(select(PlannedSlot.matched_post_id).where(PlannedSlot.id == int(raw)))
    return None


def score_matured(session: Session, settings: Settings, run: Run, now: datetime) -> ScoreSummary:
    """Score each unscored forecast once its post has a capture at age >= 48h (MODEL §1.3, §4.1)."""
    summary = ScoreSummary()
    scored_ids = select(ScoreRow.forecast_id)
    forecasts = session.scalars(
        select(Forecast).where(Forecast.workspace_id == settings.workspace_id, Forecast.id.not_in(scored_ids))
    ).all()
    for fc in forecasts:
        post_id = subject_post_id(session, fc.subject)
        if post_id is None:
            continue
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
        curve = load_curve(session, fc.curve_version)
        outcome = curve.estimate_i48(capture.impressions, age_h)
        if outcome is None:
            continue
        samples = draws.load(session, fc.draws_hash)
        s = scoring.score(samples, outcome, seed=post_id)
        late = age_h > LATE_MATURITY.total_seconds() / 3600
        session.add(
            ScoreRow(
                workspace_id=settings.workspace_id,
                forecast_id=fc.id,
                run_id=run.id,
                outcome=round(outcome, 3),
                capture_age_h=round(age_h, 3),
                curve_version=fc.curve_version,
                exact=False,
                late_maturity=late,
                pit_mid=s.pit_mid,
                pit_rand=s.pit_rand,
                crps_log=s.crps_log,
                in_80=s.in_80,
                in_50=s.in_50,
                scored_at=now,
            )
        )
        summary.scored += 1
        summary.late_maturity += int(late)
        label = scoring.surprise_label(s.pit_mid)
        summary.labels[label] = summary.labels.get(label, 0) + 1
    session.flush()
    return summary
