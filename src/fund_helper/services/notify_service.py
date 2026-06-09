"""Notification push adapters for generated analysis reports."""
from __future__ import annotations

import re
import smtplib
from dataclasses import dataclass
from email.mime.multipart import MIMEMultipart
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
    text = _strip_postprocess_comments(text)
    if len(text) <= max_chars:
        return text
    # Always keep the program-generated "零、数据可信度" section intact so
    # readers never see a bare "未接入" tail without the corrected table.
    return _truncate_preserving_availability(text, max_chars)


def _truncate_preserving_availability(text: str, max_chars: int) -> str:
    """Cut the report to ``max_chars`` while keeping section 零 verbatim.

    When the body is too long we drop later sections in order
    (六 → 五 → 四 …) until the excerpt fits, instead of truncating mid-row
    of an availability matrix that would leave dangling "❌ 缺失" cells.
    """
    if len(text) <= max_chars:
        return text

    # Find boundaries of top-level numbered sections (零, 一, 二, ...).
    section_starts = [m.start() for m in re.finditer(r"(?m)^#{1,6}\s*[零一二三四五六七八九十]+[、.．]", text)]
    if len(section_starts) < 2:
        return text[: max_chars - 40].rstrip() + "\n\n...(报告较长，已截断)"

    # Try dropping sections from the tail until we fit.
    suffix = "\n\n...(后续章节已省略，可查看完整报告)"
    for cut in range(len(section_starts), 1, -1):
        candidate = text[: section_starts[cut - 1]].rstrip() + suffix
        if len(candidate) <= max_chars:
            return candidate

    # If even the first two sections do not fit, fall back to a hard cut but
    # warn the reader that the structure was broken.
    return text[: max_chars - 80].rstrip() + "\n\n...(报告较长，邮件版本已强制截断；请查阅完整 Markdown)"


def _strip_postprocess_comments(text: str) -> str:
    """Remove the HTML comments inserted by _correct_availability_misclaims.

    These markers are useful inside the archived Markdown report (they explain
    why a row vanished), but in the email view they show up as raw HTML and
    confuse the reader. Strip them on render.
    """
    return re.sub(r"<!--\s*自动修正:[^>]*-->\s*\n?", "", text)


def push_report(cfg: AppConfig, path: Path, *, title: str | None = None) -> NotifyResult:
    if not cfg.push.enabled:
        raise RuntimeError("推送未启用，请设置 FH_PUSH_ENABLED=true")
    if not cfg.push.mails:
        raise RuntimeError("缺少收件人邮箱，请设置 FH_PUSH_MAILS")
    if not path.exists():
        raise FileNotFoundError(path)

    title = title or "Fund Helper 自动分析"
    content = build_report_excerpt(path, max_chars=cfg.push.max_chars)
    md_text = f"## {title}\n\n{content}"

    provider = cfg.push.provider.lower().strip()
    if provider == "smtp":
        return _send_smtp(cfg, md_text, title)
    raise RuntimeError(f"未知推送 provider: {cfg.push.provider}")


def _md_to_html(text: str) -> str:
    from markdown_it import MarkdownIt
    md = MarkdownIt("commonmark", {"breaks": True, "html": True})
    md.enable("table")
    return _wrap_tables(md.render(text))


def _wrap_tables(html: str) -> str:
    return re.sub(
        r"(<table>.*?</table>)",
        r'<div class="table-wrap">\1</div>',
        html,
        flags=re.DOTALL,
    )


