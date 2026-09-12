"""Small filesystem utilities shared across subcommands."""

from __future__ import annotations

import os
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically.

    Writes to a temp file in the same directory (so the final `os.replace`
    is a same-filesystem rename, not a copy) and only then replaces `path`
    with it -- a crash, or an interrupted/failing write, never leaves `path`
    truncated or partially written; a reader always sees either the old
    content or the new content, never a mix.
    """
    tmp_path = path.with_name(f".{path.name}.tmp{os.getpid()}")
    try:
        tmp_path.write_text(text)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
