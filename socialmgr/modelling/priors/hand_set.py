"""Hand-set priors used before any owner data or peer panel exists (MODEL.md §2a, A7, A22).

The follower elasticity, dispersion, link penalty and reply-thread factor are MODEL §2a's priors. The
two reach levels are placeholders (assumptions, not evidence) until owner data or a peer panel
replaces them; forecasts made from them are labelled ``prior_only_uncalibrated``.
"""

import math

from socialmgr.core.types import Distribution, PriorContext, PriorSpec


def _normal(mean: float, sd: float) -> Distribution:
    return Distribution(family="normal", params={"mean": mean, "sd": sd})


class HandSetPrior:
    name = "hand_set"
    version = "0.2"

    def prior(self, context: PriorContext) -> PriorSpec:
        return PriorSpec(
            source=self.name,
            version=self.version,
            params={
                # MODEL C6: mu_beta ~ N(0.75, 0.2^2)
                "beta": _normal(0.75, 0.2),
                # Placeholder (assumption): log 48h out-of-network impressions of an original
                "log_i48_at_zero": _normal(math.log(100), 1.0),
                # Placeholder (assumption): log in-network impressions per follower^beta
                "log_in_rate": _normal(math.log(0.5), 1.0),
                # MODEL §2a: has_url in theta_oon ~ N(-0.5, 0.7^2) [C9]
                "theta_url": _normal(-0.5, 0.7),
                # MODEL §2a: log phi ~ N(log 2, 0.7^2)
                "log_phi": _normal(math.log(2), 0.7),
                # MODEL §2a: lambda ~ half-N(1), the loading on latent post quality q ~ N(0, 1)
                "lambda": Distribution(family="half_normal", params={"sd": 1.0}),
                # MODEL §2a: rho_th ~ Beta(2, 2), in-network share kept by replies (m_in = 0.75 rho_th)
                "rho_th": Distribution(family="beta", params={"a": 2.0, "b": 2.0}),
                # Used by the trailing-median baseline's prior fallback
                "log_sd": Distribution(family="half_normal", params={"sd": 1.0}),
            },
        )
