"""The nowcast step on the fixture: posts under 48h get a provisional I48 the posts list shows."""

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from socialmgr.core.config import Settings
from socialmgr.db.models import Nowcast
from socialmgr.services import read_models


def test_young_posts_get_nowcasts(loaded: sessionmaker[Session], settings: Settings) -> None:
    with loaded() as session:
        rows = session.scalars(select(Nowcast)).all()
        assert rows and all(r.capture_age_h < 48 for r in rows)
        assert {r.method for r in rows} <= {"precision_weighting", "regression"}
        assert all(0 <= r.p_gt_median <= 1 and r.quantiles["q10"] <= r.quantiles["q90"] for r in rows)
        page = read_models.list_posts(session, settings, status="provisional")
        withnow = [p for p in page.items if p.i48.p_above_median is not None]
        assert withnow and all(p.i48.provisional and p.i48.method for p in withnow)


def test_a_degenerate_frozen_forecast_still_gives_finite_quantiles() -> None:
    import math

    import numpy as np

    from socialmgr.modelling import maturity
    from socialmgr.modelling import nowcast as nc

    pre = nc.PreForecast.from_draws(np.full(1000, 50.0))
    est = nc.nowcast(5000, 6.0, pre, 100, maturity.PRIOR)
    assert all(math.isfinite(v) and v >= 5000 for v in est.quantiles.values())
