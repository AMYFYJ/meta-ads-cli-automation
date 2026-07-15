from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # pragma: no cover - dotenv is in requirements
    load_dotenv = None

PROJECT_ROOT = Path(__file__).resolve().parents[1]


@dataclass
class AgentConfig:
    project_root: Path = PROJECT_ROOT
    workbook: Path = PROJECT_ROOT / "data" / "current.xlsx"
    archive_dir: Path = PROJECT_ROOT / "data" / "archive"
    report_dir: Path = PROJECT_ROOT / "outputs" / "reports"
    mode: str = "mock"
    model: str = "claude-opus-4-8"
    account: str = ""
    max_proposals: int = 8
    # Largest relative budget change the agent may propose without human pre-discussion.
    max_budget_change_pct: float = 50.0
    state_path: Path = PROJECT_ROOT / "state" / "mock_state.json"
    extra: dict = field(default_factory=dict)


def load_config(**overrides) -> AgentConfig:
    if load_dotenv is not None:
        load_dotenv(PROJECT_ROOT / ".env")
    cfg = AgentConfig()
    if os.environ.get("ADS_AGENT_WORKBOOK"):
        cfg.workbook = _resolve(os.environ["ADS_AGENT_WORKBOOK"])
        cfg.archive_dir = cfg.workbook.parent / "archive"
    if os.environ.get("ADS_AGENT_REPORT_DIR"):
        cfg.report_dir = _resolve(os.environ["ADS_AGENT_REPORT_DIR"])
    cfg.mode = os.environ.get("ADS_AGENT_MODE", cfg.mode)
    cfg.model = os.environ.get("ADS_AGENT_MODEL", cfg.model)
    cfg.account = os.environ.get("AD_ACCOUNT_ID", cfg.account)
    if os.environ.get("ADS_AGENT_MAX_PROPOSALS"):
        cfg.max_proposals = int(os.environ["ADS_AGENT_MAX_PROPOSALS"])
    for key, value in overrides.items():
        if value is None:
            continue
        if key in {"workbook", "archive_dir", "report_dir", "state_path"}:
            value = _resolve(str(value))
        setattr(cfg, key, value)
    if "workbook" in overrides and "archive_dir" not in overrides:
        cfg.archive_dir = cfg.workbook.parent / "archive"
    return cfg


def _resolve(path: str) -> Path:
    p = Path(path).expanduser()
    return p if p.is_absolute() else PROJECT_ROOT / p
