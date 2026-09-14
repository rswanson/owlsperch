"""`owlsperch queue run <book_id> --dry-run --fixtures <dir>` -- an in-process
driver over the whole select -> (fake) subagent -> complete -> validate loop
(spec 4.5; batch B8 acceptance criterion 7), so the escalation state machine
can be unit tested without launching real Agent-tool subagents (that's the
`/extract` skill's job, driven by the real `owlsperch queue next|complete`
and `owlsperch validate` commands).

`FixtureSubagent` is the only backend this batch: for a call to segment
`<seg_id>` it reads `<fixtures_dir>/<seg_id>/<n>.json` (n = 1, 2, 3, ...,
one per call to that segment; running out of fixture files for a segment
that gets selected again is an error) and expects
`{"files": {"<rel-path-under-data-dir>": {...json...}, ...}, "reply":
{...the subagent's final reply object...}}` -- every listed file is written
under `data_dir` (so a fixture can write a record file the way a real
subagent would) before `reply` is JSON-dumped and returned as the
subagent's final message text, exactly like a real Agent-tool subagent's
final message would be handed to `owlsperch queue complete`.

`drive_dry_run` repeats `select_and_mark` -> subagent -> `complete_segment`
-> re-validate the whole book, until a wave selects nothing (respecting
`--tier` when given -- otherwise `select_and_mark`'s own default picks
whatever tier currently has pending work -- and `--limit` as a total cap
across every wave). Each wave's re-validate calls `validate_record_files`
once over every discovered record file (batch B10c-mand4), not
`validate_record_file` in a loop -- looping the single-record wrapper would
write each segment back once per record again instead of once per wave,
losing the atomic per-segment write-back `owlsperch.validate.runner`'s
module docstring describes.
"""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from owlsperch.queue.complete import complete_segment
from owlsperch.queue.select import select_and_mark
from owlsperch.queue.summary import QueueSummary, compute_summary
from owlsperch.text.runner import default_data_dir
from owlsperch.validate.loader import CompiledSchemas, discover_record_files
from owlsperch.validate.runner import build_validation_context, validate_record_files


class FixtureExhaustedError(Exception):
    """Raised when a segment is selected again but has no next fixture file
    to serve -- almost always a sign the fixture directory doesn't have
    enough replies scripted for the scenario under test."""


class FixtureUnsafePathError(Exception):
    """Raised when a fixture's `files` mapping names a path that would
    resolve outside `data_dir` (e.g. via a `..` component or an absolute
    path) -- a fixture file must never be able to write anywhere else on
    disk."""


class Subagent(Protocol):
    def run(self, seg_id: str, *, data_dir: Path) -> str: ...


@dataclass
class FixtureSubagent:
    fixtures_dir: Path
    #: seg_id -> number of calls served so far, for computing the next
    #: fixture filename (`<n>.json`, 1-indexed).
    _calls: dict[str, int] = field(default_factory=dict)
    #: seg_id -> fixture filenames served, in call order -- for tests to
    #: assert the exact sequence a scenario went through.
    served: dict[str, list[str]] = field(default_factory=dict)

    def run(self, seg_id: str, *, data_dir: Path) -> str:
        n = self._calls.get(seg_id, 0) + 1
        self._calls[seg_id] = n
        fixture_path = self.fixtures_dir / seg_id / f"{n}.json"
        if not fixture_path.is_file():
            raise FixtureExhaustedError(
                f"no fixture at {fixture_path} for call #{n} to segment '{seg_id}'"
            )
        payload = json.loads(fixture_path.read_text())
        data_dir_resolved = data_dir.resolve()
        for rel_path, content in payload.get("files", {}).items():
            out_path = (data_dir / rel_path).resolve()
            try:
                out_path.relative_to(data_dir_resolved)
            except ValueError:
                raise FixtureUnsafePathError(
                    f"fixture {fixture_path} names a 'files' path that resolves outside "
                    f"data_dir: {rel_path!r}"
                ) from None
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(content, indent=2) + "\n")
        self.served.setdefault(seg_id, []).append(f"{n}.json")
        return json.dumps(payload["reply"])


@dataclass
class DriveResult:
    total_calls: int
    served: dict[str, list[str]]
    summary: QueueSummary


def drive_dry_run(
    book_id: str,
    *,
    fixtures_dir: Path,
    data_dir: Path,
    tier: str | None = None,
    limit: int | None = None,
    kind: str | None = None,
    schemas_dir: Path | None = None,
    manifest_path: Path | None = None,
) -> DriveResult:
    """Run the extract loop in-process against `FixtureSubagent(fixtures_dir)`
    until a wave selects nothing, capped at `limit` total subagent calls
    (`None` for unlimited). Each wave: `select_and_mark` (one tier's worth,
    or whatever tier currently has pending work when `tier` is `None`) ->
    one `subagent.run` + `complete_segment` per selected segment -> a full
    re-validate of the book's existing record files (mirrors the `/extract`
    skill running `owlsperch validate <book_id>` after every wave)."""
    subagent = FixtureSubagent(fixtures_dir=fixtures_dir)
    compiled = CompiledSchemas.load(schemas_dir)

    total_calls = 0
    remaining = limit
    while remaining is None or remaining > 0:
        wave_limit = remaining if remaining is not None else 1_000_000
        selected = select_and_mark(
            book_id,
            data_dir=data_dir,
            tier=tier,
            limit=wave_limit,
            kind=kind,
            manifest_path=manifest_path,
            schemas_dir=schemas_dir,
        )
        if not selected:
            break

        for item in selected:
            reply_text = subagent.run(item.seg_id, data_dir=data_dir)
            complete_segment(item.seg_id, reply_text, data_dir=data_dir)
            total_calls += 1
            if remaining is not None:
                remaining -= 1

        context = build_validation_context(data_dir)
        validate_record_files(
            discover_record_files(data_dir, book_id),
            data_dir=data_dir,
            compiled=compiled,
            context=context,
            book_ids={book_id},
        )

    return DriveResult(
        total_calls=total_calls,
        served=subagent.served,
        summary=compute_summary(book_id, data_dir=data_dir),
    )


def run_queue_run(
    book_id: str,
    *,
    dry_run: bool,
    fixtures_dir: Path | None = None,
    tier: str | None = None,
    limit: int | None = None,
    kind: str | None = None,
    data_dir: Path | None = None,
    schemas_dir: Path | None = None,
    manifest_path: Path | None = None,
    json_output: bool = False,
    out: Any = None,
) -> int:
    out = out if out is not None else sys.stdout
    if not dry_run:
        print(
            "error: `owlsperch queue run` only supports --dry-run in this batch -- "
            "real subagents are launched by the /extract skill.",
            file=sys.stderr,
        )
        return 1
    if fixtures_dir is None:
        print("error: --dry-run requires --fixtures <dir>", file=sys.stderr)
        return 1

    data_dir = data_dir if data_dir is not None else default_data_dir()
    result = drive_dry_run(
        book_id,
        fixtures_dir=fixtures_dir,
        data_dir=data_dir,
        tier=tier,
        limit=limit,
        kind=kind,
        schemas_dir=schemas_dir,
        manifest_path=manifest_path,
    )

    if json_output:
        print(json.dumps(result.summary.to_json()), file=out)
    else:
        print(result.summary.render(), file=out)
    return 0
