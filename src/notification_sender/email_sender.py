# -*- coding: utf-8 -*-
"""
Email 发送提醒服务

职责：
1. 通过 SMTP 发送 Email 消息
"""
import logging
import os
from typing import Optional, List
from datetime import datetime
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.mime.image import MIMEImage
from email.header import Header
from email.utils import formataddr
import smtplib

from data_provider.base import normalize_stock_code
from src.config import Config
from src.formatters import markdown_to_html_document, strip_hidden_markdown_metadata


logger = logging.getLogger(__name__)


SMTP_CONFIGS = {
    "qq.com": {"server": "smtp.qq.com", "port": 465, "ssl": True},
    "foxmail.com": {"server": "smtp.qq.com", "port": 465, "ssl": True},
    "163.com": {"server": "smtp.163.com", "port": 465, "ssl": True},
    "126.com": {"server": "smtp.126.com", "port": 465, "ssl": True},
    "gmail.com": {"server": "smtp.gmail.com", "port": 587, "ssl": False},
    "outlook.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "hotmail.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "live.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "sina.com": {"server": "smtp.sina.com", "port": 465, "ssl": True},
    "sohu.com": {"server": "smtp.sohu.com", "port": 465, "ssl": True},
    "aliyun.com": {"server": "smtp.aliyun.com", "port": 465, "ssl": True},
    "139.com": {"server": "smtp.139.com", "port": 465, "ssl": True},
}


def _env_truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _default_report_subject() -> str:
    date_str = datetime.now().strftime('%Y-%m-%d')
    if _env_truthy("V6_UNIFIED_EMAIL_FINAL", "false"):
        return f"📈 AI 美股综合日报 - {date_str}"
    return f"📈 股票智能分析报告 - {date_str}"


