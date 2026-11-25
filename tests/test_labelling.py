"""Label rules, MinHash thresholds and the LLM labellers (no network: runners and clients are faked)."""

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select

from socialmgr.core.config import PluginConfig, RunConfig
from socialmgr.core.types import LabelInput, LabelResult
from socialmgr.db import migrate
from socialmgr.db.models import Briefing, Label, Post
from socialmgr.db.session import make_engine, session_factory
from socialmgr.labelling import minhash, rules
from socialmgr.labelling.llm import (
    AnthropicApiLabeler,
    ClaudeCodeLabeler,
    LabelerError,
    output_schema,
    parse_labels,
)
from socialmgr.services import pipeline, runs
from socialmgr.services.plugins import load_plugins
from tests.conftest import make_settings

ITEMS = [
    LabelInput(key="post:1", text="Shipped a CLI that forecasts my post reach"),
    LabelInput(key="post:2", text="?"),
]


def test_format_rules() -> None:
    assert rules.format_label(post_type="original", media_kind="video", has_url=True) == "Video"
    assert rules.format_label(post_type="original", media_kind="photo", has_url=False) == "Image"
    assert rules.format_label(post_type="self_reply", media_kind=None, has_url=False) == "Thread"
    assert rules.format_label(post_type="original", media_kind=None, has_url=True) == "Text + link"
    assert rules.format_label(post_type="original", media_kind=None, has_url=False) == "Text"


def test_text_features_weight_links_and_wide_characters() -> None:
    f = rules.text_features("hi https://example.com/a/very/long/path/that/is/long @bob #a #b\nok")
    assert f.has_url and f.n_mentions == 1 and f.n_hashtags == 2 and f.newlines == 1
    assert f.text_len_weighted == len("hi  @bob #a #b\nok") + 23
    assert rules.text_features("日本").text_len_weighted == 4


def test_minhash_separates_near_duplicates_from_different_posts() -> None:
    a = minhash.signature("I built a small tool that forecasts how my posts will do after 48 hours")
    b = minhash.signature("I built a small tool that forecasts how my posts will do after 48 hours!!")
    c = minhash.signature("Today I learned how SQLite WAL mode lets readers and writers overlap safely")
    assert minhash.similarity(a, b) >= minhash.NEAR_DUPLICATE
    assert minhash.similarity(a, c) < 0.2
    assert minhash.similarity(a, minhash.from_bytes(minhash.to_bytes(a))) == 1.0
    assert minhash.similarity(minhash.signature(""), a) == 0.0


def test_parse_labels_keeps_only_the_label_set() -> None:
    payload = {
        "labels": [
            {"key": "post:1", "topic": "ai", "content_kind": "demo", "hook_type": "none"},
            {"key": "post:2", "topic": "cooking", "content_kind": "demo", "hook_type": "nope"},
            {"key": "post:9", "topic": "ai", "content_kind": "demo", "hook_type": "none"},
        ]
    }
    results = parse_labels(payload, {"post:1", "post:2"})
    assert results == [
        LabelResult(key="post:1", values={"topic": "ai", "content_kind": "demo", "hook_type": "none"}),
        LabelResult(key="post:2", values={"content_kind": "demo"}),
    ]


def test_claude_code_labeler_calls_headless_claude_with_the_schema() -> None:
    seen: dict[str, Any] = {}

    def runner(argv: list[str], stdin: str) -> str:
        seen["argv"], seen["stdin"] = argv, stdin
        out = {
            "labels": [{"key": "post:1", "topic": "ai", "content_kind": "demo", "hook_type": "bold_claim"}]
        }
        return json.dumps({"type": "result", "is_error": False, "structured_output": out})

    results = ClaudeCodeLabeler(runner=runner).label(ITEMS)
    argv = seen["argv"]
    assert argv[:4] == ["claude", "-p", "--model", "haiku"]
    assert json.loads(argv[argv.index("--json-schema") + 1]) == output_schema()
    assert "Shipped a CLI" in seen["stdin"] and "ls-1" in seen["stdin"]
    assert results[0].values["hook_type"] == "bold_claim"


