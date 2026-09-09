from __future__ import annotations

import html
import os
import re
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


def _inline_markdown(value: str) -> str:
    escaped = html.escape(str(value or ""), quote=False)
    escaped = re.sub(
        r"\*\*(.+?)\*\*",
        r"<strong>\1</strong>",
        escaped,
    )
    escaped = re.sub(
        r"`([^\n]+?)`",
        r"<code>\1</code>",
        escaped,
    )
    return escaped


def _table_cells(line: str) -> list[str]:
    return [item.strip() for item in line.strip().strip("|").split("|")]


def _is_table_separator(line: str) -> bool:
    cells = _table_cells(line)
    return bool(cells) and all(
        bool(re.fullmatch(r":?-{3,}:?", cell.replace(" ", "")))
        for cell in cells
    )


def _markdown_html(text: str) -> str:
    """Render the small Markdown subset used by realtime stock emails.

    This deliberately avoids heavyweight Markdown dependencies while rendering
    headings, callouts, bullets, inline bold/code and GitHub-style tables into
    email-safe HTML. The plaintext MIME alternative remains available.
    """

    lines = str(text or "").splitlines()
    body: list[str] = []
    index = 0
    list_open = False

    def close_list() -> None:
        nonlocal list_open
        if list_open:
            body.append("</ul>")
            list_open = False

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()

        if not stripped:
            close_list()
            body.append('<div class="spacer"></div>')
            index += 1
            continue

        # GitHub-style table: header line + separator + data rows.
        if (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and _is_table_separator(lines[index + 1].strip())
        ):
            close_list()
            headers = _table_cells(stripped)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(_table_cells(lines[index].strip()))
                index += 1

            body.append('<div class="table-wrap"><table><thead><tr>')
            for cell in headers:
                body.append(f"<th>{_inline_markdown(cell)}</th>")
            body.append("</tr></thead><tbody>")
            for row in rows:
                body.append("<tr>")
                for pos, cell in enumerate(row):
                    cls = ' class="num"' if pos >= 3 else ""
                    body.append(f"<td{cls}>{_inline_markdown(cell)}</td>")
                body.append("</tr>")
            body.append("</tbody></table></div>")
            continue

        if stripped.startswith("### "):
            close_list()
            body.append(f"<h3>{_inline_markdown(stripped[4:])}</h3>")
        elif stripped.startswith("## "):
            close_list()
            body.append(f"<h2>{_inline_markdown(stripped[3:])}</h2>")
        elif stripped.startswith("# "):
            close_list()
            body.append(f"<h1>{_inline_markdown(stripped[2:])}</h1>")
        elif stripped.startswith("> "):
            close_list()
            body.append(
                f'<div class="callout">{_inline_markdown(stripped[2:])}</div>'
            )
        elif stripped.startswith("- "):
            if not list_open:
                body.append("<ul>")
                list_open = True
            body.append(f"<li>{_inline_markdown(stripped[2:])}</li>")
        else:
            close_list()
            body.append(f"<p>{_inline_markdown(stripped)}</p>")
        index += 1

    close_list()
    rendered = "".join(body)
    return f"""<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body {{
    margin: 0;
    padding: 0;
    background: #f5f6f8;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC",
      "Microsoft YaHei", Arial, sans-serif;
    color: #1f2328;
    line-height: 1.55;
  }}
  .shell {{
    max-width: 760px;
    margin: 0 auto;
    padding: 18px 12px 32px;
  }}
  .card {{
    background: #ffffff;
    border: 1px solid #dfe3e8;
    border-radius: 12px;
    padding: 20px;
  }}
  h1 {{
    font-size: 22px;
    line-height: 1.3;
    margin: 0 0 18px;
  }}
  h2 {{
    font-size: 18px;
    line-height: 1.35;
    margin: 26px 0 10px;
    padding-bottom: 7px;
    border-bottom: 1px solid #e6e9ed;
  }}
  h3 {{
    font-size: 16px;
    margin: 20px 0 8px;
  }}
  p {{
    margin: 8px 0;
  }}
  ul {{
    margin: 8px 0 14px;
    padding-left: 22px;
  }}
  li {{
    margin: 5px 0;
  }}
  strong {{
    font-weight: 700;
  }}
  code {{
    font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
    background: #f1f3f5;
    border-radius: 4px;
    padding: 1px 4px;
    font-size: 0.92em;
  }}
  .callout {{
    margin: 8px 0 16px;
    padding: 10px 12px;
    background: #f6f8fa;
    border-left: 4px solid #8c959f;
    border-radius: 6px;
  }}
  .table-wrap {{
    width: 100%;
    overflow-x: auto;
    margin: 12px 0 18px;
  }}
  table {{
    width: 100%;
    border-collapse: collapse;
    font-size: 13px;
  }}
  th {{
    text-align: left;
    background: #f6f8fa;
    border: 1px solid #dfe3e8;
    padding: 8px 7px;
    white-space: nowrap;
  }}
  td {{
    border: 1px solid #dfe3e8;
    padding: 8px 7px;
    vertical-align: top;
  }}
  td.num {{
    text-align: right;
    white-space: nowrap;
  }}
  .spacer {{
    height: 3px;
  }}
  @media (max-width: 560px) {{
    .shell {{
      padding: 8px 4px 20px;
    }}
    .card {{
      border-radius: 8px;
      padding: 14px 10px;
    }}
    h1 {{
      font-size: 20px;
    }}
    h2 {{
      font-size: 17px;
    }}
    table {{
      font-size: 12px;
    }}
  }}
</style>
</head>
<body>
<div class="shell"><div class="card">{rendered}</div></div>
</body>
</html>"""


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
    if not sender or not password:
        raise RuntimeError("EMAIL_SENDER/EMAIL_PASSWORD are required")
    if not receiver_list:
        receiver_list = [sender]

    host, port, use_ssl = _smtp_settings(sender)
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = f"{sender_name or os.getenv('EMAIL_SENDER_NAME') or 'AI 美股助手'} <{sender}>"
    msg["To"] = ", ".join(receiver_list)
    msg.set_content(str(markdown or ""))
    msg.add_alternative(_markdown_html(markdown), subtype="html")

    if use_ssl:
        with smtplib.SMTP_SSL(
            host,
            port,
            context=ssl.create_default_context(),
            timeout=20,
        ) as smtp:
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
