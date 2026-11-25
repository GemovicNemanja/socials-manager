"""The label step: rule labels, text and cadence features, MinHash, then LLM labels (MODEL.md §1.4)."""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.core.types import LabelInput
from socialmgr.db.models import Account, Label, Post
from socialmgr.labelling import minhash, rules
from socialmgr.labelling.taxonomy import LABEL_SET_VERSION, LLM_FIELDS
from socialmgr.services.plugins import ActivePlugins

NEAR_DUP_LOOKBACK = 200  # own posts compared for near-duplicates (MODEL §1.4 near_dup_max)


@dataclass
class LabelSummary:
    rule_labelled: int = 0
    llm_labelled: int = 0
    llm_pending: int = 0
    near_duplicates: list[dict[str, object]] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)


def _add(
    session: Session, settings: Settings, post_id: int, fld: str, value: str, source: str, now: datetime
) -> None:
    session.add(
        Label(
            workspace_id=settings.workspace_id,
            post_id=post_id,
            label_set_version=LABEL_SET_VERSION,
            field=fld,
            value=value,
            source=source,
            confidence=None,
            created_at=now,
        )
    )


def _has_label(session: Session, post_id: int, fld: str, sources: tuple[str, ...]) -> bool:
    return (
        session.scalar(
            select(Label.id)
            .where(Label.post_id == post_id, Label.field == fld, Label.source.in_(sources))
            .limit(1)
        )
        is not None
    )


def label_posts(session: Session, settings: Settings, plugins: ActivePlugins, now: datetime) -> LabelSummary:
    summary = LabelSummary()
    owner_posts = list(
        session.scalars(
            select(Post)
            .join(Account, Account.id == Post.account_id)
            .where(Account.workspace_id == settings.workspace_id, Account.role == "owner")
            .order_by(Post.created_at, Post.id)
        )
    )

    # Rule labels and features, once per post.
    for i, post in enumerate(owner_posts):
        if post.text_len_weighted is not None:
            continue
        text = post.text or ""
        feats = rules.text_features(text)
        post.text_len_weighted = feats.text_len_weighted
        post.newlines = feats.newlines
        earlier = [p for p in owner_posts[:i] if p.created_at < post.created_at]
        post.gap_since_prev_min = (
            round((post.created_at - earlier[-1].created_at).total_seconds() / 60, 1) if earlier else None
        )
        post.own_posts_48h = sum(1 for p in earlier if post.created_at - p.created_at <= timedelta(hours=48))
        sig = minhash.signature(text)
        post.minhash = minhash.to_bytes(sig)
        if text:
            others = [(p.id, p.minhash) for p in earlier[-NEAR_DUP_LOOKBACK:]]
            match_id, sim = minhash.nearest(sig, others)
            if match_id is not None and sim >= minhash.NEAR_DUPLICATE:
                summary.near_duplicates.append(
                    {"post": f"post:{post.id}", "matches": f"post:{match_id}", "similarity": sim}
                )
        media_kind = (post.media or {}).get("kind")
        fmt = rules.format_label(post_type=post.type, media_kind=media_kind, has_url=post.has_url)
        _add(session, settings, post.id, "format", fmt, "rule", now)
        summary.rule_labelled += 1
    session.flush()

    # LLM labels for posts that have none yet (owner and provider labels take precedence anyway).
    pending = [
        p
        for p in owner_posts
        if p.text and p.deleted_at is None and not _has_label(session, p.id, "content_kind", ("llm", "owner"))
    ]
    if not pending or plugins.labeler.name == "none":
        return summary
    items = [
        LabelInput(key=f"post:{p.id}", text=p.text or "", post_type=p.type, format=None) for p in pending
    ]
    try:
        results = plugins.labeler.label(items)
    except Exception as exc:  # the run continues without LLM labels and says why
        summary.llm_pending = len(pending)
        summary.caveats.append(f"LLM labels skipped ({plugins.labeler.name}): {exc}")
        return summary
    by_key = {r.key: r for r in results}
    for p in pending:
        result = by_key.get(f"post:{p.id}")
        if result is None:
            summary.llm_pending += 1
            continue
        for fld, value in result.values.items():
            if fld in LLM_FIELDS:
                _add(session, settings, p.id, fld, value, "llm", now)
        summary.llm_labelled += 1
    if summary.llm_pending:
        summary.caveats.append(f"{summary.llm_pending} posts still need LLM labels; the next run retries.")
    session.flush()
    return summary