_EMAIL_CSS = """
body { margin:0; padding:0; font-family:-apple-system,"PingFang SC","Microsoft YaHei","Helvetica Neue",Arial,sans-serif;
       font-size:16px; line-height:1.75; color:#1e293b; background:#f5f7fb; word-break:normal; }
.container { max-width:1080px; margin:0 auto; padding:24px 20px; }
.header { text-align:center; padding:18px 0 8px;
          border-bottom:2px solid #5b6cff; margin-bottom:20px; }
.header h1 { font-size:22px; color:#5b6cff; margin:0; }
.header .meta { font-size:12px; color:#94a3b8; margin-top:4px; }
.content { background:#fff; border-radius:14px; padding:28px 32px;
           box-shadow:0 1px 3px rgba(0,0,0,0.06); }
.content h2 { font-size:20px; color:#0f172a; margin:28px 0 12px;
              padding-bottom:6px; border-bottom:1px solid #e2e8f0; }
.content h3 { font-size:17px; color:#334155; margin:22px 0 10px; }
.content h4 { font-size:15px; color:#475569; margin:16px 0 8px; }
.content p { margin:8px 0 12px; }
.content strong { color:#0f172a; }
.content ul, .content ol { margin:6px 0 10px; padding-left:22px; }
.content li { margin:4px 0; }
.table-wrap { margin:16px 0 22px; overflow-x:auto; -webkit-overflow-scrolling:touch;
              border:1px solid #dbe3ef; border-radius:10px; background:#fff; }
.table-wrap table { min-width:860px; width:100%; border-collapse:separate; border-spacing:0;
                    margin:0; font-size:14px; table-layout:auto; }
.table-wrap th, .table-wrap td { padding:11px 14px; border:0; border-right:1px solid #e2e8f0;
                                 border-bottom:1px solid #e2e8f0; text-align:left; vertical-align:top;
                                 word-break:normal; overflow-wrap:break-word; }
.table-wrap th:last-child, .table-wrap td:last-child { border-right:0; }
.table-wrap tr:last-child td { border-bottom:0; }
.table-wrap th { background:#eef3f9; color:#334155; font-weight:700; font-size:13px;
                 white-space:nowrap; position:sticky; top:0; }
.table-wrap td:first-child, .table-wrap th:first-child { font-weight:700; color:#0f172a; white-space:nowrap; }
.table-wrap td:nth-child(2), .table-wrap th:nth-child(2),
.table-wrap td:nth-child(3), .table-wrap th:nth-child(3) { min-width:96px; }
.content tr:nth-child(even) td { background:#fafbfd; }
.content code { font-size:13px; padding:2px 6px; border-radius:4px;
                background:#f1f5f9; color:#e11d48; }
.content pre { font-size:12px; line-height:1.5; padding:12px 16px;
               border-radius:8px; background:#1e293b; color:#e2e8f0; overflow-x:auto; }
.content pre code { background:none; color:inherit; padding:0; }
.content blockquote { margin:10px 0 14px; padding:10px 16px;
                      border-left:3px solid #5b6cff; background:#eef0ff;
                      color:#475569; border-radius:0 8px 8px 0; }
.content hr { border:none; border-top:1px solid #e2e8f0; margin:16px 0; }
.content a { color:#5b6cff; text-decoration:none; }
.footer { text-align:center; font-size:11px; color:#94a3b8;
          padding:16px 0; margin-top:16px; border-top:1px solid #e2e8f0; }
@media (max-width: 720px) {
  .container { padding:12px 8px; }
  .content { padding:18px 14px; border-radius:10px; }
  body { font-size:15px; }
  .table-wrap table { min-width:780px; font-size:13px; }
  .table-wrap th, .table-wrap td { padding:9px 10px; }
}
"""


def _build_html_body(title: str, md_text: str, as_of: str) -> str:
    html_content = _md_to_html(md_text)
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><style>{_EMAIL_CSS}</style></head>
<body>
<div class="container">
  <div class="header">
    <h1>fund-helper</h1>
    <div class="meta">{as_of} · AI 自动生成</div>
  </div>
  <div class="content">
    {html_content}
  </div>
  <div class="footer">
    fund-helper · 基于公开数据的专业分析推演，不构成投资建议 · 市场有风险，投资需谨慎
  </div>
</div>
</body></html>"""


def _send_smtp(cfg: AppConfig, markdown: str, title: str) -> NotifyResult:
    if not cfg.push.smtp_host:
        raise RuntimeError("缺少 FH_PUSH_SMTP_HOST")
    if not cfg.push.smtp_user or not cfg.push.smtp_password:
        raise RuntimeError("缺少 SMTP 认证信息，请设置 FH_PUSH_SMTP_USER / FH_PUSH_SMTP_PASSWORD")

    from datetime import datetime, timezone, timedelta
    as_of = datetime.now(timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M CST")

    html_body = _build_html_body(title, markdown, as_of)
    plain_body = _strip_md(markdown)

    # multipart: plain text fallback + HTML
    msg = MIMEMultipart("alternative")
    msg["Subject"] = title
    msg["From"] = cfg.push.smtp_user
    msg["To"] = ", ".join(cfg.push.mails)
    msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

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


def _strip_md(text: str) -> str:
    """去除 Markdown 标记，保留可读纯文本."""
    text = re.sub(r"^#{1,4}\s+", "", text, flags=re.MULTILINE)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\|", " ", text)
    text = re.sub(r"^-{3,}$", "", text, flags=re.MULTILINE)
    return text.strip()
