"""
adapters/codex_adapter.py - Codex CLI adapter for planning and auditing

Responsibilities:
  - PLAN: Analyze the task and produce a structured CodexPlan JSON
  - AUDIT: Review execution results and produce a CodexAudit JSON

Codex CLI is called via subprocess. The adapter expects:
  - codex CLI to be installed and authenticated (ChatGPT Plus / Codex subscription)
  - Structured JSON output mode (--output json or prompt-enforced)

TODO: If Codex CLI changes its interface, update _build_command() accordingly.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
from pathlib import Path
from typing import Any, Optional

from models import CodexAudit, CodexPlan, ValidationResult

logger = logging.getLogger(__name__)

# Minimal fallback plan for mock mode
MOCK_PLAN: dict[str, Any] = {
    "task_summary": "Mock plan: analyze and fix navigation stack issue",
    "task_type": "code",
    "target_packages": ["nav2_bringup", "robot_bringup"],
    "files_to_touch": ["src/robot_bringup/launch/navigation.launch.py"],
    "constraints": ["Do not modify nav2 core params", "Keep existing topic names"],
    "risk_points": ["Launch file changes may affect system startup order"],
    "execution_prompt_for_claude": (
        "Fix the navigation launch file. Ensure lifecycle nodes start in order. "
        "Add missing parameter overrides for costmap."
    ),
    "validation_steps": ["colcon build", "ros2 launch robot_bringup navigation.launch.py --dry-run"],
    "acceptance_criteria": ["Build succeeds", "launch dry-run exits 0"],
    "retry_if_failed": True,
}

MOCK_AUDIT_PASS: dict[str, Any] = {
    "verdict": "PASS",
    "requirement_coverage": {"build": True, "launch_check": True},
    "changed_files_review": [
        {"file": "navigation.launch.py", "status": "OK", "comment": "Lifecycle order fixed"}
    ],
    "test_result_review": {"passed": 1, "failed": 0, "skipped": 0},
    "sim_result_review": {"smoke_passed": True, "note": "Mock smoke test passed"},
    "remaining_risks": [],
    "next_action_for_claude": "",
    "summary_for_user": "All checks passed. Navigation stack launch file corrected.",
}

MOCK_AUDIT_REWORK: dict[str, Any] = {
    "verdict": "REWORK",
    "requirement_coverage": {"build": True, "launch_check": False},
    "changed_files_review": [
        {"file": "navigation.launch.py", "status": "ISSUE", "comment": "Lifecycle ordering still wrong"}
    ],
    "test_result_review": {"passed": 0, "failed": 1, "skipped": 0},
    "sim_result_review": {"smoke_passed": False, "note": "Node failed to transition"},
    "remaining_risks": ["Navigation stack may not start correctly in production"],
    "next_action_for_claude": (
        "Fix lifecycle node activation order. 'map_server' must be activated before 'amcl'."
    ),
    "summary_for_user": "Build passed but launch check failed. Rework required.",
}


class CodexAdapter:
    def __init__(
        self,
        codex_cmd: str = "codex",
        model: str = "",
        timeout: int = 300,
        mock_mode: bool = False,
    ):
        self.codex_cmd = codex_cmd
        self.model = model
        self.timeout = timeout
        self.mock_mode = mock_mode
        self._mock_audit_counter: dict[str, int] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def plan(self, task: str, profile_context: str, workspace: str) -> CodexPlan:
        """Call Codex to produce a structured plan for the given task."""
        if self.mock_mode:
            logger.info("[MOCK] Returning mock CodexPlan")
            return CodexPlan.from_dict(MOCK_PLAN)

        prompt = self._build_plan_prompt(task, profile_context, workspace)
        raw = self._call_codex(prompt)
        return self._parse_plan(raw)

    def audit(
        self,
        job_id: str,
        plan: CodexPlan,
        validation: ValidationResult,
        retry_count: int,
    ) -> CodexAudit:
        """Call Codex to audit the execution result."""
        if self.mock_mode:
            # First attempt: REWORK; subsequent: PASS
            count = self._mock_audit_counter.get(job_id, 0)
            self._mock_audit_counter[job_id] = count + 1
            if count == 0 and retry_count == 0:
                logger.info("[MOCK] Returning mock CodexAudit: REWORK")
                return CodexAudit.from_dict(MOCK_AUDIT_REWORK)
            logger.info("[MOCK] Returning mock CodexAudit: PASS")
            return CodexAudit.from_dict(MOCK_AUDIT_PASS)

        prompt = self._build_audit_prompt(plan, validation)
        raw = self._call_codex(prompt)
        return self._parse_audit(raw)

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_plan_prompt(self, task: str, profile_context: str, workspace: str) -> str:
        schema_path = Path(__file__).parent.parent / "schemas" / "codex_plan.schema.json"
        schema_hint = ""
        if schema_path.exists():
            schema_hint = f"\nJSON schema:\n{schema_path.read_text()}\n"

        return f"""You are a senior robotics software architect reviewing a task for a ROS2 robot project.

