"""Read and update ``.env`` in place: keep comments and unrelated settings, change only named keys."""

import os
import re
from pathlib import Path

_LINE = re.compile(r"^(?P<comment>\s*#\s*)?(?P<key>[A-Z][A-Z0-9_]*)=(?P<value>.*)$")
_PLAIN = re.compile(r"^[A-Za-z0-9_@./:,+\-]*$")
_JSON = re.compile(r"^[\[{][^\s#\'\\]*[\]}]$")


def quote(value: str) -> str:
    """A value as python-dotenv reads it back unchanged."""
    if _PLAIN.match(value) or _JSON.match(value):
        return value
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _unquote(raw: str) -> str:
    raw = raw.strip()
    if len(raw) >= 2 and raw[0] == raw[-1] == '"':
        return re.sub(r"\\(.)", r"\1", raw[1:-1])
    if len(raw) >= 2 and raw[0] == raw[-1] == "'":
        return raw[1:-1]
    return raw.split(" #", 1)[0].strip()


def read(path: Path) -> dict[str, str]:
    """Active (uncommented) settings."""
    if not path.exists():
        return {}
    out: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        m = _LINE.match(line)
        if m and not m.group("comment"):
            out[m.group("key")] = _unquote(m.group("value"))
    return out


def update(path: Path, values: dict[str, str], template: Path | None = None) -> None:
    """Set ``values`` in ``path`` (created from ``template`` if missing), owner-readable only.

    An active line for a key is replaced; otherwise its commented-out default line is; otherwise the
    setting is appended.
    """
    if path.exists():
        lines = path.read_text(encoding="utf-8").splitlines()
    elif template is not None and template.exists():
        lines = template.read_text(encoding="utf-8").splitlines()
    else:
        lines = []
    active = {m.group("key") for line in lines if (m := _LINE.match(line)) and not m.group("comment")}
    pending = dict(values)
    for i, line in enumerate(lines):
        m = _LINE.match(line)
        if not m or m.group("key") not in pending:
            continue
        key = m.group("key")
        if m.group("comment") and key in active:
            continue  # the active line elsewhere is the one to change
        lines[i] = f"{key}={quote(pending.pop(key))}"
    lines += [f"{k}={quote(v)}" for k, v in pending.items()]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.chmod(path, 0o600)
