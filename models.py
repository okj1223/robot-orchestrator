"""
models.py - Core data models for the Robot Orchestrator
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class JobStatus(str, Enum):
    RECEIVED = "RECEIVED"
    PLANNING = "PLANNING"
    EXECUTING = "EXECUTING"
    VALIDATING = "VALIDATING"
    AUDITING = "AUDITING"
    REWORK_REQUESTED = "REWORK_REQUESTED"
    COMPLETED = "COMPLETED"
    PARTIAL_COMPLETE = "PARTIAL_COMPLETE"
    FAILED = "FAILED"


class TaskType(str, Enum):
    ANALYSIS = "analysis"
    CODE = "code"
    CODE_AND_SIM = "code_and_sim"


class AuditVerdict(str, Enum):
    PASS = "PASS"
    REWORK = "REWORK"
    FAIL = "FAIL"


@dataclass
class CodexPlan:
    task_summary: str
    task_type: str
    target_packages: list[str]
    files_to_touch: list[str]
    constraints: list[str]
    risk_points: list[str]
    execution_prompt_for_claude: str
    validation_steps: list[str]
    acceptance_criteria: list[str]
    retry_if_failed: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodexPlan":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


@dataclass
class CodexAudit:
    verdict: str  # PASS | REWORK | FAIL
    requirement_coverage: dict[str, Any]
    changed_files_review: list[dict[str, Any]]
    test_result_review: dict[str, Any]
    sim_result_review: dict[str, Any]
    remaining_risks: list[str]
    next_action_for_claude: str
    summary_for_user: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "CodexAudit":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2, ensure_ascii=False)


@dataclass
class ValidationResult:
    build_success: bool
    test_success: bool
    sim_success: bool
    artifacts: list[str]
    errors: list[str]
    log_path: str
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def overall_success(self) -> bool:
        return self.build_success and self.test_success


@dataclass
class Job:
    job_id: str
    task: str
    status: JobStatus
    profile: str
    workspace: str
    retry_count: int = 0
    max_retries: int = 2
    plan: Optional[CodexPlan] = None
    audit: Optional[CodexAudit] = None
    validation: Optional[ValidationResult] = None
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    summary_for_user: str = ""
    error_message: str = ""

    def can_retry(self) -> bool:
        return self.retry_count < self.max_retries

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["status"] = self.status.value
        return d
