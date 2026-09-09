from __future__ import annotations

import html
import os
import smtplib
import ssl
from email.message import EmailMessage
from typing import Iterable


def _receivers(value: str | None) -> list[str]:
    raw = str(value or "").replace(";", ",")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _smtp_settings(sender: str) -> tuple[str, int, bool]:
    domain = sender.rsplit("@", 1)[-1].lower()
    if domain in {"gmail.com", "googlemail.com"}:
        return "smtp.gmail.com", 465, True
    if domain in {"qq.com", "foxmail.com"}:
        return "smtp.qq.com", 465, True
    if domain in {"163.com", "126.com"}:
        return f"smtp.{domain}", 465, True
    if domain in {"outlook.com", "hotmail.com", "live.com"}:
        return "smtp-mail.outlook.com", 587, False
    host = str(os.getenv("SMTP_HOST") or "").strip()
    port = int(str(os.getenv("SMTP_PORT") or "465").strip())
    if not host:
        raise RuntimeError(
            f"cannot infer SMTP host for {domain!r}; set SMTP_HOST/SMTP_PORT"
        )
    return host, port, port == 465


def _markdown_html(text: str) -> str:
    escaped = html.escape(str(text or ""))
    return (
        "<html><body>"
        "<pre style=\"white-space:pre-wrap;font-family:-apple-system,BlinkMacSystemFont,"
        "'Segoe UI','PingFang SC','Microsoft YaHei',sans-serif;line-height:1.55\">"
        + escaped
        + "</pre></body></html>"
    )


def send_realtime_email(
    subject: str,
    markdown: str,
    *,
    sender: str | None = None,
    password: str | None = None,
    receivers: Iterable[str] | None = None,
    sender_name: str | None = None,
) -> bool:
    sender = str(sender or os.getenv("EMAIL_SENDER") or "").strip()
    password = str(password or os.getenv("EMAIL_PASSWORD") or "").strip()
    receiver_list = list(receivers or _receivers(os.getenv("EMAIL_RECEIVERS")))
    if not sender or not password or not receiver_list:
        raise RuntimeError("EMAIL_SENDER/EMAIL_PASSWORD/EMAIL_RECEIVERS are required")

    host, port, use_ssl = _smtp_settings(sender)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name or os.getenv('EMAIL_SENDER_NAME') or 'AI 美股助手'} <{sender}>"
    msg["To"] = ", ".join(receiver_list)
    msg.set_content(str(markdown or ""))
    msg.add_alternative(_markdown_html(markdown), subtype="html")

    if use_ssl:
        with smtplib.SMTP_SSL(host, port, context=ssl.create_default_context(), timeout=20) as smtp:
            smtp.login(sender, password)
            smtp.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=20) as smtp:
            smtp.ehlo()
            smtp.starttls(context=ssl.create_default_context())
            smtp.ehlo()
            smtp.login(sender, password)
            smtp.send_message(msg)
    return True
