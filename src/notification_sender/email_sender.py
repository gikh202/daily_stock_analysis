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


# SMTP 服务器配置（自动识别）
SMTP_CONFIGS = {
    # QQ邮箱
    "qq.com": {"server": "smtp.qq.com", "port": 465, "ssl": True},
    "foxmail.com": {"server": "smtp.qq.com", "port": 465, "ssl": True},
    # 网易邮箱
    "163.com": {"server": "smtp.163.com", "port": 465, "ssl": True},
    "126.com": {"server": "smtp.126.com", "port": 465, "ssl": True},
    # Gmail
    "gmail.com": {"server": "smtp.gmail.com", "port": 587, "ssl": False},
    # Outlook
    "outlook.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "hotmail.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    "live.com": {"server": "smtp-mail.outlook.com", "port": 587, "ssl": False},
    # 新浪
    "sina.com": {"server": "smtp.sina.com", "port": 465, "ssl": True},
    # 搜狐
    "sohu.com": {"server": "smtp.sohu.com", "port": 465, "ssl": True},
    # 阿里云
    "aliyun.com": {"server": "smtp.aliyun.com", "port": 465, "ssl": True},
    # 139邮箱
    "139.com": {"server": "smtp.139.com", "port": 465, "ssl": True},
}


def _env_truthy(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default) or "").strip().lower() in {"1", "true", "yes", "on"}


def _stabilize_html_tables(html_content: str) -> str:
    """Keep Markdown tables as real tables in email clients such as Gmail.

    The shared HTML formatter intentionally uses ``display:block`` so tables can
    scroll in browser/image contexts. Some mail clients flatten header cells when
    that rule is preserved. Final email HTML therefore overrides the table box
    model inline, which survives email CSS sanitization more reliably.
    """
    return str(html_content or "").replace(
        "<table>",
        '<table style="display:table !important;border-collapse:collapse;'
        'width:100%;table-layout:auto;">',
    )


def _default_report_subject(content: str = "") -> str:
    date_str = datetime.now().strftime('%Y-%m-%d')
    if _env_truthy("V6_UNIFIED_EMAIL_FINAL", "false"):
        try:
            from src.v6_daily.accuracy_report import extract_investor_email_subject

            subject = extract_investor_email_subject(content)
            if subject:
                return subject[:180]
        except Exception:
            # Subject metadata is optional; never block delivery because of it.
            pass
        return f"📈 美股决策日报 - {date_str}"
    return f"📈 股票智能分析报告 - {date_str}"