def test_claude_code_labeler_raises_on_error_result() -> None:
    def runner(argv: list[str], stdin: str) -> str:
        return json.dumps({"is_error": True, "result": "usage limit reached"})

    with pytest.raises(LabelerError, match="usage limit"):
        ClaudeCodeLabeler(runner=runner).label(ITEMS)


def test_anthropic_api_labeler_uses_structured_outputs() -> None:
    calls: list[dict[str, Any]] = []

    class Messages:
        def parse(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            parsed = kwargs["output_format"](
                labels=[
                    {"key": "post:2", "topic": "other", "content_kind": "question", "hook_type": "question"}
                ]
            )
            return SimpleNamespace(parsed_output=parsed)

    results = AnthropicApiLabeler(client=SimpleNamespace(messages=Messages())).label(ITEMS)
    assert calls[0]["model"] == "claude-haiku-5-5"
    assert results == [
        LabelResult(
            key="post:2", values={"topic": "other", "content_kind": "question", "hook_type": "question"}
        )
    ]


class FakeLabeler:
    name = "fake"
    version = "1"

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0

    def label(self, items: Any) -> list[LabelResult]:
        self.calls += 1
        if self.fail:
            raise LabelerError("claude isn't logged in")
        return [LabelResult(key=i.key, values={"topic": "ai", "content_kind": "build_log"}) for i in items]


def _run_with_labeler(tmp_path: Path, labeler: Any) -> tuple[Any, Any]:
    settings = make_settings(
        tmp_path, plugins=PluginConfig(labeler="none"), run=RunConfig(regime_check=False, backup=False)
    )
    engine = make_engine(settings)
    migrate.upgrade(engine)
    session = session_factory(engine)()
    plugins = load_plugins(settings)
    plugins = type(plugins)(**{**plugins.__dict__, "labeler": labeler})
    outcome = runs.execute(session, settings, plugins, clock=lambda: datetime(2026, 10, 20, 6, tzinfo=UTC))
    return session, outcome


def test_label_step_adds_rule_and_llm_labels_with_precedence(tmp_path: Path) -> None:
    labeler = FakeLabeler()
    session, outcome = _run_with_labeler(tmp_path, labeler)
    result = outcome.steps["label"]["result"]
    assert result["llm_labelled"] == result["rule_labelled"] > 0 and labeler.calls == 1
    post = session.scalars(select(Post)).first()
    assert post.text_len_weighted and post.minhash and post.own_posts_48h is not None
    sources = set(session.scalars(select(Label.source).where(Label.post_id == post.id)))
    assert sources == {"provider", "rule", "llm"}
    labels = pipeline.effective_labels(session, post.id)
    assert labels["content_kind"] == "build_log"  # llm
    provider_format = session.scalar(
        select(Label.value).where(
            Label.post_id == post.id, Label.field == "format", Label.source == "provider"
        )
    )
    assert labels["format"] == provider_format  # provider beats rule


def test_label_step_failure_is_a_caveat_not_a_failed_run(tmp_path: Path) -> None:
    session, outcome = _run_with_labeler(tmp_path, FakeLabeler(fail=True))
    assert outcome.steps["label"]["status"] == "done"
    assert any("logged in" in c for c in outcome.steps["label"]["result"]["caveats"])
    briefing = session.scalar(select(Briefing.summary).where(Briefing.run_id == outcome.run_id))
    assert briefing is not None and "LLM labels skipped" in briefing


def test_claude_code_labeler_falls_back_to_the_text_result() -> None:
    labels = {"labels": [{"key": "post:1", "topic": "ai", "content_kind": "demo", "hook_type": "none"}]}

    def runner(argv: list[str], stdin: str) -> str:
        return json.dumps({"is_error": False, "subtype": "success", "result": json.dumps(labels)})

    assert ClaudeCodeLabeler(runner=runner).label(ITEMS)[0].values["topic"] == "ai"


def test_claude_code_labeler_says_why_when_nothing_usable_comes_back() -> None:
    def runner(argv: list[str], stdin: str) -> str:
        return json.dumps({"is_error": False, "subtype": "error_max_turns", "result": "I couldn't"})

    with pytest.raises(LabelerError, match="error_max_turns"):
        ClaudeCodeLabeler(runner=runner).label(ITEMS)
