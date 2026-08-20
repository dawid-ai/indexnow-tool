from __future__ import annotations

import hashlib
import sqlite3
from pathlib import Path
from typing import Iterable, Sequence


def url_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class Database:
    def __init__(self, db_path: Path) -> None:
        self.db_path = db_path
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        # FastAPI handlers run in worker threads; allow this connection across threads.
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False, timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
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

            CREATE TABLE IF NOT EXISTS submission_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_name TEXT NOT NULL,
                endpoint TEXT NOT NULL,
                source_type TEXT NOT NULL,
                source_ref TEXT,
                submitted_count INTEGER NOT NULL,
                accepted_count INTEGER NOT NULL,
                failed_count INTEGER NOT NULL,
                skipped_existing_count INTEGER NOT NULL,
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
            """
        )
        self.conn.commit()

    def upsert_urls(
        self, project_name: str, source_type: str, source_ref: str | None, urls: Iterable[str]
    ) -> tuple[list[int], int]:
        inserted_ids: list[int] = []
        skipped_existing = 0

        for url in urls:
            digest = url_hash(url)
            existing = self.conn.execute(
                "SELECT id FROM url_entries WHERE project_name = ? AND url_hash = ?",
                (project_name, digest),
            ).fetchone()
            if existing:
                skipped_existing += 1
                continue

            cursor = self.conn.execute(
                """
                INSERT INTO url_entries(project_name, url, url_hash, source_type, source_ref, status)
                VALUES (?, ?, ?, ?, ?, 'new')
                """,
                (project_name, url, digest, source_type, source_ref),
            )
            inserted_ids.append(int(cursor.lastrowid))

        self.conn.commit()
        return inserted_ids, skipped_existing

    def get_entries_by_status(
        self, project_name: str, statuses: Sequence[str], ids: Sequence[int] | None = None
    ) -> list[sqlite3.Row]:
        status_params = ",".join("?" for _ in statuses)
        params: list[object] = [project_name, *statuses]
        query = f"""
            SELECT * FROM url_entries
            WHERE project_name = ? AND status IN ({status_params})
        """
        if ids:
            id_params = ",".join("?" for _ in ids)
            query += f" AND id IN ({id_params})"
            params.extend(ids)
        return list(self.conn.execute(query, params).fetchall())

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
        placeholders = ",".join("?" for _ in entry_ids)
        attempt_sql = "attempt_count = attempt_count + 1," if increment_attempts else ""
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
            [status, http_code, response_excerpt, endpoint, *entry_ids],
        )
        self.conn.commit()

    def mark_manual_success(self, entry_ids: Sequence[int]) -> int:
        if not entry_ids:
            return 0
        placeholders = ",".join("?" for _ in entry_ids)
        cursor = self.conn.execute(
            f"""
            UPDATE url_entries
               SET status = 'manually_marked_success',
                   updated_at = CURRENT_TIMESTAMP
             WHERE id IN ({placeholders}) AND status = 'failed'
            """,
            [*entry_ids],
        )
        self.conn.commit()
        return int(cursor.rowcount)

    def create_run(
        self,
        project_name: str,
        endpoint: str,
        source_type: str,
        source_ref: str | None,
        submitted_count: int,
        accepted_count: int,
        failed_count: int,
        skipped_existing_count: int,
    ) -> int:
        cursor = self.conn.execute(
            """
            INSERT INTO submission_runs(
                project_name, endpoint, source_type, source_ref,
                submitted_count, accepted_count, failed_count, skipped_existing_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                project_name,
                endpoint,
                source_type,
                source_ref,
                submitted_count,
                accepted_count,
                failed_count,
                skipped_existing_count,
            ),
        )
        self.conn.commit()
        return int(cursor.lastrowid)

    def add_run_items(
        self, run_id: int, items: Sequence[tuple[int, int | None, str | None, str]]
    ) -> None:
        self.conn.executemany(
            """
            INSERT INTO submission_items(run_id, url_entry_id, http_code, response_excerpt, result)
            VALUES (?, ?, ?, ?, ?)
            """,
            [(run_id, entry_id, code, excerpt, result) for entry_id, code, excerpt, result in items],
        )
        self.conn.commit()

    def list_projects_summary(self) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT project_name,
                       COUNT(*) AS total_urls,
                       SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) AS failed_urls
                  FROM url_entries
              GROUP BY project_name
              ORDER BY project_name
                """
            ).fetchall()
        )

    def list_recent_runs(self, limit: int = 20) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM submission_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        )

    def list_failed_entries(self, project_name: str) -> list[sqlite3.Row]:
        return list(
            self.conn.execute(
                """
                SELECT * FROM url_entries
                WHERE project_name = ? AND status = 'failed'
                ORDER BY updated_at DESC
                """,
                (project_name,),
            ).fetchall()
        )
