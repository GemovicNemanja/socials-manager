"""LLM labellers. Each returns only values from the label set; anything else is dropped."""

import json
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Sequence
from typing import Any

from pydantic import BaseModel, ConfigDict

from socialmgr.core.types import LabelInput, LabelResult
from socialmgr.labelling.taxonomy import LABEL_SET_VERSION, LLM_FIELDS

BATCH = 20
HAIKU_API_MODEL = "claude-haiku-5-5"

INSTRUCTIONS = f"""You label social media posts by a software developer who posts about AI and building
software. For each post return exactly one value per field, chosen from these lists (label set
{LABEL_SET_VERSION}):
{json.dumps({k: list(v) for k, v in LLM_FIELDS.items()}, indent=1)}
topic: what the post is about. content_kind: what kind of post it is. hook_type: how the first line
tries to get attention ("none" if it doesn't). Echo each post's key. Label every post."""


def output_schema() -> dict[str, Any]:
    item = {
        "type": "object",
        "properties": {"key": {"type": "string"}}
        | {f: {"type": "string", "enum": list(v)} for f, v in LLM_FIELDS.items()},
        "required": ["key", *LLM_FIELDS],
        "additionalProperties": False,
    }
    return {
        "type": "object",
        "properties": {"labels": {"type": "array", "items": item}},
        "required": ["labels"],
        "additionalProperties": False,
    }


def prompt_for(items: Sequence[LabelInput]) -> str:
    posts = [{"key": i.key, "type": i.post_type, "format": i.format, "text": i.text} for i in items]
    return f"{INSTRUCTIONS}\n\nPosts:\n{json.dumps(posts, ensure_ascii=False, indent=1)}"


def parse_labels(payload: Any, keys: set[str]) -> list[LabelResult]:
    """Validate the model's output against the label set; drop unknown keys and values."""
    results = []
    rows = payload.get("labels", []) if isinstance(payload, dict) else []
    for row in rows:
        if not isinstance(row, dict) or row.get("key") not in keys:
            continue
        values = {f: str(row[f]) for f, allowed in LLM_FIELDS.items() if row.get(f) in allowed}
        if values:
            results.append(LabelResult(key=str(row["key"]), values=values))
    return results


def _batches(items: Sequence[LabelInput]) -> list[Sequence[LabelInput]]:
    return [items[i : i + BATCH] for i in range(0, len(items), BATCH)]


class LabelerError(RuntimeError):
    pass


Runner = Callable[[list[str], str], str]  # (argv, stdin) -> stdout


def _run_subprocess(argv: list[str], stdin: str) -> str:
    # Run outside any project folder so a repository's CLAUDE.md, hooks or settings can't change
    # the labelling call.
    proc = subprocess.run(
        argv,
        input=stdin,
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
        cwd=tempfile.gettempdir(),
    )
    if proc.returncode != 0:
        raise LabelerError(f"claude exited with {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout


class ClaudeCodeLabeler:
    """Headless Claude Code (``claude -p``) on the member's own subscription: no API key needed."""

    name = "claude_code"
    version = f"haiku+{LABEL_SET_VERSION}"

    def __init__(self, runner: Runner | None = None, binary: str = "claude") -> None:
        self._runner = runner or _run_subprocess
        self._binary = binary

    def available(self) -> bool:
        return self._runner is not _run_subprocess or shutil.which(self._binary) is not None

    def label(self, items: Sequence[LabelInput]) -> list[LabelResult]:
        if not items:
            return []
        if not self.available():
            raise LabelerError("Claude Code isn't installed (no `claude` on PATH)")
        argv = [
            self._binary,
            "-p",
            "--model",
            "haiku",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(output_schema()),
        ]
        results: list[LabelResult] = []
        for batch in _batches(items):
            out = json.loads(self._runner(argv, prompt_for(batch)))
            if out.get("is_error"):
                raise LabelerError(f"claude returned an error: {str(out.get('result'))[:300]}")
            payload = out.get("structured_output")
            if payload is None:  # older CLIs, or a reply that put the JSON in the text result
                try:
                    payload = json.loads(str(out.get("result") or ""))
                except json.JSONDecodeError:
                    payload = None
            parsed = parse_labels(payload, {i.key for i in batch})
            if not parsed:
                raise LabelerError(
                    f"claude returned no usable labels (subtype {out.get('subtype')!r}): "
                    f"{str(out.get('result'))[:200]}"
                )
            results += parsed
        return results


class _Row(BaseModel):
    model_config = ConfigDict(extra="forbid")
    key: str
    topic: str
    content_kind: str
    hook_type: str


class _Labels(BaseModel):
    labels: list[_Row]


class AnthropicApiLabeler:
    """The Anthropic API with ``SOCIALMGR_ANTHROPIC_API_KEY`` (or ``ANTHROPIC_API_KEY``)."""

    name = "anthropic_api"
    version = f"{HAIKU_API_MODEL}+{LABEL_SET_VERSION}"

    def __init__(self, api_key: str | None = None, client: Any = None) -> None:
        self._api_key = api_key
        self._client = client

    def _get_client(self) -> Any:
        if self._client is None:
            try:
                import anthropic
            except ImportError as exc:  # optional extra
                raise LabelerError("install the API extra: uv sync --extra api") from exc
            self._client = (
                anthropic.Anthropic(api_key=self._api_key) if self._api_key else anthropic.Anthropic()
            )
        return self._client

    def label(self, items: Sequence[LabelInput]) -> list[LabelResult]:
        if not items:
            return []
        client = self._get_client()
        results: list[LabelResult] = []
        for batch in _batches(items):
            response = client.messages.parse(
                model=HAIKU_API_MODEL,
                max_tokens=4000,
                messages=[{"role": "user", "content": prompt_for(batch)}],
                output_format=_Labels,
            )
            parsed = response.parsed_output
            payload = parsed.model_dump() if parsed is not None else {}
            results += parse_labels(payload, {i.key for i in batch})
        return results


class NoLabeler:
    """Rule labels only (fixtures, tests, or members who opt out)."""

    name = "none"
    version = "none"

    def label(self, items: Sequence[LabelInput]) -> list[LabelResult]:
        return []
