"""
tests/test_orchestrator.py - Integration tests for the orchestrator state machine

Runs in mock mode: no real Codex/Claude CLI calls.
Uses a temp SQLite DB so tests are fully isolated.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from config import OrchestratorConfig
from models import CodexAudit, CodexPlan, Job, JobStatus, ValidationResult
from orchestrator import RobotOrchestrator
from storage import JobStorage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

MOCK_PLAN = CodexPlan(
    task_summary="Fix nav2 lifecycle order",
    task_type="code",
    target_packages=["nav2_bringup"],
    files_to_touch=["src/robot_bringup/launch/navigation.launch.py"],
    constraints=["Do not modify nav2 core params"],
    risk_points=["Launch order may affect startup"],
    execution_prompt_for_claude="Fix lifecycle node activation order.",
    validation_steps=["colcon build"],
    acceptance_criteria=["Build succeeds"],
    retry_if_failed=True,
)

MOCK_AUDIT_PASS = CodexAudit(
    verdict="PASS",
    requirement_coverage={"build": True},
    changed_files_review=[{"file": "navigation.launch.py", "status": "OK", "comment": "Fixed"}],
    test_result_review={"passed": 1, "failed": 0, "skipped": 0},
    sim_result_review={"smoke_passed": True, "note": "OK"},
    remaining_risks=[],
    next_action_for_claude="",
    summary_for_user="All checks passed.",
)

MOCK_AUDIT_REWORK = CodexAudit(
    verdict="REWORK",
    requirement_coverage={"build": True, "launch": False},
    changed_files_review=[{"file": "navigation.launch.py", "status": "ISSUE", "comment": "Order wrong"}],
    test_result_review={"passed": 0, "failed": 1, "skipped": 0},
    sim_result_review={"smoke_passed": False, "note": "Failed"},
    remaining_risks=["Node may not start"],
    next_action_for_claude="Fix map_server -> amcl activation order.",
    summary_for_user="Rework needed.",
)

MOCK_VALIDATION_OK = ValidationResult(
    build_success=True,
    test_success=True,
    sim_success=True,
    artifacts=[],
    errors=[],
    log_path="/tmp/test_validation.log",
)

MOCK_VALIDATION_FAIL = ValidationResult(
    build_success=False,
    test_success=False,
    sim_success=False,
    artifacts=[],
    errors=["build.sh failed"],
    log_path="/tmp/test_validation_fail.log",
)


# ---------------------------------------------------------------------------
# Test base
# ---------------------------------------------------------------------------

class OrchestratorTestBase(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())
        self.config = OrchestratorConfig(
            base_dir=self.tmp,
            state_dir=self.tmp / "state",
            log_dir=self.tmp / "state" / "logs",
            db_path=self.tmp / "state" / "test.db",
            validator_dir=self.tmp / "validators",
            profile_dir=self.tmp / "profiles",
            mock_mode=True,
        )
        self.config.ensure_dirs()
        self.storage = JobStorage(self.config.db_path)
        self.orchestrator = RobotOrchestrator(self.config, self.storage)

    def tearDown(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _submit(self, task: str = "Test task") -> Job:
        return self.orchestrator.create_job(task, profile="ros2_nav", workspace=str(self.tmp))


# ---------------------------------------------------------------------------
# Tests: job creation
# ---------------------------------------------------------------------------

class TestJobCreation(OrchestratorTestBase):
    def test_create_job_persisted(self) -> None:
        job = self._submit()
        self.assertEqual(job.status, JobStatus.RECEIVED)
        stored = self.storage.load_job(job.job_id)
        self.assertIsNotNone(stored)
        self.assertEqual(stored.task, "Test task")
        self.assertEqual(stored.profile, "ros2_nav")

    def test_job_not_found(self) -> None:
        result = self.orchestrator.run_job("nonexistent-id")
        self.assertFalse(result)


# ---------------------------------------------------------------------------
# Tests: happy path (plan -> execute -> validate -> audit PASS)
# ---------------------------------------------------------------------------

class TestHappyPath(OrchestratorTestBase):
    @patch("adapters.codex_adapter.CodexAdapter.plan", return_value=MOCK_PLAN)
    @patch("adapters.claude_adapter.ClaudeAdapter.execute", return_value=(True, "OK"))
    @patch("adapters.codex_adapter.CodexAdapter.audit", return_value=MOCK_AUDIT_PASS)
    def test_complete_success(self, mock_audit, mock_execute, mock_plan) -> None:
        # Patch _run_validation to skip actual shell scripts
        self.orchestrator._run_validation = lambda job: MOCK_VALIDATION_OK

        job = self._submit()
        success = self.orchestrator.run_job(job.job_id)

        self.assertTrue(success)
        final = self.storage.load_job(job.job_id)
        self.assertEqual(final.status, JobStatus.COMPLETED)
        self.assertEqual(final.summary_for_user, "All checks passed.")
        self.assertIsNotNone(final.plan)
        self.assertIsNotNone(final.audit)

    @patch("adapters.codex_adapter.CodexAdapter.plan", side_effect=RuntimeError("Codex unavailable"))
    def test_planning_failure(self, mock_plan) -> None:
        job = self._submit()
        success = self.orchestrator.run_job(job.job_id)

        self.assertFalse(success)
        final = self.storage.load_job(job.job_id)
        self.assertEqual(final.status, JobStatus.FAILED)
        self.assertIn("Planning failed", final.error_message)

    @patch("adapters.codex_adapter.CodexAdapter.plan", return_value=MOCK_PLAN)
    @patch("adapters.claude_adapter.ClaudeAdapter.execute", return_value=(False, "compile error"))
    def test_execution_failure(self, mock_execute, mock_plan) -> None:
        job = self._submit()
        success = self.orchestrator.run_job(job.job_id)

        self.assertFalse(success)
        final = self.storage.load_job(job.job_id)
        self.assertEqual(final.status, JobStatus.FAILED)
        self.assertIn("Execution failed", final.error_message)


# ---------------------------------------------------------------------------
# Tests: retry / rework flow
# ---------------------------------------------------------------------------

class TestRetryFlow(OrchestratorTestBase):
    @patch("adapters.codex_adapter.CodexAdapter.plan", return_value=MOCK_PLAN)
    @patch("adapters.claude_adapter.ClaudeAdapter.execute", return_value=(True, "OK"))
    @patch("adapters.claude_adapter.ClaudeAdapter.rework", return_value=(True, "rework OK"))
    def test_rework_then_pass(self, mock_rework, mock_execute, mock_plan) -> None:
        """First audit REWORK, second audit PASS -> COMPLETED after 1 retry."""
        audit_calls = [0]

        def audit_side_effect(**kwargs):
            audit_calls[0] += 1
            return MOCK_AUDIT_REWORK if audit_calls[0] == 1 else MOCK_AUDIT_PASS

        self.orchestrator._run_validation = lambda job: MOCK_VALIDATION_OK
        with patch("adapters.codex_adapter.CodexAdapter.audit", side_effect=audit_side_effect):
            job = self._submit()
            success = self.orchestrator.run_job(job.job_id)

        self.assertTrue(success)
        final = self.storage.load_job(job.job_id)
        self.assertEqual(final.status, JobStatus.COMPLETED)
        self.assertEqual(final.retry_count, 1)
        mock_rework.assert_called_once()

    @patch("adapters.codex_adapter.CodexAdapter.plan", return_value=MOCK_PLAN)
    @patch("adapters.claude_adapter.ClaudeAdapter.execute", return_value=(True, "OK"))
    @patch("adapters.claude_adapter.ClaudeAdapter.rework", return_value=(True, "rework OK"))
    @patch("adapters.codex_adapter.CodexAdapter.audit", return_value=MOCK_AUDIT_REWORK)
    def test_max_retries_exhausted(self, mock_audit, mock_rework, mock_execute, mock_plan) -> None:
        """Always REWORK -> FAILED after max_retries (2)."""
        self.orchestrator._run_validation = lambda job: MOCK_VALIDATION_OK

        job = self._submit()
        success = self.orchestrator.run_job(job.job_id)

        self.assertFalse(success)
        final = self.storage.load_job(job.job_id)
        self.assertEqual(final.status, JobStatus.FAILED)
        self.assertEqual(final.retry_count, 2)  # max_retries=2

    @patch("adapters.codex_adapter.CodexAdapter.plan", return_value=MOCK_PLAN)
    @patch("adapters.claude_adapter.ClaudeAdapter.execute", return_value=(True, "OK"))
    @patch("adapters.codex_adapter.CodexAdapter.audit", return_value=MOCK_AUDIT_PASS)
    def test_validation_fail_still_audited(self, mock_audit, mock_execute, mock_plan) -> None:
        """Validation failure does not skip audit; audit determines outcome."""
        self.orchestrator._run_validation = lambda job: MOCK_VALIDATION_FAIL

        job = self._submit()
        success = self.orchestrator.run_job(job.job_id)

        # Audit returned PASS so job should complete even with validation failures
        self.assertTrue(success)
        final = self.storage.load_job(job.job_id)
        self.assertEqual(final.status, JobStatus.COMPLETED)


# ---------------------------------------------------------------------------
# Tests: storage round-trip
# ---------------------------------------------------------------------------

class TestStorage(OrchestratorTestBase):
    def test_save_and_load_with_plan(self) -> None:
        job = self._submit()
        job.plan = MOCK_PLAN
        job.status = JobStatus.EXECUTING
        self.storage.save_job(job)

        loaded = self.storage.load_job(job.job_id)
        self.assertIsNotNone(loaded.plan)
        self.assertEqual(loaded.plan.task_type, "code")
        self.assertEqual(loaded.plan.target_packages, ["nav2_bringup"])

    def test_list_jobs_filter(self) -> None:
        j1 = self._submit("task A")
        j2 = self._submit("task B")
        j2.status = JobStatus.COMPLETED
        self.storage.save_job(j2)

        completed = self.storage.list_jobs(status=JobStatus.COMPLETED)
        self.assertEqual(len(completed), 1)
        self.assertEqual(completed[0].job_id, j2.job_id)

    def test_add_and_get_logs(self) -> None:
        job = self._submit()
        self.storage.add_log(job.job_id, "INFO", "test message")
        logs = self.storage.get_logs(job.job_id)
        self.assertEqual(len(logs), 1)
        self.assertEqual(logs[0]["message"], "test message")


if __name__ == "__main__":
    unittest.main(verbosity=2)
