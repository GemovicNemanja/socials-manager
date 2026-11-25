"""The preloaded experiments E01-E09 (MODEL.md §6.10). E10-E11 (other accounts) and E12 (Premium)
are dropped in v1 (plan §7). Data only; ``engine.suggestions`` adds a live power preview."""

from dataclasses import dataclass

from socialmgr.experiments.specs import AppliesTo, ExperimentSpec, FactorSpec

ORIGINALS = ["original", "quote"]


@dataclass(frozen=True)
class Suggestion:
    key: str
    spec: ExperimentSpec
    horizon_text: str  # MODEL §6.10's horizon column
    detectability: str  # MODEL §6.10's detectability column
    mechanism: str
    note: str = ""


def _s(
    key: str,
    name: str,
    hypothesis: str,
    factor: FactorSpec,
    *,
    mechanism: str,
    detectability: str,
    horizon_text: str,
    horizon_weeks: float = 4.0,
    design: str = "per_post",
    metric: str = "log_i48",
    secondary: tuple[str, ...] = (),
    note: str = "",
) -> Suggestion:
    spec = ExperimentSpec.model_validate(
        {
            "key": key,
            "name": name,
            "hypothesis": hypothesis,
            "design": design,
            "factors": [factor],
            "primary_metric": metric,
            "secondary_metrics": list(secondary),
            "horizon_weeks": horizon_weeks,
        }
    )
    return Suggestion(key, spec, horizon_text, detectability, mechanism, note)


SUGGESTIONS: tuple[Suggestion, ...] = (
    _s(
        "E01",
        "Narrated video vs screenshot on demos",
        "Narrated native video over 10 s beats a static screenshot or GIF for the same demo.",
        FactorSpec(
            name="video",
            lever="format",
            control="Image",
            treatment="Narrated video",
            applies_to=AppliesTo(post_types=ORIGINALS, labels={"content_kind": ["demo"]}),
        ),
        mechanism="Video enters the video index at creation, bypassing the first-like gate.",
        detectability="n = 28: 2x at .37-.61; at 8 weeks .64-.90",
        horizon_text="4-8 weeks",
        secondary=("follows_per_1k",),
        note="Demo posts only (about 1/day): tag demo slots with content_kind=demo; blocked by 2-day pairs.",
    ),
    _s(
        "E02",
        "Link in the first self-reply vs in the body",
        "A link in the first self-reply beats a link in the body.",
        FactorSpec(
            name="link_reply",
            lever="link_placement",
            control="body",
            treatment="first_self_reply",
            applies_to=AppliesTo(post_types=ORIGINALS, labels={"link_post": ["true"]}),
        ),
        mechanism="has_url's effect is learned; Galeazzi reports up to 8x fewer views for links.",
        detectability="Only an effect >= 2x is plausibly detectable",
        horizon_text="4-8 weeks",
        secondary=("link_clicks",),
        note="Link posts only: tag them with link_post=true.",
    ),
    _s(
        "E03",
        "Outcome-first hook",
        "An outcome-first hook raises early like rate and reach.",
        FactorSpec(name="hook", lever="hook", control="standard", treatment="outcome_first"),
        mechanism="Thompson sampling on likes per view drives the cold-start lift.",
        detectability="1.5x at .50-.65; 2x at .92-.98",
        horizon_text="4 weeks",
        secondary=("likes_per_1k",),
    ),
    _s(
        "E04",
        "Quote an in-niche account vs standalone original",
        "Quoting an in-niche account beats a standalone original with the same commentary.",
        FactorSpec(name="quote", lever="quote", control="original", treatment="quote_in_niche"),
        mechanism="Quotes count as originals for cold start; the quoted post is embedded.",
        detectability="as E03",
        horizon_text="4 weeks",
        secondary=("follows",),
        note="Plan for weeks 5-8, factorial with E05.",
    ),
    _s(
        "E05",
        "Single long post vs thread",
        "A single long post beats a thread.",
        FactorSpec(name="long_post", lever="thread_vs_long", control="thread", treatment="single_long"),
        mechanism="Conversation dedup; self-replies are not indexed; dwell weights.",
        detectability="as E03",
        horizon_text="4-8 weeks",
        secondary=("profile_clicks", "follows"),
        note="Primary metric is the root post's I48.",
    ),
    _s(
        "E06",
        "Image vs text only on non-demo posts",
        "An image (annotated screenshot or chart) beats text only on non-demo posts.",
        FactorSpec(
            name="image",
            lever="media",
            control="Text",
            treatment="Image",
            applies_to=AppliesTo(post_types=ORIGINALS, labels_not={"content_kind": ["demo"]}),
        ),
        mechanism="Media features; the first image is embedded.",
        detectability="1.5x at about .50",
        horizon_text="4 weeks",
    ),
    _s(
        "E07",
        "Reference content vs build-log updates",
        "Reference content (cheat sheets, benchmarks) beats build-log updates.",
        FactorSpec(name="reference", lever="content_kind", control="build_log", treatment="reference"),
        mechanism="Shares weigh heavily; bookmarks and reposts stand in for unobservable shares.",
        detectability="1.5x at .50-.65 over 4 weeks",
        horizon_text="4-8 weeks",
        secondary=("bookmarks_per_1k", "follows"),
    ),
    _s(
        "E08",
        "Engager-peak window vs off-peak",
        "Posting in the engager-peak window beats off-peak.",
        FactorSpec(name="peak", lever="posting_window", control="off_peak", treatment="engager_peak"),
        mechanism="Time to first like gates the first-like index.",
        detectability="1.5x at .29-.37; 2x at .65-.78",
        horizon_text="12-16 weeks",
        horizon_weeks=12.0,
        design="switchback",
        metric="daily_mean_log_i48",
        note="Switchback in 4-day blocks (2 washout, 2 measured). Over 8 weeks: needs the owner.",
    ),
    _s(
        "E09",
        "2 vs 4 originals a day",
        "Four originals a day beat two on total daily reach.",
        FactorSpec(name="four_a_day", lever="frequency", control="2", treatment="4"),
        mechanism="Author diversity decays as 0.75*0.5^k + 0.25; the ledger shows a plateau.",
        detectability="as E08",
        horizon_text="12-16 weeks",
        horizon_weeks=12.0,
        design="switchback",
        metric="daily_sum_i48",
        note="Switchback in 4-day blocks; per-post designs stay blocked within each day. Needs the owner.",
    ),
)

BY_KEY = {s.key: s for s in SUGGESTIONS}
