from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HttpCfg(BaseModel):
    timeout: int = 10
    retries: int = 3
    rate_limit_per_sec: int = 5


class DataSourceCfg(BaseModel):
    primary: str = "eastmoney"
    fallback: list[str] = Field(default_factory=list)
    http: HttpCfg = HttpCfg()


class CacheCfg(BaseModel):
    nav_ttl_hours: int = 12
    meta_ttl_hours: int = 168


class BenchmarksCfg(BaseModel):
    default: str = "000300.SH"
    by_type: dict[str, str] = Field(default_factory=dict)


class AiCfg(BaseModel):
    enabled: bool = False
    protocol: str = "anthropic"          # anthropic | openai_chat | openai_responses
    base_url: str = ""
    api_key: str = "EMPTY"
    model: str = ""
    timeout: int = 60
    max_tokens: int = 4096
    system_prompt: str = ""


class PromptsCfg(BaseModel):
    output_framework: str = ""
    market_analysis: str = ""
    sector_analysis: str = ""


class PushCfg(BaseModel):
    enabled: bool = False
    provider: str = "smtp"
    mails: list[str] = Field(default_factory=list)
    smtp_host: str = ""
    smtp_port: int = 465
    smtp_user: str = ""
    smtp_password: str = ""
    smtp_use_tls: bool = True
    timeout: int = 15
    max_chars: int = 3600


class AppConfig(BaseModel):
    data_dir: Path = Path("./data")
    log_level: str = "INFO"
    datasource: DataSourceCfg = DataSourceCfg()
    cache: CacheCfg = CacheCfg()
    benchmarks: BenchmarksCfg = BenchmarksCfg()
    ai: AiCfg = AiCfg()
    prompts: PromptsCfg = PromptsCfg()
    push: PushCfg = PushCfg()
    risk_free_rate: float = 0.018


class EnvSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FH_", env_file=".env", extra="ignore")
    data_dir: Path | None = None
    log_level: str | None = None
    tushare_token: str | None = None
    ai_enabled: bool | None = None
    ai_protocol: str | None = None
    ai_base_url: str | None = None
    ai_api_key: str | None = None
    ai_model: str | None = None
    ai_timeout: int | None = None
    ai_max_tokens: int | None = None
    ai_system_prompt: str | None = None
    push_enabled: bool | None = None
    push_provider: str | None = None
    push_mails: str | None = None
    push_smtp_host: str | None = None
    push_smtp_port: int | None = None
    push_smtp_user: str | None = None
    push_smtp_password: str | None = None
    push_smtp_use_tls: bool | None = None
    push_timeout: int | None = None
    push_max_chars: int | None = None


def load_config(path: str | Path = "configs/settings.yaml") -> AppConfig:
    p = Path(path)
    raw: dict[str, Any] = {}
    if p.exists():
        raw = yaml.safe_load(p.read_text()) or {}
    cfg = AppConfig(**raw)
    env = EnvSettings()
    if env.data_dir:
        cfg.data_dir = env.data_dir
    if env.log_level:
        cfg.log_level = env.log_level
    if env.ai_enabled is not None:
        cfg.ai.enabled = env.ai_enabled
    for attr, value in (
        ("protocol", env.ai_protocol),
        ("base_url", env.ai_base_url),
        ("api_key", env.ai_api_key),
        ("model", env.ai_model),
        ("system_prompt", env.ai_system_prompt),
    ):
        if value:
            setattr(cfg.ai, attr, value)
    if env.ai_timeout is not None:
        cfg.ai.timeout = env.ai_timeout
    if env.ai_max_tokens is not None:
        cfg.ai.max_tokens = env.ai_max_tokens
    if env.push_enabled is not None:
        cfg.push.enabled = env.push_enabled
    if env.push_provider:
        cfg.push.provider = env.push_provider
    if env.push_mails:
        cfg.push.mails = [m.strip() for m in env.push_mails.split(";") if m.strip()]
    if env.push_smtp_host:
        cfg.push.smtp_host = env.push_smtp_host
    if env.push_smtp_port is not None:
        cfg.push.smtp_port = env.push_smtp_port
    if env.push_smtp_user:
        cfg.push.smtp_user = env.push_smtp_user
    if env.push_smtp_password:
        cfg.push.smtp_password = env.push_smtp_password
    if env.push_smtp_use_tls is not None:
        cfg.push.smtp_use_tls = env.push_smtp_use_tls
    if env.push_timeout is not None:
        cfg.push.timeout = env.push_timeout
    if env.push_max_chars is not None:
        cfg.push.max_chars = env.push_max_chars
    cfg.data_dir.mkdir(parents=True, exist_ok=True)
    prompts_path = p.parent / "prompts.yaml"
    if prompts_path.exists():
        praw = yaml.safe_load(prompts_path.read_text()) or {}
        cfg.prompts = PromptsCfg(**praw)
    return cfg
