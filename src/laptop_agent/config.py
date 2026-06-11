from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    memory_path: Path
    audit_log_path: Path
    downloads_dir: Path
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from: str | None


def load_config() -> AppConfig:
    data_dir = Path(os.environ.get("LAPTOP_AGENT_DATA_DIR", ".agent_data")).resolve()
    downloads_dir = Path(os.environ.get("LAPTOP_AGENT_DOWNLOAD_DIR", data_dir / "downloads")).resolve()
    port_raw = os.environ.get("SMTP_PORT", "587")
    try:
        smtp_port = int(port_raw)
    except ValueError:
        smtp_port = 587

    return AppConfig(
        data_dir=data_dir,
        memory_path=data_dir / "memory.json",
        audit_log_path=data_dir / "audit.jsonl",
        downloads_dir=downloads_dir,
        smtp_host=os.environ.get("SMTP_HOST"),
        smtp_port=smtp_port,
        smtp_username=os.environ.get("SMTP_USERNAME"),
        smtp_password=os.environ.get("SMTP_PASSWORD"),
        smtp_from=os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME"),
    )
