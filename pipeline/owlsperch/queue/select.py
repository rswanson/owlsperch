"""`owlsperch queue next` -- selecting pending segments for a subagent wave
and marking them `in_progress`, per spec 4.5 and B5 acceptance criterion 1.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from owlsperch.fsutil import atomic_write_text
from owlsperch.queue.common import now_iso
from owlsperch.queue.prompt import render_prompt_to_file
from owlsperch.segment.runner import Segment


@dataclass
class SelectedSegment:
    seg_id: str
    #: Relative to `$OWLSPERCH_DATA`.
    segment_path: str
    kind_hint: str
    #: Absolute path (as a string) to the rendered subagent prompt.
    prompt_path: str

    def to_json(self) -> dict[str, Any]:
        return {
            "seg_id": self.seg_id,
            "segment_path": self.segment_path,
            "kind_hint": self.kind_hint,
            "prompt_path": self.prompt_path,
        }


def select_and_mark(
    book_id: str,
    *,
    data_dir: Path,
    tier: str = "haiku",
    limit: int,
    kind: str = "spell",
    manifest_path: Path | None = None,
    schemas_dir: Path | None = None,
) -> list[SelectedSegment]:
    """Select up to `limit` segments of `book_id` that are `status ==
    "pending"`, `tier == tier`, and `kind_hint == kind` (default "spell" --
    this batch only has a schema for that kind; other kinds are simply never
    selected, and show up in `owlsperch queue summary`'s per-kind counts
    instead). Each selected segment is atomically marked `in_progress` with
    an `in_progress_since` timestamp, and has its subagent prompt rendered to
    `prompts/<book_id>/<seg_id>.md` before being returned -- an
    already-`in_progress` segment is never reselected by a later call.

    Segment files are visited in filename order (`<book_id>-p<NNNN>-<NN>`),
    i.e. book order, so a rerun with the same arguments makes deterministic
    progress through the book.
    """
    seg_dir = data_dir / "segments" / book_id
    if not seg_dir.is_dir() or limit <= 0:
        return []

    selected: list[SelectedSegment] = []
    for path in sorted(seg_dir.glob(f"{book_id}-*.json")):
        if len(selected) >= limit:
            break

        segment = Segment.model_validate_json(path.read_text())
        if segment.status != "pending" or segment.tier != tier or segment.kind_hint != kind:
            continue

        segment.status = "in_progress"
        segment.in_progress_since = now_iso()
        atomic_write_text(path, segment.model_dump_json(indent=2) + "\n")

        prompt_path = render_prompt_to_file(
            segment, data_dir=data_dir, manifest_path=manifest_path, schemas_dir=schemas_dir
        )
        selected.append(
            SelectedSegment(
                seg_id=segment.seg_id,
                segment_path=path.relative_to(data_dir).as_posix(),
                kind_hint=segment.kind_hint,
                prompt_path=str(prompt_path),
            )
        )

    return selected
