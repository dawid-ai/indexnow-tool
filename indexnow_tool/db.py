from __future__ import annotations

import hashlib
import sqlite3
import threading
from pathlib import Path
from typing import Iterable, Iterator, Sequence

# SQLite caps host parameters per statement (32766 on modern builds, 999 on old
# ones). Chunk id lists well under the lower bound so batches of 10k URLs are safe.
_PARAM_CHUNK = 900

ACTIVE_STATUSES = ("submitted", "manually_marked_success")


def url_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _chunks(items: Sequence[int], size: int = _PARAM_CHUNK) -> Iterator[Sequence[int]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI handlers and the background run worker share this connection, so
        # allow cross-thread use and serialize writes ourselves.
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self._lock = threading.RLock()
        self._init_schema()

    # ------------------------------------------------------------------ schema

    def _init_schema(self) -> None:
        with self._lock:
            self.conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS projects (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL UNIQUE,
                    host TEXT NOT NULL,
                    key TEXT NOT NULL,
                    key_location TEXT,
                    sitemap_url TEXT,
                    default_endpoint TEXT NOT NULL DEFAULT 'indexnow',
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS url_entries (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    url TEXT NOT NULL,
                    url_hash TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    status TEXT NOT NULL DEFAULT 'new',
                    last_http_code INTEGER,
                    last_response_excerpt TEXT,
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_endpoint TEXT,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    last_submitted_at DATETIME
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_url_project_hash
                  ON url_entries(project_name, url_hash);
                CREATE INDEX IF NOT EXISTS idx_url_project_status
                  ON url_entries(project_name, status);

                CREATE TABLE IF NOT EXISTS submission_runs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_name TEXT NOT NULL,
                    endpoint TEXT NOT NULL,
                    source_type TEXT NOT NULL,
                    source_ref TEXT,
                    submitted_count INTEGER NOT NULL DEFAULT 0,
                    accepted_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    skipped_existing_count INTEGER NOT NULL DEFAULT 0,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
                );

                CREATE TABLE IF NOT EXISTS submission_items (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    url_entry_id INTEGER NOT NULL,
                    http_code INTEGER,
                    response_excerpt TEXT,
                    result TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(run_id) REFERENCES submission_runs(id),
                    FOREIGN KEY(url_entry_id) REFERENCES url_entries(id)
                );

                CREATE TABLE IF NOT EXISTS run_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    run_id INTEGER NOT NULL,
                    level TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY(run_id) REFERENCES submission_runs(id)
                );
                CREATE INDEX IF NOT EXISTS idx_run_messages_run ON run_messages(run_id, id);
                """
            )
            self._migrate()
            self.conn.commit()

    def _migrate(self) -> None:
        """Add columns introduced after the first schema version."""
        existing = {row["name"] for row in self.conn.execute("PRAGMA table_info(submission_runs)")}
        additions = {
            "status": "TEXT NOT NULL DEFAULT 'completed'",
            "phase": "TEXT",
            "total_urls": "INTEGER NOT NULL DEFAULT 0",
            "processed_urls": "INTEGER NOT NULL DEFAULT 0",
            "invalid_count": "INTEGER NOT NULL DEFAULT 0",
            "error_message": "TEXT",
            "finished_at": "DATETIME",
        }
        for column, ddl in additions.items():
            if column not in existing:
                self.conn.execute(f"ALTER TABLE submission_runs ADD COLUMN {column} {ddl}")

        # A run left in 'running' by a crashed process can never finish. Mark it.
        self.conn.execute(
            """
            UPDATE submission_runs
               SET status = 'interrupted',
                   error_message = COALESCE(error_message, 'Run was interrupted by a restart.')
             WHERE status = 'running'
            """
        )

    # ---------------------------------------------------------------- projects

    def list_projects(self) -> list[sqlite3.Row]:
        return list(self.conn.execute("SELECT * FROM projects ORDER BY name").fetchall())

    def get_project(self, name: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM projects WHERE name = ?", (name,)).fetchone()

    def upsert_project(
        self,
        name: str,
        host: str,
        key: str,
        key_location: str | None,
        sitemap_url: str | None,
        default_endpoint: str,
        original_name: str | None = None,
    ) -> None:
        """Create or update a project. Renaming carries submission history along."""
        with self._lock:
            if original_name and original_name != name:
                self.conn.execute(
                    "UPDATE url_entries SET project_name = ? WHERE project_name = ?",
                    (name, original_name),
                )
                self.conn.execute(
                    "UPDATE submission_runs SET project_name = ? WHERE project_name = ?",
                    (name, original_name),
                )
                self.conn.execute("UPDATE projects SET name = ? WHERE name = ?", (name, original_name))

            self.conn.execute(
                """
                INSERT INTO projects(name, host, key, key_location, sitemap_url, default_endpoint)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    host = excluded.host,
                    key = excluded.key,
                    key_location = excluded.key_location,
                    sitemap_url = excluded.sitemap_url,
                    default_endpoint = excluded.default_endpoint,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (name, host, key, key_location, sitemap_url, default_endpoint),
            )
            self.conn.commit()

    def delete_project(self, name: str) -> None:
        """Remove the project row only. URL history is kept so counts stay auditable."""
        with self._lock:
            self.conn.execute("DELETE FROM projects WHERE name = ?", (name,))
            self.conn.commit()

    # -------------------------------------------------------------- url entries

    def stage_urls(
        self,
        project_name: str,
        source_type: str,
        source_ref: str | None,
        urls: Sequence[str],
        force: bool = False,
    ) -> tuple[list[int], int]:
        """Record URLs for a run and decide which ones still need submitting.

        Returns (entry_ids_to_submit, skipped_count). A URL already accepted by the
        API is skipped unless force is set; one that is new, failed, or left over
        from an interrupted run is picked up again.
        """
        to_submit: list[int] = []
        skipped = 0

        with self._lock:
            for url in urls:
                digest = url_hash(url)
                existing = self.conn.execute(
                    "SELECT id, status FROM url_entries WHERE project_name = ? AND url_hash = ?",
                    (project_name, digest),
                ).fetchone()

                if existing is None:
                    cursor = self.conn.execute(
                        """
                        INSERT INTO url_entries(project_name, url, url_hash, source_type, source_ref, status)
                        VALUES (?, ?, ?, ?, ?, 'new')
                        """,
                        (project_name, url, digest, source_type, source_ref),
                    )
                    to_submit.append(int(cursor.lastrowid))
                    continue

                if existing["status"] in ACTIVE_STATUSES and not force:
                    skipped += 1
                    continue

                self.conn.execute(
                    """
                    UPDATE url_entries
                       SET source_type = ?, source_ref = ?, updated_at = CURRENT_TIMESTAMP
                     WHERE id = ?
                    """,
                    (source_type, source_ref, existing["id"]),
                )
                to_submit.append(int(existing["id"]))

            self.conn.commit()
        return to_submit, skipped

    def get_entries_by_ids(self, entry_ids: Sequence[int]) -> list[sqlite3.Row]:
        if not entry_ids:
            return []
        rows: list[sqlite3.Row] = []
        for chunk in _chunks(list(entry_ids)):
            placeholders = ",".join("?" for _ in chunk)
            rows.extend(
                self.conn.execute(
                    f"SELECT * FROM url_entries WHERE id IN ({placeholders})", list(chunk)
                ).fetchall()
            )
        return rows

    def get_entries_by_status(
        self, project_name: str, statuses: Sequence[str], ids: Sequence[int] | None = None
    ) -> list[sqlite3.Row]:
        """ids=None means every entry in those statuses; ids=[] means none.

        Treating an empty selection as "everything" is how a stray click ends up
        retrying or force-marking a whole project, so the two cases stay distinct.
        """
        if ids is not None and len(ids) == 0:
            return []

        status_params = ",".join("?" for _ in statuses)
        base = f"SELECT * FROM url_entries WHERE project_name = ? AND status IN ({status_params})"

        if ids is None:
            return list(self.conn.execute(base + " ORDER BY id", [project_name, *statuses]).fetchall())

        rows: list[sqlite3.Row] = []
        for chunk in _chunks(list(ids)):
            id_params = ",".join("?" for _ in chunk)
            rows.extend(
                self.conn.execute(
                    base + f" AND id IN ({id_params}) ORDER BY id",
                    [project_name, *statuses, *chunk],
                ).fetchall()
            )
        return rows

    def mark_entries(
        self,
        entry_ids: Sequence[int],
        status: str,
        http_code: int | None = None,
        response_excerpt: str | None = None,
        endpoint: str | None = None,
        increment_attempts: bool = False,
    ) -> None:
        if not entry_ids:
            return
        attempt_sql = "attempt_count = attempt_count + 1," if increment_attempts else ""
        with self._lock:
            for chunk in _chunks(list(entry_ids)):
                placeholders = ",".join("?" for _ in chunk)
                self.conn.execute(
                    f"""
                    UPDATE url_entries
                       SET status = ?,
                           {attempt_sql}
                           last_http_code = ?,
                           last_response_excerpt = ?,
                           last_endpoint = ?,
                           updated_at = CURRENT_TIMESTAMP,
                           last_submitted_at = CURRENT_TIMESTAMP
                     WHERE id IN ({placeholders})
                    """,
                    [status, http_code, response_excerpt, endpoint, *chunk],
                )
            self.conn.commit()

    def mark_manual_success(self, entry_ids: Sequence[int]) -> int:
        if not entry_ids:
            return 0
        changed = 0
        with self._lock:
            for chunk in _chunks(list(entry_ids)):
                placeholders = ",".join("?" for _ in chunk)
                cursor = self.conn.execute(
                    f"""
                    UPDATE url_entries
                       SET status = 'manually_marked_success',
                           updated_at = CURRENT_TIMESTAMP
                     WHERE id IN ({placeholders}) AND status = 'failed'
                    """,
                    list(chunk),
                )
                changed += int(cursor.rowcount)
            self.conn.commit()
        return changed

    # -------------------------------------------------------------------- runs

    def create_run(
        self, project_name: str, endpoint: str, source_type: str, source_ref: str | None
    ) -> int:
        with self._lock:
            cursor = self.conn.execute(
                """
                INSERT INTO submission_runs(
                    project_name, endpoint, source_type, source_ref, status, phase
                ) VALUES (?, ?, ?, ?, 'running', 'starting')
                """,
                (project_name, endpoint, source_type, source_ref),
            )
            self.conn.commit()
            return int(cursor.lastrowid)

    def update_run(self, run_id: int, **fields: object) -> None:
        if not fields:
            return
        allowed = {
            "status", "phase", "total_urls", "processed_urls", "submitted_count",
            "accepted_count", "failed_count", "skipped_existing_count", "invalid_count",
            "error_message", "source_ref",
        }
        unknown = set(fields) - allowed
        if unknown:
            raise ValueError(f"Cannot update unknown run fields: {sorted(unknown)}")

        assignments = ", ".join(f"{name} = ?" for name in fields)
        with self._lock:
            self.conn.execute(
                f"UPDATE submission_runs SET {assignments} WHERE id = ?",
                [*fields.values(), run_id],
            )
            self.conn.commit()

    def finish_run(self, run_id: int, status: str, error_message: str | None = None) -> None:
        with self._lock:
            self.conn.execute(
                """
                UPDATE submission_runs
                   SET status = ?, phase = ?, error_message = ?, finished_at = CURRENT_TIMESTAMP
                 WHERE id = ?
                """,
                (status, "finished" if status == "completed" else status, error_message, run_id),
            )
            self.conn.commit()

    def get_run(self, run_id: int) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM submission_runs WHERE id = ?", (run_id,)).fetchone()

    def add_run_message(self, run_id: int, level: str, message: str) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO run_messages(run_id, level, message) VALUES (?, ?, ?)",
                (run_id, level, message[:1000]),
            )
            self.conn.commit()

    def list_run_messages(self, run_id: int, after_id: int = 0) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM run_messages WHERE run_id = ? AND id > ? ORDER BY id",
                (run_id, after_id),
            ).fetchall()
        )

    def add_run_items(
        self, run_id: int, items: Sequence[tuple[int, int | None, str | None, str]]
    ) -> None:
        if not items:
            return
        with self._lock:
            self.conn.executemany(
                """
                INSERT INTO submission_items(run_id, url_entry_id, http_code, response_excerpt, result)
                VALUES (?, ?, ?, ?, ?)
                """,
                [(run_id, entry_id, code, excerpt, result) for entry_id, code, excerpt, result in items],
            )
            self.conn.commit()

    def list_recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                "SELECT * FROM submission_runs ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        )

    # ---------------------------------------------------------------- reporting

    def list_projects_summary(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT project_name,
                       COUNT(*) AS total_urls,
                       SUM(CASE WHEN status='submitted' THEN 1 ELSE 0 END) AS submitted_urls,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_urls,
                       SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) AS pending_urls
                  FROM url_entries
              GROUP BY project_name
              ORDER BY project_name
                """
            ).fetchall()
        )

    def list_failed_entries(self, project_name: str, limit: int = 500) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM url_entries
                 WHERE project_name = ? AND status = 'failed'
              ORDER BY updated_at DESC
                 LIMIT ?
                """,
                (project_name, limit),
            ).fetchall()
        )

    def count_failed_entries(self, project_name: str) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM url_entries WHERE project_name = ? AND status = 'failed'",
            (project_name,),
        ).fetchone()
        return int(row["n"])

    def iter_entries_for_export(self, project_name: str, status: str | None = None) -> Iterable[sqlite3.Row]:
        if status:
            return self.conn.execute(
                "SELECT * FROM url_entries WHERE project_name = ? AND status = ? ORDER BY id",
                (project_name, status),
            )
        return self.conn.execute(
            "SELECT * FROM url_entries WHERE project_name = ? ORDER BY id", (project_name,)
        )
