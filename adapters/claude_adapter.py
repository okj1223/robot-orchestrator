"""
adapters/claude_adapter.py - Claude Code CLI adapter for code execution

Responsibilities:
  - Execute code modifications based on CodexPlan
  - Pass structured prompts via CLAUDE.md + CLI flags
  - Return stdout/stderr for audit logging

Claude Code CLI is called via subprocess using `claude` command.
Requires: Claude Max subscription, claude CLI installed and authenticated.

TODO: When Claude Code CLI stabilizes its API, update _build_command() flags.
"""
from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Optional

from models import CodexPlan

logger = logging.getLogger(__name__)

MOCK_OUTPUT = """[MOCK Claude Code execution]
- Analyzed navigation.launch.py
- Fixed lifecycle node activation order: map_server -> amcl -> controller_server
- Updated costmap parameter overrides
- No other files modified

Changes complete. Ready for validation.
"""


class ClaudeAdapter:
    def __init__(
        self,
        claude_cmd: str = "claude",
        timeout: int = 600,
        mock_mode: bool = False,
        prompt_dir: Optional[Path] = None,
    ):
        self.claude_cmd = claude_cmd
        self.timeout = timeout
        self.mock_mode = mock_mode
        self.prompt_dir = prompt_dir or Path(__file__).parent.parent / "prompts"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def execute(
        self,
        plan: CodexPlan,
        workspace: str,
        job_id: str,
        log_dir: Path,
    ) -> tuple[bool, str]:
        """
        Execute code modifications based on the plan.
        Returns (success, output_text).
        """
        if self.mock_mode:
            logger.info("[MOCK] Claude Code execution skipped")
            return True, MOCK_OUTPUT

        prompt = self._build_execution_prompt(plan, workspace, job_id)
        return self._call_claude(prompt, workspace, job_id, log_dir)

    def rework(
        self,
        plan: CodexPlan,
        rework_instruction: str,
        workspace: str,
        job_id: str,
        log_dir: Path,
        retry_num: int,
    ) -> tuple[bool, str]:
        """Request Claude Code to apply rework based on audit feedback."""
        if self.mock_mode:
            logger.info(f"[MOCK] Claude Code rework #{retry_num} skipped")
            return True, f"[MOCK] Rework #{retry_num} applied: {rework_instruction}"

        prompt = self._build_rework_prompt(plan, rework_instruction, retry_num)
        return self._call_claude(prompt, workspace, job_id, log_dir, suffix=f"_rework{retry_num}")

    # ------------------------------------------------------------------
    # Prompt builders
    # ------------------------------------------------------------------

    def _build_execution_prompt(self, plan: CodexPlan, workspace: str, job_id: str) -> str:
        base_template = self.prompt_dir / "claude_execute.md"
        if base_template.exists():
            template = base_template.read_text()
        else:
            template = "# Task\n{execution_prompt}\n\n# Constraints\n{constraints}\n"

        constraints_text = "\n".join(f"- {c}" for c in plan.constraints)
        files_text = "\n".join(f"- {f}" for f in plan.files_to_touch)

        return f"""# Robot Orchestrator Job: {job_id}

## Task Summary
{plan.task_summary}

## Execution Instructions
{plan.execution_prompt_for_claude}

## Files to Modify
{files_text}

## Constraints (DO NOT violate)
{constraints_text}

## Workspace
{workspace}

## Validation Steps (for reference)
{chr(10).join(f'- {s}' for s in plan.validation_steps)}

## Acceptance Criteria
{chr(10).join(f'- {c}' for c in plan.acceptance_criteria)}

---
Make ONLY the changes described above. Do not refactor unrelated code.
After changes, output a brief summary of what was modified.
"""

    def _build_rework_prompt(self, plan: CodexPlan, instruction: str, retry_num: int) -> str:
        return f"""# Robot Orchestrator Rework Request (attempt #{retry_num})

## Original Task
{plan.task_summary}

## Audit Feedback - MUST FIX
{instruction}

## Original Constraints (still apply)
{chr(10).join(f'- {c}' for c in plan.constraints)}

## Original Files in Scope
{chr(10).join(f'- {f}' for f in plan.files_to_touch)}

---
Apply ONLY the fixes described in the audit feedback.
Output a brief summary of changes made.
"""

    # ------------------------------------------------------------------
    # CLI call
    # ------------------------------------------------------------------

    def _call_claude(
        self,
        prompt: str,
        workspace: str,
        job_id: str,
        log_dir: Path,
        suffix: str = "",
    ) -> tuple[bool, str]:
        log_file = log_dir / f"{job_id}_claude{suffix}.log"

        # Write prompt to log for traceability
        prompt_log = log_dir / f"{job_id}_claude{suffix}_prompt.txt"
        prompt_log.write_text(prompt, encoding="utf-8")

        # Claude Code CLI: `claude --print "prompt"` runs non-interactively
        cmd = [
            self.claude_cmd,
            "--print",
            prompt,
            "--output-format", "text",
        ]

        logger.info(f"Calling Claude Code in workspace: {workspace}")
        logger.debug(f"Log: {log_file}")

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                cwd=workspace if workspace and Path(workspace).exists() else None,
                env={**os.environ},
            )
            output = result.stdout + (f"\n[STDERR]\n{result.stderr}" if result.stderr else "")
            log_file.write_text(output, encoding="utf-8")

            if result.returncode != 0:
                logger.warning(f"Claude Code exited {result.returncode}")
                return False, output

            return True, result.stdout

        except subprocess.TimeoutExpired:
            msg = f"Claude Code timed out after {self.timeout}s"
            logger.error(msg)
            log_file.write_text(msg, encoding="utf-8")
            return False, msg

        except FileNotFoundError:
            msg = (
                f"Claude Code CLI not found: '{self.claude_cmd}'. "
                "Install it with: npm install -g @anthropic-ai/claude-code"
            )
            logger.error(msg)
            return False, msg
