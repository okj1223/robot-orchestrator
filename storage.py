"""
storage.py - SQLite-based job storage
"""
from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Generator, Optional

from models import Job, JobStatus, CodexPlan, CodexAudit, ValidationResult

logger = logging.getLogger(__name__)


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id      TEXT PRIMARY KEY,
    task        TEXT NOT NULL,
    status      TEXT NOT NULL,
    profile     TEXT NOT NULL DEFAULT 'ros2_nav',
    workspace   TEXT NOT NULL DEFAULT '',
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 2,
    plan        TEXT,
    audit       TEXT,
    validation  TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL,
    summary_for_user TEXT NOT NULL DEFAULT '',
    error_message    TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS job_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id      TEXT NOT NULL,
    timestamp   TEXT NOT NULL,
    level       TEXT NOT NULL,
    message     TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES jobs(job_id)
);

CREATE INDEX IF NOT EXISTS idx_job_logs_job_id ON job_logs(job_id);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


class JobStorage:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA_SQL)
        logger.info(f"Database initialized at {self.db_path}")

    @contextmanager
    def _conn(self) -> Generator[sqlite3.Connection, None, None]:
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def save_job(self, job: Job) -> None:
        plan_json = job.plan.to_json() if job.plan else None
        audit_json = job.audit.to_json() if job.audit else None
        validation_json = json.dumps(job.validation.to_dict()) if job.validation else None

        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO jobs
                (job_id, task, status, profile, workspace, retry_count, max_retries,
                 plan, audit, validation, created_at, updated_at, summary_for_user, error_message)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                job.job_id, job.task, job.status.value, job.profile, job.workspace,
                job.retry_count, job.max_retries, plan_json, audit_json, validation_json,
                job.created_at, job.updated_at, job.summary_for_user, job.error_message
            ))
        logger.debug(f"Saved job {job.job_id} status={job.status.value}")

    def load_job(self, job_id: str) -> Optional[Job]:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if row is None:
            return None
        return self._row_to_job(row)

    def list_jobs(self, status: Optional[JobStatus] = None) -> list[Job]:
        with self._conn() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY created_at DESC", (status.value,)
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM jobs ORDER BY created_at DESC").fetchall()
        return [self._row_to_job(r) for r in rows]

    def add_log(self, job_id: str, level: str, message: str) -> None:
        from datetime import datetime
        ts = datetime.utcnow().isoformat()
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO job_logs (job_id, timestamp, level, message) VALUES (?, ?, ?, ?)",
                (job_id, ts, level, message)
            )

    def get_logs(self, job_id: str) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT timestamp, level, message FROM job_logs WHERE job_id=? ORDER BY id",
                (job_id,)
            ).fetchall()
        return [dict(r) for r in rows]

    def _row_to_job(self, row: sqlite3.Row) -> Job:
        plan = None
        if row["plan"]:
            try:
                plan = CodexPlan.from_dict(json.loads(row["plan"]))
            except Exception as e:
                logger.warning(f"Failed to parse plan for job {row['job_id']}: {e}")

        audit = None
        if row["audit"]:
            try:
                audit = CodexAudit.from_dict(json.loads(row["audit"]))
            except Exception as e:
                logger.warning(f"Failed to parse audit for job {row['job_id']}: {e}")

        validation = None
        if row["validation"]:
            try:
                vd = json.loads(row["validation"])
                validation = ValidationResult(**vd)
            except Exception as e:
                logger.warning(f"Failed to parse validation for job {row['job_id']}: {e}")

        return Job(
            job_id=row["job_id"],
            task=row["task"],
            status=JobStatus(row["status"]),
            profile=row["profile"],
            workspace=row["workspace"],
            retry_count=row["retry_count"],
            max_retries=row["max_retries"],
            plan=plan,
            audit=audit,
            validation=validation,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            summary_for_user=row["summary_for_user"],
            error_message=row["error_message"],
        )
