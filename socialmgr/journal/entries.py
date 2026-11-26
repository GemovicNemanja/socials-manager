"""Weekly entries: markdown files with a small front matter, kept in ``<data>/journal/entries``.

An entry is a draft until the owner publishes it (``status: published`` and ``published_at``).
Only published entries ever reach the public site. Experiment write-ups ("why I tested it",
"what I changed", the one-line answer) live beside them in ``<data>/journal/experiments/<key>.md``.

Front matter is ``key: value`` lines between ``---`` fences; values are plain strings, dates in
ISO format, and lists comma-separated. Kept deliberately simple so an editor can't break it.
"""

import re
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path

ENTRY_FIELDS = ("title", "week", "period_start", "period_end", "status", "published_at", "experiments")
SLUG_RE = re.compile(r"^week-(\d+)$")


class EntryError(ValueError):
    pass


@dataclass
class Entry:
    path: Path
    title: str
    week: int
    period_start: date
    period_end: date
    status: str  # draft | published
    published_at: datetime | None
    experiments: list[str] = field(default_factory=list)
    body: str = ""

    @property
    def slug(self) -> str:
        return f"week-{self.week}"

    @property
    def published(self) -> bool:
        return self.status == "published" and self.published_at is not None

    @property
    def summary(self) -> str:
        """The first paragraph of the body, without markdown emphasis."""
        for para in re.split(r"\n\s*\n", self.body.strip()):
            text = para.strip()
            if text and not text.startswith(("#", "-", "*", ">", "|", "```")):
                return re.sub(r"[*_`]", "", " ".join(text.split()))
        return ""


def _split(text: str) -> tuple[dict[str, str], str]:
    if not text.startswith("---"):
        raise EntryError("an entry starts with a '---' front matter block")
    parts = text.split("\n---", 1)
    if len(parts) != 2:
        raise EntryError("the front matter block isn't closed with '---'")
    meta: dict[str, str] = {}
    for line in parts[0].splitlines()[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition(":")
        if not sep:
            raise EntryError(f"front matter line without ':' — {line!r}")
        meta[key.strip()] = value.strip().strip('"').strip("'")
    body = parts[1].split("\n", 1)[1] if "\n" in parts[1] else ""
    return meta, body.lstrip("\n")


def parse(path: Path) -> Entry:
    meta, body = _split(path.read_text(encoding="utf-8"))
    try:
        published_raw = meta.get("published_at") or ""
        published = datetime.fromisoformat(published_raw) if published_raw else None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=UTC)
        return Entry(
            path=path,
            title=meta["title"],
            week=int(meta["week"]),
            period_start=date.fromisoformat(meta["period_start"]),
            period_end=date.fromisoformat(meta["period_end"]),
            status=meta.get("status", "draft"),
            published_at=published,
            experiments=[k.strip() for k in meta.get("experiments", "").split(",") if k.strip()],
            body=body,
        )
    except KeyError as exc:
        raise EntryError(f"{path.name}: missing front matter field {exc.args[0]!r}") from exc
    except ValueError as exc:
        raise EntryError(f"{path.name}: {exc}") from exc


def render(entry: Entry) -> str:
    lines = [
        "---",
        f"title: {entry.title}",
        f"week: {entry.week}",
        f"period_start: {entry.period_start.isoformat()}",
        f"period_end: {entry.period_end.isoformat()}",
        f"status: {entry.status}",
        f"published_at: {entry.published_at.isoformat() if entry.published_at else ''}",
        f"experiments: {', '.join(entry.experiments)}",
        "---",
        "",
        entry.body.strip(),
        "",
    ]
    return "\n".join(lines)


def entries_dir(journal_dir: Path) -> Path:
    return journal_dir / "entries"


def load_all(journal_dir: Path) -> list[Entry]:
    """Every entry, newest week first. A broken file raises with its name."""
    folder = entries_dir(journal_dir)
    if not folder.exists():
        return []
    entries = [parse(p) for p in sorted(folder.glob("week-*.md"))]
    weeks = [e.week for e in entries]
    dupes = {w for w in weeks if weeks.count(w) > 1}
    if dupes:
        raise EntryError(f"more than one entry for week(s) {sorted(dupes)}")
    return sorted(entries, key=lambda e: e.week, reverse=True)


def path_for(journal_dir: Path, week: int) -> Path:
    return entries_dir(journal_dir) / f"week-{week:02d}.md"


def save(entry: Entry) -> None:
    entry.path.parent.mkdir(parents=True, exist_ok=True)
    entry.path.write_text(render(entry), encoding="utf-8")


def publish(journal_dir: Path, week: int, now: datetime) -> Entry:
    """Mark a week's entry published (it reaches the site at the next publish)."""
    match = [e for e in load_all(journal_dir) if e.week == week]
    if not match:
        raise EntryError(f"there's no entry for week {week}; draft one first")
    entry = match[0]
    if not entry.body.strip():
        raise EntryError(f"week {week}'s entry has no text yet")
    if "TODO" in entry.body:
        raise EntryError(f"week {week}'s entry still has TODO markers; finish or remove them first")
    entry.status = "published"
    entry.published_at = entry.published_at or now
    save(entry)
    return entry


@dataclass
class ExperimentNotes:
    answer: str | None = None
    why: str | None = None
    changed: str | None = None


NOTE_HEADINGS = {"answer": "answer", "why i tested it": "why", "what i changed": "changed"}


def experiment_notes(journal_dir: Path, key: str) -> ExperimentNotes:
    """Owner-written sections of ``experiments/<key>.md``.

    Headings: ``## Answer``, ``## Why I tested it``, ``## What I changed``.
    """
    path = journal_dir / "experiments" / f"{key.lower()}.md"
    notes = ExperimentNotes()
    if not path.exists():
        return notes
    current: str | None = None
    buf: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        heading = re.match(r"^##\s+(.+?)\s*$", line)
        if heading:
            current = NOTE_HEADINGS.get(heading.group(1).strip().lower())
            continue
        if current:
            buf.setdefault(current, []).append(line)
    for attr, lines in buf.items():
        text = "\n".join(lines).strip()
        if text and "TODO" not in text:
            setattr(notes, attr, text)
    return notes
