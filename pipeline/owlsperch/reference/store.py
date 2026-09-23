"""Transactional storage for immutable snapshots and benchmark run documents."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Self, cast
from urllib.parse import quote

from owlsperch.reference.evaluate import DocumentError


def _json(document: Any) -> str:
    try:
        return json.dumps(document, sort_keys=True, ensure_ascii=False, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise DocumentError(f"document is not JSON serializable: {exc}") from exc


class ReferenceStore:
    _APP_ID = 0x4F575250  # OWRP: isolated Owlsperch reference pilot.

    def __init__(self, db_path: Path, *, readonly: bool = False) -> None:
        self.db_path = db_path
        self.readonly = readonly
        self.connection: sqlite3.Connection | None = None

    def __enter__(self) -> Self:
        if self.readonly:
            if not self.db_path.is_file():
                raise DocumentError(f"reference database not found: {self.db_path}")
            uri = f"file:{quote(str(self.db_path), safe='/')}?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
        else:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
            self.connection = sqlite3.connect(self.db_path)
        try:
            db = self.connection
            db.execute("PRAGMA foreign_keys = ON")
            app_id = db.execute("PRAGMA application_id").fetchone()[0]
            tables = {
                row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            if app_id == 0 and not tables and not self.readonly:
                with db:
                    db.execute("PRAGMA application_id = 1331122768")
                    db.execute(
                        "CREATE TABLE snapshots ("
                        "snapshot_id TEXT PRIMARY KEY, "
                        "document TEXT NOT NULL, artifact BLOB NOT NULL)"
                    )
                    db.execute(
                        "CREATE TABLE runs ("
                        "run_id TEXT PRIMARY KEY, inventory TEXT NOT NULL, "
                        "submission TEXT NOT NULL, report TEXT NOT NULL)"
                    )
            elif app_id != self._APP_ID or tables != {"snapshots", "runs"}:
                raise DocumentError(f"unrelated or invalid reference database: {self.db_path}")
        except sqlite3.DatabaseError as exc:
            self.connection.close()
            self.connection = None
            raise DocumentError(f"invalid reference database {self.db_path}: {exc}") from exc
        except BaseException:
            self.connection.close()
            self.connection = None
            raise
        return self

    def __exit__(self, *_exc: object) -> None:
        assert self.connection is not None
        self.connection.close()
        self.connection = None

    def _db(self) -> sqlite3.Connection:
        assert self.connection is not None
        return self.connection

    def save_snapshot(self, snapshot: dict[str, Any], artifact: bytes) -> None:
        document = _json(snapshot)
        snapshot_id = snapshot.get("snapshot_id")
        if not isinstance(snapshot_id, str) or not snapshot_id:
            raise DocumentError("snapshot_id must be a nonempty string")
        if not isinstance(artifact, bytes):
            raise DocumentError("source artifact must be bytes")
        db = self._db()
        with db:
            row = db.execute(
                "SELECT document, artifact FROM snapshots WHERE snapshot_id = ?", (snapshot_id,)
            ).fetchone()
            if row is not None:
                if row != (document, artifact):
                    raise DocumentError(f"snapshot identity conflict: {snapshot_id}")
                return
            db.execute("INSERT INTO snapshots VALUES (?, ?, ?)", (snapshot_id, document, artifact))

    def load_snapshot(self, snapshot_id: str) -> dict[str, Any]:
        row = (
            self._db()
            .execute("SELECT document FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
            .fetchone()
        )
        if row is None:
            raise DocumentError(f"snapshot not found: {snapshot_id}")
        return cast(dict[str, Any], json.loads(row[0]))

    def load_artifact(self, snapshot_id: str) -> bytes:
        row = (
            self._db()
            .execute("SELECT artifact FROM snapshots WHERE snapshot_id = ?", (snapshot_id,))
            .fetchone()
        )
        if row is None:
            raise DocumentError(f"snapshot not found: {snapshot_id}")
        return bytes(row[0])

    def save_run(
        self,
        run_id: str,
        inventory: dict[str, Any],
        submission: dict[str, Any],
        report: dict[str, Any],
    ) -> None:
        if not isinstance(run_id, str) or not run_id.strip():
            raise DocumentError("run_id must be a nonempty string")
        encoded = (_json(inventory), _json(submission), _json(report))
        db = self._db()
        with db:
            row = db.execute(
                "SELECT inventory, submission, report FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if row is not None:
                if row[:2] != encoded[:2]:
                    raise DocumentError(f"run ID conflict: {run_id}")
                return
            db.execute("INSERT INTO runs VALUES (?, ?, ?, ?)", (run_id, *encoded))

    def load_run(self, run_id: str) -> dict[str, Any]:
        row = (
            self._db()
            .execute("SELECT inventory, submission, report FROM runs WHERE run_id = ?", (run_id,))
            .fetchone()
        )
        if row is None:
            raise DocumentError(f"run not found: {run_id}")
        return {
            "inventory": json.loads(row[0]),
            "submission": json.loads(row[1]),
            "report": json.loads(row[2]),
        }
