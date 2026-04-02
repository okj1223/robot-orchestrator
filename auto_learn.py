"""
auto_learn.py - Automatic learning loop for robot-orchestrator

Reads a completed job from storage, extracts lessons, and appends them to
~/.openclaw/workspace/memory/<YYYY-MM-DD>.md

Usage:
  python3 auto_learn.py --job-id <id>
  python3 auto_learn.py --recent          # use most recently updated job
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow running from project root
sys.path.insert(0, str(Path(__file__).parent))

from config import OrchestratorConfig
from models import Job, JobStatus
from storage import JobStorage


MEMORY_DIR = Path.home() / ".openclaw" / "workspace" / "memory"
SENTINEL_TAG = "<!-- rorc-auto-learn:"  # prefix used to detect duplicate entries


# ── lesson extraction ─────────────────────────────────────────────────────────

def _extract_lessons(job: Job) -> list[str]:
    """Return a list of lesson strings derived from the job outcome."""
    lessons: list[str] = []
    status = job.status

    if status == JobStatus.COMPLETED:
        # Success path
        tool_hint = ""
        if job.plan:
            types = [job.plan.task_type]
            pkgs = job.plan.target_packages[:3]
            tool_hint = f"task_type={job.plan.task_type}, packages={pkgs}"

        summary = job.summary_for_user or (job.audit.summary_for_user if job.audit else "")
        lessons.append(f"**성공 패턴**: {tool_hint}")
        if summary:
            lessons.append(f"**결과 요약**: {summary}")
        if job.plan and job.plan.files_to_touch:
            lessons.append(f"**수정 파일**: {', '.join(job.plan.files_to_touch[:5])}")
        if job.audit and job.audit.remaining_risks:
            lessons.append(f"**잔여 위험**: {', '.join(job.audit.remaining_risks[:3])}")

    elif status in (JobStatus.FAILED, JobStatus.PARTIAL_COMPLETE):
        # Failure path
        error = job.error_message or "원인 불명"
        lessons.append(f"**실패 원인**: {error}")

        if job.validation:
            fail_parts = []
            if not job.validation.build_success:
                fail_parts.append("빌드 실패")
            if not job.validation.test_success:
                fail_parts.append("테스트 실패")
            if not job.validation.sim_success:
                fail_parts.append("시뮬 실패")
            if fail_parts:
                lessons.append(f"**실패 단계**: {', '.join(fail_parts)}")
            if job.validation.errors:
                lessons.append(f"**오류 메시지**: {'; '.join(job.validation.errors[:3])}")

        # Avoidance hint
        lessons.append(f"**회피 방법**: 동일 태스크 재시도 시 위 오류 메시지를 사전에 점검할 것")

    elif status == JobStatus.REWORK_REQUESTED:
        # Retry path
        verdict = ""
        if job.audit:
            verdict = job.audit.verdict
            lessons.append(f"**감사 판정**: {verdict}")
            lessons.append(f"**재작업 사유**: {job.audit.summary_for_user}")
        retry_info = f"retry_count={job.retry_count}/{job.max_retries}"
        lessons.append(f"**재시도 정보**: {retry_info}")

    else:
        lessons.append(f"**상태**: {status.value} — 완료되지 않은 잡")

    return lessons


def _build_entry(job: Job, lessons: list[str]) -> str:
    """Format a memory entry block for the given job."""
    now = datetime.now(timezone.utc).strftime("%H:%M UTC")
    status_emoji = {
        JobStatus.COMPLETED: "✅",
        JobStatus.FAILED: "❌",
        JobStatus.PARTIAL_COMPLETE: "⚠️",
        JobStatus.REWORK_REQUESTED: "🔄",
    }.get(job.status, "ℹ️")

    task_preview = job.task[:120] + ("…" if len(job.task) > 120 else "")

    lines = [
        f"{SENTINEL_TAG}{job.job_id} -->",
        f"## {status_emoji} [{now}] {task_preview}",
        f"- **job_id**: `{job.job_id}`",
        f"- **profile**: {job.profile}",
        f"- **status**: {job.status.value}",
    ]
    for lesson in lessons:
        lines.append(f"- {lesson}")
    lines.append("")  # trailing blank line
    return "\n".join(lines)


# ── deduplication ─────────────────────────────────────────────────────────────

def _already_recorded(memory_file: Path, job_id: str) -> bool:
    if not memory_file.exists():
        return False
    sentinel = f"{SENTINEL_TAG}{job_id} -->"
    return sentinel in memory_file.read_text(encoding="utf-8")


# ── main ──────────────────────────────────────────────────────────────────────

def learn(job_id: str | None = None, recent: bool = False) -> dict:
    config = OrchestratorConfig.from_env()
    storage = JobStorage(config.db_path)

    job: Job | None = None

    if job_id:
        job = storage.load_job(job_id)
        if not job:
            return {"error": f"Job not found: {job_id}"}
    elif recent:
        jobs = storage.list_jobs()
        terminal = {JobStatus.COMPLETED, JobStatus.FAILED,
                    JobStatus.PARTIAL_COMPLETE, JobStatus.REWORK_REQUESTED}
        for j in jobs:  # already sorted DESC by created_at
            if j.status in terminal:
                job = j
                break
        if not job:
            return {"error": "No completed/failed jobs found"}
    else:
        return {"error": "Provide --job-id or --recent"}

    today = datetime.now().strftime("%Y-%m-%d")
    MEMORY_DIR.mkdir(parents=True, exist_ok=True)
    memory_file = MEMORY_DIR / f"{today}.md"

    if _already_recorded(memory_file, job.job_id):
        return {"skipped": True, "job_id": job.job_id, "reason": "already recorded"}

    lessons = _extract_lessons(job)
    entry = _build_entry(job, lessons)

    # Create or append
    if not memory_file.exists():
        header = f"# {today} Daily Log\n\n"
        memory_file.write_text(header + entry + "\n", encoding="utf-8")
    else:
        with memory_file.open("a", encoding="utf-8") as f:
            f.write("\n" + entry + "\n")

    return {
        "recorded": True,
        "job_id": job.job_id,
        "status": job.status.value,
        "memory_file": str(memory_file),
        "lessons_count": len(lessons),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Auto-learn from robot-orchestrator job results")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job-id", help="Specific job ID to learn from")
    group.add_argument("--recent", action="store_true", help="Use most recent terminal job")
    args = parser.parse_args()

    result = learn(job_id=args.job_id, recent=args.recent)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if "error" in result:
        sys.exit(1)


if __name__ == "__main__":
    main()
