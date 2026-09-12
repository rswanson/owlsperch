"""Small filesystem utilities shared across subcommands."""

from __future__ import annotations

import os
import tempfile
from pathlib import Path


def atomic_write_text(path: Path, text: str) -> None:
    """Write `text` to `path` atomically.

    Writes to a temp file in the same directory (so the final `os.replace`
    is a same-filesystem rename, not a copy) and only then replaces `path`
    with it -- a crash, or an interrupted/failing write, never leaves `path`
    truncated or partially written; a reader always sees either the old
    content or the new content, never a mix.

    The temp file is created via `tempfile.NamedTemporaryFile` so concurrent
    writers targeting the same `path` never collide on a shared temp name.
    If `path` already exists, its permission bits are copied onto the temp
    file before the replace so the write doesn't silently change the file's
    mode; for a new `path`, the temp file is left at its umask-default mode.
    """
    tmp_file = tempfile.NamedTemporaryFile(
        dir=path.parent,
        prefix=f".{path.name}.",
        suffix=".tmp",
        mode="w",
        delete=False,
    )
    tmp_path = Path(tmp_file.name)
    try:
        with tmp_file:
            tmp_file.write(text)
        if path.exists():
            os.chmod(tmp_path, path.stat().st_mode & 0o7777)
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
