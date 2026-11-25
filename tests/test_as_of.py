"""As-of forecasts recompute bit-for-bit and use nothing captured after their snapshot run."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.db.models import FollowerSnapshot, Forecast, Post
from socialmgr.services import draws, pipeline
from socialmgr.services.hashing import sha256_json
from socialmgr.services.plugins import load_plugins


def test_recompute_from_manifest(settings: Settings, loaded: sessionmaker[Session]) -> None:
    plugins = load_plugins(settings)
    with loaded() as session:
        for fc in session.scalars(select(Forecast).order_by(Forecast.id)):
            assert sha256_json(fc.input_manifest) == fc.manifest_hash
            version, calibrated, predicted = pipeline.forecast_from_manifest(plugins, fc.input_manifest)
            assert version == fc.model_version and calibrated == fc.input_manifest["calibrated"]
            assert draws.encode(predicted.samples)[0] == fc.draws_hash


def test_manifest_ignores_later_data(loaded: sessionmaker[Session]) -> None:
    """Rebuilding a manifest after many later runs gives the frozen one: no look-ahead."""
    with loaded() as session:
        for fc in session.scalars(select(Forecast)):
            post = session.get(Post, int(fc.subject.split(":")[1]))
            assert post is not None
            rebuilt = pipeline.build_manifest(session, fc.workspace_id, post)
            assert rebuilt.pop("model_plugin") in (None, fc.input_manifest["model_plugin"])
            frozen = {
                k: v
                for k, v in fc.input_manifest.items()
                if k not in ("model_plugin", "model_version", "calibrated")
            }
            assert rebuilt == frozen
            if frozen["cutoff"] is not None:
                assert frozen["cutoff"] < post.created_at.isoformat()


def test_followers_at_post_come_from_an_earlier_run(loaded: sessionmaker[Session]) -> None:
    """As-of follower counts come from the snapshot run; posts from before the first run use the
    earliest count and say so (never a silent 0)."""
    with loaded() as session:
        first_count = session.scalar(
            select(FollowerSnapshot.followers).order_by(FollowerSnapshot.fetched_at).limit(1)
        )
        for fc in session.scalars(select(Forecast)):
            m = fc.input_manifest
            if m["snapshot_run_id"] is None:
                assert m["followers_source"] == "first_run_backfill"
                assert m["subject"]["followers_at_post"] == first_count
            else:
                assert m["followers_source"] == "snapshot"
                snap = session.scalar(
                    select(FollowerSnapshot.followers).where(FollowerSnapshot.run_id == m["snapshot_run_id"])
                )
                assert m["subject"]["followers_at_post"] == snap
