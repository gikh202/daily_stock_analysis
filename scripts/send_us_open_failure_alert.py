from __future__ import annotations

import argparse
import os
import smtplib
from email.header import Header
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.utils import formataddr
from pathlib import Path
from typing import Iterable


SMTP_CONFIGS = {
    "qq.com": ("smtp.qq.com", 465, True),
    "foxmail.com": ("smtp.qq.com", 465, True),
    "163.com": ("smtp.163.com", 465, True),
    "126.com": ("smtp.126.com", 465, True),
    "gmail.com": ("smtp.gmail.com", 587, False),
    "outlook.com": ("smtp-mail.outlook.com", 587, False),
    "hotmail.com": ("smtp-mail.outlook.com", 587, False),
    "live.com": ("smtp-mail.outlook.com", 587, False),
    "sina.com": ("smtp.sina.com", 465, True),
    "sohu.com": ("smtp.sohu.com", 465, True),
    "aliyun.com": ("smtp.aliyun.com", 465, True),
    "139.com": ("smtp.139.com", 465, True),
}


def _split_receivers(raw: str | None) -> list[str]:
    text = str(raw or "").replace(";", ",")
    return [item.strip() for item in text.split(",") if item.strip()]


def _first_failure(items: Iterable[tuple[str, str]]) -> str:
    for label, outcome in items:
        normalized = str(outcome or "").strip().lower()
        if normalized == "failure":
            return label
    return "开盘确认流程在生成成功邮件前失败"


def build_message(
    *,
    session_date: str,
    run_url: str,
    prior_v6_outcome: str,
    plan_outcome: str,
    confirmation_outcome: str,
) -> tuple[str, str]:
    failure_point = _first_failure(
        (
            ("查找上一收盘 V6 计划失败", prior_v6_outcome),
            ("校验上一收盘结构化计划失败", plan_outcome),
            ("运行开盘实时确认或发送邮件失败", confirmation_outcome),
        )
    )
    subject = f"⚠️ 美股开盘确认失败 - {session_date}"
    body = "\n".join(
        [
            f"# 美股开盘确认失败 · {session_date}",
            "",
            f"- **失败位置**：{failure_point}",
            f"- **V6 计划查找**：{prior_v6_outcome or '未执行/未知'}",
            f"- **结构化计划校验**：{plan_outcome or '未执行/未知'}",
            f"- **开盘确认/邮件发送**：{confirmation_outcome or '未执行/未知'}",
            f"- **GitHub Actions**：{run_url}",
            "",
            "本邮件仅表示自动开盘确认链路失败，当前不要依据缺失/陈旧数据建立新仓。",
            "后续开盘补偿候选会继续按计划尝试；只有正式开盘确认邮件发送成功后，才会写入当日成功标记。",
        ]
    )
    return subject, body


def send_failure_email(subject: str, body: str) -> bool:
    sender = str(os.getenv("EMAIL_SENDER") or "").strip()
    password = str(os.getenv("EMAIL_PASSWORD") or "").strip()
    receivers = _split_receivers(os.getenv("EMAIL_RECEIVERS"))
    sender_name = str(os.getenv("EMAIL_SENDER_NAME") or "AI 美股开盘执行确认").strip()

    if not sender or not password:
        print("EMAIL_SENDER/EMAIL_PASSWORD 未配置，无法发送开盘失败告警。")
        return False
    if not receivers:
        receivers = [sender]

    domain = sender.rsplit("@", 1)[-1].lower() if "@" in sender else ""
    host, port, use_ssl = SMTP_CONFIGS.get(domain, (f"smtp.{domain}", 465, True))

    msg = MIMEMultipart("alternative")
    msg["Subject"] = Header(subject, "utf-8")
    msg["From"] = formataddr((str(Header(sender_name, "utf-8")), sender))
    msg["To"] = ", ".join(receivers)
    msg.attach(MIMEText(body, "plain", "utf-8"))

    server: smtplib.SMTP | None = None
    try:
        if use_ssl:
            server = smtplib.SMTP_SSL(host, port, timeout=30)
        else:
            server = smtplib.SMTP(host, port, timeout=30)
            server.starttls()
        server.login(sender, password)
        server.send_message(msg)
        print(f"开盘失败告警邮件发送成功: {receivers}")
        return True
    except Exception as exc:
        print(f"开盘失败告警邮件发送失败: {type(exc).__name__}: {exc}")
        return False
    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                try:
                    server.close()
                except Exception:
                    pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Send a once-per-day U.S. open confirmation failure email")
    parser.add_argument("--session-date", required=True)
    parser.add_argument("--run-url", required=True)
    parser.add_argument("--prior-v6-outcome", default="")
    parser.add_argument("--plan-outcome", default="")
    parser.add_argument("--confirmation-outcome", default="")
    parser.add_argument("--output-dir", default="open_confirmation_reports")
    args = parser.parse_args()

    subject, body = build_message(
        session_date=args.session_date,
        run_url=args.run_url,
        prior_v6_outcome=args.prior_v6_outcome,
        plan_outcome=args.plan_outcome,
        confirmation_outcome=args.confirmation_outcome,
    )

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "us_open_confirmation_failure.md").write_text(body + "\n", encoding="utf-8")

    return 0 if send_failure_email(subject, body) else 2


if __name__ == "__main__":
    raise SystemExit(main())
