import itertools

import numpy as np
import pytest

from socialmgr.core.config import Settings
from socialmgr.core.registry import Registry
from socialmgr.modelling import maturity, scoring


def test_prior_curve_shape() -> None:
    assert maturity.prior_curve(48) == pytest.approx(1.0)
    g72 = maturity.prior_curve(72)
    assert maturity.prior_curve(80 / 60) / g72 == pytest.approx(0.5, abs=1e-3)
    assert maturity.prior_curve(24) / g72 == pytest.approx(0.94, abs=1e-3)
    ages = np.geomspace(0.1, 24 * 30, 50)
    values = [maturity.prior_curve(a) for a in ages]
    assert all(b > a for a, b in itertools.pairwise(values))
    assert maturity.prior_curve(0) == 0.0


def test_pit_and_labels() -> None:
    samples = np.random.default_rng(0).lognormal(6, 1, 4000)
    median = float(np.median(samples))
    assert scoring.pit_mid(samples, median) == pytest.approx(0.5, abs=0.02)
    assert scoring.pit_mid(samples, 1e9) == 1.0
    assert scoring.pit_mid(samples, 0) == 0.0
    assert scoring.surprise_label(0.85) == "above"
    assert scoring.surprise_label(0.5) == "as_expected"
    assert scoring.surprise_label(0.2) == "below"


def test_crps_is_smaller_near_the_centre() -> None:
    samples = np.random.default_rng(1).lognormal(6, 1, 2000)
    centre = scoring.crps_log(samples, float(np.median(samples)))
    tail = scoring.crps_log(samples, float(np.quantile(samples, 0.99)))
    assert 0 <= centre < tail


def test_registry_errors_name_the_options() -> None:
    reg: Registry[int] = Registry("thing")
    reg.register("one")(lambda s: 1)
    assert reg.resolve("one", Settings(_env_file=None)) == 1  # type: ignore[call-arg]
    with pytest.raises(LookupError, match="Built-ins: one"):
        reg.resolve("two", Settings(_env_file=None))  # type: ignore[call-arg]
