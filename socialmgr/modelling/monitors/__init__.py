"""Drift detectors (MODEL §4.3, plan §6): D1 charts, diagnostics, D2 goal check, D3 ACI.

Pure numpy/scipy, no database. Every state is an immutable dataclass with ``to_dict``/``from_dict`` (JSON
serialisable) so a run can persist it and resume next run; :func:`run_monitors` advances all of them for
one run and returns a :class:`MonitorReport`.
"""

from socialmgr.modelling.monitors.aci import AciState, aci_recursion
from socialmgr.modelling.monitors.arl import (
    cusum_arl,
    ewma_arl,
    ewma_limit_for_arl,
    increment_cdf,
    threshold_for_arl,
    two_sided_arl,
)
from socialmgr.modelling.monitors.charts import (
    LOCATION_H,
    SCALE_REFERENCE_H,
    Alarm,
    CusumChart,
    EwmaChart,
    chart_input,
    cusum_recursion,
    ewma_chart,
    follower_arl0,
    follower_chart,
    location_chart,
    pit_to_z,
    pits_to_z,
    scale_chart,
    scale_thresholds,
)
from socialmgr.modelling.monitors.diagnostics import (
    BerkowitzResult,
    CoverageResult,
    Diagnostics,
    UniformityResult,
    berkowitz_test,
    binomial_band,
    coverage_check,
    diagnostics_due,
    pit_uniformity_test,
    run_diagnostics,
)
from socialmgr.modelling.monitors.goal import (
    GoalCheck,
    check_goal,
    poisson_binomial_pmf,
    poisson_binomial_test,
)
from socialmgr.modelling.monitors.report import (
    ChartSummary,
    GoalInputs,
    MonitorReport,
    MonitorState,
    initial_state,
    run_monitors,
)

__all__ = [
    "LOCATION_H",
    "SCALE_REFERENCE_H",
    "AciState",
    "Alarm",
    "BerkowitzResult",
    "ChartSummary",
    "CoverageResult",
    "CusumChart",
    "Diagnostics",
    "EwmaChart",
    "GoalCheck",
    "GoalInputs",
    "MonitorReport",
    "MonitorState",
    "UniformityResult",
    "aci_recursion",
    "berkowitz_test",
    "binomial_band",
    "chart_input",
    "check_goal",
    "coverage_check",
    "cusum_arl",
    "cusum_recursion",
    "diagnostics_due",
    "ewma_arl",
    "ewma_chart",
    "ewma_limit_for_arl",
    "follower_arl0",
    "follower_chart",
    "increment_cdf",
    "initial_state",
    "location_chart",
    "pit_to_z",
    "pit_uniformity_test",
    "pits_to_z",
    "poisson_binomial_pmf",
    "poisson_binomial_test",
    "run_diagnostics",
    "run_monitors",
    "scale_chart",
    "scale_thresholds",
    "threshold_for_arl",
    "two_sided_arl",
]