class EmailSender:
    
    def __init__(self, config: Config):
        """
        初始化 Email 配置

        Args:
            config: 配置对象
        """
        self._email_config = {
            'sender': config.email_sender,
            'sender_name': getattr(config, 'email_sender_name', 'daily_stock_analysis股票分析助手'),
            'password': config.email_password,
            'receivers': config.email_receivers or ([config.email_sender] if config.email_sender else []),
        }
        self._stock_email_groups = getattr(config, 'stock_email_groups', None) or []

    @staticmethod
    def _is_upstream_unified_email_suppressed() -> bool:
        """Return whether this is the intentionally suppressed upstream V4 mail."""
        return (
            os.getenv("GITHUB_ACTIONS") == "true"
            and _env_truthy("MERGE_EMAIL_NOTIFICATION", "false")
            and not _env_truthy("V6_UNIFIED_EMAIL_FINAL", "false")
        )
        
    def _is_email_configured(self) -> bool:
        """检查邮件配置是否完整，并支持 V4→V6 单封综合日报模式。"""
        # 00-daily-analysis.yml 已默认注入 MERGE_EMAIL_NOTIFICATION=true。
        # 在 GitHub Actions 中启用该模式时，V4 仍生成报告/数据库/Artifact，
        # 但不直接发送邮件；随后 V6 workflow 下载同一次 V4 Artifact，合并后
        # 再发送唯一的最终中文综合日报。V6 workflow 不注入这个上游开关。
        if self._is_upstream_unified_email_suppressed():
            logger.info("统一日报模式已启用：跳过上游 V4 邮件，等待 V6 综合日报统一发送")
            return False
        return bool(self._email_config['sender'] and self._email_config['password'])
    
    def get_receivers_for_stocks(self, stock_codes: List[str]) -> List[str]:
        """
        Look up email receivers for given stock codes based on stock_email_groups.
        Returns union of receivers for all matching groups; falls back to default if none match.
        Stock codes are canonicalized before comparison so that equivalent
        formats (e.g. SH600519 vs 600519) match correctly.
        """
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
        """
        Return union of all configured email receivers (all groups + default).
        Used for market review which should go to everyone.
        """
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
        """Encode display name safely so non-ASCII sender names work across SMTP providers."""
        sender_name = self._email_config.get('sender_name') or '股票分析助手'
        return formataddr((str(Header(str(sender_name), 'utf-8')), sender))

    @staticmethod
    def _close_server(server: Optional[smtplib.SMTP]) -> None:
        """Best-effort SMTP cleanup to avoid leaving sockets open on header/build errors.

        Exceptions from quit()/close() are intentionally silenced — connection may already
        be in a broken state, and there is nothing useful to do at this point.
        """
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
        """
        通过 SMTP 发送 Email（最终 V6 日报使用精简投资者视图）。
        
        Args:
            content: 邮件内容（支持 Markdown，会转换为 HTML）
            subject: 邮件主题（可选，默认自动生成）
            receivers: 收件人列表（可选，默认使用配置的 receivers）
            
        Returns:
            是否发送成功；统一日报上游免发属于预期完成，不计为失败。
        """
        if self._is_upstream_unified_email_suppressed():
            logger.info("统一日报模式：上游邮件已按策略免发，本步骤视为完成；最终邮件由 V6 综合日报发送")
            return True

        if not self._is_email_configured():
            logger.warning("邮件配置不完整，跳过推送")
            return False
        
        sender = self._email_config['sender']
        password = self._email_config['password']
        receivers = receivers or self._email_config['receivers']
        server: Optional[smtplib.SMTP] = None
        
        try:
            prepared_content = strip_hidden_markdown_metadata(content).strip()
            if _env_truthy("V6_UNIFIED_EMAIL_FINAL", "false"):
                try:
                    from src.v6_daily.accuracy_report import build_investor_email_markdown

                    prepared_content = build_investor_email_markdown(prepared_content)
                except Exception as exc:
                    logger.warning("投资者邮件视图生成失败，回退完整日报: %s", exc)

            # 生成主题；最终综合日报优先读取投资者视图中的隐藏主题元数据。
            if subject is None:
                subject = _default_report_subject(prepared_content)

            sanitized_content = strip_hidden_markdown_metadata(prepared_content).strip()
            
            # 将 Markdown 转换为 HTML；正文已在上一步收敛为适合邮件阅读的结构。
            html_content = markdown_to_html_document(sanitized_content)
            if _env_truthy("V6_UNIFIED_EMAIL_FINAL", "false"):
                html_content = _stabilize_html_tables(html_content)
            
            # 构建邮件
            msg = MIMEMultipart('alternative')
            msg['Subject'] = Header(subject, 'utf-8')
            msg['From'] = self._format_sender_address(sender)
            msg['To'] = ', '.join(receivers)
            
            # 添加纯文本和 HTML 两个版本
            text_part = MIMEText(sanitized_content, 'plain', 'utf-8')
            html_part = MIMEText(html_content, 'html', 'utf-8')
            msg.attach(text_part)
            msg.attach(html_part)
            
            # 自动识别 SMTP 配置
            domain = sender.split('@')[-1].lower()
            smtp_config = SMTP_CONFIGS.get(domain)
            
            if smtp_config:
                smtp_server = smtp_config['server']
                smtp_port = smtp_config['port']
                use_ssl = smtp_config['ssl']
                logger.info(f"自动识别邮箱类型: {domain} -> {smtp_server}:{smtp_port}")
            else:
                # 未知邮箱，尝试通用配置
                smtp_server = f"smtp.{domain}"
                smtp_port = 465
                use_ssl = True
                logger.warning(f"未知邮箱类型 {domain}，尝试通用配置: {smtp_server}:{smtp_port}")
            
            # 根据配置选择连接方式
            if use_ssl:
                # SSL 连接（端口 465）
                server = smtplib.SMTP_SSL(smtp_server, smtp_port, timeout=timeout_seconds or 30)
            else:
                # TLS 连接（端口 587）
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
            logger.info("统一日报模式：上游图片邮件已按策略免发，本步骤视为完成")
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