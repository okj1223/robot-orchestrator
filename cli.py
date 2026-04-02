"""
cli.py - Command-line interface for the Robot Orchestrator

Usage:
  python cli.py submit --task "Fix nav2 lifecycle order" [--profile ros2_nav] [--workspace /path]
  python cli.py run    --job-id <id> [--mock]
  python cli.py list   [--status COMPLETED]
  python cli.py show   --job-id <id>
  python cli.py retry  --job-id <id>
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Allow running from project root without package install
sys.path.insert(0, str(Path(__file__).parent))

from config import OrchestratorConfig
from models import JobStatus
from orchestrator import RobotOrchestrator
from storage import JobStorage

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("orchestrator.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


def _make_orchestrator(mock: bool = False) -> tuple[OrchestratorConfig, JobStorage, RobotOrchestrator]:
    if mock:
        os.environ["MOCK_MODE"] = "true"
    config = OrchestratorConfig.from_env()
    storage = JobStorage(config.db_path)
    orchestrator = RobotOrchestrator(config, storage)
    return config, storage, orchestrator


def cmd_submit(args: argparse.Namespace) -> None:
    _, _, orchestrator = _make_orchestrator()
    job = orchestrator.create_job(
        task=args.task,
        profile=args.profile,
        workspace=args.workspace or str(Path.cwd()),
    )
    print(f"Job submitted: {job.job_id}")
    print(f"  Status  : {job.status.value}")
    print(f"  Profile : {job.profile}")
    print(f"  Workspace: {job.workspace}")
    print()
    print(f"  Run with: python cli.py run --job-id {job.job_id}")


def cmd_run(args: argparse.Namespace) -> None:
    _, _, orchestrator = _make_orchestrator(mock=args.mock)
    print(f"Running job {args.job_id} ({'MOCK' if args.mock else 'REAL'} mode)...")
    success = orchestrator.run_job(args.job_id)

    # Show final state
    job = orchestrator.storage.load_job(args.job_id)
    if job:
        print(f"\nJob {job.job_id[:8]}  status={job.status.value}  retries={job.retry_count}")
        if job.summary_for_user:
            print(f"Summary : {job.summary_for_user}")
        if job.error_message:
            print(f"Error   : {job.error_message}")
    sys.exit(0 if success else 1)


def cmd_list(args: argparse.Namespace) -> None:
    _, storage, _ = _make_orchestrator()
    status_filter: JobStatus | None = None
    if args.status:
        try:
            status_filter = JobStatus(args.status.upper())
        except ValueError:
            print(f"Unknown status '{args.status}'. Valid: {[s.value for s in JobStatus]}")
            sys.exit(1)

    jobs = storage.list_jobs(status_filter)
    if not jobs:
        print("No jobs found.")
        return

    fmt = "{:<36}  {:<20}  {:<10}  {}"
    print(fmt.format("JOB ID", "STATUS", "RETRIES", "TASK"))
    print("-" * 90)
    for job in jobs:
        task_preview = job.task[:45] + ("..." if len(job.task) > 45 else "")
        print(fmt.format(job.job_id, job.status.value, f"{job.retry_count}/{job.max_retries}", task_preview))


def cmd_show(args: argparse.Namespace) -> None:
    _, storage, _ = _make_orchestrator()
    job = storage.load_job(args.job_id)
    if not job:
        print(f"Job not found: {args.job_id}")
        sys.exit(1)

    print(f"Job ID    : {job.job_id}")
    print(f"Status    : {job.status.value}")
    print(f"Profile   : {job.profile}")
    print(f"Workspace : {job.workspace}")
    print(f"Retries   : {job.retry_count}/{job.max_retries}")
    print(f"Created   : {job.created_at}")
    print(f"Updated   : {job.updated_at}")
    print(f"Task      : {job.task}")

    if job.plan:
        print("\n--- Plan ---")
        print(f"  Type     : {job.plan.task_type}")
        print(f"  Summary  : {job.plan.task_summary}")
        print(f"  Packages : {', '.join(job.plan.target_packages)}")
        print(f"  Files    : {', '.join(job.plan.files_to_touch)}")

    if job.validation:
        print("\n--- Validation ---")
        print(f"  Build : {'OK' if job.validation.build_success else 'FAIL'}")
        print(f"  Tests : {'OK' if job.validation.test_success else 'FAIL'}")
        print(f"  Sim   : {'OK' if job.validation.sim_success else 'FAIL'}")
        if job.validation.errors:
            print(f"  Errors: {'; '.join(job.validation.errors[:3])}")

    if job.audit:
        print("\n--- Audit ---")
        print(f"  Verdict : {job.audit.verdict}")
        print(f"  Summary : {job.audit.summary_for_user}")
        if job.audit.remaining_risks:
            print(f"  Risks   : {', '.join(job.audit.remaining_risks[:3])}")

    if job.summary_for_user:
        print(f"\nFinal Summary: {job.summary_for_user}")
    if job.error_message:
        print(f"\nError: {job.error_message}")

    if args.json:
        print("\n--- JSON ---")
        print(json.dumps(job.to_dict(), indent=2, ensure_ascii=False))


def cmd_retry(args: argparse.Namespace) -> None:
    _, storage, orchestrator = _make_orchestrator(mock=args.mock)
    job = storage.load_job(args.job_id)
    if not job:
        print(f"Job not found: {args.job_id}")
        sys.exit(1)

    retryable = {JobStatus.FAILED, JobStatus.REWORK_REQUESTED, JobStatus.PARTIAL_COMPLETE}
    if job.status not in retryable:
        print(f"Cannot retry job in status {job.status.value}. Must be one of: {[s.value for s in retryable]}")
        sys.exit(1)

    if not job.can_retry():
        print(f"Max retries ({job.max_retries}) already reached. Reset retry_count in DB to force.")
        sys.exit(1)

    # Reset to a recoverable state
    job.status = JobStatus.EXECUTING if job.plan else JobStatus.RECEIVED
    job.error_message = ""
    storage.save_job(job)
    print(f"Retrying job {args.job_id} from {job.status.value}...")
    success = orchestrator.run_job(args.job_id)

    job = storage.load_job(args.job_id)
    if job:
        print(f"Final status: {job.status.value}")
    sys.exit(0 if success else 1)


def cmd_logs(args: argparse.Namespace) -> None:
    _, storage, _ = _make_orchestrator()
    job = storage.load_job(args.job_id)
    if not job:
        print(f"Job not found: {args.job_id}")
        sys.exit(1)

    from config import load_config
    config = load_config()
    log_dir = config.log_dir

    log_files = sorted(log_dir.glob(f"{args.job_id}*"))
    if not log_files:
        print(f"No log files found for job {args.job_id}")
        return

    for lf in log_files:
        print(f"\n=== {lf.name} ===")
        try:
            print(lf.read_text(encoding="utf-8", errors="replace"))
        except Exception as e:
            print(f"(could not read: {e})")

    # Also show DB logs
    db_logs = storage.get_logs(args.job_id)
    if db_logs:
        print(f"\n=== DB logs ({len(db_logs)} entries) ===")
        for entry in db_logs[-50:]:
            print(f"  [{entry['timestamp']}] {entry['level']}: {entry['message']}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Robot Orchestrator – AI-driven ROS2 workflow manager",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python cli.py submit --task "Fix nav2 lifecycle order" --profile ros2_nav
  python cli.py run --job-id <id> --mock
  python cli.py list
  python cli.py list --status FAILED
  python cli.py show --job-id <id>
  python cli.py show --job-id <id> --json
  python cli.py retry --job-id <id> --mock
  python cli.py logs --job-id <id>
""",
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # submit
    p_submit = subparsers.add_parser("submit", help="Submit a new job")
    p_submit.add_argument("--task", required=True, help="Task description")
    p_submit.add_argument("--profile", default="ros2_nav", help="Profile (default: ros2_nav)")
    p_submit.add_argument("--workspace", default="", help="ROS2 workspace path")
    p_submit.set_defaults(func=cmd_submit)

    # run
    p_run = subparsers.add_parser("run", help="Execute a job through the state machine")
    p_run.add_argument("--job-id", required=True)
    p_run.add_argument("--mock", action="store_true", help="Use mock adapters (no real CLI calls)")
    p_run.set_defaults(func=cmd_run)

    # list
    p_list = subparsers.add_parser("list", help="List jobs")
    p_list.add_argument("--status", default="", help="Filter by status (e.g. FAILED, COMPLETED)")
    p_list.set_defaults(func=cmd_list)

    # show
    p_show = subparsers.add_parser("show", help="Show job details")
    p_show.add_argument("--job-id", required=True)
    p_show.add_argument("--json", action="store_true", help="Also print full JSON")
    p_show.set_defaults(func=cmd_show)

    # retry
    p_retry = subparsers.add_parser("retry", help="Retry a failed job")
    p_retry.add_argument("--job-id", required=True)
    p_retry.add_argument("--mock", action="store_true", help="Use mock adapters")
    p_retry.set_defaults(func=cmd_retry)

    # logs
    p_logs = subparsers.add_parser("logs", help="Show logs for a job")
    p_logs.add_argument("--job-id", required=True)
    p_logs.set_defaults(func=cmd_logs)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
