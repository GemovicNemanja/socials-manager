"""Hand-set priors used before any owner data or peer panel exists (MODEL.md §2a, A7, A22).

Only the follower elasticity is evidence-backed (MODEL C6). The level and spread are placeholders
that M1 replaces with the full MODEL §2a prior set; forecasts made from them are labelled
``prior_only_uncalibrated`` and kept out of calibration.
"""

import math

from socialmgr.core.types import Distribution, PriorContext, PriorSpec


class HandSetPrior:
    name = "hand_set"
    version = "0.1"

    def prior(self, context: PriorContext) -> PriorSpec:
        return PriorSpec(
            source=self.name,
            version=self.version,
            params={
                # MODEL C6: mu_beta ~ N(0.75, 0.2^2)
                "beta": Distribution(family="normal", params={"mean": 0.75, "sd": 0.2}),
                # Placeholder (assumption): log 48h impressions of an original at 0 followers
                "log_i48_at_zero": Distribution(family="normal", params={"mean": math.log(100), "sd": 1.0}),
                # MODEL §2a: log phi ~ N(log 2, 0.7^2); used as a predictive log-sd scale here
                "log_sd": Distribution(family="half_normal", params={"sd": 1.0}),
            },
        )
