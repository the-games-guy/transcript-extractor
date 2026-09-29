"""Sending summary emails over SMTP."""

from __future__ import annotations

import html
import re
import smtplib
import ssl
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

from .config import Settings


@dataclass
class EmailItem:
    title: str
    url: str
    channel: str
    summary: str
    markdown_path: Path | None = None


def _inline(text: str) -> str:
    text = html.escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)
    text = re.sub(r"(?<!\*)\*(?!\s)(.+?)(?<!\s)\*(?!\*)", r"<em>\1</em>", text)
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', text)
    return text


def markdown_to_html(md: str) -> str:
    """Minimal Markdown -> HTML for summary emails (headings, bullets, paragraphs)."""
    out: list[str] = []
    in_list = False
    paragraph: list[str] = []

    def flush_paragraph():
        if paragraph:
            out.append("<p>" + "<br>".join(_inline(p) for p in paragraph) + "</p>")
            paragraph.clear()

    for raw in md.splitlines():
        line = raw.rstrip()
        bullet = re.match(r"^\s*[-*+]\s+(.*)", line)
        heading = re.match(r"^(#{1,6})\s+(.*)", line)
        if bullet:
            flush_paragraph()
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(bullet.group(1))}</li>")
            continue
        if in_list:
            out.append("</ul>")
            in_list = False
        if heading:
            flush_paragraph()
            level = min(len(heading.group(1)) + 2, 6)
            out.append(f"<h{level}>{_inline(heading.group(2))}</h{level}>")
        elif not line.strip():
            flush_paragraph()
        else:
            paragraph.append(line.strip())
    flush_paragraph()
    if in_list:
        out.append("</ul>")
    return "\n".join(out)


def build_message(
    items: list[EmailItem],
    *,
    sender: str,
    recipients: list[str],
    subject: str | None = None,
) -> EmailMessage:
    if not items:
        raise ValueError("No items to email")
    if subject is None:
        subject = (
            f"Video summary: {items[0].title}"
            if len(items) == 1
            else f"{len(items)} new video summaries"
        )

    text_parts, html_parts = [], []
    for item in items:
        byline = f" - {item.channel}" if item.channel else ""
        saved = f"\nTranscript saved to: {item.markdown_path}" if item.markdown_path else ""
        text_parts.append(f"{item.title}{byline}\n{item.url}\n\n{item.summary}{saved}")
        html_parts.append(
            f'<h2><a href="{html.escape(item.url)}">{html.escape(item.title)}</a></h2>'
            + (f"<p><em>{html.escape(item.channel)}</em></p>" if item.channel else "")
            + markdown_to_html(item.summary)
            + (
                f'<p style="color:#666;font-size:12px">Transcript saved to '
                f"<code>{html.escape(str(item.markdown_path))}</code></p>"
                if item.markdown_path
                else ""
            )
        )

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(("\n\n" + "-" * 60 + "\n\n").join(text_parts))
    msg.add_alternative(
        '<html><body style="font-family:sans-serif;max-width:720px">'
        + "<hr>".join(html_parts)
        + "</body></html>",
        subtype="html",
    )
    return msg


def send_email(
    settings: Settings,
    items: list[EmailItem],
    *,
    recipients: list[str] | None = None,
    subject: str | None = None,
    attach_markdown: bool = True,
) -> None:
    settings.require_email()
    recipients = recipients or settings.email_to
    msg = build_message(items, sender=settings.email_from, recipients=recipients, subject=subject)
    if attach_markdown:
        for item in items:
            if item.markdown_path and Path(item.markdown_path).exists():
                msg.add_attachment(
                    Path(item.markdown_path).read_bytes(),
                    maintype="text",
                    subtype="markdown",
                    filename=Path(item.markdown_path).name,
                )

    context = ssl.create_default_context()
    if settings.smtp_port == 465:
        with smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, context=context) as smtp:
            _login_and_send(smtp, settings, msg)
    else:
        with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=30) as smtp:
            if settings.smtp_use_tls:
                smtp.starttls(context=context)
            _login_and_send(smtp, settings, msg)


def _login_and_send(smtp: smtplib.SMTP, settings: Settings, msg: EmailMessage) -> None:
    if settings.smtp_username and settings.smtp_password:
        smtp.login(settings.smtp_username, settings.smtp_password)
    smtp.send_message(msg)
