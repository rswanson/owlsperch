"""The curated book manifest: schema, loading/validation, and `manifest check`.

See docs/specs/2026-09-12-dnd-reference-site-spec.md, sections "Scope
boundaries" and 4.3, for the field definitions and the in-scope rule this
module implements.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

Edition = Literal["3.0", "3.5"]
Kind = Literal[
    "rulebook",
    "supplement",
    "setting",
    "guide",
    "magazine",
    "errata",
    "update",
    "web_enhancement",
    "index",
    "excluded",
]

#: Kinds that are "override" sources per spec 4.3: they modify another book
#: rather than being an in-scope book themselves.
OVERRIDE_KINDS: frozenset[Kind] = frozenset({"errata", "update", "web_enhancement"})

#: Files ignored when scanning the PDF directory (not manifest entries).
DEFAULT_IGNORED_FILENAMES: frozenset[str] = frozenset({".DS_Store"})

_PUBLISHED_RE = re.compile(r"^\d{4}-\d{2}$")


class ManifestError(Exception):
    """Raised when the manifest fails to load or validate.

    Always names the offending entry's book_id (or its position, if the
    book_id itself couldn't be read) and the field/issue at fault.
    """


class ManifestEntry(BaseModel):
    """One manifest entry, per spec 4.3."""

    model_config = ConfigDict(extra="forbid")

    book_id: str
    title: str
    file: str
    edition: Edition
    kind: Kind
    published: str | None = None
    applies_to: str | None = None
    scanned: bool = False
    preferred_over: str | None = None
    exclude_reason: str | None = None

    @field_validator("published")
    @classmethod
    def _validate_published(cls, value: str | None) -> str | None:
        if value is not None and not _PUBLISHED_RE.match(value):
            raise ValueError(f"published must be YYYY-MM, got {value!r}")
        return value


def _load_raw_entries(path: Path) -> list[dict[str, Any]]:
    try:
        raw = yaml.safe_load(path.read_text())
    except FileNotFoundError as exc:
        raise ManifestError(f"manifest file not found: {path}") from exc
    except yaml.YAMLError as exc:
        raise ManifestError(f"manifest file is not valid YAML: {path}: {exc}") from exc

    if not isinstance(raw, dict) or "entries" not in raw:
        raise ManifestError(f"manifest file must have a top-level 'entries' list: {path}")
    entries = raw["entries"]
    if not isinstance(entries, list):
        raise ManifestError("manifest 'entries' must be a list")
    return entries


def load_manifest(path: Path) -> list[ManifestEntry]:
    """Load and validate the manifest at `path`.

    Raises ManifestError, naming the offending entry's book_id and field,
    on any schema violation or cross-reference problem (bad applies_to,
    excluded entry missing exclude_reason, duplicate book_id, etc).
    """
    raw_entries = _load_raw_entries(path)

    entries: list[ManifestEntry] = []
    for index, raw_entry in enumerate(raw_entries):
        book_id_hint = (
            raw_entry.get("book_id", f"<entry at index {index}>")
            if isinstance(raw_entry, dict)
            else f"<entry at index {index}>"
        )
        try:
            entries.append(ManifestEntry.model_validate(raw_entry))
        except ValidationError as exc:
            fields = ", ".join(".".join(str(p) for p in err["loc"]) for err in exc.errors())
            raise ManifestError(
                f"manifest entry '{book_id_hint}': invalid field(s) [{fields}]: {exc}"
            ) from exc

    _validate_cross_references(entries)
    return entries


def _validate_cross_references(entries: list[ManifestEntry]) -> None:
    book_ids = [e.book_id for e in entries]
    duplicates = {b for b in book_ids if book_ids.count(b) > 1}
    if duplicates:
        raise ManifestError(f"duplicate book_id(s): {', '.join(sorted(duplicates))}")

    known_ids = set(book_ids)

    for entry in entries:
        if entry.kind in OVERRIDE_KINDS:
            if not entry.applies_to:
                raise ManifestError(
                    f"manifest entry '{entry.book_id}': kind '{entry.kind}' requires applies_to"
                )
            if entry.applies_to not in known_ids:
                raise ManifestError(
                    f"manifest entry '{entry.book_id}': applies_to "
                    f"'{entry.applies_to}' is not a known book_id"
                )
        if entry.kind == "excluded" and not entry.exclude_reason:
            raise ManifestError(
                f"manifest entry '{entry.book_id}': kind 'excluded' requires exclude_reason"
            )
        if entry.preferred_over is not None and entry.preferred_over not in known_ids:
            raise ManifestError(
                f"manifest entry '{entry.book_id}': preferred_over "
                f"'{entry.preferred_over}' is not a known book_id"
            )


def in_scope(entry: ManifestEntry, entries: list[ManifestEntry]) -> bool:
    """Whether `entry` is an in-scope book, per spec 4.3.

    A book is in scope when its kind is neither `excluded` nor `index`, and
    either its edition is 3.5 or some `update` entry has applies_to pointing
    at it. Errata/update/web_enhancement entries are never "in scope" as
    books themselves -- they are reported as "override" sources instead.
    """
    if entry.kind in OVERRIDE_KINDS or entry.kind in ("excluded", "index"):
        return False
    if entry.edition == "3.5":
        return True
    update_targets = {e.applies_to for e in entries if e.kind == "update" and e.applies_to}
    return entry.book_id in update_targets


def status_for(entry: ManifestEntry, entries: list[ManifestEntry]) -> str:
    """One of 'in_scope', 'override', 'index', 'excluded', 'out_of_scope'."""
    if entry.kind in OVERRIDE_KINDS:
        return "override"
    if entry.kind == "index":
        return "index"
    if entry.kind == "excluded":
        return "excluded"
    return "in_scope" if in_scope(entry, entries) else "out_of_scope"


def default_pdf_dir() -> Path:
    return Path(os.environ.get("OWLSPERCH_PDFS", str(Path.home() / "D_D")))


def default_manifest_path() -> Path:
    # pipeline/owlsperch/manifest.py -> pipeline/manifest.yaml
    return Path(__file__).resolve().parent.parent / "manifest.yaml"


def list_pdf_dir_files(
    pdf_dir: Path, ignored_filenames: frozenset[str] = DEFAULT_IGNORED_FILENAMES
) -> set[str]:
    if not pdf_dir.is_dir():
        raise ManifestError(f"PDF directory not found: {pdf_dir}")
    return {p.name for p in pdf_dir.iterdir() if p.is_file() and p.name not in ignored_filenames}


@dataclass
class CheckResult:
    counts_by_kind: dict[str, int] = field(default_factory=dict)
    counts_by_status: dict[str, int] = field(default_factory=dict)
    missing_from_manifest: list[str] = field(default_factory=list)
    missing_from_dir: list[str] = field(default_factory=list)

    @property
    def exit_code(self) -> int:
        return 1 if (self.missing_from_manifest or self.missing_from_dir) else 0

    def render(self) -> str:
        lines = ["Counts by kind:"]
        for kind, count in sorted(self.counts_by_kind.items()):
            lines.append(f"  {kind}: {count}")
        lines.append("Counts by status:")
        for status, count in sorted(self.counts_by_status.items()):
            lines.append(f"  {status}: {count}")
        if self.missing_from_manifest:
            lines.append(
                f"Files in PDF dir but missing from manifest ({len(self.missing_from_manifest)}):"
            )
            for name in self.missing_from_manifest:
                lines.append(f"  {name}")
        if self.missing_from_dir:
            lines.append(
                f"Manifest entries whose file is missing from the PDF dir "
                f"({len(self.missing_from_dir)}):"
            )
            for name in self.missing_from_dir:
                lines.append(f"  {name}")
        lines.append(f"exit code: {self.exit_code}")
        return "\n".join(lines)


def check(pdf_dir: Path | None = None, manifest_path: Path | None = None) -> CheckResult:
    pdf_dir = pdf_dir if pdf_dir is not None else default_pdf_dir()
    manifest_path = manifest_path if manifest_path is not None else default_manifest_path()

    entries = load_manifest(manifest_path)
    actual_files = list_pdf_dir_files(pdf_dir)
    manifest_files = {e.file for e in entries}

    result = CheckResult()
    result.missing_from_manifest = sorted(actual_files - manifest_files)
    result.missing_from_dir = sorted(manifest_files - actual_files)
    result.counts_by_kind = dict(Counter(e.kind for e in entries))
    result.counts_by_status = dict(Counter(status_for(e, entries) for e in entries))
    return result


def run_check(
    pdf_dir: Path | None = None, manifest_path: Path | None = None, out: Any = None
) -> int:
    import sys

    out = out if out is not None else sys.stdout
    result = check(pdf_dir=pdf_dir, manifest_path=manifest_path)
    print(result.render(), file=out)
    return result.exit_code
