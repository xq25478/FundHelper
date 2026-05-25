"""Notification push adapters for generated analysis reports."""
from __future__ import annotations

import re
import smtplib
from dataclasses import dataclass
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

from ..config import AppConfig


@dataclass(slots=True)
class NotifyResult:
    provider: str
    ok: bool
    message: str
    response: dict[str, Any] | None = None


def build_report_excerpt(path: Path, *, max_chars: int) -> str:
    text = path.read_text(encoding="utf-8")
    text = _strip_details(text).strip()
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 40].rstrip() + "\n\n...(报告较长，已截断)"


def push_report(cfg: AppConfig, path: Path, *, title: str | None = None) -> NotifyResult:
    if not cfg.push.enabled:
        raise RuntimeError("推送未启用，请设置 FH_PUSH_ENABLED=true")
    if not cfg.push.mails:
        raise RuntimeError("缺少收件人邮箱，请设置 FH_PUSH_MAILS")
    if not path.exists():
        raise FileNotFoundError(path)

    title = title or "Fund Helper 自动分析"
    content = build_report_excerpt(path, max_chars=cfg.push.max_chars)
    markdown = f"## {title}\n\n{content}"

    provider = cfg.push.provider.lower().strip()
    if provider == "smtp":
        return _send_smtp(cfg, markdown, title)
    raise RuntimeError(f"未知推送 provider: {cfg.push.provider}")


def _send_smtp(cfg: AppConfig, markdown: str, title: str) -> NotifyResult:
    if not cfg.push.smtp_host:
        raise RuntimeError("缺少 FH_PUSH_SMTP_HOST")
    if not cfg.push.smtp_user or not cfg.push.smtp_password:
        raise RuntimeError("缺少 SMTP 认证信息，请设置 FH_PUSH_SMTP_USER / FH_PUSH_SMTP_PASSWORD")

    msg = MIMEText(markdown, "plain", "utf-8")
    msg["Subject"] = title
    msg["From"] = cfg.push.smtp_user
    msg["To"] = ", ".join(cfg.push.mails)

    try:
        if cfg.push.smtp_port == 465 or cfg.push.smtp_use_tls:
            server = smtplib.SMTP_SSL(cfg.push.smtp_host, cfg.push.smtp_port, timeout=cfg.push.timeout)
        else:
            server = smtplib.SMTP(cfg.push.smtp_host, cfg.push.smtp_port, timeout=cfg.push.timeout)
            server.starttls()
        server.login(cfg.push.smtp_user, cfg.push.smtp_password)
        server.sendmail(cfg.push.smtp_user, cfg.push.mails, msg.as_string())
        server.quit()
    except Exception as e:
        raise RuntimeError(f"邮件发送失败: {e}") from e

    return NotifyResult(
        provider="smtp",
        ok=True,
        message=f"已发送至 {len(cfg.push.mails)} 个收件人",
    )


def _strip_details(text: str) -> str:
    return re.sub(r"<details>.*?</details>", "", text, flags=re.DOTALL)