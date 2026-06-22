from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppConfig:
    data_dir: Path
    memory_path: Path
    audit_log_path: Path
    token_vault_path: Path
    downloads_dir: Path
    smtp_host: str | None
    smtp_port: int
    smtp_username: str | None
    smtp_password: str | None
    smtp_from: str | None
    imap_host: str | None
    imap_port: int
    imap_username: str | None
    imap_password: str | None
    imap_mailbox: str
    google_client_id: str | None
    google_client_secret: str | None
    google_redirect_uri: str
    microsoft_client_id: str | None
    microsoft_client_secret: str | None
    microsoft_tenant: str
    microsoft_redirect_uri: str
    llm_provider: str
    llm_base_url: str
    llm_model: str | None
    llm_smart_model: str | None
    llm_ultra_model: str | None
    llm_vision_model: str | None
    llm_api_key: str | None
    obsidian_vault: str | None
    search_provider: str = ""
    search_api_key: str | None = None
    # Optional cross-provider fallback: when the primary (e.g. NVIDIA) tiers are
    # congested, chat falls over to OpenRouter's free models to keep answering.
    openrouter_api_key: str | None = None
    openrouter_model: str | None = None
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    # Optional Jobright.ai credentials for the daily lead pull. Prefer a saved browser
    # session (stored under data_dir); these are the fallback when no session exists.
    jobright_email: str | None = None
    jobright_password: str | None = None
    # Chain-of-thought token budget for reasoning models (NVIDIA Nemotron ultra tier).
    llm_reasoning_budget: int = 16384


def _load_dotenv(path: str = ".env") -> None:
    """Populate os.environ from a local .env file without overriding real env vars."""
    env_path = Path(path)
    if not env_path.exists():
        return
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key:
            os.environ.setdefault(key, value)


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


def load_config() -> AppConfig:
    _load_dotenv()
    data_dir = Path(os.environ.get("LAPTOP_AGENT_DATA_DIR", ".agent_data")).resolve()
    downloads_dir = Path(os.environ.get("LAPTOP_AGENT_DOWNLOAD_DIR", data_dir / "downloads")).resolve()
    port_raw = os.environ.get("SMTP_PORT", "587")
    try:
        smtp_port = int(port_raw)
    except ValueError:
        smtp_port = 587
    imap_port_raw = os.environ.get("IMAP_PORT", "993")
    try:
        imap_port = int(imap_port_raw)
    except ValueError:
        imap_port = 993

    # Optional real search API (Brave / Serper.dev / SerpApi). Infer the provider from
    # whichever key is present if not set explicitly; absent → fall back to DuckDuckGo.
    search_provider = os.environ.get("SEARCH_PROVIDER", "").strip().lower()
    search_api_key = (
        os.environ.get("SEARCH_API_KEY")
        or os.environ.get("BRAVE_API_KEY")
        or os.environ.get("SERPER_API_KEY")
        or os.environ.get("SERPAPI_API_KEY")
        or os.environ.get("SERPAPI_KEY")
    )
    if not search_provider:
        if os.environ.get("BRAVE_API_KEY"):
            search_provider = "brave"
        elif os.environ.get("SERPER_API_KEY"):
            search_provider = "serper"
        elif os.environ.get("SERPAPI_API_KEY") or os.environ.get("SERPAPI_KEY"):
            search_provider = "serpapi"

    return AppConfig(
        data_dir=data_dir,
        memory_path=data_dir / "memory.json",
        audit_log_path=data_dir / "audit.jsonl",
        token_vault_path=data_dir / "email_tokens.json",
        downloads_dir=downloads_dir,
        smtp_host=os.environ.get("SMTP_HOST"),
        smtp_port=smtp_port,
        smtp_username=os.environ.get("SMTP_USERNAME"),
        smtp_password=os.environ.get("SMTP_PASSWORD"),
        smtp_from=os.environ.get("SMTP_FROM") or os.environ.get("SMTP_USERNAME"),
        imap_host=os.environ.get("IMAP_HOST"),
        imap_port=imap_port,
        imap_username=os.environ.get("IMAP_USERNAME") or os.environ.get("SMTP_USERNAME"),
        imap_password=os.environ.get("IMAP_PASSWORD"),
        imap_mailbox=os.environ.get("IMAP_MAILBOX", "INBOX"),
        google_client_id=os.environ.get("GOOGLE_CLIENT_ID"),
        google_client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
        google_redirect_uri=os.environ.get("GOOGLE_REDIRECT_URI", "http://localhost:8765/oauth/callback"),
        microsoft_client_id=os.environ.get("MICROSOFT_CLIENT_ID"),
        microsoft_client_secret=os.environ.get("MICROSOFT_CLIENT_SECRET"),
        microsoft_tenant=os.environ.get("MICROSOFT_TENANT", "common"),
        microsoft_redirect_uri=os.environ.get("MICROSOFT_REDIRECT_URI", "http://localhost:8765/oauth/callback"),
        llm_provider=os.environ.get("LAPTOP_AGENT_LLM_PROVIDER", "heuristic").lower(),
        llm_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
        llm_model=os.environ.get("OPENAI_MODEL"),
        llm_smart_model=os.environ.get("OPENAI_SMART_MODEL"),
        llm_ultra_model=os.environ.get("OPENAI_ULTRA_MODEL"),
        llm_vision_model=os.environ.get("OPENAI_VISION_MODEL"),
        llm_api_key=os.environ.get("OPENAI_API_KEY"),
        obsidian_vault=os.environ.get("OBSIDIAN_VAULT"),
        search_provider=search_provider,
        search_api_key=search_api_key,
        openrouter_api_key=os.environ.get("OPENROUTER_API_KEY"),
        # A capable, clean-output free model by default; override with OPENROUTER_MODEL.
        openrouter_model=os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-120b:free"),
        openrouter_base_url=os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"),
        jobright_email=os.environ.get("JOBRIGHT_EMAIL"),
        jobright_password=os.environ.get("JOBRIGHT_PASSWORD"),
        llm_reasoning_budget=_env_int("OPENAI_REASONING_BUDGET", 16384),
    )
