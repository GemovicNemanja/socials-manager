"""Deterministic labels and text features computed from the post alone (MODEL.md §1.4)."""

import re
import unicodedata
from dataclasses import dataclass

URL_RE = re.compile(r"https?://\S+")
URL_WEIGHT = 23  # X counts every link as 23 characters


@dataclass(frozen=True)
class TextFeatures:
    text_len_weighted: int
    newlines: int
    n_mentions: int
    n_hashtags: int
    has_url: bool


def _char_weight(ch: str) -> int:
    # X's weighted length: most Latin and punctuation count 1, CJK and emoji count 2.
    code = ord(ch)
    if code <= 0x10FF or 0x2000 <= code <= 0x200D or 0x2010 <= code <= 0x201F or 0x2032 <= code <= 0x2037:
        return 1
    return 2


def text_features(text: str) -> TextFeatures:
    urls = URL_RE.findall(text)
    stripped = URL_RE.sub("", text)
    weighted = sum(_char_weight(ch) for ch in unicodedata.normalize("NFC", stripped)) + URL_WEIGHT * len(urls)
    return TextFeatures(
        text_len_weighted=weighted,
        newlines=text.count("\n"),
        n_mentions=len(re.findall(r"(?<!\w)@\w{1,15}", text)),
        n_hashtags=len(re.findall(r"(?<!\w)#\w+", text)),
        has_url=bool(urls),
    )


def format_label(*, post_type: str, media_kind: str | None, has_url: bool, is_poll: bool = False) -> str:
    """One format per post, in order of what dominates how it's shown."""
    if is_poll:
        return "Poll"
    if media_kind in ("video", "animated_gif"):
        return "Video"
    if media_kind == "photo":
        return "Image"
    if post_type == "self_reply":
        return "Thread"
    if has_url:
        return "Text + link"
    return "Text"
