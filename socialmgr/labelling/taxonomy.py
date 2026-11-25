"""The versioned label set (MODEL.md §1.4). Changing any list here means bumping ``LABEL_SET_VERSION``."""

from typing import Final

LABEL_SET_VERSION: Final = "ls-1"

# Rule-based (deterministic, from the post itself).
FORMATS: Final = ("Text", "Image", "Video", "Text + link", "Thread", "Poll")

# LLM-based. Topic maps onto MODEL §1.4's software_development / ai / science_technology niche.
TOPICS: Final = ("ai", "software_development", "science_technology", "career", "personal", "other")
CONTENT_KINDS: Final = (
    "demo",
    "build_log",
    "reference",
    "explainer",
    "opinion",
    "launch",
    "question",
    "other",
)
HOOK_TYPES: Final = ("question", "bold_claim", "story", "list", "how_to", "number", "none")

LLM_FIELDS: Final = {"topic": TOPICS, "content_kind": CONTENT_KINDS, "hook_type": HOOK_TYPES}

# When several sources label the same field, the highest wins.
SOURCE_PRECEDENCE: Final = {"owner": 4, "provider": 3, "llm": 2, "rule": 1}
