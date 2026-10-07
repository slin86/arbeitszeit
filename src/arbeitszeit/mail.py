"""E-Mail-Versand über einen externen SMTP-Server (z. B. das Postfach beim Domain-Anbieter).

Zustellbarkeit hängt vor allem daran, dass die Mail über den SMTP-Server der Absenderdomain
geht (dann greifen SPF/DKIM des Anbieters) und die Absenderadresse zur Domain passt.
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formataddr, formatdate, make_msgid

from .config import Settings

log = logging.getLogger("arbeitszeit.mail")


def build_message(settings: Settings, to: str, subject: str, text: str) -> EmailMessage:
    assert settings.smtp_from
    msg = EmailMessage()
    msg["From"] = formataddr((settings.smtp_from_name, settings.smtp_from))
    msg["To"] = to
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    # Message-ID mit der Domain des Absenders (kein "localhost"/Container-Hostname → weniger Spam-Punkte)
    msg["Message-ID"] = make_msgid(domain=settings.smtp_from.rsplit("@", 1)[-1])
    msg["Auto-Submitted"] = "auto-generated"  # verhindert Abwesenheits-Antwortschleifen
    if settings.smtp_reply_to:
        msg["Reply-To"] = settings.smtp_reply_to
    msg.set_content(text)
    return msg


def deliver(msg: EmailMessage, settings: Settings) -> None:
    """Verschickt eine Nachricht. Wirft bei Fehlern (Aufrufer entscheidet, ob das fatal ist)."""
    ctx = ssl.create_default_context()
    if settings.smtp_security == "ssl":
        server: smtplib.SMTP = smtplib.SMTP_SSL(settings.smtp_host, settings.smtp_port, timeout=20, context=ctx)
    else:
        server = smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20)
    with server:
        server.ehlo()
        if settings.smtp_security == "starttls":
            server.starttls(context=ctx)
            server.ehlo()
        if settings.smtp_user:
            server.login(settings.smtp_user, settings.smtp_password or "")
        server.send_message(msg)


def send_many(messages: list[EmailMessage], settings: Settings) -> None:
    """Hintergrund-Task: Fehler werden geloggt, brechen aber nie die Web-Anfrage ab."""
    if not settings.mail_enabled:
        return
    for msg in messages:
        try:
            deliver(msg, settings)
        except Exception:  # noqa: BLE001
            log.exception("E-Mail an %s konnte nicht gesendet werden", msg["To"])
