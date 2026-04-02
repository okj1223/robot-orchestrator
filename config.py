"""
config.py - Configuration management for the Robot Orchestrator
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


BASE_DIR = Path(__file__).parent.resolve()
STATE_DIR = BASE_DIR / "state"
LOG_DIR = STATE_DIR / "logs"
DB_PATH = STATE_DIR / "jobs.db"
SCHEMA_DIR = BASE_DIR / "schemas"
PROMPT_DIR = BASE_DIR / "prompts"
PROFILE_DIR = BASE_DIR / "profiles"
TEMPLATE_DIR = BASE_DIR / "templates"
VALIDATOR_DIR = BASE_DIR / "validators"


@dataclass
class OrchestratorConfig:
    # Paths
    base_dir: Path = BASE_DIR
    state_dir: Path = STATE_DIR
    log_dir: Path = LOG_DIR
    db_path: Path = DB_PATH
    validator_dir: Path = VALIDATOR_DIR
    profile_dir: Path = PROFILE_DIR

    # Retry policy
    max_retries: int = 2

    # Timeouts (seconds)
    codex_timeout: int = 300      # 5 min for plan/audit
    claude_timeout: int = 600     # 10 min for code execution
    build_timeout: int = 300
    test_timeout: int = 300
    sim_timeout: int = 120        # 2 min for smoke test

    # Codex CLI settings
    codex_cmd: str = field(default_factory=lambda: os.environ.get("CODEX_CMD", "codex"))
    # Empty string = use codex's own config (~/.codex/config.toml model field)
    codex_model: str = field(default_factory=lambda: os.environ.get("CODEX_MODEL", ""))

    # Claude Code CLI settings
    claude_cmd: str = field(default_factory=lambda: os.environ.get("CLAUDE_CMD", "claude"))

    # Default profile
    default_profile: str = "ros2_nav"

    # Mock mode (skip actual CLI calls)
    mock_mode: bool = field(default_factory=lambda: os.environ.get("MOCK_MODE", "false").lower() == "true")

    def ensure_dirs(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        cfg = cls()
        cfg.ensure_dirs()
        return cfg


def load_config() -> OrchestratorConfig:
    return OrchestratorConfig.from_env()
