"""Experiment arms in plain words: what the owner does for a slot, and how a protocol reads.

Pure: no database. The web views (``services/experiment_views.py``) use these to turn a slot's
drawn arms into instructions such as "put the link in the first self-reply, not in the post".
"""

from collections.abc import Iterable

from socialmgr.experiments.specs import AppliesTo

# (lever, arm value) -> instruction. Values come from the E01-E09 suggestions (suggestions.py).
_KNOWN: dict[tuple[str, str], str] = {
    ("format", "Narrated video"): "attach a narrated native video over 10 s",
    ("format", "Image"): "use a static screenshot or GIF, no video",
    ("link_placement", "first_self_reply"): "put the link in the first self-reply, not in the post",
    ("link_placement", "body"): "put the link in the post itself",
    ("hook", "outcome_first"): "open with the outcome: lead with the result",
    ("hook", "standard"): "open the way you usually do",
    ("quote", "quote_in_niche"): "quote an in-niche account's post and add your commentary",
    ("quote", "original"): "post the same commentary as a standalone original",
    ("thread_vs_long", "single_long"): "write it as one long post",
    ("thread_vs_long", "thread"): "write it as a thread",
    ("media", "Image"): "attach an image (annotated screenshot or chart)",
    ("media", "Text"): "text only, no media",
    ("content_kind", "reference"): "make it reference content (a cheat sheet or benchmark)",
    ("content_kind", "build_log"): "make it a build-log update",
    ("posting_window", "engager_peak"): "post in the engager-peak window",
    ("posting_window", "off_peak"): "post off-peak",
}

LEVER_LABELS: dict[str, str] = {
    "format": "Format",
    "media": "Media",
    "link_placement": "Link placement",
    "hook": "Hook",
    "cta": "Call to action",
    "content_kind": "Content kind",
    "topic_ordering": "Topic order",
    "quote": "Quote vs original",
    "thread_vs_long": "Thread vs long post",
    "posting_window": "Posting window",
    "frequency": "Cadence",
}

METRIC_LABELS: dict[str, str] = {
    "log_i48": "impressions at 48h",
    "daily_mean_log_i48": "mean 48h impressions per post, per measured day",
    "daily_sum_i48": "total 48h impressions per posting day",
}


def lever_label(lever: str) -> str:
    return LEVER_LABELS.get(lever, lever.replace("_", " ").capitalize())


def metric_label(metric: str) -> str:
    return METRIC_LABELS.get(metric, metric.replace("_", " "))


def arm_instruction(lever: str, value: str) -> str:
    """One arm as an instruction: ("link_placement", "first_self_reply") -> "put the link in ..."."""
    known = _KNOWN.get((lever, value))
    if known:
        return known
    if lever == "frequency" and value.isdigit():
        return f"post {value} originals that day"
    return f"{lever_label(lever).lower()}: {value.replace('_', ' ')}"


def slot_instructions(
    local_time: str, tz: str, arms: Iterable[tuple[str, str]], cancelled: bool = False
) -> str:
    """Everything to do for one slot, given its (lever, value) arms."""
    if cancelled:
        return "Not used: the day's switchback condition picked the other slot list. Don't post here."
    steps = [arm_instruction(lever, value) for lever, value in arms]
    when = f"Post at {local_time} ({tz}), within 30 minutes"
    if not steps:
        return f"{when}; no experiment lever applies to this slot."
    return f"{when}: " + "; ".join(steps) + "."


def applies_to_text(applies: AppliesTo) -> str:
    """AppliesTo in words: "originals and quotes tagged link_post=true"."""
    names = {"original": "originals", "quote": "quotes", "reply": "replies", "self_reply": "thread replies"}
    types = [names.get(t, t) for t in applies.post_types]
    text = " and ".join(types) if len(types) <= 2 else ", ".join(types[:-1]) + " and " + types[-1]
    tags = [f"{k}={'/'.join(v)}" for k, v in applies.labels.items()]
    not_tags = [f"{k}={'/'.join(v)}" for k, v in applies.labels_not.items()]
    if tags:
        text += " tagged " + ", ".join(tags)
    if not_tags:
        text += " not tagged " + ", ".join(not_tags)
    return text


def design_label(design: str) -> str:
    return "Switchback, 4-day" if design == "switchback" else "Per-post, randomized"