class EmailSender:

    def __init__(self, config: Config):
        self._email_config = {
            'sender': config.email_sender,
            'sender_name': getattr(config, 'email_sender_name', 'daily_stock_analysis股票分析助手'),
            'password': config.email_password,
            'receivers': config.email_receivers or ([config.email_sender] if config.email_sender else []),
        }
        self._stock_email_groups = getattr(config, 'stock_email_groups', None) or []

    def _is_upstream_unified_email_suppressed(self) -> bool:
        """Return whether this is the intentionally suppressed upstream V4 email."""
        return (
            os.getenv("GITHUB_ACTIONS") == "true"
            and _env_truthy("MERGE_EMAIL_NOTIFICATION", "false")
            and not _env_truthy("V6_UNIFIED_EMAIL_FINAL", "false")
        )

    def _is_email_configured(self) -> bool:
        """检查 SMTP 凭据是否完整；主动免发不等同于配置失败。"""
        return bool(self._email_config['sender'] and self._email_config['password'])

    def get_receivers_for_stocks(self, stock_codes: List[str]) -> List[str]:
        if not stock_codes or not self._stock_email_groups:
            return self._email_config['receivers']
        normalized_codes = [normalize_stock_code(c) for c in stock_codes]
        seen: set = set()
        result: List[str] = []
        for stocks, emails in self._stock_email_groups:
            for code in normalized_codes:
                if code in stocks:
                    for e in emails:
                        if e not in seen:
                            seen.add(e)
                            result.append(e)
                    break
        return result if result else self._email_config['receivers']

    def get_all_email_receivers(self) -> List[str]:
        seen: set = set()
        result: List[str] = []
        for _, emails in self._stock_email_groups:
            for e in emails:
                if e not in seen:
                    seen.add(e)
                    result.append(e)
        for e in self._email_config['receivers']:
            if e not in seen:
                seen.add(e)
                result.append(e)
        return result

    def _format_sender_address(self, sender: str) -> str:
        sender_name = self._email_config.get('sender_name') or '股票分析助手'
        return formataddr((str(Header(str(sender_name), 'utf-8')), sender))

    @staticmethod
    def _close_server(server: Optional[smtplib.SMTP]) -> None:
        if server is None:
            return
        try:
            server.quit()
        except Exception:
            try:
                server.close()
            except Exception:
                pass

    def send_to_email(
        self,
        content: str,
        subject: Optional[str] = None,
        receivers: Optional[List[str]] = None,
        *,
        timeout_seconds: Optional[float] = None,
    ) -> bool:
        """通过 SMTP 发送 Email；V4 主动免发返回成功，避免被统计成推送失败。"""
        if self._is_upstream_unified_email_suppressed():
            logger.info("统一日报模式已启用：上游 V4 邮件按设计免发，等待 V6 融合日报统一发送")
            return True
        if not self._is_email_configured():
            logger.warning("邮件配置不完整，跳过推送")
            return False

        sender = self._email_config['sender']
        password = self._email_config['password']
        receivers = receivers or self._email_config['receivers']
        server: Optional[smtplib.SMTP] = None

        try:
            if subject is None:
                subject = _default_report_subject()

            sanitized_content = strip_hidden_markdown_metadata(content).strip()
            html_content = markdown_to_html_document(sanitized_content)

            msg = MIMEMultipart('alternative')
            msg['Subject'] = Header(subject, 'utf-8')
            msg['From'] = self._format_sender_address(sender)
            msg['To'] = ', '.join(receivers)
            msg.attach(MIMEText(sanitized_content, 'plain', 'utf-8'))
            msg.attach(MIMEText(html_content, 'html', 'utf-8'))

            domain = sender.split('@')[-1].lower()
            smtp_config = SMTP_CONFIGS.get(domain)
            if smtp_config:
                smtp_server = smtp_config['server']
                smtp_port = smtp_config['port']
                use_ssl = smtp_config['ssl']
                logger.info(f"自动识别邮箱类型: {domain} -> {smtp_server}:{smtp_port}")
            else:
                smtp_server = f"smtp.{domain}"
                smtp_port = 465
                use_ssl = True
                logger.warning(f"未知邮箱类型 {domain}，尝试通用配置: {smtp_server}:{smtp_port}")

            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=timeout_seconds or 30)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=timeout_seconds or 30)
                server.starttls()

            server.login(sender, password)
            server.send_message(msg)
            logger.info(f"邮件发送成功，收件人: {receivers}")
            return True

        except smtplib.SMTPAuthenticationError:
            logger.error("邮件发送失败：认证错误，请检查邮箱和授权码是否正确")
            return False
        except smtplib.SMTPConnectError as e:
            logger.error(f"邮件发送失败：无法连接 SMTP 服务器 - {e}")
            return False
        except Exception as e:
            logger.error(f"发送邮件失败: {e}")
            return False
        finally:
            self._close_server(server)

    def _send_email_with_inline_image(
        self, image_bytes: bytes, receivers: Optional[List[str]] = None
    ) -> bool:
        """Send email with inline image attachment (Issue #289)."""
        if self._is_upstream_unified_email_suppressed():
            logger.info("统一日报模式已启用：上游 V4 图片邮件按设计免发")
            return True
        if not self._is_email_configured():
            return False
        sender = self._email_config['sender']
        password = self._email_config['password']
        receivers = receivers or self._email_config['receivers']
        server: Optional[smtplib.SMTP] = None
        try:
            subject = _default_report_subject()
            msg = MIMEMultipart('related')
            msg['Subject'] = Header(subject, 'utf-8')
            msg['From'] = self._format_sender_address(sender)
            msg['To'] = ', '.join(receivers)

            alt = MIMEMultipart('alternative')
            alt.attach(MIMEText('报告已生成，详见下方图片。', 'plain', 'utf-8'))
            html_body = (
                '<p>报告已生成，详见下方图片（点击可查看大图）：</p>'
                '<p><img src="cid:report-image" alt="股票分析报告" style="max-width:100%%;" /></p>'
            )
            alt.attach(MIMEText(html_body, 'html', 'utf-8'))
            msg.attach(alt)

            img_part = MIMEImage(image_bytes, _subtype='png')
            img_part.add_header('Content-Disposition', 'inline', filename='report.png')
            img_part.add_header('Content-ID', '<report-image>')
            msg.attach(img_part)

            domain = sender.split('@')[-1].lower()
            smtp_config = SMTP_CONFIGS.get(domain)
            if smtp_config:
                smtp_server, smtp_port = smtp_config['server'], smtp_config['port']
                use_ssl = smtp_config['ssl']
            else:
                smtp_server, smtp_port = f"smtp.{domain}", 465
                use_ssl = True

            if use_ssl:
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=30)
            else:
                server = smtplib.SMTP(smtp_server, smtp_port, timeout=30)
                server.starttls()
            server.login(sender, password)
            server.send_message(msg)
            logger.info("邮件（内联图片）发送成功，收件人: %s", receivers)
            return True
        except Exception as e:
            logger.error("邮件（内联图片）发送失败: %s", e)
            return False
        finally:
            self._close_server(server)
