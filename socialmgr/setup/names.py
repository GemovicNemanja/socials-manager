"""What an install is called on this Mac: its app, daily-run job, MCP server and dashboard port.

The normal install has no instance name. A named instance (``SOCIALMGR_INSTANCE=test``) gets its own
of each, so a second, throwaway install can run next to the real one without touching it.
"""

import re
from dataclasses import dataclass

_INSTANCE = re.compile(r"^[a-z0-9][a-z0-9-]{0,19}$")
LIVE_PORT = 8765


def check_instance(value: str) -> str:
    value = value.strip()
    if value and not _INSTANCE.match(value):
        raise ValueError("An instance name is up to 20 lowercase letters, digits or dashes, like `test`.")
    return value


@dataclass(frozen=True)
class Names:
    instance: str = ""

    @property
    def app_name(self) -> str:
        return f"Social Manager ({self.instance})" if self.instance else "Social Manager"

    @property
    def app_id(self) -> str:
        return "local.socialmgr.app" + (f".{self.instance}" if self.instance else "")

    @property
    def run_label(self) -> str:
        return "local.socialmgr.daily-run" + (f".{self.instance}" if self.instance else "")

    @property
    def mcp_name(self) -> str:
        return "socialmgr" + (f"-{self.instance}" if self.instance else "")

    @property
    def default_port(self) -> int:
        """The dashboard's port. The X callback stays on 8765 for every instance (it must match the
        X app), so a named instance serves its dashboard elsewhere and borrows 8765 only to connect."""
        return LIVE_PORT if not self.instance else LIVE_PORT + 1


LIVE = Names()