TASK:
{task}

PROJECT CONTEXT:
{profile_context}

WORKSPACE: {workspace}

Your job is to produce a STRUCTURED ANALYSIS PLAN as JSON.
{schema_hint}
Output ONLY valid JSON matching the schema above. No markdown, no explanation outside JSON.

Required fields:
- task_summary (string)
- task_type (one of: "analysis", "code", "code_and_sim")
- target_packages (array of strings)
- files_to_touch (array of strings, relative paths)
- constraints (array of strings)
- risk_points (array of strings)
- execution_prompt_for_claude (string: the exact instruction for the code editor)
- validation_steps (array of strings)
- acceptance_criteria (array of strings)
- retry_if_failed (boolean)

Be concise. Focus on correctness over completeness.
"""

    def _build_audit_prompt(self, plan: CodexPlan, validation: ValidationResult) -> str:
        schema_path = Path(__file__).parent.parent / "schemas" / "codex_audit.schema.json"
        schema_hint = ""
        if schema_path.exists():
            schema_hint = f"\nJSON schema:\n{schema_path.read_text()}\n"

        return f"""You are a senior robotics code reviewer performing a post-execution audit.

ORIGINAL PLAN:
{plan.to_json()}

VALIDATION RESULTS:
Build success: {validation.build_success}
Test success: {validation.test_success}
Sim success: {validation.sim_success}
Errors: {validation.errors}
Artifacts: {validation.artifacts}

{schema_hint}
Output ONLY valid JSON matching the CodexAudit schema. No markdown, no explanation.

Required fields:
- verdict ("PASS" | "REWORK" | "FAIL")
- requirement_coverage (object: criteria -> bool)
- changed_files_review (array of objects: file, status, comment)
- test_result_review (object: passed, failed, skipped)
- sim_result_review (object: smoke_passed, note)
- remaining_risks (array of strings)
- next_action_for_claude (string: concrete fix instruction, empty if PASS)
- summary_for_user (string: human-readable outcome)
"""

    # ------------------------------------------------------------------
    # CLI call
    # ------------------------------------------------------------------

    def _call_codex(self, prompt: str) -> str:
        """Call codex CLI non-interactively via `codex exec`.

        Passes the prompt on stdin and reads the last agent message from
        a temp output file (-o flag).  The --ephemeral flag prevents the
        session from being persisted to disk.
        """
        with tempfile.NamedTemporaryFile(
            mode="w", suffix="_out.txt", delete=False, encoding="utf-8"
        ) as f:
            out_file = f.name

        try:
            # `codex exec [--model M] --ephemeral -o outfile -`
            # reads prompt from stdin ("-"), writes last message to out_file
            cmd = [self.codex_cmd, "exec", "--ephemeral", "-o", out_file]
            if self.model:
                cmd += ["--model", self.model]
            cmd.append("-")  # read from stdin

            logger.info(f"Calling Codex: {' '.join(cmd[:-1])} -")
            result = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env={**os.environ},
            )

            output = Path(out_file).read_text(encoding="utf-8").strip() if Path(out_file).exists() else ""
            if not output:
                # Fall back to stdout if -o produced nothing (older versions)
                output = result.stdout.strip()

            if result.returncode != 0 and not output:
                raise RuntimeError(
                    f"Codex exited {result.returncode}: {result.stderr[:500]}"
                )
            return output
        finally:
            Path(out_file).unlink(missing_ok=True)

    # ------------------------------------------------------------------
    # Parsers
    # ------------------------------------------------------------------

    def _parse_plan(self, raw: str) -> CodexPlan:
        data = self._extract_json(raw)
        try:
            return CodexPlan.from_dict(data)
        except (TypeError, KeyError) as e:
            raise ValueError(f"Codex plan JSON missing required fields: {e}\nRaw: {raw[:300]}")

    def _parse_audit(self, raw: str) -> CodexAudit:
        data = self._extract_json(raw)
        try:
            return CodexAudit.from_dict(data)
        except (TypeError, KeyError) as e:
            raise ValueError(f"Codex audit JSON missing required fields: {e}\nRaw: {raw[:300]}")

    @staticmethod
    def _extract_json(text: str) -> dict:
        """Extract the first JSON object from text (handles markdown code blocks)."""
        text = text.strip()
        # Strip markdown code fences if present
        if text.startswith("```"):
            lines = text.splitlines()
            text = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        # Find first { ... } block
        start = text.find("{")
        end = text.rfind("}") + 1
        if start == -1 or end == 0:
            raise ValueError(f"No JSON object found in Codex output:\n{text[:300]}")

        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError as e:
            raise ValueError(f"Invalid JSON from Codex: {e}\nText: {text[start:start+300]}")
