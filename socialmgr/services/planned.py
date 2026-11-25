"""Planned posts: register ahead of time with a frozen live forecast, then match at a later run.

MODEL.md §1.3 (live forecasts), §6 (matching by tag or nearest planned time within ±30 min) and
§7 (near-duplicate and spacing guardrails).
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from socialmgr.core.config import Settings
from socialmgr.core.types import LabelInput, PostType
from socialmgr.db.models import Forecast, PlannedSlot, Post
from socialmgr.labelling import minhash, rules
from socialmgr.services import pipeline
from socialmgr.services.hashing import sha256_text
from socialmgr.services.plugins import ActivePlugins

MATCH_WINDOW = timedelta(minutes=30)
TEXT_MATCH_WINDOW = timedelta(hours=24)  # a near-identical text matches within a day of its plan
MISSED_AFTER = timedelta(hours=24)
MIN_ORIGINAL_SPACING = timedelta(hours=3)  # MODEL §7: originals less than 3h apart are blocked
NEAR_DUP_LOOKBACK = 200


class PlanRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    planned_at: datetime
    text: str | None = None
    post_type: PostType = "original"
    labels: dict[str, str] = Field(default_factory=dict)  # format / topic etc., optional
    registered_by: str = "cli"


class GuardrailHit(BaseModel):
    model_config = ConfigDict(frozen=True)

    rule: str
    severity: str  # block | warn
    detail: str
    ref: str | None = None


class PlanRefused(ValueError):
    def __init__(self, hits: list[GuardrailHit]) -> None:
        self.hits = hits
        super().__init__("; ".join(h.detail for h in hits))


@dataclass
class PlanResult:
    slot: PlannedSlot
    forecast: Forecast
    guardrails: list[GuardrailHit] = field(default_factory=list)


def check_guardrails(session: Session, owner_id: int, req: PlanRequest, now: datetime) -> list[GuardrailHit]:
    hits: list[GuardrailHit] = []
    if req.text:
        sig = minhash.signature(req.text)
        recent = session.execute(
            select(Post.id, Post.minhash, Post.text)
            .where(Post.account_id == owner_id, Post.text.is_not(None))
            .order_by(Post.created_at.desc())
            .limit(NEAR_DUP_LOOKBACK)
        ).all()
        # Posts collected but not labelled yet have no stored signature: compute it from the text.
        signatures = [(r[0], r[1] or minhash.to_bytes(minhash.signature(r[2] or ""))) for r in recent]
        match_id, sim = minhash.nearest(sig, signatures)
        if match_id is not None and sim >= minhash.NEAR_DUPLICATE:
            hits.append(
                GuardrailHit(
                    rule="near_duplicate",
                    severity="block",
                    detail=(
                        f"Near-duplicate of post:{match_id} (MinHash similarity {sim:.2f}); "
                        "X penalises repeats."
                    ),
                    ref=f"post:{match_id}",
                )
            )
        feats = rules.text_features(req.text)
        if feats.n_hashtags > 1:
            hits.append(GuardrailHit(rule="hashtags", severity="warn", detail="More than one hashtag."))
    if req.post_type in ("original", "quote"):
        lo, hi = req.planned_at - MIN_ORIGINAL_SPACING, req.planned_at + MIN_ORIGINAL_SPACING
        near_posts = session.scalars(
            select(Post.id).where(
                Post.account_id == owner_id,
                Post.type.in_(("original", "quote")),
                Post.created_at > lo,
                Post.created_at < hi,
            )
        ).all()
        near_slots = session.scalars(
            select(PlannedSlot.id).where(
                PlannedSlot.account_id == owner_id,
                PlannedSlot.state == "planned",
                PlannedSlot.post_type.in_(("original", "quote")),
                PlannedSlot.planned_at > lo,
                PlannedSlot.planned_at < hi,
            )
        ).all()
        for ref in [f"post:{i}" for i in near_posts] + [f"slot:{i}" for i in near_slots]:
            hits.append(
                GuardrailHit(
                    rule="original_spacing",
                    severity="warn",
                    detail=f"Within 3h of {ref}; originals this close compete for the same audience.",
                    ref=ref,
                )
            )
    return hits


def register_planned_post(
    session: Session,
    settings: Settings,
    plugins: ActivePlugins,
    req: PlanRequest,
    now: datetime,
    allow_warnings: bool = True,
) -> PlanResult:
    """Freeze and hash the live forecast at registration, before the post exists (MODEL §1.3)."""
    if req.planned_at <= now:
        raise ValueError("planned_at must be in the future: a live forecast is frozen before the post exists")
    owner = pipeline.owner_account(session, settings.workspace_id)
    if owner is None:
        owner = pipeline.ensure_accounts(session, settings, now)[0]
    hits = check_guardrails(session, owner.id, req, now)
    blocking = [h for h in hits if h.severity == "block" or not allow_warnings]
    if blocking:
        raise PlanRefused(blocking)

    labels = dict(req.labels)
    if req.text:
        feats = rules.text_features(req.text)
        labels.setdefault("has_url", "true" if feats.has_url else "false")
        labels.setdefault(
            "format", rules.format_label(post_type=req.post_type, media_kind=None, has_url=feats.has_url)
        )
        if "topic" not in labels and plugins.labeler.name != "none":
            try:
                result = plugins.labeler.label(
                    [LabelInput(key="draft", text=req.text, post_type=req.post_type)]
                )
                labels.update({k: v for r in result for k, v in r.values.items() if k not in labels})
            except Exception:  # labels are optional for the forecast; registration must not fail on them
                pass
    slot = PlannedSlot(
        workspace_id=settings.workspace_id,
        account_id=owner.id,
        planned_at=req.planned_at,
        text=req.text,
        text_hash=sha256_text(req.text) if req.text else None,
        minhash=minhash.to_bytes(minhash.signature(req.text)) if req.text else None,
        post_type=req.post_type,
        labels=labels,
        lever_context=None,
        state="planned",
        registered_by=req.registered_by,
        created_at=now,
    )
    session.add(slot)
    session.flush()
    manifest = pipeline.build_live_manifest(session, settings.workspace_id, slot, now)
    forecast = pipeline.freeze_forecast(session, settings, plugins, f"slot:{slot.id}", "live", manifest, now)
    return PlanResult(slot=slot, forecast=forecast, guardrails=hits)


@dataclass
class MatchSummary:
    matched: int = 0
    missed: int = 0


def _type_matches(slot: PlannedSlot, post_type: str) -> bool:
    """An experiment slot randomizing quote vs original (MODEL §6.10 E04) matches either type."""
    if post_type == slot.post_type:
        return True
    levers = {
        f.get("lever")
        for factors in ((slot.lever_context or {}).get("experiments") or {}).values()
        for f in factors.values()
    }
    return "quote" in levers and {post_type, slot.post_type} <= {"original", "quote"}


def match_planned(session: Session, settings: Settings, cutoff: datetime) -> MatchSummary:
    """Match published posts to planned slots: near-identical text within a day, else nearest time ±30 min."""
    summary = MatchSummary()
    owner = pipeline.owner_account(session, settings.workspace_id)
    if owner is None:
        return summary
    slots = list(
        session.scalars(
            select(PlannedSlot)
            .where(PlannedSlot.account_id == owner.id, PlannedSlot.state == "planned")
            .order_by(PlannedSlot.planned_at)
        )
    )
    taken = set(
        session.scalars(select(PlannedSlot.matched_post_id).where(PlannedSlot.matched_post_id.is_not(None)))
    )
    for slot in slots:
        candidates = [
            p
            for p in session.scalars(
                select(Post).where(
                    Post.account_id == owner.id,
                    Post.slot_id.is_(None),
                    Post.created_at >= slot.created_at,  # a live forecast must predate its post
                    Post.created_at >= slot.planned_at - TEXT_MATCH_WINDOW,
                    Post.created_at <= slot.planned_at + TEXT_MATCH_WINDOW,
                )
            )
            if p.id not in taken
        ]
        chosen: Post | None = None
        if slot.text:
            sig = minhash.signature(slot.text)
            scored = [
                (minhash.similarity(sig, minhash.signature(p.text or "")), p) for p in candidates if p.text
            ]
            scored = [(s, p) for s, p in scored if s >= minhash.NEAR_DUPLICATE]
            if scored:
                chosen = max(scored, key=lambda sp: sp[0])[1]
        if chosen is None:
            in_window = [
                p
                for p in candidates
                if abs(p.created_at - slot.planned_at) <= MATCH_WINDOW and _type_matches(slot, p.type)
            ]
            if in_window:
                chosen = min(in_window, key=lambda p: abs(p.created_at - slot.planned_at))
        if chosen is not None:
            slot.matched_post_id = chosen.id
            slot.state = "posted"
            chosen.slot_id = slot.id
            taken.add(chosen.id)
            summary.matched += 1
        elif cutoff - slot.planned_at > MISSED_AFTER:
            slot.state = "missed"
            summary.missed += 1
    session.flush()
    return summary


@dataclass
class DraftForecast:
    quantiles: dict[str, float]
    model_version: str
    calibrated: bool
    curve_version: str
    guardrails: list[GuardrailHit]
    labels: dict[str, str]


def draft_forecast(
    session: Session, settings: Settings, plugins: ActivePlugins, req: PlanRequest, now: datetime
) -> DraftForecast:
    """What a planned post would be forecast at, without registering or freezing anything."""
    owner = pipeline.owner_account(session, settings.workspace_id)
    if owner is None:
        raise ValueError("no owner account yet: run `socialmgr run` first")
    labels = dict(req.labels)
    if req.text:
        feats = rules.text_features(req.text)
        labels.setdefault("has_url", "true" if feats.has_url else "false")
        labels.setdefault(
            "format", rules.format_label(post_type=req.post_type, media_kind=None, has_url=feats.has_url)
        )
    draft = PlannedSlot(
        workspace_id=settings.workspace_id,
        account_id=owner.id,
        planned_at=req.planned_at,
        post_type=req.post_type,
        labels=labels,
        state="draft",
        registered_by=req.registered_by,
        created_at=now,
    )
    manifest = pipeline.build_live_manifest(session, settings.workspace_id, draft, now)
    manifest["subject"]["subject_id"] = f"draft:{sha256_text(req.model_dump_json())[:16]}"
    version, calibrated, predicted = pipeline.forecast_from_manifest(plugins, manifest)
    return DraftForecast(
        quantiles={f"q{round(q * 100):02d}": predicted.quantile(q) for q in pipeline.QUANTILES},
        model_version=version,
        calibrated=calibrated,
        curve_version=manifest["curve_version"],
        guardrails=check_guardrails(session, owner.id, req, now),
        labels=labels,
    )
