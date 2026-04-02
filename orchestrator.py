"""
orchestrator.py - Core state machine orchestrator for robot project AI workflow

State flow:
  RECEIVED      -> EXECUTING    (after Codex plan)
  EXECUTING     -> VALIDATING   (after Claude execute)
  VALIDATING    -> AUDITING     (after validators; failures stored, not blocking)
  AUDITING      -> COMPLETED    (verdict PASS)
               -> REWORK_REQUESTED (verdict REWORK + retries remaining)
               -> FAILED        (verdict FAIL, or REWORK with no retries left)
  REWORK_REQUESTED -> VALIDATING (after Claude rework)
               -> FAILED        (rework execution failed)
"""
from __future__ import annotations

import logging
import subprocess
import uuid
from pathlib import Path

from adapters.claude_adapter import ClaudeAdapter
from adapters.codex_adapter import CodexAdapter
from config import OrchestratorConfig
from models import Job, JobStatus, ValidationResult
from storage import JobStorage

logger = logging.getLogger(__name__)


class RobotOrchestrator:
    def __init__(self, config: OrchestratorConfig, storage: JobStorage):
        self.config = config
        self.storage = storage
        self.codex_adapter = CodexAdapter(
            codex_cmd=config.codex_cmd,
            model=config.codex_model,
            timeout=config.codex_timeout,
            mock_mode=config.mock_mode,
        )
        self.claude_adapter = ClaudeAdapter(
            claude_cmd=config.claude_cmd,
            timeout=config.claude_timeout,
            mock_mode=config.mock_mode,
            prompt_dir=config.base_dir / "prompts",
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_job(
        self,
        task: str,
        profile: str = "ros2_nav",
        workspace: str = "",
    ) -> Job:
        job_id = str(uuid.uuid4())
        job = Job(
            job_id=job_id,
            task=task,
            status=JobStatus.RECEIVED,
            profile=profile,
            workspace=workspace or str(Path.cwd()),
        )
        self.storage.save_job(job)
        logger.info(f"Created job {job_id[:8]} | profile={profile} | task={task[:60]}")
        return job

    def run_job(self, job_id: str) -> bool:
        """Run a job through the state machine. Returns True on COMPLETED/PARTIAL_COMPLETE."""
        job = self.storage.load_job(job_id)
        if not job:
            logger.error(f"Job {job_id} not found")
            return False

        logger.info(f"Starting job {job_id[:8]} (current status={job.status.value})")
        try:
            self._run_state_machine(job)
        except Exception as e:
            logger.error(f"Job {job_id[:8]} crashed unexpectedly: {e}", exc_info=True)
            job.status = JobStatus.FAILED
            job.error_message = f"Unexpected error: {e}"
            self.storage.save_job(job)

        return job.status in (JobStatus.COMPLETED, JobStatus.PARTIAL_COMPLETE)

    # ------------------------------------------------------------------
    # State machine
    # ------------------------------------------------------------------

    _TERMINAL = {JobStatus.COMPLETED, JobStatus.PARTIAL_COMPLETE, JobStatus.FAILED}

    def _run_state_machine(self, job: Job) -> None:
        while job.status not in self._TERMINAL:
            prev_status = job.status

            if job.status == JobStatus.RECEIVED:
                self._step_plan(job)
            elif job.status == JobStatus.PLANNING:
                # Transient: re-enter planning
                job.status = JobStatus.RECEIVED
            elif job.status == JobStatus.EXECUTING:
                self._step_execute(job)
            elif job.status == JobStatus.VALIDATING:
                self._step_validate(job)
            elif job.status == JobStatus.AUDITING:
                self._step_audit(job)
            elif job.status == JobStatus.REWORK_REQUESTED:
                self._step_rework(job)
            else:
                logger.error(f"[{job.job_id[:8]}] Unhandled status {job.status.value}")
                job.status = JobStatus.FAILED
                break

            # Safety: prevent infinite loop if a step forgot to change status
            if job.status == prev_status:
                logger.error(
                    f"[{job.job_id[:8]}] Status stuck at {prev_status.value} – aborting"
                )
                job.status = JobStatus.FAILED
                job.error_message = f"State machine stuck at {prev_status.value}"
                self.storage.save_job(job)
                break

    # ------------------------------------------------------------------
    # Steps
    # ------------------------------------------------------------------

    def _step_plan(self, job: Job) -> None:
        """RECEIVED -> EXECUTING (plan saved) | FAILED"""
        logger.info(f"[{job.job_id[:8]}] --- PLANNING ---")
        job.status = JobStatus.PLANNING
        self.storage.save_job(job)

        profile_context = self._load_profile_context(job.profile)
        try:
            plan = self.codex_adapter.plan(job.task, profile_context, job.workspace)
            job.plan = plan
            job.status = JobStatus.EXECUTING
            logger.info(
                f"[{job.job_id[:8]}] Plan: type={plan.task_type} "
                f"packages={plan.target_packages} "
                f"summary={plan.task_summary[:80]}"
            )
        except Exception as e:
            logger.error(f"[{job.job_id[:8]}] Planning failed: {e}")
            job.status = JobStatus.FAILED
            job.error_message = f"Planning failed: {e}"

        self.storage.save_job(job)

    def _step_execute(self, job: Job) -> None:
        """EXECUTING -> VALIDATING | FAILED"""
        logger.info(f"[{job.job_id[:8]}] --- EXECUTING (retry={job.retry_count}) ---")

        if not job.plan:
            job.status = JobStatus.FAILED
            job.error_message = "No plan available for execution"
            self.storage.save_job(job)
            return

        try:
            success, output = self.claude_adapter.execute(
                plan=job.plan,
                workspace=job.workspace,
                job_id=job.job_id,
                log_dir=self.config.log_dir,
            )
            if success:
                job.status = JobStatus.VALIDATING
                logger.info(f"[{job.job_id[:8]}] Execution succeeded")
            else:
                job.status = JobStatus.FAILED
                job.error_message = f"Execution failed: {output[:300]}"
                logger.error(f"[{job.job_id[:8]}] Execution failed")
        except Exception as e:
            logger.error(f"[{job.job_id[:8]}] Execution error: {e}")
            job.status = JobStatus.FAILED
            job.error_message = f"Execution error: {e}"

        self.storage.save_job(job)

    def _step_validate(self, job: Job) -> None:
        """VALIDATING -> AUDITING (always; failures captured in ValidationResult)"""
        logger.info(f"[{job.job_id[:8]}] --- VALIDATING ---")

        validation = self._run_validation(job)
        job.validation = validation
        job.status = JobStatus.AUDITING

        logger.info(
            f"[{job.job_id[:8]}] Validation: "
            f"build={validation.build_success} "
            f"test={validation.test_success} "
            f"sim={validation.sim_success}"
        )
        self.storage.save_job(job)

    def _step_audit(self, job: Job) -> None:
        """AUDITING -> COMPLETED | REWORK_REQUESTED | FAILED"""
        logger.info(f"[{job.job_id[:8]}] --- AUDITING ---")

        if not job.plan or not job.validation:
            job.status = JobStatus.FAILED
            job.error_message = "Missing plan or validation for audit"
            self.storage.save_job(job)
            return

        try:
            audit = self.codex_adapter.audit(
                job_id=job.job_id,
                plan=job.plan,
                validation=job.validation,
                retry_count=job.retry_count,
            )
            job.audit = audit
            logger.info(f"[{job.job_id[:8]}] Audit verdict: {audit.verdict}")

            if audit.verdict == "PASS":
                job.status = JobStatus.COMPLETED
                job.summary_for_user = audit.summary_for_user

            elif audit.verdict == "REWORK" and job.can_retry():
                job.retry_count += 1
                job.status = JobStatus.REWORK_REQUESTED
                logger.info(
                    f"[{job.job_id[:8]}] Rework requested "
                    f"(attempt {job.retry_count}/{job.max_retries})"
                )

            else:
                reason = (
                    f"REWORK but max retries ({job.max_retries}) reached"
                    if audit.verdict == "REWORK"
                    else audit.verdict
                )
                job.status = JobStatus.FAILED
                job.summary_for_user = audit.summary_for_user
                job.error_message = (
                    f"Audit {reason}. Next action: {audit.next_action_for_claude[:200]}"
                )

        except Exception as e:
            logger.error(f"[{job.job_id[:8]}] Audit error: {e}")
            job.status = JobStatus.FAILED
            job.error_message = f"Audit error: {e}"

        self.storage.save_job(job)

    def _step_rework(self, job: Job) -> None:
        """REWORK_REQUESTED -> VALIDATING | FAILED"""
        logger.info(
            f"[{job.job_id[:8]}] --- REWORK (attempt {job.retry_count}/{job.max_retries}) ---"
        )

        if not job.plan or not job.audit:
            job.status = JobStatus.FAILED
            job.error_message = "Missing plan or audit for rework"
            self.storage.save_job(job)
            return

        try:
            success, output = self.claude_adapter.rework(
                plan=job.plan,
                rework_instruction=job.audit.next_action_for_claude,
                workspace=job.workspace,
                job_id=job.job_id,
                log_dir=self.config.log_dir,
                retry_num=job.retry_count,
            )
            if success:
                job.status = JobStatus.VALIDATING
                logger.info(f"[{job.job_id[:8]}] Rework execution succeeded")
            else:
                job.status = JobStatus.FAILED
                job.error_message = f"Rework execution failed: {output[:300]}"
                logger.error(f"[{job.job_id[:8]}] Rework execution failed")
        except Exception as e:
            logger.error(f"[{job.job_id[:8]}] Rework error: {e}")
            job.status = JobStatus.FAILED
            job.error_message = f"Rework error: {e}"

        self.storage.save_job(job)

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _run_validation(self, job: Job) -> ValidationResult:
        errors: list[str] = []
        log_path = str(self.config.log_dir / f"{job.job_id}_validation.log")

        build_ok = self._run_validator("build.sh", job, errors)
        test_ok = self._run_validator("test.sh", job, errors)
        sim_ok = self._run_validator("sim_smoke.sh", job, errors)
        artifacts = self._collect_artifacts(job)

        return ValidationResult(
            build_success=build_ok,
            test_success=test_ok,
            sim_success=sim_ok,
            artifacts=artifacts,
            errors=errors,
            log_path=log_path,
        )

    def _run_validator(self, script: str, job: Job, errors: list[str]) -> bool:
        script_path = self.config.validator_dir / script
        if not script_path.exists():
            msg = f"Validator not found: {script_path}"
            logger.warning(f"[{job.job_id[:8]}] {msg}")
            errors.append(msg)
            return False

        timeout_map = {
            "build.sh": self.config.build_timeout,
            "test.sh": self.config.test_timeout,
            "sim_smoke.sh": self.config.sim_timeout,
        }
        timeout = timeout_map.get(script, self.config.build_timeout)

        try:
            import os
            result = subprocess.run(
                [str(script_path), job.job_id, job.profile, job.workspace, str(self.config.log_dir)],
                capture_output=True,
                text=True,
                timeout=timeout,
                env={**os.environ},
            )

            # Write combined log
            log_file = self.config.log_dir / f"{job.job_id}_{script.replace('.sh', '')}.log"
            combined = result.stdout
            if result.stderr:
                combined += f"\n[STDERR]\n{result.stderr}"
            log_file.write_text(combined, encoding="utf-8")

            if result.returncode != 0:
                tail = result.stdout.strip().splitlines()
                tail_msg = "\n".join(tail[-5:]) if tail else "(no output)"
                errors.append(f"{script} exit={result.returncode}: {tail_msg}")
                logger.warning(f"[{job.job_id[:8]}] {script} failed (exit {result.returncode})")
                return False

            logger.info(f"[{job.job_id[:8]}] {script} passed")
            return True

        except subprocess.TimeoutExpired:
            msg = f"{script} timed out after {timeout}s"
            logger.error(f"[{job.job_id[:8]}] {msg}")
            errors.append(msg)
            return False
        except Exception as e:
            msg = f"{script} error: {e}"
            logger.error(f"[{job.job_id[:8]}] {msg}")
            errors.append(msg)
            return False

    def _collect_artifacts(self, job: Job) -> list[str]:
        script_path = self.config.validator_dir / "collect_artifacts.sh"
        if not script_path.exists():
            return []
        try:
            import os
            result = subprocess.run(
                [str(script_path), job.job_id, job.profile, job.workspace, str(self.config.log_dir)],
                capture_output=True,
                text=True,
                timeout=60,
                env={**os.environ},
            )
            lines = result.stdout.strip().splitlines()
            artifacts_dir = lines[-1].strip() if lines else ""
            if artifacts_dir and Path(artifacts_dir).is_dir():
                return [str(p) for p in sorted(Path(artifacts_dir).iterdir())]
        except Exception as e:
            logger.warning(f"[{job.job_id[:8]}] Artifact collection failed: {e}")
        return []

    # ------------------------------------------------------------------
    # Profile helpers
    # ------------------------------------------------------------------

    def _load_profile_context(self, profile_name: str) -> str:
        profile_path = self.config.profile_dir / f"{profile_name}.yaml"
        if not profile_path.exists():
            logger.warning(f"Profile '{profile_name}' not found; using minimal context")
            return f"ROS2 robot project. Profile: {profile_name}"
        return profile_path.read_text(encoding="utf-8")
