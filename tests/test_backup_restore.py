from sqlalchemy import func, select, text
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.db.models import Forecast, Post
from socialmgr.services.backups import backup, restore


def test_round_trip(settings: Settings, loaded: sessionmaker[Session]) -> None:
    with loaded() as session:
        posts = session.scalar(select(func.count(Post.id)))
        forecasts = session.scalar(select(func.count(Forecast.id)))

    path = backup(settings)
    assert path.exists() and path.parent == settings.resolved_backup_dir

    with loaded() as session:  # a mutable change after the backup
        session.execute(text("UPDATE posts SET text = NULL"))
        session.commit()

    restore(settings, path)

    with loaded() as session:
        assert session.scalar(select(func.count(Post.id))) == posts
        assert session.scalar(select(func.count(Forecast.id))) == forecasts
        assert session.scalar(select(func.count(Post.id)).where(Post.text.is_(None))) == 0
