"""``python -m socialmgr``: the CLI without the console-script entry point.

Useful when macOS marks the virtualenv's .pth files hidden (Python 3.13 skips hidden .pth files,
so ``uv run socialmgr`` can't import the package): ``uv run python -m socialmgr run`` still works
from the repository folder.
"""

from socialmgr.cli import app

app()
